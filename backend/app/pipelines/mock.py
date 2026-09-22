"""Mock 演示管线(MOCK_MODE=true 时启用)

用途:
- 无任何模型端点的环境下,开发/验收前端 UI 与全流程;
- 返回内容为固定的样例批改数据,走与真实管线完全相同的
  统一 Schema 与 Markdown 渲染路径,保证演示效果真实。

注意:此管线仅供开发演示,生产环境必须置 MOCK_MODE=false。
"""

from __future__ import annotations

import asyncio

from app.models.schemas import (
    DetailLevel,
    EssayCorrectionResult,
    ErrorItem,
    GradingStandard,
    OcrExtractionResult,
    PipelineChoice,
)
from app.pipelines.base import (
    AbstractCorrectionPipeline,
    CorrectionContext,
    CorrectionOutcome,
)
from app.services.report_renderer import finalize_result

# 样例转录文本(含典型语法错误,便于演示错误定位高亮)
_MOCK_TRANSCRIPT = (
    "Meine Sommerferien\n\n"
    "In den Sommerferien habe ich mit meine Familie nach Beijing gefahren. "
    "Wir haben viele Sehenswürdigkeiten besucht, zum Beispiel die Große Mauer. "
    "Ich denke, dass die Reise war sehr interessant. "
    "Obwohl das Wetter war heiß, wir hatten viel Spaß. "
    "Nächste Jahr möchte ich wieder dorthin fahren."
)

# 样例错误清单(覆盖语序 / 变格 / 框型结构等高频考点)
_MOCK_ERRORS = [
    ErrorItem(
        original_text="mit meine Familie",
        corrected_text="mit meiner Familie",
        error_type="名词变格",
        explanation="介词 mit 要求接第三格(Dativ),meine 应为 meiner。",
    ),
    ErrorItem(
        original_text="nach Beijing gefahren",
        corrected_text="nach Peking gefahren",
        error_type="词汇选择",
        explanation="德语中北京的地名写法为 Peking。",
    ),
    ErrorItem(
        original_text="dass die Reise war sehr interessant",
        corrected_text="dass die Reise sehr interessant war",
        error_type="动词位序",
        explanation="dass 引导的从句中,变位动词必须放在句末(动词末位规则)。",
    ),
    ErrorItem(
        original_text="Obwohl das Wetter war heiß, wir hatten viel Spaß",
        corrected_text="Obwohl das Wetter heiß war, hatten wir viel Spaß",
        error_type="动词位序",
        explanation="Obwohl 从句动词置于句末;主句因从句前置而采用倒装(动词提前)。",
    ),
    ErrorItem(
        original_text="Nächste Jahr",
        corrected_text="Nächstes Jahr",
        error_type="名词变格",
        explanation="Jahr 为中性名词(das Jahr),形容词词尾应为 -es:Nächstes Jahr。",
    ),
]

_MOCK_HIGHLIGHTS = [
    "zum Beispiel —— 举例衔接词使用自然",
    "Ich denke, dass ... —— 表达了个人观点,句式意识良好",
    "Obwohl ... —— 敢于使用让步从句,篇章逻辑有层次",
]


#: 演示管线展示名(报告头展示)
MOCK_DISPLAY_NAME = "演示模式(Mock)"
#: 演示样例固定身份(「清除演示数据」维护动作据此识别,固定成对匹配,真实数据不受影响)
MOCK_STUDENT_NAME = "李明"
MOCK_STUDENT_ID = "20260123"


class MockPipeline(AbstractCorrectionPipeline):
    """演示管线:返回固定样例数据,不调用任何外部服务"""

    choice = PipelineChoice.PIPELINE_A_LOCAL  # choice 仅为占位,工厂在 MOCK_MODE 下不关心

    @property
    def display_name(self) -> str:
        return MOCK_DISPLAY_NAME

    async def correct(self, ctx: CorrectionContext) -> CorrectionOutcome:
        """模拟真实管线的耗时节奏,产出与真实管线完全同构的结果"""
        # 模拟 OCR 阶段
        await ctx.report_progress("OCR", 0.1)
        await asyncio.sleep(0.5)

        # 人工复核(两条管线行为一致):开启复核且尚未携带确认转录时挂起
        if (
            ctx.config.require_ocr_review
            and ctx.ocr_result is None
        ):
            await ctx.report_progress("OCR", 0.5)
            return CorrectionOutcome(
                waiting_review=True,
                ocr_result=OcrExtractionResult(
                    student_name=MOCK_STUDENT_NAME,
                    student_id=MOCK_STUDENT_ID,
                    transcribed_text=_MOCK_TRANSCRIPT,
                ),
                pipeline_display_name=self.display_name,
            )

        # 模拟评分阶段
        await ctx.report_progress("GRADING", 0.6)
        await asyncio.sleep(0.8)
        await ctx.report_progress("RENDERING", 0.9)

        # 若携带人工复核后的转录,则使用教师确认过的内容
        transcript = ctx.ocr_result.transcribed_text if ctx.ocr_result else _MOCK_TRANSCRIPT
        student_name = ctx.ocr_result.student_name if ctx.ocr_result else MOCK_STUDENT_NAME
        student_id = (ctx.ocr_result.student_id if ctx.ocr_result else None) or MOCK_STUDENT_ID

        # 按细致度裁剪样例数据(模拟真实模型的行为)
        errors = _MOCK_ERRORS
        if ctx.config.detail_level == DetailLevel.LOW:
            errors = [
                ErrorItem(
                    original_text=e.original_text,
                    corrected_text="",
                    error_type=e.error_type,
                    explanation=None,
                )
                for e in _MOCK_ERRORS
            ]
        elif ctx.config.detail_level == DetailLevel.MEDIUM:
            errors = [
                ErrorItem(
                    original_text=e.original_text,
                    corrected_text=e.corrected_text,
                    error_type=e.error_type,
                    explanation=None,
                )
                for e in _MOCK_ERRORS
            ]

        if ctx.config.grading_standard == GradingStandard.GAOKAO:
            score = "18 / 25"
            comment = (
                "作文内容切题、表达基本连贯,能够使用从句与让步结构,值得肯定。"
                "主要扣分点集中在:介词后变格词尾错误、dass/obwohl 从句的动词末位语序、"
                "以及形容词词尾。建议专项复习框型结构与名词变格表。"
            )
        else:
            score = "B1 Pass"
            comment = (
                "整体达到 CEFR B1 水平:能就熟悉话题进行连贯叙述,并给出个人观点。"
                "段落结构清晰(引入—经历—展望),但从句语序与变格准确率不足,"
                "建议加强 Satzklammer 与 Dativ 的练习以冲击 B2。"
            )

        result = EssayCorrectionResult(
            student_name=student_name,
            student_id=student_id,
            transcribed_text=transcript,
            overall_score=score,
            overall_comment=comment,
            errors=errors,
            highlights=_MOCK_HIGHLIGHTS,
        )
        finalize_result(
            result=result,
            pipeline_choice=ctx.config.pipeline_choice,
            pipeline_display_name=self.display_name,
            standard=ctx.config.grading_standard,
            detail=ctx.config.detail_level,
        )
        return CorrectionOutcome(
            waiting_review=False,
            completed_result=result,
            pipeline_display_name=self.display_name,
        )
