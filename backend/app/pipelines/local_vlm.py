"""管线 A:统一本地 VLM 策略(LocalVLMPipeline)

机制(与管线 B 功能对齐,唯一差异为后端端点/模型):
- 关闭复核(默认):单次 Prompt 执行——手写图片直接输入,一步产出
  学生信息 + 转录文本 + 评分 + 错误清单 + 亮点;
- 开启复核:两段式——阶段1 以「OCR 角色提示词」调用本地 VLM 完成识别后挂起
  (WAITING_REVIEW,含与 B 同口径的异常检测/重试/质量纠偏);教师确认后阶段2
  以「评分角色提示词」仅依据已确认转录完成评分与报告(不重复识别)。

约定(与管线 B 完全一致):
- 上下文携带 `ctx.ocr_result`(人工复核后的转录)即跳过识别、直接进入评分;
- 输出统一为 `EssayCorrectionResult`(markdown_report 由渲染器填充)。
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
from app.pipelines.prompts import (
    build_grading_prompt,
    build_grading_user_message,
    build_ocr_prompt,
    build_ocr_user_message,
    build_pipeline_a_prompt,
    build_pipeline_a_user_message,
)
from app.services.llm_client import LLMClient, build_image_message_parts
from app.services.ocr_anomaly import raise_if_vision_failed, run_ocr_with_guard
from app.services.ocr_client import OcrClient
from app.services.parser import build_repair_user_message, extract_json_dict, normalize_optional_str
from app.services.report_renderer import finalize_result
from app.services.prompt_overrides import get_appendix
from app.services.style_learning_service import get_active_style_text

logger = logging.getLogger(__name__)


class LocalVLMPipeline(AbstractCorrectionPipeline):
    """管线 A:本地统一 VLM(默认单次执行;开启复核时两段式)"""

    choice = PipelineChoice.PIPELINE_A_LOCAL

    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def display_name(self) -> str:
        """展示名:带出实际模型名,便于报告与排查"""
        model = self._settings.local_vlm_model.rsplit("/", 1)[-1]
        return f"本地 {model}"

    def _make_client(self, tag: str) -> LLMClient:
        """构造指向本地 VLM 端点的客户端(不同阶段仅 pipeline_tag 不同)"""
        s = self._settings
        return LLMClient(
            base_url=s.local_vlm_base_url,
            api_key=s.local_vlm_api_key,
            model=s.local_vlm_model,
            timeout=s.local_vlm_timeout,
            pipeline_tag=tag,
            params_target="pipeline_a",
        )

    async def correct(self, ctx: CorrectionContext) -> CorrectionOutcome:
        """执行批改(三分支与管线 B 完全对齐,仅端点不同)

        1) 上下文携带已(人工复核)的转录 -> 跳过识别,直接评分;
        2) 开启人工复核 -> 识别(OCR 角色)后挂起等待教师确认;
        3) 否则 -> 保持既有单次执行行为(识别 + 评分一次完成)。
        """
        if not self._settings.local_vlm_base_url:
            raise PipelineConfigError(
                "LOCAL_VLM_BASE_URL 未配置", pipeline=self.choice.value
            )

        # 约定(与 B 相同):携带已复核转录 → 直接进入评分阶段
        if ctx.ocr_result is not None:
            return await self._grade_confirmed(ctx)

        # 开启人工复核:两段式第一阶段——识别后挂起
        if ctx.config.require_ocr_review:
            ocr = await self._run_ocr_stage(ctx)
            await ctx.report_progress("OCR", 0.5)
            return CorrectionOutcome(
                waiting_review=True,
                ocr_result=ocr,
                pipeline_display_name=self.display_name,
            )

        # 关闭复核:保持既有单次执行行为不变
        return await self._run_single_pass(ctx)

    # ---------------------------------------------------------
    # 路径 1:单次执行(关闭复核;既有行为,原样保留)
    # ---------------------------------------------------------
    async def _run_single_pass(self, ctx: CorrectionContext) -> CorrectionOutcome:
        """单次执行完整批改流程(识别 + 评分一次完成)"""
        # 组装多模态消息
        # 示范学习(风格画像)+ 设置中心(教师自定义附录):均为空时行为与现状完全一致
        system_prompt = build_pipeline_a_prompt(
            ctx.config.grading_standard,
            ctx.config.detail_level,
            style_context=get_active_style_text(),
            appendix=get_appendix("pipeline_a"),
        )
        content = build_image_message_parts(
            ctx.image_paths, build_pipeline_a_user_message(len(ctx.image_paths))
        )
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        client = self._make_client(self.choice.value)

        # 执行(含一次"修复重试")
        await ctx.report_progress("GRADING", 0.2)
        raw = await client.chat(messages)
        grading_output = await self._parse_with_repair(client, messages, raw)

        # 组装统一结果并渲染 Markdown
        await ctx.report_progress("RENDERING", 0.9)
        result = EssayCorrectionResult(
            student_name=grading_output.student_name or "未知",
            student_id=grading_output.student_id,
            transcribed_text=grading_output.transcribed_text,
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
            pipeline_display_name=self.display_name,
        )

    # ---------------------------------------------------------
    # 路径 2:阶段 1 识别(开启复核时;与 B 的 OCR 阶段同口径)
    # ---------------------------------------------------------
    async def _run_ocr_stage(self, ctx: CorrectionContext) -> OcrExtractionResult:
        """识别阶段:OCR 角色提示词 + 本地 VLM;异常检测/重试/纠偏与 B 共用实现"""
        s = self._settings
        existing = [p for p in ctx.image_paths if p.exists()]
        if not existing:
            raise PipelineConfigError(
                "任务缺少可用的图片文件(文件缺失或被移动),无法进行识别;"
                "请在队列页重试或重新上传",
                pipeline=self.choice.value,
            )
        client = self._make_client("PIPELINE_A_LOCAL-OCR")
        content = build_image_message_parts(
            existing, build_ocr_user_message(len(existing))
        )
        messages: list[dict] = [
            {"role": "system", "content": build_ocr_prompt(get_appendix("ocr"))},
            {"role": "user", "content": content},
        ]
        await ctx.report_progress("OCR", 0.1)

        async def produce() -> OcrExtractionResult:
            raw = await client.chat(messages)
            ocr_result = OcrClient._parse_ocr_output(raw)
            # 视觉失败识别:模型自述“未收到图片”→ 可操作的配置错误
            raise_if_vision_failed(
                ocr_result, f"{s.local_vlm_base_url} / {s.local_vlm_model}"
            )
            return ocr_result

        ocr, _report = await run_ocr_with_guard(
            produce,
            settings=s,
            image_count=len(ctx.image_paths),
            pipeline_tag=self.choice.value,
        )
        return ocr

    # ---------------------------------------------------------
    # 路径 3:阶段 2/确认续跑 评分(仅依据已确认转录;与 B 评分阶段同口径)
    # ---------------------------------------------------------
    async def _grade_confirmed(self, ctx: CorrectionContext) -> CorrectionOutcome:
        """评分阶段:评分角色提示词 + 本地 VLM,仅依据已有(教师确认)转录"""
        ocr = ctx.ocr_result
        if ocr is None:  # 防御:分支条件已保证,理论不可达
            raise PipelineConfigError(
                "缺少人工复核转录,无法进入评分阶段", pipeline=self.choice.value
            )

        client = self._make_client("PIPELINE_A_LOCAL-GRADING")
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

        await ctx.report_progress("GRADING", 0.6)
        raw = await client.chat(messages)
        grading_output = await self._parse_with_repair(client, messages, raw)

        # 组装统一结果(与 B 相同的合并规则:学生信息与转录以 OCR/复核结果优先)
        await ctx.report_progress("RENDERING", 0.9)
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
            ocr_result=ocr,  # 回传供服务层落库(与 B 一致,便于前端追溯)
            pipeline_display_name=self.display_name,
        )

    # ---------------------------------------------------------
    # 解析工具(两条路径共用;逻辑不变)
    # ---------------------------------------------------------
    async def _parse_with_repair(
        self, client: LLMClient, messages: list[dict], raw: str
    ) -> GradingLLMOutput:
        """解析 LLM 输出;失败时携带错误信息发起一次修复重试

        Raises:
            PipelineParseError: 修复重试后仍无法解析/校验
        """
        first_error_msg = ""
        try:
            return self._validate(raw)
        except (ValueError, ValidationError) as first_error:
            # 注意:except 块结束后异常变量会被 Python 自动删除,需先转存为字符串
            first_error_msg = str(first_error)
            logger.warning("[%s] 首次解析失败,发起修复重试:%s", self.choice.value, first_error_msg)

        # 修复重试:把原始输出与错误信息回传模型
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
        # 归一化可能出现的空值文本
        obj["student_id"] = normalize_optional_str(obj.get("student_id"))
        return GradingLLMOutput.model_validate(obj)
