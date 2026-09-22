"""Markdown 报告渲染器单元测试

重点验证"双管线输出一致性"的渲染层保障:
- 三档细致度(LOW/MEDIUM/HIGH)的层级裁剪是否正确;
- 报告结构是否符合规格(标题/管线信息/总体评价/详细批改/亮点短板);
- 错误片段在转录全文中的定位高亮。
"""

from app.models.schemas import (
    DetailLevel,
    EssayCorrectionResult,
    ErrorItem,
    GradingStandard,
    PipelineChoice,
)
from app.services.report_renderer import finalize_result, render_markdown_report, render_student_report

# 公共测试数据
_TRANSCRIPT = "Ich habe mit meine Familie nach Berlin gefahren."
_ERRORS = [
    ErrorItem(
        original_text="mit meine Familie",
        corrected_text="mit meiner Familie",
        error_type="Case Ending",
        explanation="mit 后接第三格,meine 应为 meiner。",
    )
]


def _make_result() -> EssayCorrectionResult:
    """构造标准测试结果"""
    return EssayCorrectionResult(
        student_name="王小明",
        student_id="20260001",
        transcribed_text=_TRANSCRIPT,
        overall_score="18 / 25",
        overall_comment="总体表现良好。",
        errors=_ERRORS,
        highlights=["zum Beispiel 使用自然"],
    )


class TestReportStructure:
    """报告结构测试"""

    def test_header_and_sections(self):
        """报告头与各章节齐全"""
        report = render_markdown_report(
            _make_result(),
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="本地 Qwen3.8-27B",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.HIGH,
        )
        assert "# 德语作文批改报告 - 王小明 / 20260001" in report
        assert "**运行管线:** 本地 Qwen3.8-27B" in report
        assert "**考试标准:** 高考" in report
        assert "**细致度:** 高" in report
        assert "**综合得分:** 18 / 25" in report
        assert "### 📝 总体评价" in report
        assert "### 🔍 详细批改" in report
        assert "### 📊 词汇与句型亮点/短板" in report

    def test_dsd_standard_label(self):
        """DSD 标准标签渲染"""
        report = render_markdown_report(
            _make_result(),
            pipeline_choice=PipelineChoice.PIPELINE_B_CLOUD,
            pipeline_display_name="云端 deepseek-chat",
            standard=GradingStandard.DSD,
            detail=DetailLevel.MEDIUM,
        )
        assert "**考试标准:** DSD" in report
        assert "**运行管线:** 云端 deepseek-chat" in report

    def test_error_located_with_context(self):
        """错误片段应在转录全文上下文中被 ❌ 包裹定位"""
        report = render_markdown_report(
            _make_result(),
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.HIGH,
        )
        assert "❌**mit meine Familie**❌" in report

    def test_weakness_frequency_statistics(self):
        """薄弱点应按规范化考点分类的频次统计(英文别名自动归一为中文标签)"""
        result = _make_result()
        result.errors = [
            ErrorItem(original_text="a", corrected_text="b", error_type="Word Order"),
            ErrorItem(original_text="c", corrected_text="d", error_type="Word Order"),
            ErrorItem(original_text="e", corrected_text="f", error_type="Case Ending"),
        ]
        report = render_markdown_report(
            result,
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.MEDIUM,
        )
        assert "动词位序(出现 2 次)" in report
        assert "名词变格" in report


class TestDetailLevels:
    """细致度层级裁剪测试(双管线输出的核心差异点)"""

    def test_low_only_original(self):
        """LOW:仅原句标注,不显示修改与解析"""
        report = render_markdown_report(
            _make_result(),
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.LOW,
        )
        assert "> **原句**" in report
        assert "**修正**" not in report
        assert "**错因解析**" not in report
        assert "**细致度:** 低" in report

    def test_medium_adds_correction(self):
        """MEDIUM:显示修改,不显示错因解析"""
        report = render_markdown_report(
            _make_result(),
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.MEDIUM,
        )
        assert "✅ mit meiner Familie ✅" in report
        assert "**错因解析**" not in report

    def test_high_adds_explanation(self):
        """HIGH:显示修改与中文错因解析"""
        report = render_markdown_report(
            _make_result(),
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.HIGH,
        )
        assert "✅ mit meiner Familie ✅" in report
        assert "mit 后接第三格" in report


class TestFinalizeResult:
    """finalize_result 填充行为测试"""

    def test_markdown_report_populated(self):
        """finalize_result 应填充 markdown_report 字段"""
        result = _make_result()
        assert result.markdown_report == ""
        returned = finalize_result(
            result=result,
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="本地 Qwen3.8-27B",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.MEDIUM,
        )
        assert returned is result  # 原地修改并返回同一对象
        assert result.markdown_report.startswith("# 德语作文批改报告 - 王小明 / 20260001")

    def test_no_errors_fallback_text(self):
        """无错误时应渲染鼓励文案"""
        result = _make_result()
        result.errors = []
        finalize_result(
            result=result,
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.MEDIUM,
        )
        assert "本次批改未发现明显语法错误" in result.markdown_report
        assert "暂未发现高频语法薄弱点" in result.markdown_report

    def test_unknown_student_title(self):
        """姓名为"未知"时的标题渲染(不应出现学号分隔符)"""
        result = _make_result()
        result.student_name = "未知"
        result.student_id = None
        finalize_result(
            result=result,
            pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
            pipeline_display_name="",
            standard=GradingStandard.GAOKAO,
            detail=DetailLevel.LOW,
        )
        assert result.markdown_report.startswith("# 德语作文批改报告 - 未知")


class TestStudentReport:
    """学生版报告(#11)测试"""

    def test_student_report_structure(self):
        """学生版报告应含订正清单/亮点/练习重点/寄语,且不展示分数"""
        report = render_student_report(
            _make_result(), standard=GradingStandard.GAOKAO, detail=DetailLevel.HIGH
        )
        assert report.startswith("# 德语作文订正单 - 王小明")
        assert "### 一、你的订正清单" in report
        assert "### 二、做得好的地方" in report
        assert "### 三、下一步练习重点" in report
        assert "### 四、教师寄语" in report
        # 弱化分数:不应出现综合得分数字
        assert "18 / 25" not in report
        # HIGH 细致度含小提示与修正
        assert "✅ mit meiner Familie ✅" in report
        assert "**小提示**" in report
        # 练习重点含分类建议
        assert "名词变格" in report

    def test_student_report_low_detail(self):
        """LOW 细致度:仅标注错误位置,无修正与小提示"""
        result = _make_result()
        result.errors = [
            ErrorItem(original_text="mit meine Familie", corrected_text="", error_type="名词变格")
        ]
        report = render_student_report(
            result, standard=GradingStandard.GAOKAO, detail=DetailLevel.LOW
        )
        assert "❌**mit meine Familie**❌" in report
        assert "**修正**" not in report
        assert "**小提示**" not in report

    def test_student_report_no_errors(self):
        """无错误时的鼓励文案与默认练习建议"""
        result = _make_result()
        result.errors = []
        report = render_student_report(
            result, standard=GradingStandard.DSD, detail=DetailLevel.MEDIUM
        )
        assert "没有发现明显错误" in report
        assert "连接词或从句" in report
