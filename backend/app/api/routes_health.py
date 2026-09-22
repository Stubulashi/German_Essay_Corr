"""健康检查与配置状态路由

前端"设置抽屉"通过本接口展示当前运行配置(mock 模式、管线可用性、模型名等)。
"""

from fastapi import APIRouter

from app.api.deps import get_queue_service
from app.config import settings
from app.models.schemas import HealthResponse, PipelineChoice

router = APIRouter(tags=["系统"])


@router.get("/health", response_model=HealthResponse, summary="健康检查与配置状态")
async def health() -> HealthResponse:
    """返回服务状态与当前配置摘要(不包含任何密钥内容)"""
    return HealthResponse(
        status="ok",
        mock_mode=settings.mock_mode,
        allow_auto_fallback=settings.allow_auto_fallback,
        default_pipeline=PipelineChoice(settings.default_pipeline),
        default_grading_standard=settings.default_grading_standard,
        default_detail_level=settings.default_detail_level,
        startup_data_picker=settings.startup_data_picker,
        pipeline_a={
            "name": "管线 A:本地统一 VLM",
            "ready": settings.pipeline_a_ready() or settings.mock_mode,
            "base_url": settings.local_vlm_base_url,
            "model": settings.local_vlm_model,
            "timeout": settings.local_vlm_timeout,
        },
        pipeline_b={
            "name": "管线 B:云端解耦",
            "ready": settings.pipeline_b_ready() or settings.mock_mode,
            "ocr_provider": settings.ocr_provider,
            "ocr_model": settings.ocr_model
            if settings.ocr_provider == "vlm_openai"
            else "Azure Computer Vision",
            "grading_model": settings.deepseek_reasoning_model
            if settings.deepseek_use_reasoning
            else settings.deepseek_model,
            "ocr_review_supported": True,
        },
    )
