"""管线 B 异常自愈测试(OCR 重跑预算 / 强制复核降级 / 纠偏后继续评分)

使用 monkeypatch 替换 OCR 客户端与评分阶段,不触达真实模型。
"""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.schemas import (
    CorrectionConfig,
    DetailLevel,
    GradingLLMOutput,
    GradingStandard,
    OcrExtractionResult,
    PipelineChoice,
)
from app.pipelines.base import CorrectionContext
from app.pipelines.cloud_decoupled import DecoupledCloudPipeline
from tests.test_ocr_anomaly import CLEAN, SAMPLE


def _make_context(require_review: bool = False) -> CorrectionContext:
    async def noop_progress(stage: str, value: float) -> None:  # noqa: ARG001
        return None

    config = CorrectionConfig(
        pipeline_choice=PipelineChoice.PIPELINE_B_CLOUD,
        grading_standard=GradingStandard.GAOKAO,
        detail_level=DetailLevel.MEDIUM,
        require_ocr_review=require_review,
    )
    return CorrectionContext(
        task_id=1,
        image_paths=[],
        config=config,
        ocr_result=None,
        on_progress=noop_progress,
    )


def _grading_stub() -> GradingLLMOutput:
    return GradingLLMOutput(
        student_name="赖佳莹",
        student_id=None,
        transcribed_text="",
        overall_score="18 / 25",
        overall_comment="stub",
        errors=[],
        highlights=[],
    )


def _install_stubs(pipeline: DecoupledCloudPipeline, texts: list[str]) -> dict:
    calls = {"ocr": 0}

    async def fake_extract(paths):  # noqa: ARG001
        index = min(calls["ocr"], len(texts) - 1)
        calls["ocr"] += 1
        return OcrExtractionResult(
            student_name="赖佳莹",
            transcribed_text=texts[index],
            recognition_quality="high",
            quality_note="",
        )

    async def fake_grading(ctx, ocr):  # noqa: ARG001
        return _grading_stub()

    pipeline._ocr_client = SimpleNamespace(extract=fake_extract)
    pipeline._run_grading = fake_grading
    return calls


@pytest.fixture
def retry_settings(monkeypatch):
    monkeypatch.setattr(settings, "ocr_anomaly_enabled", True)
    monkeypatch.setattr(settings, "ocr_anomaly_max_retry", 1)
    monkeypatch.setattr(settings, "ocr_anomaly_force_review", True)


class TestOcrAnomalySelfHeal:
    async def test_retry_recovers_with_clean_second_result(self, retry_settings):
        pipeline = DecoupledCloudPipeline(settings)
        calls = _install_stubs(pipeline, [SAMPLE, CLEAN])
        outcome = await pipeline.correct(_make_context())
        assert calls["ocr"] == 2  # 首次异常 -> 自动重跑一次
        assert outcome.waiting_review is False
        assert outcome.completed_result is not None
        assert outcome.completed_result.transcribed_text == CLEAN

    async def test_persistent_anomaly_forces_review(self, retry_settings):
        pipeline = DecoupledCloudPipeline(settings)
        calls = _install_stubs(pipeline, [SAMPLE, SAMPLE])
        outcome = await pipeline.correct(_make_context(require_review=False))
        assert calls["ocr"] == 2  # 预算用尽(1 次重试)
        assert outcome.waiting_review is True  # 强制进入人工复核
        assert outcome.ocr_result is not None
        assert outcome.ocr_result.recognition_quality == "low"  # 自评已纠偏
        assert "系统检测" in (outcome.ocr_result.quality_note or "")

    async def test_force_review_off_still_degrades_quality(self, retry_settings, monkeypatch):
        monkeypatch.setattr(settings, "ocr_anomaly_force_review", False)
        pipeline = DecoupledCloudPipeline(settings)
        calls = _install_stubs(pipeline, [SAMPLE, SAMPLE])
        outcome = await pipeline.correct(_make_context(require_review=False))
        assert calls["ocr"] == 2
        assert outcome.waiting_review is False
        assert outcome.completed_result is not None
        assert outcome.ocr_result is not None
        assert outcome.ocr_result.recognition_quality == "low"

    async def test_max_retry_zero_detects_only(self, retry_settings, monkeypatch):
        monkeypatch.setattr(settings, "ocr_anomaly_max_retry", 0)
        pipeline = DecoupledCloudPipeline(settings)
        calls = _install_stubs(pipeline, [SAMPLE])
        outcome = await pipeline.correct(_make_context())
        assert calls["ocr"] == 1  # 不重试,仅检测
        assert outcome.waiting_review is True  # 仍触发强制复核降级
