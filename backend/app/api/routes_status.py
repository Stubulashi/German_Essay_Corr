"""全局状态路由(真实进度 + 真实自检;不含任何占位式假提示)

- GET /api/status/overview  活跃任务真实进度(percent/ETA)与真实自检摘要。

注意:本路由不挂数据守卫——加密锁定时全局状态条仍需可用
(checkup 仅做系统级检查,不读学生数据)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue_service
from app.db.database import get_db
from app.models.schemas import StatusOverviewOut
from app.services import status_service

router = APIRouter(tags=["status"])


@router.get("/status/overview", response_model=StatusOverviewOut, summary="全局状态总览")
async def status_overview(
    db: AsyncSession = Depends(get_db),
    queue=Depends(get_queue_service),
) -> StatusOverviewOut:
    """聚合批改/预识别/压缩/训练的真实进度与真实自检结果(自检 10 秒缓存)"""
    data = await status_service.build_overview(db, queue)
    return StatusOverviewOut(**data)
