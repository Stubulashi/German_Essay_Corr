"""标准答题卷自动找平与姓名区裁剪(双类定位块检测 + 透视校正;纯 PIL,零新依赖)

与前端打印模板(/print/answer-sheet,AnswerSheetPage.tsx)共享同一几何,
规范画布为精确 mm 映射:1500×2121 px,A4 210×297mm,px/mm = 1500/210 ≈ 7.1429。

定位块(两类多排):
- 主定位块 ×4:16×16mm 实心黑方,外侧距页边 12mm(中心 20mm)→ 规范坐标 (142.9,142.9) 等;
- 次定位块 ×2:8×8mm,y=68mm、左右 x=12/190mm → 用于姓名区与正文区的分界校验。

检测策略(块真实性三级校验,防“背景/噪点假阳性”):
1. 缩略图 48×48 网格采样 → 连通暗簇候选(按距外角升序,最多 3 个);
2. 每个候选在原分辨率窗口内做“实心黑方块”校验:暗占比、bbox 填充率≥0.55、
   宽高比 0.45-2.2、尺寸下限、排斥整窗近全黑(纯背景)——唯有通过者才作为块心;
3. 仅 3 角命中时按平行四边形假设外推第 4 角(限点落页内;缺 ≥2 角放弃)。

处理流程:
1. 源四边形外推为“整页四角”(块心距页边 20mm,块心框=170×257mm),
   QUAD 直接输出全幅 1500×2121 —— 块完整保留且坐标与理论值精确对应
   (注:QUAD 会把源四边形之外全部裁掉,故必须外推到页边界,否则会裁掉半个角块);
2. 空白度校验(极端误检防护);任何异常 → 原字节回退,绝不阻断上传。

姓名/学号信息区裁切(依据定位点,见 crop_name_region_ex):
- 规范画布输入:四角“理论锚定”检测(4/4 命中黑块方可信) → 规范几何裁切;
- 原始照片输入:定位点检测 → 找平 → 画布上锚定复检 → 规范几何裁切;
- 无定位点依据 → 返回 None(不裁;由调用方决定整图识别等回退,不做启发式裁剪)。

姓名/学号信息区(规范坐标,与模板互注):
- 姓名+学号两框区域 ≈ (86, 310)–(1086, 416);次定位块中心 y≈514 可校准下界;
- 正文书写区:y ≥ 600(84mm 起,9mm 行距 21 行)。

设计原则:任何异常(解码失败/未命中/校验不过)一律安全回退(找平回原字节/裁剪返回 None),
绝不导致上传失败,也绝不以未经验证的“定位块”污染输出。
"""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

#: 规范画布(px)与精确 mm 映射
CANON_WIDTH = 1500
CANON_HEIGHT = 2121
PX_PER_MM = CANON_WIDTH / 210  # ≈7.1429
#: 主定位块中心(理论规范坐标;TL,TR,BR,BL)
BLOCK_CENTER_PX = round(20 * PX_PER_MM, 1)  # 142.9
TARGET_CORNERS: tuple[tuple[float, float], ...] = (
    (BLOCK_CENTER_PX, BLOCK_CENTER_PX),
    (round(CANON_WIDTH - BLOCK_CENTER_PX, 1), BLOCK_CENTER_PX),
    (round(CANON_WIDTH - BLOCK_CENTER_PX, 1), round(CANON_HEIGHT - BLOCK_CENTER_PX, 1)),
    (BLOCK_CENTER_PX, round(CANON_HEIGHT - BLOCK_CENTER_PX, 1)),
)
#: 次定位块中心(理论规范坐标;用于姓名/正文分界)
MARKER_TARGET_CORNERS: tuple[tuple[float, float], ...] = (
    (round(16 * PX_PER_MM, 1), round(72 * PX_PER_MM, 1)),  # 左:12+8/2=16mm 中心 x;68+8/2=72mm 中心 y
    (round((210 - 16) * PX_PER_MM, 1), round(72 * PX_PER_MM, 1)),
)
#: 姓名+学号信息区裁剪框(左,上,右,下;规范 px)
NAME_REGION_PX: tuple[int, int, int, int] = (86, 310, 1086, 416)

