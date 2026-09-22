"""教学分析路由(#2)

- GET /api/analytics/class-diagnosis  班级/批次/时间范围的共性错因诊断(含讲评摘要)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.schemas import ClassDiagnosis
from app.services.analytics_service import build_class_diagnosis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["教学分析"])


def _parse_date_or_400(value: str, field: str) -> datetime:
    """解析 YYYY-MM-DD(非法时返回 400)"""
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"{field} 应为 YYYY-MM-DD 格式:{value}") from e


@router.get(
    "/analytics/class-diagnosis",
    response_model=ClassDiagnosis,
    summary="班级共性错因诊断",
)
async def class_diagnosis(
    class_id: int | None = Query(default=None, description="按班级筛选"),
    batch_id: str | None = Query(default=None, description="按上传批次筛选"),
    date_from: str | None = Query(default=None, description="起始日期(YYYY-MM-DD)"),
    date_to: str | None = Query(default=None, description="截止日期(YYYY-MM-DD,含当日)"),
    with_ledger: bool = Query(default=False, description="附:作业台账概览(跨模块联动)"),
    with_exam: bool = Query(default=False, description="附:考试统计概览(跨模块联动)"),
    db: AsyncSession = Depends(get_db),
) -> ClassDiagnosis:
    """聚合已完成任务的错因数据,生成讲评摘要与分布统计

    筛选条件为"与"关系;全部为空时统计所有已完成任务。
    with_ledger / with_exam 开启时额外附台账与考试概览(缺省不返回,向后兼容)。
    """
    from_dt = _parse_date_or_400(date_from, "date_from") if date_from else None
    to_dt = _parse_date_or_400(date_to, "date_to") + timedelta(days=1) if date_to else None

    return await build_class_diagnosis(
        db,
        class_id=class_id,
        batch_id=batch_id,
        date_from=from_dt,
        date_to=to_dt,
        with_ledger=with_ledger,
        with_exam=with_exam,
    )
