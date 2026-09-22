"""本地二维码身份编解码(纯离线;解码 cv2 / 生成 qrcode,均为可选组件)

用途(「强制性姓名识别」与标准答题卷绑定):
- 标准答题卷在"姓名/学号"行右端打印学生专属二维码;
- 教师上传/选择图片时,本地解码二维码即时获取身份(优先于本地 OCR 链)。

二维码内容格式(确定性生成,无需入库):
    V1|<student_id>|<name>     如 V1|20260123|Li Ming

全部本地运算,不调用任何云端/远程端点;组件缺失/解码失败一律静默降级
(返回 None),由调用方回退既有链路,绝不中断任何流程。
"""

from __future__ import annotations

import logging
from io import BytesIO

logger = logging.getLogger(__name__)

try:  # 解码组件(cv2 随 RapidOCR 分发;缺失时 QR 通道静默不可用)
    import cv2
    import numpy as np
except Exception:  # noqa: BLE001
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

try:  # 生成组件(标准答题卷二维码 PNG;缺失时打印页明确报态)
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H
except Exception:  # noqa: BLE001
    qrcode = None  # type: ignore[assignment]
    ERROR_CORRECT_H = None  # type: ignore[assignment]

#: 本系统二维码负载前缀(版本化,便于将来扩展)
_QR_PREFIX = "V1|"

#: 解码重试时缩放大图的长边上限(相机原图直扫偏慢时使用)
_SMALL_MAX_SIDE = 1800

#: 分块扫描:tile 尺寸按全图比例(3 列×4 行均匀覆盖,邻块重叠 >20%)
_TILE_W_RATIO = 0.42
_TILE_H_RATIO = 0.32

#: 规范画布(1500×2121)上的 QR 版式区(含 60px 边距):
#: 二维码位于卷面 x 174–190mm / y 36–52mm → 画布像素 (1243–1357, 257–371)
_CANVAS_QR_ROI = (1183, 197, 1417, 431)

#: 无定位块时的宽容差版式 ROI(相对整图比例;x/y 双档覆盖小幅旋转)
_FALLBACK_ROIS = (
    (0.74, 0.06, 0.99, 0.30),
    (0.86, 0.10, 1.00, 0.34),
)


def qr_decode_available() -> bool:
    """本地二维码解码组件(cv2)是否可用"""
    return cv2 is not None and np is not None


def qr_render_available() -> bool:
    """二维码生成组件(qrcode)是否可用"""
    return qrcode is not None


def build_qr_payload(student_id: str | None, name: str | None) -> str:
    """生成二维码负载(确定性;与 parse_qr_payload 互逆)"""
    return f"{_QR_PREFIX}{student_id or ''}|{name or ''}"


def parse_qr_payload(text: str | None) -> dict | None:
    """解析二维码负载为 {"student_id","name"};非本系统格式/无有效信息返回 None

    格式:V1|<student_id>|<name>(学号与姓名至少一个非空;其余字段以 | 追加时忽略)
    """
    if not text or not text.startswith(_QR_PREFIX):
        return None
    parts = text.split("|")
    if len(parts) < 3:
        return None
    student_id = parts[1].strip() or None
    name = parts[2].strip() or None
    if not student_id and not name:
        return None
    return {"student_id": student_id, "name": name}


def _load_image(content: bytes):
    """字节 → (PIL RGB 图, OpenCV BGR 数组)(EXIF 纠偏);失败返回 None

    PIL 图供找平链(sheet_align)使用;BGR 数组供 cv2 解码使用。
    """
    try:
        from PIL import Image, ImageOps

        with Image.open(BytesIO(content)) as source:
            pil_image = ImageOps.exif_transpose(source).convert("RGB")
        return pil_image, cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return None


def _decode_once(detector, image) -> str | None:
    """单次解码(异常静默;入参统一连续化——cv2 对非连续切片数组检测不稳)"""
    try:
        text, _points, _straight = detector.detectAndDecode(np.ascontiguousarray(image))
        return text or None
    except Exception:  # noqa: BLE001
        return None