# ---------- 页底“类型带”定位点(编码纸张类型;表驱动可扩展) ----------
#: 类型带中心 y(与底主块同带中部;单位 mm)
TYPE_BAND_Y_MM = 277.0
#: 三槽位(L/C/R)中心 x(单位 mm);槽位块 8mm,与底角块互不干扰
TYPE_SLOT_X_MM: tuple[float, float, float] = (90.0, 105.0, 120.0)
#: 槽位块尺寸(mm)
TYPE_MARK_SIZE_MM = 8.0
#: 位串(1=有块)→纸张类型映射;未登记组合/无块 → home(首页,兼容旧卷);
#: 表驱动设计:未来新增用途(如 "100"=草稿页)仅追加映射,检测零改动
PAGE_TYPE_MAP: dict[str, str] = {"101": "continuation"}
PAGE_TYPE_HOME = "home"

#: 定位块搜索窗口(图像四象限的比例范围;容忍小角度旋转)
_QUADRANT = 0.40
#: 黑色判定阈值
_DARK_MAX = 60
#: 最小可处理边长(过小的图不处理)
_MIN_SIDE = 400
#: 缩略图粗定位网格(采样点/边)与最小连通簇(格数)
_COARSE_GRID = 48
_COARSE_CLUSTER_MIN = 4
#: 块真实性校验(窗口下采样 96×96):暗占比下限 / bbox 填充率下限 /
#: 宽高比范围 / 最小边长(占窗口) / bbox 面积占比上限(排斥整窗近全黑=纯背景)
_SAMPLE_SIDE = 96
_WINDOW_DARK_MIN = 0.06
_BLOCK_FILL_MIN = 0.55
_BLOCK_ASPECT_RANGE = (0.45, 2.2)
_BLOCK_SIZE_MIN = 0.10
_BLOCK_AREA_MAX = 0.92
#: 规范画布判定容差与“理论锚定”检测半径(px)
_CANON_TOLERANCE = 4
_CANON_ANCHOR_RADIUS = 64
#: 次定位块校验窗口半径(px)
_MARKER_WINDOW = 26
#: 类型带槽位窗口半径与判定阈值(槽位块 8mm≈57px;窗口 64×64≈4096px,块占满≈3249)
_TYPE_WINDOW = 32
_TYPE_DARK_MIN = 1200


def _block_center_in_window(
    gray: Image.Image,
    approx: tuple[float, float],
    radius: int,
    expected_size: float | None = None,
) -> tuple[float, float] | None:
    """在原图窗口内寻找“实心黑方块”并返回其中心(块真实性三级校验)

    与旧的“近角质心”不同:本函数要求窗口内存在一个形状合格、填充率高的暗块,
    专治“背景黑带/孤立噪点/纸面纹理被当成定位块”的假阳性:
    - 窗口下采样 96×96 统计;需存在显著暗块(总占比≥0.06);
    - 暗像素 bbox:排斥整窗近全黑(纯背景)、最小边长、宽高比 0.45-2.2、
      填充率 ≥0.55(实心度);
    - expected_size(块边长估计,px)给定时,bbox 长边需落在 0.35-1.6 倍区间
      (杀“黑带穿窗”伪装的细长实心条;贴窗被裁的真块仍可命中);
    未命中返回 None(调用方按“缺角”处理,而非退回粗值)。
    """
    x0 = max(0, int(approx[0] - radius))
    y0 = max(0, int(approx[1] - radius))
    x1 = min(gray.width, int(approx[0] + radius))
    y1 = min(gray.height, int(approx[1] + radius))
    if x1 - x0 < 12 or y1 - y0 < 12:
        return None
    side = _SAMPLE_SIDE
    sample = gray.crop((x0, y0, x1, y1)).resize((side, side))
    data = sample.tobytes()
    dark_indices = [index for index, value in enumerate(data) if value <= _DARK_MAX]
    if len(dark_indices) < side * side * _WINDOW_DARK_MIN:
        return None
    min_x, min_y, max_x, max_y = side, side, -1, -1
    for index in dark_indices:
        xx, yy = index % side, index // side
        if xx < min_x:
            min_x = xx
        if xx > max_x:
            max_x = xx
        if yy < min_y:
            min_y = yy
        if yy > max_y:
            max_y = yy
    box_w = max_x - min_x + 1
    box_h = max_y - min_y + 1
    area = box_w * box_h
    if area >= side * side * _BLOCK_AREA_MAX:  # 整窗近全黑:纯背景,非块
        return None
    if box_w < side * _BLOCK_SIZE_MIN or box_h < side * _BLOCK_SIZE_MIN:
        return None  # 过小:噪点
    aspect = box_w / box_h
    if not (_BLOCK_ASPECT_RANGE[0] <= aspect <= _BLOCK_ASPECT_RANGE[1]):
        return None  # 细长黑带/黑线
    if len(dark_indices) / area < _BLOCK_FILL_MIN:
        return None  # 填充率不足:文字/纹理
    scale_x = (x1 - x0) / side
    scale_y = (y1 - y0) / side
    if expected_size is not None:
        actual_long = max(box_w * scale_x, box_h * scale_y)
        if actual_long > expected_size * 1.6 or actual_long < expected_size * 0.35:
            return None  # 尺寸先验不符:黑带穿窗/异常大团
    return x0 + (min_x + max_x + 1) / 2 * scale_x, y0 + (min_y + max_y + 1) / 2 * scale_y


