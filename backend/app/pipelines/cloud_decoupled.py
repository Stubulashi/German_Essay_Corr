"""管线 B:解耦云端策略(DecoupledCloudPipeline)

机制(两段式):
- 阶段 1(视觉/OCR):Qwen2.5-VL 或 Azure OCR,仅提取 学生信息 + 转录文本;
- 阶段 2(可选):若开启 `require_ocr_review`,任务暂停为"待人工复核",
  教师在校订 UI 修改转录后提交,再继续评分;
- 阶段 3(评分):DeepSeek-V3 / DeepSeek-R1 文本 LLM,结构化评分。

输出与管线 A 完全一致:统一 `EssayCorrectionResult` + 后端渲染的 Markdown 报告。
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.config import Settings
from app.models.schemas import (
    EssayCorrectionResult,
    GradingLLMOutput,
    OcrExtractionResult,
    PipelineChoice,
)
from app.pipelines.base import (
    AbstractCorrectionPipeline,
    CorrectionContext,
    CorrectionOutcome,
    PipelineConfigError,
    PipelineParseError,
)
from app.pipelines.prompts import build_grading_prompt, build_grading_user_message
from app.services.llm_client import LLMClient
from app.services.ocr_client import OcrClient
from app.services.ocr_anomaly import run_ocr_with_guard
from app.services.parser import build_repair_user_message, extract_json_dict, normalize_optional_str
from app.services.report_renderer import finalize_result
from app.services.prompt_overrides import get_appendix
from app.services.style_learning_service import get_active_style_text

logger = logging.getLogger(__name__)


class DecoupledCloudPipeline(AbstractCorrectionPipeline):
    """管线 B:云端解耦(OCR + 文本 LLM 两段式)"""

    choice = PipelineChoice.PIPELINE_B_CLOUD

    def __init__(self, settings: Settings):
        self._settings = settings
        self._ocr_client = OcrClient(settings)

    @property
    def display_name(self) -> str:
        """展示名:DeepSeek 模型名(评分层的核心模型)"""
        model = (
            self._settings.deepseek_reasoning_model
            if self._settings.deepseek_use_reasoning
            else self._settings.deepseek_model
        )
        return f"云端 {model}"

    async def correct(self, ctx: CorrectionContext) -> CorrectionOutcome:
        """两段式执行:OCR(可暂停复核)-> 评分"""
        # ---------- 阶段 1:OCR(可暂停复核;内容异常时预算内自动重跑识别) ----------
        s = self._settings
        ocr: OcrExtractionResult | None = ctx.ocr_result
        if ocr is None:
            # 未携带已复核的转录 -> 执行 OCR(检测/重试/纠偏与管线 A 共用同一实现)
            await ctx.report_progress("OCR", 0.1)
            ocr, report = await run_ocr_with_guard(
                lambda: self._ocr_client.extract(ctx.image_paths),
                settings=s,
                image_count=len(ctx.image_paths),
                pipeline_tag=self.choice.value,
            )

            # 若开启了人工复核(或异常强制复核),第一阶段到此为止,挂起等待教师确认
            force_review = report.anomalous and s.ocr_anomaly_force_review
            if ctx.config.require_ocr_review or force_review:
                await ctx.report_progress("OCR", 0.5)
                return CorrectionOutcome(
                    waiting_review=True,
                    ocr_result=ocr,
                    pipeline_display_name=self.display_name,
                )
        else:
            logger.info("[%s] 检测到人工复核后的转录数据,跳过 OCR 直接评分", self.choice.value)

        # ---------- 阶段 2:文本 LLM 评分 ----------
        await ctx.report_progress("GRADING", 0.6)
        grading_output = await self._run_grading(ctx, ocr)

        # ---------- 阶段 3:组装统一结果并渲染 Markdown ----------
        await ctx.report_progress("RENDERING", 0.9)
        # 学生信息优先使用 OCR 阶段的结果(教师复核过,更可信)
        result = EssayCorrectionResult(
            student_name=ocr.student_name or grading_output.student_name or "未知",
            student_id=ocr.student_id or grading_output.student_id,
            transcribed_text=ocr.transcribed_text or grading_output.transcribed_text,
            overall_score=grading_output.overall_score,
            overall_comment=grading_output.overall_comment,
            errors=grading_output.errors,
            highlights=grading_output.highlights,
        )
        finalize_result(
            result=result,
            pipeline_choice=self.choice,
            pipeline_display_name=self.display_name,
            standard=ctx.config.grading_standard,
            detail=ctx.config.detail_level,
        )

        return CorrectionOutcome(
            waiting_review=False,
            completed_result=result,
            ocr_result=ocr,  # 一并回传,供服务层落库(便于前端追溯转录原文)
            pipeline_display_name=self.display_name,
        )

    # ---------------------------------------------------------
    # 评分阶段(含一次修复重试)
    # ---------------------------------------------------------
    async def _run_grading(
        self, ctx: CorrectionContext, ocr: OcrExtractionResult
    ) -> GradingLLMOutput:
        """调用 DeepSeek 文本 LLM 完成结构化评分"""
        s = self._settings
        if not s.deepseek_api_key:
            raise PipelineConfigError("DEEPSEEK_API_KEY 未配置", pipeline=self.choice.value)

        model = s.deepseek_reasoning_model if s.deepseek_use_reasoning else s.deepseek_model
        client = LLMClient(
            base_url=s.deepseek_base_url,
            api_key=s.deepseek_api_key,
            model=model,
            timeout=s.deepseek_timeout,
            pipeline_tag=self.choice.value,
            params_target="deepseek",
        )

        # 示范学习(风格画像)+ 设置中心(教师自定义附录):均为空时行为与现状完全一致
        system_prompt = build_grading_prompt(
            ctx.config.grading_standard,
            ctx.config.detail_level,
            style_context=get_active_style_text(),
            appendix=get_appendix("grading"),
        )
        user_message = build_grading_user_message(
            transcribed_text=ocr.transcribed_text,
            student_name=ocr.student_name,
            student_id=ocr.student_id,
            quality=ocr.recognition_quality,
            quality_note=ocr.quality_note,
        )
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        raw = await client.chat(messages)

        # 解析 + 一次修复重试
        first_error_msg = ""
        try:
            return self._validate(raw)
        except (ValueError, ValidationError) as first_error:
            first_error_msg = str(first_error)
            logger.warning("[%s] 首次解析失败,发起修复重试:%s", self.choice.value, first_error_msg)

        repair_messages = [
            *messages,
            {"role": "assistant", "content": raw},
            {"role": "user", "content": build_repair_user_message(raw, first_error_msg)},
        ]
        raw2 = await client.chat(repair_messages)
        try:
            return self._validate(raw2)
        except (ValueError, ValidationError) as second_error:
            raise PipelineParseError(
                f"JSON 解析/校验失败(已修复重试一次):{second_error}",
                raw_output=raw2,
                pipeline=self.choice.value,
            ) from second_error

    @staticmethod
    def _validate(raw: str) -> GradingLLMOutput:
        """将原始文本解析并校验为 GradingLLMOutput"""
        obj = extract_json_dict(raw)
        obj["student_id"] = normalize_optional_str(obj.get("student_id"))
        return GradingLLMOutput.model_validate(obj)
