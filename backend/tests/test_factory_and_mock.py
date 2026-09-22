"""策略工厂与 Mock 管线单元测试

验证:
- 工厂按配置实例化正确的管线实现;
- MOCK_MODE 优先返回演示管线;
- Mock 管线产出的结果符合统一 Schema 且 Markdown 与真实管线同构;
- 管线 B + 人工复核开关时返回"等待复核"状态。
"""

from pathlib import Path

from app.config import Settings
from app.models.schemas import (
    CorrectionConfig,
    DetailLevel,
    GradingStandard,
    PipelineChoice,
)
from app.pipelines.base import CorrectionContext
from app.pipelines.cloud_decoupled import DecoupledCloudPipeline
from app.pipelines.factory import get_pipeline
from app.pipelines.local_vlm import LocalVLMPipeline
from app.pipelines.mock import MockPipeline


def _default_settings() -> Settings:
    """构造测试用配置(mock 关闭)"""
    return Settings(mock_mode=False)


class TestFactory:
    """策略工厂测试"""

    def test_pipeline_a_instantiation(self):
        """选择 A 时返回 LocalVLMPipeline"""
        config = CorrectionConfig(pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL)
        pipeline = get_pipeline(config, _default_settings())
        assert isinstance(pipeline, LocalVLMPipeline)

    def test_pipeline_b_instantiation(self):
        """选择 B 时返回 DecoupledCloudPipeline"""
        config = CorrectionConfig(pipeline_choice=PipelineChoice.PIPELINE_B_CLOUD)
        pipeline = get_pipeline(config, _default_settings())
        assert isinstance(pipeline, DecoupledCloudPipeline)

    def test_mock_mode_priority(self):
        """MOCK_MODE=true 时无论选择哪条管线都返回 MockPipeline"""
        settings = Settings(mock_mode=True)
        for choice in (PipelineChoice.PIPELINE_A_LOCAL, PipelineChoice.PIPELINE_B_CLOUD):
            config = CorrectionConfig(pipeline_choice=choice)
            assert isinstance(get_pipeline(config, settings), MockPipeline)


class TestMockPipeline:
    """Mock 管线端到端行为测试"""

    async def test_pipeline_a_flow_produces_unified_result(self):
        """管线 A 流程:一次执行到底,产出统一 Schema 结果"""
        pipeline = MockPipeline()
        ctx = CorrectionContext(
            task_id=1,
            image_paths=[Path("fake_page.jpg")],
            config=CorrectionConfig(
                pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
                grading_standard=GradingStandard.GAOKAO,
                detail_level=DetailLevel.HIGH,
            ),
        )
        outcome = await pipeline.correct(ctx)

        assert outcome.waiting_review is False
        result = outcome.completed_result
        assert result is not None
        # 统一 Schema 关键字段
        assert result.student_name != ""
        assert result.overall_score == "18 / 25"
        assert len(result.errors) > 0
        assert len(result.highlights) > 0
        # Markdown 报告由渲染器生成且结构合规
        assert result.markdown_report.startswith("# 德语作文批改报告")
        assert "### 📝 总体评价" in result.markdown_report
        assert "### 🔍 详细批改" in result.markdown_report
        assert "### 📊 词汇与句型亮点/短板" in result.markdown_report
        # HIGH 细致度:包含错因解析
        assert "**错因解析**" in result.markdown_report

    async def test_pipeline_b_review_pause_and_resume(self):
        """管线 B + 复核开关:先返回等待复核,携带复核数据后完成评分"""
        pipeline = MockPipeline()
        config = CorrectionConfig(
            pipeline_choice=PipelineChoice.PIPELINE_B_CLOUD,
            grading_standard=GradingStandard.DSD,
            detail_level=DetailLevel.MEDIUM,
            require_ocr_review=True,
        )

        # 第一次执行:应挂起等待复核
        ctx1 = CorrectionContext(task_id=2, image_paths=[Path("x.jpg")], config=config)
        outcome1 = await pipeline.correct(ctx1)
        assert outcome1.waiting_review is True
        assert outcome1.ocr_result is not None
        assert outcome1.completed_result is None

        # 第二次执行:携带教师复核后的转录,应直接产出结果
        reviewed = outcome1.ocr_result.model_copy(update={"student_name": "复核后的名字"})
        ctx2 = CorrectionContext(
            task_id=2, image_paths=[Path("x.jpg")], config=config, ocr_result=reviewed
        )
        outcome2 = await pipeline.correct(ctx2)
        assert outcome2.waiting_review is False
        assert outcome2.completed_result is not None
        assert outcome2.completed_result.student_name == "复核后的名字"
        assert outcome2.completed_result.overall_score == "B1 Pass"

    async def test_low_detail_crops_explanation(self):
        """LOW 细致度:报告中不含修改与解析"""
        pipeline = MockPipeline()
        ctx = CorrectionContext(
            task_id=3,
            image_paths=[Path("x.jpg")],
            config=CorrectionConfig(
                pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL,
                detail_level=DetailLevel.LOW,
            ),
        )
        outcome = await pipeline.correct(ctx)
        report = outcome.completed_result.markdown_report
        assert "**修正**" not in report
        assert "**错因解析**" not in report
        # 错误条目仍被标注(corrected_text 为空)
        assert all(e.corrected_text == "" for e in outcome.completed_result.errors)
