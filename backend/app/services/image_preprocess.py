"""图片预处理(上传后自动执行)

目的:提升手写作文照片的识别质量,降低 OCR / VLM 的错误率与人工复核率。
处理顺序(固定):
1. EXIF 方向纠正 —— 手机拍摄的照片普遍带旋转信息;
2. 统一 RGB(丢弃透明度;HEIC 在 pillow-heif 可用时解码);
3. 去阴影 / 光照均衡 —— 背景估计除法,改善阴影与光照不均(默认开启);
4. 灰度化(可选,默认关闭);
5. 对比度增强(autocontrast);
6. 保守自动裁边 —— 仅当检测到明确的"内容边界"且裁剪幅度合理时才执行;
7. 按原格式编码输出(HEIC/未知格式统一输出 JPEG)。

设计原则:
- 任何步骤失败都回退为原图(绝不因预处理导致上传失败);
- 通过 .env 开关控制:IMAGE_PREPROCESS(总开关)/ IMAGE_SHADOW_REMOVAL / IMAGE_GRAYSCALE。

HEIC 支持:
- 依赖可选的 pillow-heif(缺失时 HEIC 上传会被路由层明确拒绝并提示转存 JPG);
- HEIF_AVAILABLE 由导入时探测,pillow_heif 注册后 Pillow 可直接 open HEIC。
"""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

# ---------- 可选依赖探测(pillow-heif:iPhone HEIC 照片) ----------
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:  # noqa: BLE001 —— 缺失或不兼容时静默降级
    HEIF_AVAILABLE = False

# ---------- 可选依赖探测(numpy:去阴影加速;缺失时跳过该步骤) ----------
try:
    import numpy as np

    _NUMPY_OK = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _NUMPY_OK = False

# 自动裁边安全阈值:内容区域占比低于下阈值(裁得太狠)或高于上阈值(几乎没裁)时放弃
_CROP_MIN_RATIO = 0.08
_CROP_MAX_RATIO = 0.97
# 内容检测的灰度阈值(低于该灰度视为"有内容")
_CONTENT_THRESHOLD = 235
# 裁边保留的边距比例
_CROP_MARGIN_RATIO = 0.02

# 去阴影参数:背景估计的缩图比例 / 背景下限(避免过暗背景放大噪声)/ 白场目标
_SHADOW_DOWNSCALE = 16
_SHADOW_BG_FLOOR = 32.0
_SHADOW_WHITE_POINT = 240.0


def heif_available() -> bool:
    """当前环境是否支持 HEIC/HEIF 解码(供路由层校验上传扩展名)"""
    return HEIF_AVAILABLE


def preprocess_image_bytes(
    content: bytes,
    shadow_removal: bool = True,
    grayscale: bool = False,
) -> bytes:
    """对图片字节流执行预处理,返回处理后的字节流

    Args:
        content:        原始图片字节(JPEG/PNG/WEBP/BMP/HEIC 等 Pillow 支持的格式)
        shadow_removal: 是否执行去阴影/光照均衡
        grayscale:      是否输出灰度图

    Returns:
        处理后的图片字节;处理失败时原样返回输入
    """
    try:
        img = Image.open(BytesIO(content))
        original_format = (img.format or "JPEG").upper()

        # 1. EXIF 方向纠正(等价于手机相册的"自动旋转")
        img = ImageOps.exif_transpose(img)

        # 2. 转为 RGB(统一后续处理;丢弃透明度,作业照片无需 alpha)
        if img.mode != "RGB":
            img = img.convert("RGB")

        # 3. 去阴影 / 光照均衡(默认开启,失败自动跳过)
        if shadow_removal:
            img = _remove_shadow(img)

        # 4. 灰度化(可选)
        if grayscale:
            img = ImageOps.grayscale(img).convert("RGB")

        # 5. 对比度增强
        img = ImageOps.autocontrast(img, cutoff=1)

        # 6. 保守自动裁边
        img = _gentle_autocrop(img)

        # 7. 按原格式编码输出(HEIC 等特殊格式统一输出 JPEG)
        buf = BytesIO()
        if original_format in ("JPG", "JPEG"):
            img.save(buf, "JPEG", quality=92, optimize=True)
        elif original_format == "PNG":
            img.save(buf, "PNG", optimize=True)
        elif original_format == "WEBP":
            img.save(buf, "WEBP", quality=92)
        elif original_format == "BMP":
            img.save(buf, "BMP")
        else:
            # HEIF / HEIC / 未知格式统一按 JPEG 输出
            img.save(buf, "JPEG", quality=92, optimize=True)

        processed = buf.getvalue()
        return processed if processed else content
    except Exception as e:  # noqa: BLE001 —— 预处理失败必须回退原图
        logger.warning("图片预处理失败,回退原图:%s", e)
        return content


