"""PDF 展开(扫描件逐页转图;纯 wheel 依赖,离线可用)

- 依赖 pypdfium2(可选组件):缺失时上传 PDF 返回可读 400 错误,其余格式不受影响;
- 每页渲染为 JPEG(scale≈2.2,约 158dpi A4;随后仍会走预处理与存档压缩);
- 输出文件名 `stem_pN.jpg`(stem 取自原文件名,保持"文件名匹配指派"可用);
- 加密/损坏/超页数/渲染失败 → PdfExpandError(路由层转 400 可读错误)。

语义(与既有上传模式一致):
- 单篇模式:PDF 各页按顺序 = 同一篇作文的多页;
- 批量模式:每页 = 一个独立任务(与"每张图=一篇"一致)。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: 单 PDF 页数上限(与 MAX_BATCH_FILES 双护栏)
MAX_PDF_PAGES = 60
#: 渲染倍率(72dpi 基准;2.2 ≈ 158dpi,A4 约 1300×1850)
RENDER_SCALE = 2.2

try:  # 可选组件:PDF 解析(离线分发时随 runtime 携带)
    import pypdfium2 as _pdfium
except Exception:  # noqa: BLE001
    _pdfium = None


class PdfExpandError(ValueError):
    """PDF 无法展开为图片(加密/损坏/超页数等),由路由层转为 400 可读错误"""


def pdf_available() -> bool:
    """PDF 解析组件是否可用"""
    return _pdfium is not None


def expand_pdf(name: str, content: bytes) -> list[tuple[str, bytes]]:
    """把 PDF 每一页渲染为 JPEG 字节

    Returns:
        [(文件名, JPEG 字节), ...](按页序;文件名形如 "扫描件_p1.jpg")
    """
    if _pdfium is None:
        raise PdfExpandError(
            "当前环境未启用 PDF 解析组件,暂不支持上传 PDF;请上传图片,或联系管理员升级组件。"
        )
    try:
        doc = _pdfium.PdfDocument(content)
    except Exception as error:  # noqa: BLE001
        text = str(error).lower()
        if "password" in text:
            raise PdfExpandError(f"PDF 已加密,请去除密码后上传:{name}") from error
        raise PdfExpandError(f"PDF 无法解析(文件可能损坏):{name}") from error
    try:
        page_count = len(doc)
        if page_count == 0:
            raise PdfExpandError(f"PDF 不含任何页面:{name}")
        if page_count > MAX_PDF_PAGES:
            raise PdfExpandError(
                f"PDF 页数过多({page_count} 页 > 上限 {MAX_PDF_PAGES} 页):{name},请拆分后上传"
            )
        stem = Path(name).stem or "scan"
        images: list[tuple[str, bytes]] = []
        for index in range(page_count):
            page = doc[index]
            try:
                bitmap = page.render(scale=RENDER_SCALE)
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=90, optimize=True)
            images.append((f"{stem}_p{index + 1}.jpg", buffer.getvalue()))
        logger.info("PDF 展开完成:%s → %s 页", name, len(images))
        return images
    except PdfExpandError:
        raise
    except Exception as error:  # noqa: BLE001
        raise PdfExpandError(
            f"PDF 渲染失败,请改用图片上传:{name}({type(error).__name__})"
        ) from error
    finally:
        doc.close()
