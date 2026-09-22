"""存档图片压缩测试(压缩比/幂等/格式分支/上传钩子/媒体类型嗅探/零残留)

策略:使用生成的大尺寸(噪声)图片保证"结果更小"稳定成立;所有文件操作在 tmp_path。
"""

import os

import pytest
from PIL import Image

from app.api import routes_corrections, routes_tasks
from app.config import settings
from app.services import image_compress_service
from app.services.image_compress import compress_file, compress_image_bytes


def _noise_image(width: int, height: int, fmt: str, path, **save_kwargs) -> bytes:
    """生成噪声图(高熵,保证有压缩空间);返回写入的原始字节"""
    data = os.urandom(width * height * 3)
    image = Image.frombytes("RGB", (width, height), data)
    image.save(path, fmt, **save_kwargs)
    return path.read_bytes()


class TestCompressBytes:
    """字节级压缩行为"""

    def test_large_jpeg_shrinks_and_remains_readable(self, tmp_path):
        raw = _noise_image(3200, 2400, "JPEG", tmp_path / "big.jpg", quality=95)
        output, changed = compress_image_bytes(raw, max_side=2200, quality=88)
        assert changed is True
        assert len(output) < len(raw)
        with Image.open(__import__("io").BytesIO(output)) as image:
            assert image.format == "JPEG"
            assert max(image.size) <= 2200

    def test_small_image_unchanged(self, tmp_path):
        # 小尺寸且已最优编码(创建时即带 optimize,重压缩参数一致 -> 字节恒等)
        raw = _noise_image(400, 300, "PNG", tmp_path / "small.png", optimize=True)
        output, changed = compress_image_bytes(raw, max_side=2200, quality=88)
        assert changed is False
        assert output == raw

    def test_png_stays_png(self, tmp_path):
        raw = _noise_image(2600, 1800, "PNG", tmp_path / "big.png")
        output, changed = compress_image_bytes(raw, max_side=2200, quality=88)
        assert changed is True and len(output) < len(raw)
        with Image.open(__import__("io").BytesIO(output)) as image:
            assert image.format == "PNG"
            assert max(image.size) <= 2200

    def test_bmp_converted_to_jpeg(self, tmp_path):
        raw = _noise_image(2500, 1200, "BMP", tmp_path / "big.bmp")
        output, changed = compress_image_bytes(raw, max_side=2200, quality=88)
        assert changed is True
        assert output.startswith(b"\xff\xd8\xff")  # JPEG 魔数

    def test_corrupt_bytes_fallback(self):
        raw = b"not-an-image"
        output, changed = compress_image_bytes(raw)
        assert changed is False and output == raw


class TestCompressFileAndService:
    """文件级原子替换与批量任务(幂等/零残留)"""

    async def test_file_atomic_and_no_tmp_residue(self, tmp_path):
        path = tmp_path / "a.jpg"
        raw = _noise_image(3000, 2000, "JPEG", path, quality=95)
        before, after, changed = compress_file(path, max_side=2200, quality=88)
        assert changed is True and after < before
        assert path.read_bytes() != raw
        assert not list(tmp_path.glob("*.tmp"))

    async def test_batch_idempotent(self, tmp_path):
        _noise_image(3000, 2000, "JPEG", tmp_path / "big.jpg", quality=95)
        _noise_image(400, 300, "PNG", tmp_path / "small.png", optimize=True)  # 已最优编码 -> 跳过
        await image_compress_service.run_compress(tmp_path, max_side=2200, quality=88)
        first = image_compress_service.progress()
        assert first["done"] == 2 and first["changed"] == 1 and first["skipped"] == 1
        assert first["error"] is None

        # 第二轮:全部跳过(幂等)
        await image_compress_service.run_compress(tmp_path, max_side=2200, quality=88)
        second = image_compress_service.progress()
        assert second["changed"] == 0 and second["skipped"] == 2


class TestUploadHook:
    """上传钩子:开关控制与压缩收益"""

    async def test_disabled_returns_identical(self, tmp_path, monkeypatch):
        raw = _noise_image(3200, 2400, "JPEG", tmp_path / "big.jpg", quality=95)
        monkeypatch.setattr(settings, "image_compress_enabled", False)
        assert await routes_corrections._maybe_compress(raw) == raw

    async def test_enabled_shrinks_large_image(self, tmp_path, monkeypatch):
        raw = _noise_image(3200, 2400, "JPEG", tmp_path / "big.jpg", quality=95)
        monkeypatch.setattr(settings, "image_compress_enabled", True)
        output = await routes_corrections._maybe_compress(raw)
        assert len(output) < len(raw)


class TestMediaTypeSniff:
    """图片端点媒体类型嗅探(存量压缩后编码变化也正确)"""

    def test_sniff_variants(self, tmp_path):
        jpeg_path = tmp_path / "x.png"  # 扩展名故意与内容不符
        _noise_image(80, 60, "JPEG", jpeg_path, quality=90)
        assert routes_tasks._sniff_media_type(jpeg_path) == "image/jpeg"

        png_path = tmp_path / "y.png"
        _noise_image(80, 60, "PNG", png_path)
        assert routes_tasks._sniff_media_type(png_path) == "image/png"

        text_path = tmp_path / "z.txt"
        text_path.write_bytes(b"hello world")
        assert routes_tasks._sniff_media_type(text_path) is None
        assert routes_tasks._sniff_media_type(tmp_path / "missing.png") is None
