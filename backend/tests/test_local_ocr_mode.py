"""纯本地 OCR 模式(LOCAL_OCR_ONLY)测试:短路/契约/报错/预识别联动/设置同步

全部使用桩与临时文件;不触真实引擎、网络与真实数据。
"""

import io

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Base, CorrectionTask
from app.models.schemas import OcrExtractionResult
from app.pipelines.base import PipelineConfigError, PipelineNetworkError
from app.services import name_pre_ocr as pre_module
from app.services.ocr_client import OcrClient


def _write_image(tmp_path, name: str = "page.jpg"):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buffer, "JPEG")
    path = tmp_path / name
    path.write_bytes(buffer.getvalue())
    return path


async def _make_task_db(tmp_path):
    """临时库 + 任务(含真实图片文件);返回 (engine, factory, upload_dir, task_id)"""
    upload_dir = tmp_path / "uploads"
    (upload_dir / "task_1").mkdir(parents=True)
    page = upload_dir / "task_1" / "001.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buffer, "JPEG")
    page.write_bytes(buffer.getvalue())

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'lo.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        task = CorrectionTask(
            student_name="未知",
            status="PENDING",
            stage="UPLOADED",
            progress=0.0,
            image_paths=["task_1/001.jpg"],
            result=None,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    return engine, factory, upload_dir, task_id


class TestLocalOcrMode:
    async def test_local_mode_returns_contract(self, tmp_path, monkeypatch):
        """开启开关 → extract 短路本地:多页拼接 / 姓名解析(真 parse_identity) / 质量映射"""
        monkeypatch.setattr(settings, "local_ocr_only", True)
        page1 = _write_image(tmp_path, "p1.jpg")
        page2 = _write_image(tmp_path, "p2.jpg")

        def fake_recognize(path):
            if path.name == "p1.jpg":
                return [("Name: Li Ming", 0.95), ("ID: 20260123", 0.94)]
            if path.name == "p2.jpg":
                return [("Das ist ein Satz.", 0.90)]
            return [("Name: Li Ming", 0.96)]  # 姓名区裁图临时文件

        import app.services.sheet_align as sheet_module

        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(pre_module, "recognize_lines", fake_recognize)
        monkeypatch.setattr(
            sheet_module,
            "crop_name_region_ex",
            lambda _c: (b"jpeg-bytes", {"basis": "landmarks", "main_points": [[1.0, 1.0]] * 4}),
        )

        result = await OcrClient(settings).extract([page1, page2])
        assert isinstance(result, OcrExtractionResult)
        assert "Name: Li Ming" in result.transcribed_text
        assert "Das ist ein Satz." in result.transcribed_text
        assert result.student_name == "Li Ming"
        assert result.student_id == "20260123"
        assert result.recognition_quality == "high"  # 全行均值≈0.9375
        assert result.quality_note and "本地" in result.quality_note

    async def test_no_landmark_falls_back_to_page_text(self, tmp_path, monkeypatch):
        """无定位点依据(裁剪 None) → 姓名/学号退用首页整页文本解析"""
        monkeypatch.setattr(settings, "local_ocr_only", True)
        page = _write_image(tmp_path)

        import app.services.sheet_align as sheet_module

        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(
            pre_module,
            "recognize_lines",
            lambda _p: [("Name: Wang Wei", 0.9), ("ID: 20250001", 0.9)],
        )
        monkeypatch.setattr(sheet_module, "crop_name_region_ex", lambda _c: (None, {"basis": "none"}))

        result = await OcrClient(settings).extract([page])
        assert result.student_name == "Wang Wei"
        assert result.student_id == "20250001"

    async def test_missing_component_config_error(self, tmp_path, monkeypatch):
        """组件缺失 → 明确配置错误(绝不静默回退云端,保障纯本地语义)"""
        monkeypatch.setattr(settings, "local_ocr_only", True)
        monkeypatch.setattr(pre_module, "rapid_available", lambda: False)
        page = _write_image(tmp_path)
        with pytest.raises(PipelineConfigError, match="本地 OCR 组件"):
            await OcrClient(settings).extract([page])

    async def test_all_pages_empty_network_error(self, tmp_path, monkeypatch):
        """全部页无文本 → 网络错误(与 Azure 空结果策略一致)"""
        monkeypatch.setattr(settings, "local_ocr_only", True)
        monkeypatch.setattr(pre_module, "rapid_available", lambda: True)
        monkeypatch.setattr(pre_module, "recognize_lines", lambda _p: None)
        page = _write_image(tmp_path)
        with pytest.raises(PipelineNetworkError, match="未识别到任何文本"):
            await OcrClient(settings).extract([page])

    async def test_switch_off_uses_provider_path(self, tmp_path, monkeypatch):
        """开关关闭 → 不走本地短路(仍走 vlm_openai 的既有路径)"""
        monkeypatch.setattr(settings, "local_ocr_only", False)
        monkeypatch.setattr(settings, "ocr_provider", "vlm_openai")
        monkeypatch.setattr(settings, "ocr_api_key", "")
        monkeypatch.setattr(
            pre_module, "recognize_lines", lambda _p: pytest.fail("开关关闭时不应触发本地短路")
        )
        page = _write_image(tmp_path)
        with pytest.raises(PipelineNetworkError, match="OCR_API_KEY"):
            await OcrClient(settings).extract([page])


class TestPreOcrLocalMode:
    async def test_local_mode_skips_endpoints(self, tmp_path, monkeypatch):
        """开关开启 → 预识别跳过全部端点,仅本地引擎(端点桩零调用)"""
        engine, factory, upload_dir, task_id = await _make_task_db(tmp_path)
        monkeypatch.setattr(pre_module, "SessionLocal", factory)
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        monkeypatch.setattr(settings, "mock_mode", False)
        monkeypatch.setattr(settings, "local_ocr_only", True)
        monkeypatch.setattr(
            pre_module,
            "crop_name_region_ex",
            lambda _c: (b"jpeg-bytes", {"basis": "landmarks", "main_points": [[1.0, 1.0]] * 4}),
        )
        monkeypatch.setattr(pre_module, "_RapidOCR", object())

        endpoint_calls: list[tuple] = []

        async def fake_endpoint(*args, **kwargs):
            endpoint_calls.append(args)
            return OcrExtractionResult(student_name="不应使用", student_id=None, transcribed_text="")

        monkeypatch.setattr(pre_module, "_extract_via_endpoint", fake_endpoint)
        monkeypatch.setattr(pre_module, "_extract_via_local_rapid", lambda _p: "Name: Wang Wei 20250001")

        await pre_module.run_pre_ocr(task_id)
        async with factory() as session:
            task = await session.get(CorrectionTask, task_id)
            assert task.student_name == "Wang Wei"
            assert task.student_id == "20250001"
        assert endpoint_calls == []  # 端点未被调用(完全离线)
        await engine.dispose()


class TestSettingsSync:
    def test_field_registered(self):
        """设置注册表:LOCAL_OCR_ONLY 已登记(管线 B·OCR 组,开关)"""
        from app.services.settings_service import FIELDS

        specs = {spec.key: spec for spec in FIELDS}
        assert "LOCAL_OCR_ONLY" in specs
        assert specs["LOCAL_OCR_ONLY"].group == "pipeline_b_ocr"
        assert specs["LOCAL_OCR_ONLY"].control == "switch"

    def test_update_request_accepts(self):
        """SettingsUpdateRequest 可接收 local_ocr_only"""
        from app.models.schemas import SettingsUpdateRequest

        request = SettingsUpdateRequest(local_ocr_only=True)
        assert request.local_ocr_only is True
