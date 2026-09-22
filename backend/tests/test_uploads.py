"""上传管线优化测试(方向三)

覆盖:
- 扩展名校验(HEIC 可用性门控、ZIP 仅批量端点接受);
- 两阶段校验:文件数 / 单文件大小 / 请求总量(不读内容、不落盘即拦截);
- ZIP 解压护栏:条目数 / 单条大小 / 解压总量(防解压炸弹);
- ZIP 中文文件名 GBK 还原(Windows 压缩包)与 UTF-8 标志位行为;
- 失败回滚:已保存文件的清理(杜绝孤儿文件);
- batch_id 与指派方式参数校验。
"""

import io
import struct
import zipfile
import zlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile

from app.api import routes_corrections as rc
from app.config import Settings


# ---------------------------------------------------------
# 构造工具
# ---------------------------------------------------------
def _upload_file(filename: str, size: int = 0) -> UploadFile:
    """构造仅供校验阶段使用的 UploadFile(size 模拟 multipart 解析出的大小)"""
    return UploadFile(file=io.BytesIO(b""), size=size, filename=filename)


def _zip_bytes(entries: list[tuple[str, bytes]], compression: int = zipfile.ZIP_STORED) -> bytes:
    """标准库写出 ZIP(ASCII / UTF-8 文件名)"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def _raw_gbk_zip(entries: list[tuple[str, bytes]]) -> bytes:
    """手工构造 ZIP:文件名按 GBK 原始字节写入且不设 UTF-8 标志位

    模拟 Windows 资源管理器(简体中文系统)压缩出的"传统编码"压缩包,
    它是 _zip_entry_name 需要还原的真实场景。
    """
    out = io.BytesIO()
    central: list[bytes] = []
    offset = 0
    for name, data in entries:
        raw_name = name.encode("gbk")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        # 本地文件头:签名/版本/标志位(0)/压缩法(0=存储)/时间/日期/CRC/大小/名长/扩展长
        local = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50, 20, 0, 0, 0, 0,
            crc, len(data), len(data), len(raw_name), 0,
        )
        out.write(local)
        out.write(raw_name)
        out.write(data)
        # 中央目录记录(字段顺序:见 ZIP 规范,偏移 46 字节)
        central.append(
            struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50, 20, 20, 0, 0, 0, 0,
                crc, len(data), len(data),
                len(raw_name), 0, 0, 0, 0, 0,
                offset,
            )
            + raw_name
        )
        offset += len(local) + len(raw_name) + len(data)
    cd_start = out.tell()
    for record in central:
        out.write(record)
    cd_size = out.tell() - cd_start
    out.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central), cd_size, cd_start, 0))
    return out.getvalue()


# ---------------------------------------------------------
# 扩展名校验
# ---------------------------------------------------------
class TestCheckExtension:
    """_check_extension 扩展名与 HEIC 门控"""

    def test_common_formats(self):
        assert rc._check_extension("a.JPG") == ".jpg"
        assert rc._check_extension("b.jpeg") == ".jpeg"
        assert rc._check_extension("c.png") == ".png"
        assert rc._check_extension("d.webp") == ".webp"
        assert rc._check_extension("e.bmp") == ".bmp"

    def test_rejects_unknown(self):
        with pytest.raises(HTTPException) as err:
            rc._check_extension("note.txt")
        assert err.value.status_code == 400

    def test_zip_only_for_batch(self):
        with pytest.raises(HTTPException):
            rc._check_extension("pack.zip", allow_zip=False)
        assert rc._check_extension("pack.zip", allow_zip=True) == ".zip"

    def test_heic_gated_by_component(self, monkeypatch):
        """组件可用时接受 HEIC/HEIF;缺失时给出转存提示(400)"""
        monkeypatch.setattr(rc, "heif_available", lambda: True)
        assert rc._check_extension("photo.HEIC") == ".heic"
        assert rc._check_extension("photo.heif") == ".heif"

        monkeypatch.setattr(rc, "heif_available", lambda: False)
        with pytest.raises(HTTPException, match="HEIC"):
            rc._check_extension("photo.heic")


# ---------------------------------------------------------
# 第一阶段:请求级校验(不落盘)
# ---------------------------------------------------------
class TestValidateUploadRequest:
    """_validate_upload_request 数量 / 单文件 / 总量护栏"""

    def test_empty_rejected(self):
        with pytest.raises(HTTPException, match="未上传"):
            rc._validate_upload_request([])

    def test_count_limit(self, monkeypatch):
        monkeypatch.setattr(rc.settings, "max_batch_files", 3)
        files = [_upload_file(f"{i}.jpg") for i in range(4)]
        with pytest.raises(HTTPException, match="最多"):
            rc._validate_upload_request(files)

    def test_single_file_size_limit(self):
        files = [_upload_file("big.jpg", size=51 * 1024 * 1024)]
        with pytest.raises(HTTPException, match="过大"):
            rc._validate_upload_request(files)

    def test_total_size_limit(self, monkeypatch):
        monkeypatch.setattr(rc.settings, "max_upload_total_mb", 1)
        files = [
            _upload_file("a.jpg", size=600 * 1024),
            _upload_file("b.jpg", size=600 * 1024),
        ]
        with pytest.raises(HTTPException, match="总量"):
            rc._validate_upload_request(files)

    def test_zip_exempt_from_single_size(self, monkeypatch):
        """ZIP 包体积只受总量约束(解压后另有独立护栏)"""
        monkeypatch.setattr(rc.settings, "max_upload_total_mb", 500)
        files = [_upload_file("pack.zip", size=80 * 1024 * 1024)]
        rc._validate_upload_request(files, allow_zip=True)  # 不应抛异常

    def test_valid_passes(self):
        files = [_upload_file("a.jpg", size=1024), _upload_file("b.png", size=2048)]
        rc._validate_upload_request(files)


# ---------------------------------------------------------
# ZIP 文件名编码
# ---------------------------------------------------------
class TestZipEntryName:
    """_zip_entry_name GBK 还原与标志位行为"""

    def test_ascii_unchanged(self):
        info = zipfile.ZipInfo("homework.jpg")
        info.flag_bits = 0
        assert rc._zip_entry_name(info) == "homework.jpg"

    def test_gbk_restored(self):
        """zipfile 读 GBK 压缩包时按 CP437 解码,需反转码还原中文"""
        info = zipfile.ZipInfo("placeholder")
        info.filename = "李明.jpg".encode("gbk").decode("cp437")
        info.flag_bits = 0  # 未置 UTF-8 标志位
        assert rc._zip_entry_name(info) == "李明.jpg"

    def test_utf8_flag_keeps_name(self):
        """置了 UTF-8 标志位的名称原样返回,不做 GBK 还原"""
        info = zipfile.ZipInfo("王芳.png")
        info.flag_bits = 0x800
        assert rc._zip_entry_name(info) == "王芳.png"

    def test_non_gbk_falls_back_silently(self):
        """CP437 可编码但不是合法 GBK 序列时保持原样(不抛异常)"""
        info = zipfile.ZipInfo("café.jpg")
        info.flag_bits = 0
        assert rc._zip_entry_name(info) == "café.jpg"


# ---------------------------------------------------------
# ZIP 解压护栏
# ---------------------------------------------------------
class TestExtractZipImages:
    """_extract_zip_images 提取与防解压炸弹护栏"""

    def test_extracts_images_skips_others(self):
        content = _zip_bytes(
            [
                ("readme.txt", b"hello"),
                ("名单/", b""),  # 目录条目
                ("名单/001.jpg", b"fake-jpg-1"),
                ("002.png", b"fake-png-2"),
            ]
        )
        results = rc._extract_zip_images(content, "pack.zip")
        assert [name for name, _ in results] == ["001.jpg", "002.png"]
        assert results[0][1] == b"fake-jpg-1"

    def test_gbk_filename_end_to_end(self):
        """GBK 压缩包:中文文件名完整还原(含子目录路径取文件名)"""
        content = _raw_gbk_zip([("作文/李明.jpg", b"fake"), ("王芳.png", b"fake2")])
        results = rc._extract_zip_images(content, "中文压缩包.zip")
        assert [name for name, _ in results] == ["李明.jpg", "王芳.png"]

    def test_no_images_rejected(self):
        content = _zip_bytes([("a.txt", b"x")])
        with pytest.raises(HTTPException, match="未找到"):
            rc._extract_zip_images(content, "pack.zip")

    def test_bad_zip_rejected(self):
        with pytest.raises(HTTPException, match="损坏"):
            rc._extract_zip_images(b"not a zip at all", "pack.zip")

    def test_entry_count_guard(self, monkeypatch):
        monkeypatch.setattr(rc.settings, "max_batch_files", 2)
        content = _zip_bytes([(f"{i}.jpg", b"x") for i in range(3)])
        with pytest.raises(HTTPException, match="过多"):
            rc._extract_zip_images(content, "bomb.zip")

    def test_single_entry_size_guard(self, monkeypatch):
        monkeypatch.setattr(rc, "MAX_FILE_SIZE", 10)
        content = _zip_bytes([("big.jpg", b"x" * 20)])
        with pytest.raises(HTTPException, match="单张图片过大"):
            rc._extract_zip_images(content, "bomb.zip")

    def test_total_uncompressed_guard(self, monkeypatch):
        """压缩包体积小、解压后超总量上限 —— 防解压炸弹的核心用例"""
        monkeypatch.setattr(rc.settings, "max_upload_total_mb", 1)
        content = _zip_bytes([("a.jpg", b"x" * 1_500_000)], compression=zipfile.ZIP_DEFLATED)
        assert len(content) < 100_000  # 压缩后远小于解压后
        with pytest.raises(HTTPException, match="解压后总大小"):
            rc._extract_zip_images(content, "bomb.zip")


# ---------------------------------------------------------
# 失败回滚(孤儿文件清理)
# ---------------------------------------------------------
class TestCleanupSaved:
    """_cleanup_saved 回滚删除与空目录清理"""

    def test_removes_files_and_nested_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Settings, "upload_path", property(lambda self: tmp_path))
        (tmp_path / "batch_x" / "sub").mkdir(parents=True)
        f1 = tmp_path / "batch_x" / "001_a.jpg"
        f2 = tmp_path / "batch_x" / "sub" / "002_b.png"
        f1.write_bytes(b"a")
        f2.write_bytes(b"b")

        rc._cleanup_saved(["batch_x/001_a.jpg", "batch_x/sub/002_b.png"])

        assert not f1.exists() and not f2.exists()
        assert not (tmp_path / "batch_x" / "sub").exists()  # 子目录已清
        assert not (tmp_path / "batch_x").exists()  # 父目录已清

    def test_tolerates_missing_entries(self, tmp_path, monkeypatch):
        """不存在的文件/目录不抛异常(回滚必须永远安全)"""
        monkeypatch.setattr(Settings, "upload_path", property(lambda self: tmp_path))
        rc._cleanup_saved(["batch_missing/ghost.jpg"])


# ---------------------------------------------------------
# 参数校验
# ---------------------------------------------------------
class TestParseParams:
    """batch_id 与 assign_mode 参数校验"""

    def test_batch_id(self):
        assert rc._parse_batch_id(None) is None
        assert rc._parse_batch_id("") is None
        assert rc._parse_batch_id("abc123") == "abc123"
        assert rc._parse_batch_id("batch-2026_09") == "batch-2026_09"
        with pytest.raises(HTTPException):
            rc._parse_batch_id("short")  # 不足 6 位
        with pytest.raises(HTTPException):
            rc._parse_batch_id("bad id!")  # 非法字符
        with pytest.raises(HTTPException):
            rc._parse_batch_id("x" * 33)  # 超过 32 位

    def test_assign_mode(self):
        assert rc._parse_assign_mode(None) == "recognize"
        assert rc._parse_assign_mode("FILENAME") == "filename"
        assert rc._parse_assign_mode("order") == "order"
        with pytest.raises(HTTPException):
            rc._parse_assign_mode("guess")


# ---------------------------------------------------------
# 任务预填(方向四)
# ---------------------------------------------------------
class TestCreateTaskPrefill:
    """_create_task 的 batch_id 与学生预填(缺省保持 "未知" 交回识别)"""

    def _config(self) -> dict:
        return rc._parse_config("PIPELINE_A_LOCAL", "GAOKAO", "MEDIUM", False)

    def test_prefilled_student_and_batch(self):
        task = rc._create_task(
            MagicMock(), ["batch_x/1.jpg"], self._config(),
            batch_id="abc123", student_name="李明", student_id="20260101",
        )
        assert task.batch_id == "abc123"
        assert task.student_name == "李明"
        assert task.student_id == "20260101"

    def test_defaults_to_unknown(self):
        task = rc._create_task(MagicMock(), ["batch_x/1.jpg"], self._config())
        assert task.batch_id is None
        assert task.student_name == "未知"
        assert task.student_id is None
