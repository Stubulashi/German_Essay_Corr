"""图片预处理单元测试

覆盖:
- EXIF 方向纠正、对比度增强、保守裁边的输出合法性;
- 无效输入与异常场景的原图回退(绝不抛异常);
- 格式保持(JPEG/PNG)。
"""

from io import BytesIO

from PIL import Image

from app.services.image_preprocess import preprocess_image_bytes


def _make_image(width: int = 300, height: int = 400, fmt: str = "JPEG") -> bytes:
    """生成一张"白底 + 中部黑块"的测试图片"""
    img = Image.new("RGB", (width, height), "white")
    # 中部绘制内容块(约占 60% 面积)
    box = (int(width * 0.2), int(height * 0.2), int(width * 0.8), int(height * 0.8))
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3], 2):
            img.putpixel((x, y), (30, 30, 30))
    buf = BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


class TestPreprocessImage:
    """preprocess_image_bytes 行为测试"""

    def test_returns_valid_image(self):
        """输出应是可重新打开的合法图片"""
        out = preprocess_image_bytes(_make_image(fmt="PNG"))
        img = Image.open(BytesIO(out))
        assert img.size[0] > 0 and img.size[1] > 0

    def test_png_format_preserved(self):
        """PNG 输入应输出 PNG"""
        out = preprocess_image_bytes(_make_image(fmt="PNG"))
        assert Image.open(BytesIO(out)).format == "PNG"

    def test_jpeg_format_preserved(self):
        """JPEG 输入应输出 JPEG"""
        out = preprocess_image_bytes(_make_image(fmt="JPEG"))
        assert Image.open(BytesIO(out)).format == "JPEG"

    def test_autocrop_reduces_blank_border(self):
        """白边明显的图片应被保守裁边(输出尺寸小于等于原图)"""
        original = _make_image(300, 400, fmt="PNG")
        out = preprocess_image_bytes(original)
        src = Image.open(BytesIO(original))
        dst = Image.open(BytesIO(out))
        assert dst.size[0] <= src.size[0] and dst.size[1] <= src.size[1]
        # 应确实裁掉了一些空白(内容占比约 60%,阈值上限 97%,必然触发)
        assert dst.size[0] < src.size[0] or dst.size[1] < src.size[1]

    def test_nearly_full_content_not_cropped_to_nothing(self):
        """内容几乎铺满整图时不裁或裁幅极小(避免误裁)"""
        img = Image.new("RGB", (200, 200), (40, 40, 40))  # 全暗
        buf = BytesIO()
        img.save(buf, "PNG")
        out = preprocess_image_bytes(buf.getvalue())
        dst = Image.open(BytesIO(out))
        assert dst.size[0] >= 150 and dst.size[1] >= 150

    def test_invalid_bytes_fallback(self):
        """非图片字节流应原样返回(回退策略)"""
        bad = b"this is not an image at all"
        assert preprocess_image_bytes(bad) == bad

    def test_empty_bytes_fallback(self):
        """空字节流应原样返回"""
        assert preprocess_image_bytes(b"") == b""

    def test_tiny_image_untouched_size(self):
        """极小图片不执行裁边(保持原尺寸)"""
        img = Image.new("RGB", (32, 32), "white")
        img.putpixel((16, 16), (0, 0, 0))
        buf = BytesIO()
        img.save(buf, "PNG")
        out = preprocess_image_bytes(buf.getvalue())
        assert Image.open(BytesIO(out)).size == (32, 32)
