"""视觉失败识别与空内容增强测试(F1/F2/F3/F4)

覆盖:“未收到图片”类模型回复不再降级为“识别质量低”,而是可操作的配置错误;
思考型空 content 的明确报错;连通性视觉探针的判定纯函数。

注意:真实 LLMClient.chat 现在会写审计(data/audit.log),本模块统一拦截,避免测试污染真实审计文件。
"""

import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.config import settings
from app.models.schemas import (
    CorrectionConfig,
    DetailLevel,
    GradingStandard,
    OcrExtractionResult,
    PipelineChoice,
)
from app.pipelines import local_vlm as local_vlm_module
from app.pipelines.base import CorrectionContext, PipelineConfigError, PipelineNetworkError
from app.pipelines.local_vlm import LocalVLMPipeline
from app.services import audit_service
from app.services import llm_client as llm_client_module
from app.services.llm_client import LLMClient
from app.services.ocr_anomaly import raise_if_vision_failed
from app.services.ocr_client import OcrClient
from app.services.settings_service import _classify_vision_probe


@pytest.fixture(autouse=True)
def audit_silencer(monkeypatch):
    """拦截审计写入(不触碰真实 audit.log)"""

    async def _fake(event: str, *, ok: bool = True, detail: str = "", source: str = "api") -> None:
        return None

    monkeypatch.setattr(audit_service, "audit", _fake)

VISION_FAIL_JSON = {
    "student_name": "未知",
    "student_id": None,
    "transcribed_text": "",
    "recognition_quality": "low",
    "quality_note": "未收到任何图片,无法进行转录。",
}


class _FakeVisionFailClient:
    """A 管线桩:固定返回“未收到图片”形态的 OCR JSON"""

    def __init__(self, *args, **kwargs):
        pass

    async def chat(self, messages, **kwargs):  # noqa: ARG002
        return json.dumps(VISION_FAIL_JSON, ensure_ascii=False)


def _probe_image(tmp_path: Path) -> list[Path]:
    path = tmp_path / "page.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "JPEG")
    path.write_bytes(buffer.getvalue())
    return [path]


class TestImageExistenceGuard:
    def test_missing_files_raise_config_error(self, tmp_path):
        """F1:图片文件全缺失 → 配置错误(不发“无图请求”)"""
        client = OcrClient(settings)
        with pytest.raises(PipelineConfigError) as exc:
            import asyncio

            asyncio.run(client.extract([tmp_path / "not-exists.jpg"]))
        assert "缺少可用的图片" in str(exc.value)


class TestVisionFailureGuard:
    def test_pattern_hit_raises_config_error(self):
        """F2 单元:模型自述未收到图片 → 可操作配置错误(含端点与替代路径)"""
        ocr = OcrExtractionResult(
            student_name="未知",
            student_id=None,
            transcribed_text="",
            recognition_quality="low",
            quality_note="未收到任何图片,无法进行转录。",
        )
        with pytest.raises(PipelineConfigError) as exc:
            raise_if_vision_failed(ocr, "https://school.example/v1 / qwen3.8-27b")
        message = str(exc.value)
        assert "不支持图片输入" in message
        assert "qwen-vl-plus" in message and "school.example" in message

    def test_normal_result_passes(self):
        ocr = OcrExtractionResult(
            student_name="李明",
            student_id=None,
            transcribed_text="Hallo Welt.",
            recognition_quality="high",
            quality_note="",
        )
        assert raise_if_vision_failed(ocr, "x") is None

    async def test_pipeline_a_stage1_raises_and_skips_quality_path(self, tmp_path, monkeypatch):
        """F2 集成:A 复核阶段1 收到“未收到图片”→ 配置错误,不产生质量纠偏"""
        monkeypatch.setattr(local_vlm_module, "LLMClient", _FakeVisionFailClient)

        async def on_progress(stage: str, value: float) -> None:
            return None

        ctx = CorrectionContext(
            task_id=1,
            image_paths=_probe_image(tmp_path),
            config=CorrectionConfig(
                pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
                grading_standard=GradingStandard.GAOKAO,
                detail_level=DetailLevel.MEDIUM,
                require_ocr_review=True,
            ),
            ocr_result=None,
            on_progress=on_progress,
        )
        with pytest.raises(PipelineConfigError) as exc:
            await LocalVLMPipeline(settings).correct(ctx)
        assert "不支持图片输入" in str(exc.value)


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    payload: dict = {}

    def __init__(self, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):  # noqa: ARG002
        return _FakeResponse(_FakeAsyncClient.payload)


class TestEmptyContentEnhancement:
    """F3:思考型空 content → 明确报错(区分于一般空响应)"""

    async def test_reasoning_only_content(self, monkeypatch):
        monkeypatch.setattr(llm_client_module.httpx, "AsyncClient", _FakeAsyncClient)
        _FakeAsyncClient.payload = {
            "choices": [{"message": {"content": "", "reasoning_content": "思考中……"}}]
        }
        client = LLMClient("http://x/v1", "k", "m", pipeline_tag="TEST")
        with pytest.raises(PipelineNetworkError) as exc:
            await client.chat([{"role": "user", "content": "hi"}])
        assert "思考内容" in str(exc.value)

    async def test_plain_empty_content(self, monkeypatch):
        monkeypatch.setattr(llm_client_module.httpx, "AsyncClient", _FakeAsyncClient)
        _FakeAsyncClient.payload = {"choices": [{"message": {"content": None}}]}
        client = LLMClient("http://x/v1", "k", "m", pipeline_tag="TEST")
        with pytest.raises(PipelineNetworkError) as exc:
            await client.chat([{"role": "user", "content": "hi"}])
        assert "内容为空" in str(exc.value)

    async def test_normal_content_passes(self, monkeypatch):
        monkeypatch.setattr(llm_client_module.httpx, "AsyncClient", _FakeAsyncClient)
        _FakeAsyncClient.payload = {"choices": [{"message": {"content": "OK"}}]}
        client = LLMClient("http://x/v1", "k", "m", pipeline_tag="TEST")
        assert await client.chat([{"role": "user", "content": "hi"}]) == "OK"


class TestVisionProbeClassifier:
    """F4:连通性视觉探针判定纯函数"""

    def test_supports_image_rejection(self):
        ok, detail = _classify_vision_probe(400, "invalid image_url content")
        assert ok is False and "不支持图片输入" in detail

    def test_success(self):
        ok, detail = _classify_vision_probe(200, "OK")
        assert ok is True and "视觉输入可用" in detail

    def test_200_but_model_says_no_image(self):
        ok, detail = _classify_vision_probe(200, "抱歉,没有收到任何图片")
        assert ok is False

    def test_other_error(self):
        ok, detail = _classify_vision_probe(500, "internal error")
        assert ok is False and "HTTP 500" in detail
