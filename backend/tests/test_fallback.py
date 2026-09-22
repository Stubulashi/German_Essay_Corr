"""A→B 故障转移单元测试

验证 `CorrectionService._execute_with_fallback` 的转移规则:
1. 管线 A 抛出 PipelineNetworkError 且开关开启 -> 自动切换管线 B,标记 fallback_used=True;
2. 开关关闭 -> 直接抛出,不转移;
3. 非网络异常(ParseError / ConfigError)-> 不转移,直接抛出。
"""

from pathlib import Path

import pytest

from app.config import Settings
from app.models.schemas import (
    CorrectionConfig,
    EssayCorrectionResult,
    PipelineChoice,
)
from app.pipelines.base import (
    AbstractCorrectionPipeline,
    CorrectionContext,
    CorrectionOutcome,
    PipelineNetworkError,
    PipelineParseError,
)
from app.services import correction_service as service_module
from app.services.correction_service import CorrectionService


class FakeNetworkFailPipeline(AbstractCorrectionPipeline):
    """模拟"网络失败"的管线 A"""

    choice = PipelineChoice.PIPELINE_A_LOCAL

    @property
    def display_name(self) -> str:
        return "测试-网络失败管线"

    async def correct(self, ctx: CorrectionContext) -> CorrectionOutcome:
        raise PipelineNetworkError("连接被拒绝(测试模拟)", pipeline=self.choice.value)


class FakeParseFailPipeline(AbstractCorrectionPipeline):
    """模拟"解析失败"的管线 A(不应触发转移)"""

    choice = PipelineChoice.PIPELINE_A_LOCAL

    @property
    def display_name(self) -> str:
        return "测试-解析失败管线"

    async def correct(self, ctx: CorrectionContext) -> CorrectionOutcome:
        raise PipelineParseError("JSON 校验失败(测试模拟)", raw_output="{bad}", pipeline=self.choice.value)


class FakeSuccessPipeline(AbstractCorrectionPipeline):
    """模拟成功的管线 B"""

    choice = PipelineChoice.PIPELINE_B_CLOUD

    @property
    def display_name(self) -> str:
        return "测试-成功管线B"

    async def correct(self, ctx: CorrectionContext) -> CorrectionOutcome:
        result = EssayCorrectionResult(
            student_name="测试学生",
            transcribed_text="Test text",
            overall_score="20 / 25",
            overall_comment="来自 B 管线",
            errors=[],
            highlights=[],
            markdown_report="# 测试报告",
        )
        return CorrectionOutcome(
            completed_result=result, pipeline_display_name=self.display_name
        )


def _make_ctx() -> CorrectionContext:
    """构造测试上下文(管线 A 选择)"""
    return CorrectionContext(
        task_id=99,
        image_paths=[Path("test.jpg")],
        config=CorrectionConfig(pipeline_choice=PipelineChoice.PIPELINE_A_LOCAL),
    )


def _make_service(allow_fallback: bool) -> CorrectionService:
    """构造服务实例(session_factory 在该测试路径中不被使用)"""
    settings = Settings(allow_auto_fallback=allow_fallback, mock_mode=False)
    return CorrectionService(session_factory=None, settings=settings)  # type: ignore[arg-type]


class TestFallbackRules:
    """故障转移规则测试"""

    async def test_network_error_triggers_fallback(self, monkeypatch):
        """A 网络失败 + 开关开启 -> 自动切换 B,fallback_used=True"""
        pipelines = {PipelineChoice.PIPELINE_A_LOCAL: FakeNetworkFailPipeline(),
                     PipelineChoice.PIPELINE_B_CLOUD: FakeSuccessPipeline()}

        def fake_get_pipeline(config, settings=None):
            return pipelines[config.pipeline_choice]

        monkeypatch.setattr(service_module, "get_pipeline", fake_get_pipeline)

        service = _make_service(allow_fallback=True)
        outcome, used_pipeline, fallback_used = await service._execute_with_fallback(_make_ctx())

        assert fallback_used is True
        assert used_pipeline == PipelineChoice.PIPELINE_B_CLOUD
        assert outcome.completed_result is not None
        assert outcome.completed_result.overall_score == "20 / 25"

    async def test_network_error_without_switch_raises(self, monkeypatch):
        """A 网络失败 + 开关关闭 -> 不转移,异常向上抛出"""

        def fake_get_pipeline(config, settings=None):
            return FakeNetworkFailPipeline()

        monkeypatch.setattr(service_module, "get_pipeline", fake_get_pipeline)

        service = _make_service(allow_fallback=False)
        with pytest.raises(PipelineNetworkError):
            await service._execute_with_fallback(_make_ctx())

    async def test_parse_error_never_falls_back(self, monkeypatch):
        """A 解析失败 -> 即使开关开启也不转移"""

        def fake_get_pipeline(config, settings=None):
            return FakeParseFailPipeline()

        monkeypatch.setattr(service_module, "get_pipeline", fake_get_pipeline)

        service = _make_service(allow_fallback=True)
        with pytest.raises(PipelineParseError):
            await service._execute_with_fallback(_make_ctx())

    async def test_b_selected_no_fallback_direction(self, monkeypatch):
        """选择 B 时,B 自身失败不做任何转移(仅 A→B 单向)"""

        class FakeBFailPipeline(FakeNetworkFailPipeline):
            choice = PipelineChoice.PIPELINE_B_CLOUD

        def fake_get_pipeline(config, settings=None):
            return FakeBFailPipeline()

        monkeypatch.setattr(service_module, "get_pipeline", fake_get_pipeline)

        service = _make_service(allow_fallback=True)
        ctx = CorrectionContext(
            task_id=100,
            image_paths=[Path("test.jpg")],
            config=CorrectionConfig(pipeline_choice=PipelineChoice.PIPELINE_B_CLOUD),
        )
        with pytest.raises(PipelineNetworkError):
            await service._execute_with_fallback(ctx)
