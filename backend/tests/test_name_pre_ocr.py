"""上传后姓名预识别服务测试(引擎链 / 解析器 / 防覆盖 / 静默降级)

全部使用临时库与桩,不触真实端点与真实数据。
"""

import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Base, CorrectionTask
from app.models.schemas import OcrExtractionResult
from app.services import name_pre_ocr as module
from app.services.name_pre_ocr import parse_identity, run_pre_ocr


class TestParseIdentity:
    def test_pinyin_name_and_id(self):
        text = "Name: Li Ming\nID: 20260123"
        assert parse_identity(text) == ("Li Ming", "20260123")

    def test_chinese_name(self):
        name, _ = parse_identity("姓名: 张伟\n学号 20250001")
        assert name == "张伟"

    def test_id_only(self):
        _, student_id = parse_identity("202612345")
        assert student_id == "202612345"

    def test_stopwords_not_taken_as_name(self):
        name, _ = parse_identity("姓名\nName\nKlasse")
        assert name is None

    def test_empty_and_garbage(self):
        assert parse_identity(None) == (None, None)
        assert parse_identity("") == (None, None)
        assert parse_identity("!!!") == (None, None)


async def _make_task_db(tmp_path: Path, *, status: str = "PENDING", result=None):
    """临时库+任务(含真实图片文件);返回 (engine, factory, upload_dir, task_id)"""
    upload_dir = tmp_path / "uploads"
    (upload_dir / "task_1").mkdir(parents=True)
    page = upload_dir / "task_1" / "001.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buffer, "JPEG")
    page.write_bytes(buffer.getvalue())

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'p.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        task = CorrectionTask(
            student_name="未知",
            status=status,
            stage="UPLOADED",
            progress=0.0,
            image_paths=["task_1/001.jpg"],
            result=result,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    return engine, factory, upload_dir, task_id


