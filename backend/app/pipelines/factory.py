"""策略工厂(Strategy Factory)

`get_pipeline(config)` 是双管线热切换的入口:
- 依据 `MOCK_MODE` 返回演示管线(优先级最高);
- 依据 `pipeline_choice` 返回对应真实管线实例;
- 所有调用方(路由/服务)只依赖 `AbstractCorrectionPipeline` 抽象,不感知具体实现。
"""

from __future__ import annotations

import logging

from app.config import Settings, settings as global_settings
from app.models.schemas import CorrectionConfig, PipelineChoice
from app.pipelines.base import AbstractCorrectionPipeline
from app.pipelines.cloud_decoupled import DecoupledCloudPipeline
from app.pipelines.local_vlm import LocalVLMPipeline
from app.pipelines.mock import MockPipeline

logger = logging.getLogger(__name__)

# 管线注册表:PipelineChoice -> 实现类
# 扩展新管线:在 schemas.PipelineChoice 增加枚举值后,在此注册即可
_PIPELINE_REGISTRY: dict[PipelineChoice, type[AbstractCorrectionPipeline]] = {
    PipelineChoice.PIPELINE_A_LOCAL: LocalVLMPipeline,
    PipelineChoice.PIPELINE_B_CLOUD: DecoupledCloudPipeline,
}


def get_pipeline(
    config: CorrectionConfig,
    settings: Settings | None = None,
) -> AbstractCorrectionPipeline:
    """策略工厂:根据配置实例化对应的批改管线

    Args:
        config:   教师配置(含 pipeline_choice)
        settings: 全局配置(默认使用模块级单例,测试时可注入)

    Returns:
        AbstractCorrectionPipeline 具体实例

    Raises:
        ValueError: 枚举值未注册(理论上不会发生,防御性保护)
    """
    s = settings or global_settings

    # 演示模式优先级最高:忽略管线选择,返回 Mock 管线
    if s.mock_mode:
        logger.info("MOCK_MODE=true,使用演示管线(不调用真实模型)")
        return MockPipeline()

    pipeline_cls = _PIPELINE_REGISTRY.get(config.pipeline_choice)
    if pipeline_cls is None:
        raise ValueError(f"未知管线:{config.pipeline_choice}(未在工厂注册)")

    logger.debug("工厂实例化管线:%s", config.pipeline_choice.value)
    return pipeline_cls(s)
