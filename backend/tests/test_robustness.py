"""破坏性测试与鲁棒性回归(并发竞态 / 畸形输入 / 解析模糊 / 加密边界)

对应实施方案:
- S1/S2/S3 三项并发缺陷的永久反例用例(修复前必失败);
- 畸形 ZIP/图片/扩展名、解析 fuzz 与病态正则、加密口令边界;
- 全部在 tmp 路径/桩对象上进行,不触碰真实数据。
"""

import asyncio
import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.api.routes_corrections import _check_extension, _extract_zip_images
from app.models.db_models import Base, CorrectionTask
from app.models.schemas import TranscriptUpdateRequest
from app.services import image_compress_service
from app.services.image_compress import compress_image_bytes
from app.services.ocr_anomaly import analyze_transcription
from app.services.parser import extract_json_dict
from app.services.queue_service import QueueService
from app.services.recorrect_service import RecorrectError, apply_recorrect


class _StubCorrectionService:
    """记录每次处理的最简桩(用于队列去重断言)"""

    def __init__(self, delay: float = 0.01) -> None:
        self.calls: list[int] = []
        self._delay = delay

    async def process_task(self, task_id: int) -> None:
        self.calls.append(task_id)
        await asyncio.sleep(self._delay)


async def _drain_queue(queue: QueueService, expected: int, timeout: float = 5.0) -> None:
    """等待 expected 个任务全部处理完"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.03)
        if queue.pending_count == 0:
            await asyncio.sleep(0.15)  # 最后一个任务的处理收尾
            return
    raise AssertionError("队列未在预期时间内清空")


# =============================================================
# S1:同一任务重复入队去重(修复前:被处理多次)
# =============================================================
class TestQueueDedup:
    async def test_same_id_enqueued_times_processed_once(self):
        stub = _StubCorrectionService()
        queue = QueueService(stub, max_concurrent=2)  # type: ignore[arg-type]
        await queue.start()
        try:
            await asyncio.gather(*(queue.enqueue(7) for _ in range(5)))
            await _drain_queue(queue, 1)
        finally:
            await queue.stop()
        assert stub.calls.count(7) == 1  # 只处理一次

    async def test_different_ids_all_processed(self):
        stub = _StubCorrectionService()
        queue = QueueService(stub, max_concurrent=2)  # type: ignore[arg-type]
        await queue.start()
        try:
            for task_id in range(1, 21):
                await queue.enqueue(task_id)
            await _drain_queue(queue, 20)
        finally:
            await queue.stop()
        assert sorted(stub.calls) == list(range(1, 21))  # 无丢失、无重复

    async def test_re_enqueue_after_completion_works(self):
        stub = _StubCorrectionService()
        queue = QueueService(stub, max_concurrent=1)  # type: ignore[arg-type]
        await queue.start()
        try:
            await queue.enqueue(9)
            await _drain_queue(queue, 1)
            await queue.enqueue(9)  # 完成后再入队(重试语义)应正常
            await _drain_queue(queue, 1)
        finally:
            await queue.stop()
        assert stub.calls.count(9) == 2


# =============================================================
# S2:并发双发重新批改(修复前:两次都成功、计数互覆)
# =============================================================
class TestRecorrectConcurrency:
    async def test_concurrent_apply_only_one_succeeds(self, tmp_path, monkeypatch):
        upload_dir = tmp_path / "uploads"
        (upload_dir / "task_x").mkdir(parents=True)
        (upload_dir / "task_x" / "001.jpg").write_bytes(b"fake-image")
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))

        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'r.db').as_posix()}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as seed:
            seed.add(
                CorrectionTask(
                    student_name="并发测试",
                    status="COMPLETED",
                    stage="DONE",
                    progress=1.0,
                    image_paths=["task_x/001.jpg"],
                    result={"overall_score": "18 / 25", "errors": []},
                )
            )
            await seed.commit()

        async def one_attempt() -> bool:
            async with factory() as session:
                task = await session.get(CorrectionTask, 1)
                try:
                    await apply_recorrect(session, task, {})
                    return True
                except RecorrectError:
                    return False

        results = await asyncio.gather(one_attempt(), one_attempt())
        assert sum(results) == 1  # 恰好一成一败
        async with factory() as check:
            task = await check.get(CorrectionTask, 1)
            assert task.recorrect_count == 1  # 计数不被双增/互覆
            assert task.status == "PENDING"
        await engine.dispose()


# =============================================================
# S3:压缩任务双启动竞态(修复前:第二次未被拦截)
# =============================================================
class TestCompressDoubleStart:
    async def test_second_start_rejected_while_running(self, tmp_path):
        await image_compress_service.start_compress(tmp_path, max_side=2200, quality=88)
        try:
            with pytest.raises(ValueError):
                await image_compress_service.start_compress(tmp_path, max_side=2200, quality=88)
        finally:
            for _ in range(100):  # 等待首轮完成,避免影响其他用例
                await asyncio.sleep(0.05)
                if not image_compress_service.progress()["running"]:
                    break
        assert image_compress_service.progress()["running"] is False


# =============================================================
# A:畸形 ZIP / 图片 / 扩展名边界
# =============================================================
def _zip_bytes(entries: dict[str, bytes], *, gbk_names: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in entries.items():
            if gbk_names:
                name = name.encode("gbk").decode("cp437")
            zf.writestr(name, payload)
    return buffer.getvalue()


class TestMalformedZip:
    def test_truncated_zip_readable_error(self):
        raw = _zip_bytes({"a.jpg": b"x" * 64})
        with pytest.raises(HTTPException) as exc:
            _extract_zip_images(raw[: len(raw) // 2], "坏.zip")
        assert exc.value.status_code == 400

    def test_entry_count_bomb(self, monkeypatch):
        monkeypatch.setattr(settings, "max_batch_files", 5)
        raw = _zip_bytes({f"{i}.jpg": b"x" * 16 for i in range(10)})
        with pytest.raises(HTTPException) as exc:
            _extract_zip_images(raw, "炸弹.zip")
        assert exc.value.status_code == 400

    def test_path_traversal_names_stripped(self):
        raw = _zip_bytes({"../evil.jpg": b"x" * 32, "..\\evil2.jpg": b"x" * 32})
        results = _extract_zip_images(raw, "穿越.zip")
        for name, _ in results:
            assert ".." not in name and "/" not in name and "\\" not in name

    def test_gbk_filename_roundtrip(self):
        """无 UTF-8 标志位的 GBK 文件名(zipfile 读真实包时的形态)→ 正确反转码

        注:writestr 会自动为中文名置 UTF-8 标志,无法构造该形态;
        此处直接对 _zip_entry_name 喂入真实形态的 ZipInfo(契约级单测)。
        """
        from app.api.routes_corrections import _zip_entry_name

        info = zipfile.ZipInfo(filename="中文名.jpg".encode("gbk").decode("cp437"))
        info.flag_bits = 0  # 无 UTF-8 标志位(真实 GBK 压缩包的形态)
        assert _zip_entry_name(info) == "中文名.jpg"

    def test_only_non_images_rejected(self):
        raw = _zip_bytes({"a.txt": b"hello", "b.docx": b"world"})
        with pytest.raises(HTTPException) as exc:
            _extract_zip_images(raw, "无图.zip")
        assert exc.value.status_code == 400


class TestMalformedImages:
    def test_garbage_bytes_fallback(self):
        out, changed = compress_image_bytes(b"\x00\x01garbage-not-an-image")
        assert changed is False and out == b"\x00\x01garbage-not-an-image"

    def test_truncated_jpeg_fallback(self):
        buffer = io.BytesIO()
        Image.new("RGB", (800, 600), "white").save(buffer, "JPEG", quality=90)
        truncated = buffer.getvalue()[:60]  # 掐断的 JPEG
        out, changed = compress_image_bytes(truncated)
        assert out == truncated  # 解码失败 → 原样回退

    def test_extreme_strip_never_upscaled(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1, 20000), "white").save(buffer, "JPEG", quality=90)
        out, changed = compress_image_bytes(buffer.getvalue(), max_side=2200, quality=88)
        if changed:
            with Image.open(io.BytesIO(out)) as image:
                assert max(image.size) <= 2200
                assert image.width >= 1  # 长条不塌缩为 0
        else:
            assert out == buffer.getvalue()

    def test_check_extension_edges(self):
        assert _check_extension("a.JPG") == ".jpg"
        assert _check_extension("😀表情.jpg") == ".jpg"
        assert _check_extension("x" * 300 + ".png") == ".png"
        with pytest.raises(HTTPException):
            _check_extension("无扩展名")
        with pytest.raises(HTTPException):
            _check_extension("恶意.exe")
        with pytest.raises(HTTPException):
            _check_extension("包.zip")  # 非批量端点不允许 zip


class TestTextLimits:
    def test_transcript_length_bounds(self):
        TranscriptUpdateRequest(transcribed_text="x" * 50000)  # 上限内可用
        with pytest.raises(Exception):
            TranscriptUpdateRequest(transcribed_text="x" * 50001)
        with pytest.raises(Exception):
            TranscriptUpdateRequest(transcribed_text="")


# =============================================================
# B:解析 fuzz 与病态正则(不变量:dict 或 ValueError,且限时)
# =============================================================
class TestParserFuzz:
    CASES = [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '前言 ```\n{"a": 1}\n``` 后记',
        '\ufeff{"a": 1}',  # BOM 前缀
        '{"a": 1',  # 截断
        '{"a": {"b": [1, 2,',  # 深层截断
        '["not an object"]',
        '\xca\xfd\xbe\xdd{"a": 1}',  # GBK 字节串在前
        '{"a": "' + "x" * 100000 + '"}',  # 超长值
        "[" * 50000 + "]",  # 病态嵌套
        "",  # 空
    ]

    def test_fuzz_never_hangs_and_invariant_holds(self):
        for raw in self.CASES:
            start = time.perf_counter()
            try:
                obj = extract_json_dict(raw)
                assert isinstance(obj, dict)
            except ValueError:
                pass
            elapsed = time.perf_counter() - start
            assert elapsed < 1.5, f"解析耗时异常({elapsed:.2f}s):{raw[:40]!r}"

    def test_pathological_regex_inputs(self):
        for text in ("[" * 100000, "a" * 1_000_000, "[unsicher:未闭合"):
            start = time.perf_counter()
            report = analyze_transcription(text, quality="high")
            assert time.perf_counter() - start < 1.0
            assert report.anomalous is False  # 病态输入不应误报为内容异常
