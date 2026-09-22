"""存档图片压缩(学生答卷/作文原图本地瘦身)

方案(与预处理管线串联,独立开关):
- EXIF 纠偏 → 仅当长边超过上限时 Lanczos 等比缩小(不放大)→ 编码:
  JPEG/HEIC/BMP/未知 → JPEG(quality, optimize);PNG → PNG(optimize, 无损);WEBP → WEBP(quality);
- 守恒规则:仅当结果更小且未放大时才替换,否则原样返回;任何异常回退原图;
- 原子替换(同目录 .tmp + os.replace):零残留、路径与文件名不变(下游全部无缝)。

注意:压缩为有损(质量可配);HEIC 依赖既有可选组件 pillow-heif,缺失时跳过解码失败文件。
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo

logger = logging.getLogger(__name__)

#: 压缩标记(写入文件元数据;重跑时据此跳过,保证严格幂等——避免 JPEG 代际再编码)
_MARKER = "corrector-compress"

try:  # 可选组件:HEIC/HEIF 解码(缺失时不影响其他格式)
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pass

#: 支持的图片扩展名(存量遍历与上传落盘共用口径)
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}


def _already_compressed(image: Image.Image) -> bool:
    """检测压缩标记(JPEG COM 注释 / PNG Software 文本块)"""
    info = image.info or {}
    comment = info.get("comment")
    if isinstance(comment, bytes):
        comment = comment.decode("latin-1", "ignore")
    if comment and _MARKER in str(comment):
        return True
    return _MARKER in str(info.get("Software") or "")


def compress_image_bytes(
    content: bytes, *, max_side: int = 2200, quality: int = 88
) -> tuple[bytes, bool]:
    """压缩图片字节;返回 (输出字节, 是否发生变化)

    仅在"结果更小"时视为变化;解码失败/收益为负/已带压缩标记时原样返回。
    """
    try:
        image = Image.open(io.BytesIO(content))
        image_format = (image.format or "JPEG").upper()
        if _already_compressed(image):
            return content, False  # 已压缩过:跳过,保证批量重跑幂等
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        # 仅当确实超出上限时缩放(绝不放大)
        if max(image.size) > max_side:
            ratio = max_side / max(image.size)
            new_size = (
                max(1, round(image.width * ratio)),
                max(1, round(image.height * ratio)),
            )
            image = image.resize(new_size, Image.LANCZOS)

        buffer = io.BytesIO()
        if image_format == "PNG":
            pnginfo = PngInfo()
            pnginfo.add_text("Software", _MARKER)
            image.save(buffer, "PNG", optimize=True, pnginfo=pnginfo)
        elif image_format == "WEBP":
            image.save(buffer, "WEBP", quality=quality)
        else:  # JPEG / HEIC / BMP / 未知格式 → 统一 JPEG(带压缩标记)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(
                buffer, "JPEG", quality=quality, optimize=True, comment=_MARKER.encode()
            )

        output = buffer.getvalue()
        if output and len(output) < len(content):
            return output, True
        return content, False
    except Exception as error:  # noqa: BLE001 —— 压缩是增强项,失败必须回退原图
        logger.warning("图片压缩失败,保留原图:%s", error)
        return content, False


def compress_file(
    path: Path, *, max_side: int = 2200, quality: int = 88
) -> tuple[int, int, bool]:
    """就地压缩单个文件(原子替换);返回 (原字节数, 新字节数, 是否变化)"""
    before = path.stat().st_size
    content = path.read_bytes()
    output, changed = compress_image_bytes(content, max_side=max_side, quality=quality)
    if not changed:
        return before, before, False
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(output)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return before, len(output), True
