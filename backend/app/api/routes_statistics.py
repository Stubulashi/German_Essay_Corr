"""统一统计层路由(成绩总表 / 分布统计 / 排行与趋势;全部只读)

- GET /api/statistics/gradebook         学生成绩总表(三源聚合)
- GET /api/statistics/gradebook/export  总表导出 CSV(UTF-8-SIG,Excel 友好)
- GET /api/statistics/distribution      单来源分布统计
- GET /api/statistics/rankings          高阶报表:综合/考试/进步/覆盖率排行
- GET /api/statistics/trends            趋势分析(月度平均 + 学生序列)

说明:统计只读、不写业务表;空数据返回空结构。接口挂数据解锁守卫(main.py)。
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services import statistics_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["统计分析"])


def _parse_date(value: str | None, field: str) -> date | None:
    """解析 YYYY-MM-DD(非法时 400)"""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"{field} 应为 YYYY-MM-DD 格式:{value}") from e


def _parse_sources(value: str | None) -> set[str] | None:
    """解析来源列表(correction,exam,ledger;缺省全部)"""
    if not value:
        return None
    items = {part.strip() for part in value.split(",") if part.strip()}
    unknown = items - set(statistics_service.SOURCES)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"未知统计来源:{', '.join(sorted(unknown))}(可选 correction/exam/ledger)",
        )
    return items or None


@router.get("/statistics/gradebook", summary="学生成绩总表")
async def get_gradebook(
    class_id: int | None = Query(default=None),
    date_from: str | None = Query(default=None, description="起始日期(YYYY-MM-DD)"),
    date_to: str | None = Query(default=None, description="截止日期(YYYY-MM-DD,含当日)"),
    sources: str | None = Query(default=None, description="来源过滤:correction,exam,ledger"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按学生聚合三类来源(批改 / 考试 / 台账)的评估序列与平均分"""
    from_d = _parse_date(date_from, "date_from")
    to_d = _parse_date(date_to, "date_to")
    if to_d is not None:
        to_d = date.fromordinal(to_d.toordinal() + 1)  # 截止日含当日(半开区间)
    return await statistics_service.build_gradebook(
        db, class_id=class_id, date_from=from_d, date_to=to_d, sources=_parse_sources(sources)
    )


@router.get("/statistics/gradebook/export", summary="总表导出 CSV")
async def export_gradebook(
    class_id: int | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    sources: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """导出成绩总表 CSV(UTF-8-SIG,可直接用 Excel 打开)"""
    from_d = _parse_date(date_from, "date_from")
    to_d = _parse_date(date_to, "date_to")
    if to_d is not None:
        to_d = date.fromordinal(to_d.toordinal() + 1)
    gradebook = await statistics_service.build_gradebook(
        db, class_id=class_id, date_from=from_d, date_to=to_d, sources=_parse_sources(sources)
    )
    csv_text = statistics_service.build_gradebook_csv(gradebook)
    filename = f"gradebook_{date.today().isoformat()}.csv"
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/statistics/distribution", summary="成绩分布统计")
async def get_distribution(
    source: str = Query(description="统计来源:correction | exam | ledger"),
    class_id: int | None = Query(default=None),
    target_id: int | None = Query(default=None, description="exam 来源时限定某场考试"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """单来源分布统计:分数段 / 平均 / 最高 / 最低 / 及格率"""
    try:
        return await statistics_service.build_distribution(
            db, source=source, class_id=class_id, target_id=target_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/statistics/rankings", summary="排行榜(综合/考试/进步/覆盖率)")
async def get_rankings(
    class_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """高阶报表:综合平均榜、最近一场考试榜、进步/退步榜、台账覆盖率榜"""
    return await statistics_service.build_rankings(db, class_id=class_id)


@router.get("/statistics/trends", summary="趋势分析")
async def get_trends(
    class_id: int | None = Query(default=None),
    student: str | None = Query(default=None, description="可选:学生姓名或学号(对比序列)"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """趋势分析:班级月度平均 + 选定学生的评估序列"""
    return await statistics_service.build_trends(db, class_id=class_id, student=student)
