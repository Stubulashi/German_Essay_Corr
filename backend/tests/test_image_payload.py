"""图片发送层归一化测试(校方口径:OpenAI 格式 + 直接 jpg)

覆盖:jpg 原样直发(零重编码)、png/webp 转 jpg、透明 PNG 白底、超大图缩边护栏、
损坏文件回退、无扩展名嗅探、多图 parts 组装。
"""

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image

from app.services.llm_client import build_image_message_parts, image_to_data_url


def _decode(url: str) -> bytes:
    assert url.startswith("data:")
    header, payload = url.split(",", 1)
    return base64.b64decode(payload)


def _save(image: Image.Image, path: Path, fmt: str) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    raw = buffer.getvalue()
    path.write_bytes(raw)
    return raw


class TestImagePayloadNormalization:
    def test_small_jpeg_sent_as_is(self, tmp_path):
        """jpg 小图 → 原字节直发(零重编码,符合“直接上传 jpg”)"""
        path = tmp_path / "a.jpg"
        raw = _save(Image.new("RGB", (320, 480), "white"), path, "JPEG")
        url = image_to_data_url(path)
        assert url.startswith("data:image/jpeg;base64,")
        assert _decode(url) == raw

    def test_png_converted_to_jpeg(self, tmp_path):
        """png 源 → 自动转码为 jpeg 发送(MIME 与实际编码一致)"""
        path = tmp_path / "a.png"
        _save(Image.new("RGB", (320, 480), "white"), path, "PNG")
        url = image_to_data_url(path)
        assert url.startswith("data:image/jpeg;base64,")
        with Image.open(io.BytesIO(_decode(url))) as decoded:
            assert decoded.format == "JPEG"
            assert decoded.size == (320, 480)

    def test_transparent_png_white_background(self, tmp_path):
        """RGBA 透明 PNG → 白底合成(透明区域不变黑)"""
        path = tmp_path / "t.png"
        image = Image.new("RGBA", (64, 64), (255, 0, 0, 0))  # 全透明
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        path.write_bytes(buffer.getvalue())
        url = image_to_data_url(path)
        with Image.open(io.BytesIO(_decode(url))) as decoded:
            assert decoded.format == "JPEG"
            pixel = decoded.convert("RGB").getpixel((32, 32))
            assert all(channel >= 240 for channel in pixel), f"透明区应为白底:{pixel}"

    def test_oversized_jpeg_downscaled(self, tmp_path):
        """超大噪声 jpg → 触发护栏:长边 ≤2400 且体积下降"""
        rng = np.random.default_rng(42)
        noise = rng.integers(0, 256, size=(3400, 2600, 3), dtype=np.uint8)
        path = tmp_path / "big.jpg"
        raw = _save(Image.fromarray(noise), path, "JPEG")
        assert len(raw) > 3_500_000  # 前置:确实超护栏
        url = image_to_data_url(path)
        output = _decode(url)
        assert len(output) < len(raw)
        with Image.open(io.BytesIO(output)) as decoded:
            assert max(decoded.size) <= 2400

    def test_corrupted_file_falls_back(self, tmp_path):
        """损坏文件 → 回退原字节(不阻断发送)"""
        path = tmp_path / "bad.jpg"
        path.write_bytes(b"\x00\x01not-an-image")
        url = image_to_data_url(path)
        assert _decode(url) == b"\x00\x01not-an-image"

    def test_extensionless_png_sniffed_and_converted(self, tmp_path):
        """无扩展名但内容为 PNG → 嗅探后仍以 jpeg 发送"""
        path = tmp_path / "scan.bin"
        _save(Image.new("RGB", (64, 64), "white"), path, "PNG")
        url = image_to_data_url(path)
        assert url.startswith("data:image/jpeg;base64,")

    def test_build_parts_multi_images(self, tmp_path):
        """多图组装:首元素为文本,后续均为 jpeg image_url"""
        first = tmp_path / "p1.jpg"
        second = tmp_path / "p2.png"
        _save(Image.new("RGB", (200, 200), "white"), first, "JPEG")
        _save(Image.new("RGB", (200, 200), "white"), second, "PNG")
        parts = build_image_message_parts([first, second], "转录以下作文")
        assert parts[0] == {"type": "text", "text": "转录以下作文"}
        assert len(parts) == 3
        for part in parts[1:]:
            assert part["type"] == "image_url"
            assert part["image_url"]["url"].startswith("data:image/jpeg;base64,")
