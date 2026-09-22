"""管线 A 人工复核能力测试(挂起 → 确认续跑 → 完成;关闭复核回归;异常自愈同口径)

通过 monkeypatch 模块级 LLMClient 为脚本化桩,断言调用角色/次数与进度序列。
"""

import io
import json
from pathlib import Path

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
from app.pipelines.base import CorrectionContext
from app.pipelines.local_vlm import LocalVLMPipeline
from tests.test_ocr_anomaly import SAMPLE as ANOMALY_TEXT

CLEAN_OCR = {
    "student_name": "李明",
    "student_id": None,
    "transcribed_text": "Hallo Welt. Ich heiße Li.",
    "recognition_quality": "high",
    "quality_note": "",
}
ANOMALY_OCR = {
    "student_name": "赖佳莹",
    "student_id": None,
    "transcribed_text": ANOMALY_TEXT,
    "recognition_quality": "high",
    "quality_note": "",
}
GRADING_JSON = {
    "student_name": "忽略值",
    "student_id": None,
    "transcribed_text": "忽略值",
    "overall_score": "18 / 25",
    "overall_comment": "ok",
    "errors": [],
    "highlights": [],
}


class FakeLLMClient:
    """脚本化桩:按序返回预设 JSON;记录每次调用的 tag 与消息"""

    scripted: list[str] = []
    calls: list[dict] = []

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0, pipeline_tag: str = "", **kwargs):
        self.tag = pipeline_tag
        self.base_url = base_url

    async def chat(self, messages, **kwargs):  # noqa: ARG002
        FakeLLMClient.calls.append({"tag": self.tag, "messages": messages})
        if not FakeLLMClient.scripted:
            raise AssertionError("FakeLLMClient 脚本已耗尽(调用次数超出预期)")
        return FakeLLMClient.scripted.pop(0)


