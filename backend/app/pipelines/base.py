"""双管线策略模式 - 抽象基类与异常体系

本模块是"双管线热切换架构"的核心:
- `AbstractCorrectionPipeline`: 所有批改管线的抽象基类(Strategy Pattern)
- `CorrectionContext`:          管线执行上下文(图片、配置、进度回调等)
- `CorrectionOutcome`:          管线执行结果(完成 / 等待人工复核)
- 异常体系:                      PipelineError 及其子类,用于故障转移判定
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.models.schemas import (
    CorrectionConfig,
    EssayCorrectionResult,
    OcrExtractionResult,
    PipelineChoice,
)

# =============================================================
# 异常体系
# =============================================================


class PipelineError(Exception):
    """管线异常基类

    Attributes:
        pipeline: 抛出异常的管线标识,便于日志定位
    """

    def __init__(self, message: str, pipeline: str = ""):
        self.pipeline = pipeline
        super().__init__(message)


class PipelineNetworkError(PipelineError):
    """网络错误 / 超时(Pipeline A 抛出此类异常时允许自动故障转移至 B)"""


class PipelineParseError(PipelineError):
    """LLM 输出解析失败(经内部修复重试后仍无法反序列化为标准 Schema)"""

    def __init__(self, message: str, raw_output: str = "", pipeline: str = ""):
        self.raw_output = raw_output  # 保留原始输出,便于排查
        super().__init__(message, pipeline=pipeline)


class PipelineConfigError(PipelineError):
    """配置缺失(如 API Key 未填写),此错误不触发故障转移,直接失败"""


# =============================================================
# 执行上下文与结果
# =============================================================

# 进度回调类型: (stage: str, progress: float) -> Awaitable[None]
ProgressCallback = Callable[[str, float], Awaitable[None]]


@dataclass
class CorrectionContext:
    """管线执行上下文

    Attributes:
        task_id:         任务 ID(用于日志)
        image_paths:     作文图片的绝对路径列表(按页序)
        config:          本次批改的教师配置(管线/标准/细致度/复核开关)
        ocr_result:      人工复核后的转录数据(两条管线通用;有值时跳过 OCR 直接进入评分)
        on_progress:     进度回调(更新数据库 status/stage/progress)
    """

    task_id: int
    image_paths: list[Path]
    config: CorrectionConfig
    ocr_result: OcrExtractionResult | None = None
    on_progress: ProgressCallback | None = None

    async def report_progress(self, stage: str, progress: float) -> None:
        """向外部报告进度(若回调存在)"""
        if self.on_progress is not None:
            await self.on_progress(stage, progress)


@dataclass
class CorrectionOutcome:
    """管线执行结果

    设计说明:
    - 开启人工复核时,第一阶段结束后返回 waiting_review=True,
      由上层服务把任务置为 WAITING_REVIEW 并等待教师确认;
    - 教师确认后再次调用 correct(),此时上下文携带 ocr_result,直接执行评分阶段;
    - 关闭人工复核时,管线 A 单次执行到底,返回 completed_result。
    """

    waiting_review: bool = False
    completed_result: EssayCorrectionResult | None = None
    ocr_result: OcrExtractionResult | None = None
    # 实际执行管线的展示名(如 "本地 Qwen3.8-27B" / "云端 DeepSeek"),用于报告头
    pipeline_display_name: str = ""
    extra: dict = field(default_factory=dict)


# =============================================================
# 抽象管线基类
# =============================================================


class AbstractCorrectionPipeline(ABC):
    """批改管线抽象基类(Strategy Pattern)

    所有管线必须实现 `correct()` 方法,且最终输出统一的
    `EssayCorrectionResult`,保证前端渲染与数据库 Schema 完全一致。
    """

    #: 管线标识(子类覆盖)
    choice: PipelineChoice

    @property
    @abstractmethod
    def display_name(self) -> str:
        """管线展示名(用于 Markdown 报告头,如 '本地 Qwen3.8-27B')"""

    @abstractmethod
    async def correct(self, ctx: CorrectionContext) -> CorrectionOutcome:
        """执行批改(核心策略方法)

        Args:
            ctx: 执行上下文

        Returns:
            CorrectionOutcome: 完成结果或"等待人工复核"状态

        Raises:
            PipelineNetworkError: 网络错误/超时(可能触发故障转移)
            PipelineParseError:   LLM 输出无法解析
            PipelineConfigError:  配置缺失
        """