def _corner_candidates(
    thumb: Image.Image, box: tuple[int, int, int, int], corner: tuple[int, int]
) -> list[tuple[float, float]]:
    """缩略图窗口内的“稠密暗簇”候选(按质心距外角升序;最多 3 个)

    替代旧的“近角 1/4 暗像素质心”粗定位:后者会被背景暗带/孤立噪点拉偏;
    本实现先做网格连通聚类,再按距离排序——每个候选仍将在原分辨率窗口内
    做块真实性校验(_block_center_in_window),唯有通过者才被采信。
    """
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    if width < 8 or height < 8:
        return []
    pixels = thumb.load()
    step = max(1, min(width, height) // _COARSE_GRID)
    cols = max(1, width // step)
    rows = max(1, height // step)
    cells: set[tuple[int, int]] = set()
    for gy in range(rows):
        for gx in range(cols):
            px = x0 + gx * step + step // 2
            py = y0 + gy * step + step // 2
            if px < thumb.width and py < thumb.height and pixels[px, py] <= _DARK_MAX:
                cells.add((gx, gy))
    if not cells:
        return []
    clusters: list[set[tuple[int, int]]] = []
    while cells:
        seed = cells.pop()
        component = {seed}
        stack = [seed]
        while stack:
            cx, cy = stack.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbor = (cx + dx, cy + dy)
                    if neighbor in cells:
                        cells.remove(neighbor)
                        component.add(neighbor)
                        stack.append(neighbor)
        if len(component) >= _COARSE_CLUSTER_MIN:
            clusters.append(component)
    centers: list[tuple[float, float]] = []
    for component in clusters:
        mean_x = sum(cell[0] for cell in component) / len(component)
        mean_y = sum(cell[1] for cell in component) / len(component)
        centers.append((x0 + mean_x * step + step / 2, y0 + mean_y * step + step / 2))
    centers.sort(key=lambda p: (p[0] - corner[0]) ** 2 + (p[1] - corner[1]) ** 2)
    return centers[:3]


def _detect_fiducials(image: Image.Image) -> list[tuple[float, float]] | None:
    """返回主定位块中心 [TL, TR, BR, BL](原图坐标系)

    精度策略(块真实性三级校验,一次成型):
    1. 缩略图 48×48 网格采样 → 四象限“稠密暗簇”候选(距角升序,≤3 个/角);
    2. 每个候选映射回原分辨率,在窗口内做“实心黑方块”校验
       (_block_center_in_window:填充率/宽高比/尺寸/排斥纯背景),
       首个通过者即该角块心——背景暗带/噪点无法伪装成方块;
    3. 仅缺 1 角时按平行四边形假设外推;缺 ≥2 返回 None。
    """
    gray = image.convert("L")
    width, height = gray.size
    if min(width, height) < _MIN_SIDE:
        return None
    scale = max(width, height) / 800.0
    if scale > 1:
        thumb = gray.resize((max(1, int(width / scale)), max(1, int(height / scale))))
    else:
        thumb = gray
        scale = 1.0
    tw, th = thumb.size
    qw = int(tw * _QUADRANT)
    qh = int(th * _QUADRANT)
    windows = [
        (0, 0, qw, qh, (0, 0)),                # TL
        (tw - qw, 0, tw, qh, (tw, 0)),         # TR
        (tw - qw, th - qh, tw, th, (tw, th)),  # BR
        (0, th - qh, qw, th, (0, th)),         # BL
    ]
    # 窗口半径:定位块 ≈ 页短边 7.5%(16mm/210mm) 的 0.9 倍;块尺寸先验同源
    expected_block = 0.075 * min(width, height)
    radius = max(12, int(expected_block * 0.9))
    points: list[tuple[float, float] | None] = []
    for box_x0, box_y0, box_x1, box_y1, corner in windows:
        found: tuple[float, float] | None = None
        for cand_x, cand_y in _corner_candidates(thumb, (box_x0, box_y0, box_x1, box_y1), corner):
            center = _block_center_in_window(
                gray, (cand_x * scale, cand_y * scale), radius, expected_size=expected_block
            )
            if center is not None:
                found = center
                break
        points.append(found)

    missing = [index for index, point in enumerate(points) if point is None]
    if len(missing) > 1:
        return None
    if len(missing) == 1:
        # 平行四边形外推:缺角 = 对角 + 相邻两角之差
        index = missing[0]
        opposite = (index + 2) % 4
        left = (index - 1) % 4
        right = (index + 1) % 4
        base_a = points[opposite]
        base_b = points[left]
        base_c = points[right]
        assert base_a is not None and base_b is not None and base_c is not None
        guess = (base_b[0] + base_c[0] - base_a[0], base_b[1] + base_c[1] - base_a[1])
        if not (0.05 * width <= guess[0] <= 0.95 * width and 0.05 * height <= guess[1] <= 0.95 * height):
            return None
        points[index] = guess

    # 注意:块心已在原分辨率完成真实性校验,此处不再缩放坐标
    result = [point for point in points if point is not None]
    return result if len(result) == 4 else None


def _is_valid_quad(points: list[tuple[float, float]], width: int, height: int) -> bool:
    """四点为顺序正确的凸四边形,且内部四边形面积占比合理(防误检)"""

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    signs = []
    count = len(points)
    for index in range(count):
        value = cross(points[index], points[(index + 1) % count], points[(index + 2) % count])
        if abs(value) < 1e-6:
            return False
        signs.append(value > 0)
    if len(set(signs)) != 1:
        return False
    area = 0.0
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        area += x1 * y2 - x2 * y1
    area = abs(area) / 2
    if area < width * height * 0.35:
        return False
    if area > width * height * 1.05:
        return False
    return True


def _find_marker_center(canvas_gray: Image.Image, target: tuple[float, float]) -> tuple[float, float] | None:
    """规范画布上校验次定位块中心(±_MARKER_WINDOW 窗口内暗团 bbox 中心)"""
    x0 = max(0, int(target[0] - _MARKER_WINDOW))
    y0 = max(0, int(target[1] - _MARKER_WINDOW))
    x1 = min(canvas_gray.width, int(target[0] + _MARKER_WINDOW))
    y1 = min(canvas_gray.height, int(target[1] + _MARKER_WINDOW))
    crop = canvas_gray.crop((x0, y0, x1, y1))
    pixels = crop.load()
    min_x, min_y, max_x, max_y = x1 - x0, y1 - y0, -1, -1
    count = 0
    for y in range(crop.height):
        for x in range(crop.width):
            if pixels[x, y] <= _DARK_MAX:
                count += 1
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
    if count < 25:  # 8mm 块 @7.14px/mm ≈ 57px 边长,窗口内应远超该阈值
        return None
    return x0 + (min_x + max_x) / 2, y0 + (min_y + max_y) / 2


def _is_canonical_canvas(image: Image.Image) -> bool:
    """输入是否已是规范画布(1500×2121 ±容差)——决定检测模式的关键判定"""
    return (
        abs(image.width - CANON_WIDTH) <= _CANON_TOLERANCE
        and abs(image.height - CANON_HEIGHT) <= _CANON_TOLERANCE
    )


def _detect_blocks_at_targets(canvas: Image.Image) -> list[tuple[float, float]] | None:
    """规范画布上的“理论锚定”四角块检测(锚定理论位置小窗;4/4 命中方可信)

    与 _detect_fiducials 的全象限盲搜不同:画布输入的坐标系已由定位点建立,
    四角块必然在理论位置附近——只在此小窗内做块真实性校验,几乎不可能误检;
    任一角未命中即返回 None(调用方按“不可信画布”处理,绝不勉强裁切)。
    """
    gray = canvas.convert("L")
    points: list[tuple[float, float]] = []
    expected_block = 16.0 * PX_PER_MM  # 16mm 主块 @规范画布分辨率
    for target_x, target_y in TARGET_CORNERS:
        center = _block_center_in_window(
            gray, (target_x, target_y), _CANON_ANCHOR_RADIUS, expected_size=expected_block
        )
        if center is None:
            return None
        points.append(center)
    return points


def _align_to_canvas(image: Image.Image) -> Image.Image | None:
    """找平到规范画布(供落盘与姓名区裁剪共用);未命中返回 None

    QUAD 会把“源四边形之外”完全裁掉,因此源四边形需外推为“整页四角”
    (块心距页边 20mm:主块 16mm/2 + 边距 12mm;块心框=170×257mm),
    输出直接为全幅 1500×2121 —— 四角块完整保留且坐标与理论值精确对应。
    """
    width, height = image.size
    if min(width, height) < _MIN_SIDE:
        return None
    points = _detect_fiducials(image)
    if points is None or not _is_valid_quad(points, width, height):
        return None

    tl, tr, br, bl = points
    span_x = ((tr[0] - tl[0]) ** 2 + (tr[1] - tl[1]) ** 2) ** 0.5
    span_y = ((bl[0] - tl[0]) ** 2 + (bl[1] - tl[1]) ** 2) ** 0.5
    inset_x = span_x * (20.0 / 170.0)
    inset_y = span_y * (20.0 / 257.0)
    page_tl = (tl[0] - inset_x, tl[1] - inset_y)
    page_tr = (tr[0] + inset_x, tr[1] - inset_y)
    page_br = (br[0] + inset_x, br[1] + inset_y)
    page_bl = (bl[0] - inset_x, bl[1] + inset_y)
    quad = (*page_tl, *page_bl, *page_br, *page_tr)  # PIL QUAD 顺序:左上、左下、右下、右上
    canvas = image.transform(
        (CANON_WIDTH, CANON_HEIGHT), Image.QUAD, data=quad, resample=Image.BICUBIC
    )

    # 空白度校验:整页几乎全黑或几乎全白(极端误检) → 放弃
    probe = canvas.convert("L").resize((150, 212))
    pixels = probe.tobytes()
    dark_ratio = sum(1 for value in pixels if value <= _DARK_MAX) / len(pixels)
    if not (0.002 <= dark_ratio <= 0.6):
        logger.warning("标准卷找平后空白度异常(暗像素占比 %.3f),放弃找平", dark_ratio)
        return None

    return canvas


def _decode_page_type(canvas: Image.Image) -> str:
    """在规范画布上解码页底“类型带”(三槽位位串→纸张类型)

    仅应在找平成功的画布上调用;无块/未登记组合 → home(安全回退)。
    """
    gray = canvas.convert("L")
    band_y = TYPE_BAND_Y_MM * PX_PER_MM
    bits: list[str] = []
    for x_mm in TYPE_SLOT_X_MM:
        cx = x_mm * PX_PER_MM
        x0 = max(0, int(cx - _TYPE_WINDOW))
        y0 = max(0, int(band_y - _TYPE_WINDOW))
        x1 = min(gray.width, int(cx + _TYPE_WINDOW))
        y1 = min(gray.height, int(band_y + _TYPE_WINDOW))
        dark = sum(1 for value in gray.crop((x0, y0, x1, y1)).tobytes() if value <= _DARK_MAX)
        bits.append("1" if dark >= _TYPE_DARK_MIN else "0")
    code = "".join(bits)
    page_type = PAGE_TYPE_MAP.get(code, PAGE_TYPE_HOME)
    if page_type != PAGE_TYPE_HOME:
        logger.info("标准卷类型带解码:%s → %s", code, page_type)
    return page_type


def _encode_like(image: Image.Image, original_format: str | None) -> bytes:
    """按原格式编码(HEIC/未知统一 JPEG);PNG 源保留 PNG"""
    buffer = BytesIO()
    fmt = (original_format or "").upper()
    if fmt == "PNG":
        image.save(buffer, "PNG", optimize=True)
    else:
        image.convert("RGB").save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def align_standard_sheet(content: bytes) -> tuple[bytes, bool, str]:
    """检测双类定位块 + 页底类型带解码;未命中或任何异常 → (原字节, False, "home")

    返回 (找平后字节, 是否已找平, 纸张类型)。纸张类型仅在本轮找平成功时解码
    (普通照片/未命中定位块永远 home——两重防护防误合并)。
    """
    try:
        with Image.open(BytesIO(content)) as source:
            original_format = source.format
            image = ImageOps.exif_transpose(source).convert("RGB")
            canvas = _align_to_canvas(image)
            if canvas is None:
                return content, False, PAGE_TYPE_HOME
            page_type = _decode_page_type(canvas)
            encoded = _encode_like(canvas, original_format)
            del image, canvas
            return encoded, True, page_type
    except Exception as error:  # noqa: BLE001 —— 找平失败绝不阻断上传
        logger.warning("标准卷找平失败,回退原图:%s", error)
        return content, False, PAGE_TYPE_HOME


def _scale_crop_output(cropped: Image.Image) -> Image.Image:
    """裁剪结果尺寸归一(宽 >900 缩至 900;宽 <400 适度放大),便于阅读与传输"""
    if cropped.width > 900:
        ratio = 900 / cropped.width
        return cropped.resize((900, max(1, round(cropped.height * ratio))), Image.LANCZOS)
    if cropped.width < 400:  # 过小则适度放大,便于阅读
        ratio = min(3.0, 640 / max(1, cropped.width))
        return cropped.resize(
            (max(1, round(cropped.width * ratio)), max(1, round(cropped.height * ratio))),
            Image.LANCZOS,
        )
    return cropped


def crop_name_region_ex(content: bytes) -> tuple[bytes | None, dict]:
    """依据定位点裁剪姓名/学号信息区小图;返回 (JPEG 字节|None, meta)

    裁切依据(全部锚定“实际检测到的定位块”,无任何启发式区域):
    1. 规范画布输入(1500×2121):四角“理论锚定”检测 4/4 命中 → 规范几何裁切;
    2. 原始照片输入:定位点全象限检测(块真实性三级校验)→ 找平为画布 →
       画布上再进行一次“理论锚定”复检(4/4)→ 规范几何裁切;
    3. 任一环节不通过 → (None, meta):不裁,由调用方决定整图识别等回退。
    次定位块实测命中时用于收紧姓名区下界。

    meta 字段: basis(canonical|landmarks|none) / main_points(裁切所依据的实测块心,
    画布坐标) / marker_points / region(画布坐标系裁切框) / note —— 供审计与排查引用。
    """
    meta: dict = {"basis": "none", "main_points": [], "marker_points": [], "region": None, "note": ""}
    try:
        with Image.open(BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")

            canvas: Image.Image | None = None
            anchors: list[tuple[float, float]] | None = None
            if _is_canonical_canvas(image):
                anchors = _detect_blocks_at_targets(image)
                if anchors is not None:
                    canvas = image
                    meta["basis"] = "canonical"
                else:
                    meta["note"] = "画布四角锚定检测未通过(画布不可信)"
            else:
                points = _detect_fiducials(image)
                if points is not None and _is_valid_quad(points, image.width, image.height):
                    canvas = _align_to_canvas(image)
                    if canvas is not None:
                        anchors = _detect_blocks_at_targets(canvas)
                        if anchors is not None:
                            meta["basis"] = "landmarks"
                        else:
                            canvas = None
                            meta["note"] = "找平后锚定复检未通过"
                    else:
                        meta["note"] = "找平失败"
                else:
                    meta["note"] = "未检出有效定位块(无定位点依据)"

            if canvas is None or anchors is None:
                return None, meta
            meta["main_points"] = [[round(x, 1), round(y, 1)] for x, y in anchors]

            # 次定位块实测(用于收紧下界;未命中不影响裁切)
            gray = canvas.convert("L")
            marker_hits = [
                center
                for center in (_find_marker_center(gray, target) for target in MARKER_TARGET_CORNERS)
                if center is not None
            ]
            meta["marker_points"] = [[round(x, 1), round(y, 1)] for x, y in marker_hits]
            if len(marker_hits) == len(MARKER_TARGET_CORNERS):
                marker_y = sum(center[1] for center in marker_hits) / len(marker_hits)
                # 仅“收紧”下界(防比例失调提前截断),不放宽到默认区之外
                bottom = min(NAME_REGION_PX[3], max(340, int(marker_y) - 12))
            else:
                bottom = NAME_REGION_PX[3]
            region = (*NAME_REGION_PX[:3], bottom)
            meta["region"] = [region[0], region[1], region[2], region[3]]
            cropped = canvas.crop(region)
            del canvas, gray

            cropped = _scale_crop_output(cropped)
            buffer = BytesIO()
            cropped.save(buffer, "JPEG", quality=88, optimize=True)
            return buffer.getvalue(), meta
    except Exception as error:  # noqa: BLE001 —— 展示/识别小图失败不影响任何主流程
        logger.warning("姓名区裁剪失败:%s", error)
        meta["note"] = f"裁剪异常:{type(error).__name__}"
        return None, meta


def crop_name_region(content: bytes) -> bytes | None:
    """裁剪姓名/学号信息区小图(兼容旧签名;依据定位点,详见 crop_name_region_ex)

    - 标准卷(画布或可找平照片):按定位点几何裁切;
    - 无定位点依据:返回 None(不做启发式裁剪)。
    """
    data, _meta = crop_name_region_ex(content)
    return data