def _remove_shadow(img: Image.Image) -> Image.Image:
    """去阴影 / 光照均衡(背景估计除法)

    算法:
    1. 大幅缩小图片 → 高斯模糊 → 放大回原尺寸,得到"缓慢变化的背景光照"估计;
    2. 原图逐像素除以背景并拉白到目标白场(带背景下限保护,避免过暗处放大噪声)。

    对光照均匀的扫描件近似恒等变换;明确阴影/侧光照片可显著提升对比一致性。
    """
    if not _NUMPY_OK:
        return img
    try:
        width, height = img.size
        if width < 64 or height < 64:
            return img
        arr = np.asarray(img, dtype=np.float32)

        # 背景估计:缩图 + 高斯模糊 + 放大(近似大半径模糊,速度远快于原图直接模糊)
        small_w = max(1, width // _SHADOW_DOWNSCALE)
        small_h = max(1, height // _SHADOW_DOWNSCALE)
        background = img.resize((small_w, small_h), Image.BILINEAR)
        background = background.filter(ImageFilter.GaussianBlur(radius=4))
        background = background.resize((width, height), Image.BILINEAR)
        bg_arr = np.asarray(background, dtype=np.float32)

        # 除法归一化 + 下限保护 + 截断
        bg_arr = np.maximum(bg_arr, _SHADOW_BG_FLOOR)
        normalized = np.clip(arr / bg_arr * _SHADOW_WHITE_POINT, 0, 255)
        return Image.fromarray(normalized.astype(np.uint8), "RGB")
    except Exception as e:  # noqa: BLE001
        logger.debug("去阴影跳过:%s", e)
        return img


def _gentle_autocrop(img: Image.Image) -> Image.Image:
    """保守自动裁边:检测内容边界框,仅在合理时裁剪

    算法:
    1. 灰度化后二值化(灰度 < 阈值 视为内容);
    2. 计算内容包围盒(bbox);
    3. 若 bbox 面积占比过低(疑似误裁)或过高(几乎无裁),放弃;
    4. 否则按包围盒 + 2% 边距裁剪。
    """
    try:
        width, height = img.size
        if width < 64 or height < 64:  # 过小的图不处理
            return img

        gray = ImageOps.grayscale(img)
        # 反相后定位"内容":内容(暗)变成高亮,便于 getbbox
        binary = gray.point(lambda px: 255 if px < _CONTENT_THRESHOLD else 0)
        bbox = binary.getbbox()
        if bbox is None:
            return img

        content_ratio = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (width * height)
        if content_ratio < _CROP_MIN_RATIO or content_ratio > _CROP_MAX_RATIO:
            return img

        margin_x = int(width * _CROP_MARGIN_RATIO)
        margin_y = int(height * _CROP_MARGIN_RATIO)
        left = max(0, bbox[0] - margin_x)
        top = max(0, bbox[1] - margin_y)
        right = min(width, bbox[2] + margin_x)
        bottom = min(height, bbox[3] + margin_y)
        if right - left < 64 or bottom - top < 64:
            return img
        return img.crop((left, top, right, bottom))
    except Exception as e:  # noqa: BLE001
        logger.debug("自动裁边跳过:%s", e)
        return img