class TestEngineChain:
    async def test_endpoint_engine_writes_back(self, tmp_path, monkeypatch):
        engine, factory, upload_dir, task_id = await _make_task_db(tmp_path)
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", False)
        monkeypatch.setattr(
            module,
            "crop_name_region_ex",
            lambda _content: (b"jpeg-bytes", {"basis": "landmarks", "main_points": [[0.0, 0.0]] * 4}),
        )

        async def fake_endpoint(base_url, api_key, model, image_path, tag):
            assert Path(image_path).exists()  # 临时文件在识别时存在
            return OcrExtractionResult(
                student_name="Li Ming", student_id="20260123", transcribed_text=""
            )

        monkeypatch.setattr(module, "_extract_via_endpoint", fake_endpoint)
        monkeypatch.setattr(module, "_extract_via_local_rapid", lambda _path: None)
        await run_pre_ocr(task_id)
        async with factory() as session:
            task = await session.get(CorrectionTask, task_id)
            assert task.student_name == "Li Ming"
            assert task.student_id == "20260123"
        await engine.dispose()

    async def test_endpoint_fail_falls_back_to_local(self, tmp_path, monkeypatch):
        engine, factory, upload_dir, task_id = await _make_task_db(tmp_path)
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", False)
        monkeypatch.setattr(
            module,
            "crop_name_region_ex",
            lambda _content: (b"jpeg-bytes", {"basis": "landmarks", "main_points": [[0.0, 0.0]] * 4}),
        )

        async def failing_endpoint(*args, **kwargs):
            raise RuntimeError("endpoint down")

        monkeypatch.setattr(module, "_extract_via_endpoint", failing_endpoint)
        # 显式声明本地引擎可用:本用例只验证回退链路,不依赖环境是否安装 rapidocr
        monkeypatch.setattr(module, "_RapidOCR", object())
        monkeypatch.setattr(
            module, "_extract_via_local_rapid", lambda _path: "Wang Wei 20250001"
        )
        await run_pre_ocr(task_id)
        async with factory() as session:
            task = await session.get(CorrectionTask, task_id)
            assert task.student_name == "Wang Wei"
            assert task.student_id == "20250001"
        await engine.dispose()

    async def test_all_engines_fail_silently(self, tmp_path, monkeypatch):
        engine, factory, upload_dir, task_id = await _make_task_db(tmp_path)
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", False)
        monkeypatch.setattr(
            module,
            "crop_name_region_ex",
            lambda _content: (b"jpeg-bytes", {"basis": "landmarks", "main_points": [[0.0, 0.0]] * 4}),
        )

        async def failing_endpoint(*args, **kwargs):
            raise RuntimeError("down")

        monkeypatch.setattr(module, "_extract_via_endpoint", failing_endpoint)
        monkeypatch.setattr(module, "_extract_via_local_rapid", lambda _path: None)
        await run_pre_ocr(task_id)  # 不抛异常
        async with factory() as session:
            task = await session.get(CorrectionTask, task_id)
            assert task.student_name == "未知"  # 保持原值
        await engine.dispose()

    async def test_completed_task_never_overwritten(self, tmp_path, monkeypatch):
        """防覆盖:批改已完成(result 非空)的任务绝不被预识别改写"""
        engine, factory, upload_dir, task_id = await _make_task_db(
            tmp_path, status="COMPLETED", result={"overall_score": "20 / 25"}
        )
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", False)
        monkeypatch.setattr(
            module,
            "crop_name_region_ex",
            lambda _content: (b"jpeg-bytes", {"basis": "landmarks", "main_points": [[0.0, 0.0]] * 4}),
        )

        async def fake_endpoint(*args, **kwargs):
            return OcrExtractionResult(
                student_name="不该写入", student_id="000000", transcribed_text=""
            )

        monkeypatch.setattr(module, "_extract_via_endpoint", fake_endpoint)
        await run_pre_ocr(task_id)
        async with factory() as session:
            task = await session.get(CorrectionTask, task_id)
            assert task.student_name == "未知"  # 未被改写
        await engine.dispose()

    async def test_mock_mode_shortcircuit(self, tmp_path, monkeypatch):
        engine, factory, upload_dir, task_id = await _make_task_db(tmp_path)
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", True)
        monkeypatch.setattr(
            module,
            "crop_name_region_ex",
            lambda _content: (b"jpeg-bytes", {"basis": "landmarks", "main_points": [[0.0, 0.0]] * 4}),
        )
        monkeypatch.setattr(module, "_extract_via_local_rapid", lambda _path: None)
        await run_pre_ocr(task_id)
        async with factory() as session:
            task = await session.get(CorrectionTask, task_id)
            assert task.student_name == "李明"
            assert task.student_id == "20260123"
        await engine.dispose()

    async def test_temp_file_cleaned_after_run(self, tmp_path, monkeypatch):
        """临时文件用后即删:端点桩记录路径,运行结束后不应残留"""
        engine, factory, upload_dir, task_id = await _make_task_db(tmp_path)
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", False)
        monkeypatch.setattr(
            module,
            "crop_name_region_ex",
            lambda _content: (b"jpeg-bytes", {"basis": "landmarks", "main_points": [[0.0, 0.0]] * 4}),
        )
        captured: list[Path] = []

        async def fake_endpoint(base_url, api_key, model, image_path, tag):
            captured.append(Path(image_path))
            return OcrExtractionResult(
                student_name="Li Ming", student_id=None, transcribed_text=""
            )

        monkeypatch.setattr(module, "_extract_via_endpoint", fake_endpoint)
        monkeypatch.setattr(module, "_extract_via_local_rapid", lambda _path: None)
        await run_pre_ocr(task_id)
        assert captured and all(not path.exists() for path in captured)
        await engine.dispose()

    async def test_waiting_review_task_still_written(self, tmp_path, monkeypatch):
        """复核挂起(WAITING_REVIEW)任务:预识别仍先行写入(不覆盖正式结果的语义不变)"""
        engine, factory, upload_dir, task_id = await _make_task_db(tmp_path, status="WAITING_REVIEW")
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", False)
        monkeypatch.setattr(
            module,
            "crop_name_region_ex",
            lambda _content: (b"jpeg-bytes", {"basis": "canonical", "main_points": [[1.0, 1.0]] * 4}),
        )

        async def fake_endpoint(base_url, api_key, model, image_path, tag):
            return OcrExtractionResult(
                student_name="Li Ming", student_id="20260123", transcribed_text=""
            )

        monkeypatch.setattr(module, "_extract_via_endpoint", fake_endpoint)
        monkeypatch.setattr(module, "_extract_via_local_rapid", lambda _path: None)
        await run_pre_ocr(task_id)
        async with factory() as session:
            task = await session.get(CorrectionTask, task_id)
            assert task.student_name == "Li Ming"
            assert task.student_id == "20260123"
        await engine.dispose()

    async def test_no_landmark_uses_full_image(self, tmp_path, monkeypatch):
        """无定位点依据 → 整图识别(不裁;不做启发式裁剪),写入照常"""
        engine, factory, upload_dir, task_id = await _make_task_db(tmp_path)
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", False)
        monkeypatch.setattr(
            module, "crop_name_region_ex", lambda _content: (None, {"basis": "none", "main_points": []})
        )
        monkeypatch.setattr(module, "_scaled_full_image", lambda _content: b"full-image-bytes")
        calls: list[Path] = []

        async def fake_endpoint(base_url, api_key, model, image_path, tag):
            calls.append(Path(image_path))
            return OcrExtractionResult(student_name="Wang Wei", student_id=None, transcribed_text="")

        monkeypatch.setattr(module, "_extract_via_endpoint", fake_endpoint)
        monkeypatch.setattr(module, "_extract_via_local_rapid", lambda _path: None)
        await run_pre_ocr(task_id)
        assert calls  # 整图输入确实被送入识别引擎
        async with factory() as session:
            task = await session.get(CorrectionTask, task_id)
            assert task.student_name == "Wang Wei"
        await engine.dispose()


class TestServiceScheduling:
    def test_schedule_dedup_and_disable(self, monkeypatch):
        """幂等入队;开关关闭时不调度"""
        from app.services.name_pre_ocr import NamePreOcrService

        service = NamePreOcrService(max_concurrent=1)
        monkeypatch.setattr(settings, "name_pre_ocr_enabled", True)
        service.schedule([1, 1, 2])
        assert service.pending_count == 2
        monkeypatch.setattr(settings, "name_pre_ocr_enabled", False)
        service.schedule([3])
        assert service.pending_count == 2