@pytest.fixture(autouse=True)
def _reset_fakes(monkeypatch):
    FakeLLMClient.scripted = []
    FakeLLMClient.calls = []
    monkeypatch.setattr(local_vlm_module, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(settings, "ocr_anomaly_enabled", True)
    monkeypatch.setattr(settings, "ocr_anomaly_max_retry", 1)
    yield


def _probe_images(tmp_path: Path) -> list[Path]:
    """生成一张真实存在的 1×1 图片(新的存在性守卫需要文件实际存在)"""
    path = tmp_path / "page.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "JPEG")
    path.write_bytes(buffer.getvalue())
    return [path]


def _make_context(
    *, require_review: bool, ocr: OcrExtractionResult | None = None, image_paths: list[Path] | None = None
) -> tuple[CorrectionContext, list[tuple[str, float]]]:
    progress: list[tuple[str, float]] = []

    async def on_progress(stage: str, value: float) -> None:
        progress.append((stage, value))

    config = CorrectionConfig(
        pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
        grading_standard=GradingStandard.GAOKAO,
        detail_level=DetailLevel.MEDIUM,
        require_ocr_review=require_review,
    )
    ctx = CorrectionContext(
        task_id=1,
        image_paths=image_paths or [],
        config=config,
        ocr_result=ocr,
        on_progress=on_progress,
    )
    return ctx, progress


class TestPipelineAReview:
    async def test_phase1_pauses_for_review(self, tmp_path):
        """A 开启复核:阶段1 识别后挂起(与 B 同口径的程序与进度)"""
        FakeLLMClient.scripted = [json.dumps(CLEAN_OCR, ensure_ascii=False)]
        pipeline = LocalVLMPipeline(settings)
        ctx, progress = _make_context(require_review=True, image_paths=_probe_images(tmp_path))

        outcome = await pipeline.correct(ctx)

        assert outcome.waiting_review is True
        assert outcome.completed_result is None
        assert outcome.ocr_result is not None
        assert outcome.ocr_result.student_name == "李明"
        assert progress == [("OCR", 0.1), ("OCR", 0.5)]
        assert len(FakeLLMClient.calls) == 1
        assert FakeLLMClient.calls[0]["tag"] == "PIPELINE_A_LOCAL-OCR"
        assert "纯转录官" in FakeLLMClient.calls[0]["messages"][0]["content"]

    async def test_resume_grades_confirmed_transcript_without_reocr(self, tmp_path):
        """A 开启复核:确认后仅做文本评分,不重复识别;教师姓名/文本优先"""
        FakeLLMClient.scripted = [json.dumps(GRADING_JSON, ensure_ascii=False)]
        pipeline = LocalVLMPipeline(settings)
        confirmed = OcrExtractionResult(
            student_name="教师改", student_id="T001", transcribed_text="教师确认文本。"
        )
        ctx, progress = _make_context(
            require_review=True, ocr=confirmed, image_paths=_probe_images(tmp_path)
        )

        outcome = await pipeline.correct(ctx)

        assert outcome.waiting_review is False
        assert outcome.completed_result is not None
        assert outcome.completed_result.transcribed_text == "教师确认文本。"
        assert outcome.completed_result.student_name == "教师改"  # OCR/确认结果优先
        assert outcome.completed_result.student_id == "T001"
        assert outcome.completed_result.overall_score == "18 / 25"
        assert progress == [("GRADING", 0.6), ("RENDERING", 0.9)]
        assert len(FakeLLMClient.calls) == 1  # 仅评分一次,未重复识别
        grading_call = FakeLLMClient.calls[0]
        assert grading_call["tag"] == "PIPELINE_A_LOCAL-GRADING"
        assert "只依据给定转录的评卷人" in grading_call["messages"][0]["content"]
        user_content = grading_call["messages"][1]["content"]
        assert "教师确认文本。" in user_content and "教师改" in user_content and "T001" in user_content
        assert outcome.completed_result.markdown_report.startswith("# 德语作文批改报告 - 教师改")

    async def test_phase1_anomaly_retry_then_recover(self, tmp_path):
        """A 开启复核:识别异常时预算内重跑一次(与 B 同口径);第二次干净则继续挂起"""
        FakeLLMClient.scripted = [
            json.dumps(ANOMALY_OCR, ensure_ascii=False),
            json.dumps(CLEAN_OCR, ensure_ascii=False),
        ]
        pipeline = LocalVLMPipeline(settings)
        ctx, _progress = _make_context(require_review=True, image_paths=_probe_images(tmp_path))

        outcome = await pipeline.correct(ctx)

        assert outcome.waiting_review is True
        assert len(FakeLLMClient.calls) == 2  # 首次异常 → 自动重跑一次
        assert outcome.ocr_result is not None
        assert outcome.ocr_result.student_name == "李明"
        assert outcome.ocr_result.recognition_quality == "high"  # 第二次干净,无纠偏

    async def test_phase1_anomaly_persistent_degrades_quality(self, tmp_path):
        """A 开启复核:两次均异常 → 质量纠偏为 low + note,并继续挂起等待人工核对"""
        FakeLLMClient.scripted = [
            json.dumps(ANOMALY_OCR, ensure_ascii=False),
            json.dumps(ANOMALY_OCR, ensure_ascii=False),
        ]
        pipeline = LocalVLMPipeline(settings)
        ctx, _progress = _make_context(require_review=True, image_paths=_probe_images(tmp_path))

        outcome = await pipeline.correct(ctx)

        assert outcome.waiting_review is True
        assert len(FakeLLMClient.calls) == 2  # 预算用尽(1 次重试)
        assert outcome.ocr_result is not None
        assert outcome.ocr_result.recognition_quality == "low"
        assert "系统检测" in (outcome.ocr_result.quality_note or "")

    async def test_review_off_single_pass_regression(self, tmp_path):
        """A 关闭复核:保持既有单次执行行为(无挂起、进度不变)"""
        FakeLLMClient.scripted = [json.dumps(GRADING_JSON, ensure_ascii=False)]
        pipeline = LocalVLMPipeline(settings)
        ctx, progress = _make_context(require_review=False, image_paths=_probe_images(tmp_path))

        outcome = await pipeline.correct(ctx)

        assert outcome.waiting_review is False
        assert outcome.completed_result is not None
        assert outcome.ocr_result is None  # 单次路径不回传 OCR 中间结果(既有语义)
        assert progress == [("GRADING", 0.2), ("RENDERING", 0.9)]
        assert len(FakeLLMClient.calls) == 1
        assert FakeLLMClient.calls[0]["tag"] == "PIPELINE_A_LOCAL"
        assert "看图一手批改者" in FakeLLMClient.calls[0]["messages"][0]["content"]