def _decode_with_warp(detector, image) -> str | None:
    """检测到候选但直扫失败:透视矫正(含二值化)重试——处理倾斜/低对比场景"""
    try:
        image = np.ascontiguousarray(image)  # type: ignore[union-attr]
        found, points = detector.detect(image)
        if not found or points is None:
            return None
        quad = np.asarray(points, dtype="float32").reshape(-1, 2)  # type: ignore[union-attr]
        if quad.shape[0] != 4:
            return None
        sides = [
            float(np.linalg.norm(quad[i] - quad[(i + 1) % 4]))  # type: ignore[union-attr]
            for i in range(4)
        ]
        side = max(80, int(max(sides) * 1.15))
        target = np.array(  # type: ignore[union-attr]
            [[0, 0], [side - 1, 0], [side - 1, side - 1], [0, side - 1]], dtype="float32"
        )
        matrix = cv2.getPerspectiveTransform(quad, target)  # type: ignore[union-attr]
        warped = cv2.warpPerspective(image, matrix, (side, side))  # type: ignore[union-attr]
        payload = _decode_once(detector, warped)
        if payload:
            return payload
        gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)  # type: ignore[union-attr]
        _ok, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)  # type: ignore[union-attr]
        return _decode_once(detector, cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR))  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return None


def _scan_tiles(detector, image) -> str | None:
    """网格分块扫描(3 列×4 行,邻块重叠 >20%)

    解决“大图中二维码占比小、全图直扫检不出”的经典问题:
    分块后二维码在块内占比显著提高,逐块直扫/透视矫正重试;命中即返回。
    """
    try:
        height, width = image.shape[:2]
        tile_w = int(width * _TILE_W_RATIO)
        tile_h = int(height * _TILE_H_RATIO)
        if tile_w <= 0 or tile_h <= 0:
            return None
        xs = sorted({0, (width - tile_w) // 2, width - tile_w})
        ys = sorted({0, (height - tile_h) // 3, (height - tile_h) * 2 // 3, height - tile_h})
        for y in ys:
            for x in xs:
                block = image[max(0, y) : y + tile_h, max(0, x) : x + tile_w]
                if block.size == 0:
                    continue
                payload = _decode_once(detector, block)
                if payload:
                    return payload
                payload = _decode_with_warp(detector, block)
                if payload:
                    return payload
        return None
    except Exception:  # noqa: BLE001
        return None


def _decode_roi_variants(detector, roi) -> str | None:
    """单个 ROI 直扫 + 增强重试(实测:二次插值模糊的二维码可被 Otsu/×2 上采样救回)"""
    payload = _decode_once(detector, roi)
    if payload:
        return payload
    try:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)  # type: ignore[union-attr]
        _ok, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)  # type: ignore[union-attr]
        payload = _decode_once(detector, cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR))  # type: ignore[union-attr]
        if payload:
            return payload
        upscaled = cv2.resize(  # type: ignore[union-attr]
            roi, (roi.shape[1] * 2, roi.shape[0] * 2), interpolation=cv2.INTER_CUBIC
        )
        payload = _decode_once(detector, upscaled)
        if payload:
            return payload
        binary_up = cv2.resize(  # type: ignore[union-attr]
            binary, (binary.shape[1] * 2, binary.shape[0] * 2), interpolation=cv2.INTER_CUBIC
        )
        return _decode_once(detector, cv2.cvtColor(binary_up, cv2.COLOR_GRAY2BGR))  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return None


def _decode_on_canvas(detector, pil_image) -> str | None:
    """找平路径(定位块命中时):找平为规范画布 → QR 版式区 ROI(含增强) → 画布全图兑底

    - 画布上二维码位置由版式确定(尺寸 16mm≈114px,占比高、近端正)→ 解码最稳;
    - 未检测到定位块 → 返回 None(由上层继续其他路径)。
    """
    try:
        from app.services.sheet_align import _align_to_canvas  # 同包内复用找平能力

        canvas = _align_to_canvas(pil_image)
        if canvas is None:
            return None
        canvas_arr = cv2.cvtColor(np.asarray(canvas.convert("RGB")), cv2.COLOR_RGB2BGR)  # type: ignore[union-attr]
        x0, y0, x1, y1 = _CANVAS_QR_ROI
        roi = np.ascontiguousarray(canvas_arr[y0:y1, x0:x1])  # type: ignore[union-attr]
        payload = _decode_roi_variants(detector, roi)
        if payload:
            return payload
        return _decode_once(detector, np.ascontiguousarray(canvas_arr))  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return None


def decode_qr_identity(content: bytes) -> dict | None:
    """解码图片中的身份二维码;命中返回 {"student_id","name"},否则 None

    策略(由快到全,全部本地;任一步命中即止;实测口径见各步骤成本):
    1. 原图直扫(约 20-60ms;大 QR / 高分辨率近拍直接命中);
    2. 超大图缩放后重扫(相机原图;兼顾读取成本);
    3. 找平路径(定位块命中时):找平为规范画布 → QR 版式区 ROI 直扫
       + 增强重试(Otsu/×2 上采样,可救回插值模糊)+ 画布全图兑底
       (实测:旋转 0°/3° 直出,8° 数字模拟经增强可出);
    4. 宽容差版式 ROI(无定位块时;相对比例双档)直扫 + 增强;
    5. 网格分块扫描(通用兑底);
    - 组件缺失或任何异常:静默返回 None(调用方回退既有链路,不中断)。
    """
    if not qr_decode_available():
        return None
    try:
        loaded = _load_image(content)
        if loaded is None:
            return None
        pil_image, image = loaded
        detector = cv2.QRCodeDetector()  # type: ignore[union-attr]

        payload = _decode_once(detector, image)
        if payload:
            parsed = parse_qr_payload(payload)
            if parsed:
                return parsed

        height, width = image.shape[:2]
        scan_image = image
        if max(height, width) > _SMALL_MAX_SIDE:
            ratio = _SMALL_MAX_SIDE / max(height, width)
            scan_image = cv2.resize(  # type: ignore[union-attr]
                image,
                (max(1, int(width * ratio)), max(1, int(height * ratio))),
                interpolation=cv2.INTER_AREA,  # type: ignore[union-attr]
            )
            payload = _decode_once(detector, scan_image)
            if payload:
                parsed = parse_qr_payload(payload)
                if parsed:
                    return parsed

        # 找平路径(有定位块的卷:主路径)
        payload = _decode_on_canvas(detector, pil_image)
        if payload:
            return parse_qr_payload(payload)

        # 宽容差版式 ROI(无定位块兜底;相对比例)
        for x0r, y0r, x1r, y1r in _FALLBACK_ROIS:
            crop = scan_image[int(height * y0r) : int(height * y1r), int(width * x0r) : int(width * x1r)]
            if crop.size == 0:
                continue
            payload = _decode_roi_variants(detector, np.ascontiguousarray(crop))  # type: ignore[union-attr]
            if payload:
                return parse_qr_payload(payload)

        # 网格分块扫描兑底
        payload = _scan_tiles(detector, scan_image)
        if payload:
            return parse_qr_payload(payload)
        return None
    except Exception as error:  # noqa: BLE001
        logger.debug("二维码解码异常:%s", type(error).__name__)
        return None


def render_qr_png(payload: str, box_size: int = 10, border: int = 4) -> bytes | None:
    """渲染二维码 PNG(H 级纠错;静区 4 模块符合 QR 规范;组件缺失返回 None)

    尺寸建议:卷面打印边长 16mm;box_size=10 时 PNG 约 370×370px,打印清晰。
    """
    if not qr_render_available():
        return None
    try:
        code = qrcode.QRCode(  # type: ignore[union-attr]
            version=None,
            error_correction=ERROR_CORRECT_H,
            box_size=box_size,
            border=border,
        )
        code.add_data(payload)
        code.make(fit=True)
        image = code.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception as error:  # noqa: BLE001
        logger.info("二维码生成失败:%s", type(error).__name__)
        return None
