"""PDF 展开测试(逐页转图 / 页序命名 / 异常与页数护栏)

依赖可选组件 pypdfium2;缺失时整文件跳过(与运行时降级行为一致)。
加密 PDF 的 400 路径依赖真实加密文件,此处不合成(由实现代码路径覆盖)。
"""

import io

import pytest
from PIL import Image, ImageDraw

from app.services import pdf_expand
from app.services.pdf_expand import PdfExpandError, expand_pdf, pdf_available

pytestmark = pytest.mark.skipif(not pdf_available(), reason="pypdfium2 未安装(可选组件)")


def _make_pdf(pages: int = 2) -> bytes:
    """PIL 合成多页图片型 PDF(与手机扫描软件的导出结构同类)"""
    images = []
    for index in range(pages):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((40, 60), f"Seite {index + 1}: Hallo Welt", fill="black")
        images.append(image)
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:], resolution=150)
    return buffer.getvalue()


class TestExpandPdf:
    def test_two_pages_to_jpeg_in_order(self):
        pages = expand_pdf("扫件.pdf", _make_pdf(2))
        assert [name for name, _ in pages] == ["扫件_p1.jpg", "扫件_p2.jpg"]
        for _, payload in pages:
            assert payload.startswith(b"\xff\xd8\xff")  # JPEG 魔数
            with Image.open(io.BytesIO(payload)) as image:
                assert image.size[0] > 500 and image.size[1] > 700  # 渲染约 2.2x

    def test_corrupt_bytes_readable_error(self):
        with pytest.raises(PdfExpandError, match="损坏"):
            expand_pdf("坏文件.pdf", b"not a pdf at all")

    def test_over_page_limit(self, monkeypatch):
        monkeypatch.setattr(pdf_expand, "MAX_PDF_PAGES", 1)
        with pytest.raises(PdfExpandError, match="页数过多"):
            expand_pdf("多页.pdf", _make_pdf(2))
