"""练习卷路由(依据历史作业错因生成练习卷 + 标准答案)

- GET    /api/practice/options           题型与数量选项(唯一来源)
- GET    /api/practice/sources           可选作业来源(任务数/日期区间/高频错因 Top3)
- GET    /api/practice/sheets            练习卷列表(可按班级筛选)
- POST   /api/practice/sheets            生成练习卷(同步;失败不落库,返回可读错误)
- GET    /api/practice/sheets/{id}       练习卷详情(试卷本体 + 标准答案 + 结构化题目)
- DELETE /api/practice/sheets/{id}       删除(需 confirm=true)

说明:数据来源为已批改任务的错因数据(与错题本/班级分析同源,只读溯源);
所有接口挂数据解锁守卫(main.py)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.db_models import PracticeSheet, SchoolClass
from app.models.schemas import (
    PracticeGenerateRequest,
    PracticeOptionsOut,
    PracticeQuestionTypeOut,
    PracticeSheetBrief,
    PracticeSheetOut,
    PracticeSourceItem,
    PracticeSourcesOut,
)
from app.services import practice_service
from app.services.practice_service import (
    DEFAULT_COUNT,
    MAX_COUNT,
    MIN_COUNT,
    QUESTION_TYPES,
    PracticeError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["练习卷"])


def _to_brief(sheet: PracticeSheet, class_name: str | None) -> PracticeSheetBrief:
    return PracticeSheetBrief(
        id=sheet.id,
        class_id=sheet.class_id,
        class_name=class_name,
        title=sheet.title,
        params=sheet.params or {},
        question_count=len((sheet.content or {}).get("questions") or []),
        model=sheet.model or "",
        created_at=sheet.created_at,
    )


def _to_out(sheet: PracticeSheet, class_name: str | None) -> PracticeSheetOut:
    brief = _to_brief(sheet, class_name)
    return PracticeSheetOut(
        **brief.model_dump(),
        source=sheet.source or {},
        questions=(sheet.content or {}).get("questions") or [],
        worksheet_markdown=sheet.worksheet_markdown or "",
        answer_markdown=sheet.answer_markdown or "",
    )


async def _class_name_map(db: AsyncSession, class_ids: set[int]) -> dict[int, str]:
    if not class_ids:
        return {}
    rows = (await db.execute(select(SchoolClass).where(SchoolClass.id.in_(class_ids)))).scalars().all()
    return {cls.id: cls.name for cls in rows}


@router.get("/practice/options", response_model=PracticeOptionsOut, summary="题型与数量选项")
async def get_practice_options() -> PracticeOptionsOut:
    """生成参数选项(题型集合为后端唯一来源,前端动态拉取)"""
    return PracticeOptionsOut(
        question_types=[
            PracticeQuestionTypeOut(key=key, label=label) for key, label in QUESTION_TYPES
        ],
        min_count=MIN_COUNT,
        max_count=MAX_COUNT,
        default_count=DEFAULT_COUNT,
    )


@router.get("/practice/sources", response_model=PracticeSourcesOut, summary="可选作业来源")
async def get_practice_sources(
    class_id: int | None = Query(default=None, description="按班级筛选来源"),
    db: AsyncSession = Depends(get_db),
) -> PracticeSourcesOut:
    """按 (班级, 作业名) 聚合的可选来源;未命名作业归「(未命名作业)」独立分组"""
    rows = await practice_service.build_sources(db, class_id)
    return PracticeSourcesOut(assignments=[PracticeSourceItem(**row) for row in rows])


@router.get("/practice/sheets", response_model=list[PracticeSheetBrief], summary="练习卷列表")
async def list_practice_sheets(
    class_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[PracticeSheetBrief]:
    """练习卷列表(按创建时间倒序;可按归档班级筛选)"""
    sheets = await practice_service.list_sheets(db, class_id)
    names = await _class_name_map(db, {s.class_id for s in sheets if s.class_id})
    return [_to_brief(sheet, names.get(sheet.class_id) if sheet.class_id else None) for sheet in sheets]


@router.post("/practice/sheets", response_model=PracticeSheetOut, summary="生成练习卷")
async def generate_practice_sheet(
    payload: PracticeGenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> PracticeSheetOut:
    """依据所选历史作业的错因证据生成练习卷与标准答案(同步;失败不落库)"""
    try:
        sheet = await practice_service.generate_sheet(db, payload)
    except PracticeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 —— 生成失败(网络/模型异常)给出可读错误
        logger.exception("练习卷生成失败")
        raise HTTPException(status_code=502, detail=f"练习卷生成失败:{e}") from e
    names = await _class_name_map(db, {sheet.class_id} if sheet.class_id else set())
    return _to_out(sheet, names.get(sheet.class_id) if sheet.class_id else None)


@router.get("/practice/sheets/{sheet_id}", response_model=PracticeSheetOut, summary="练习卷详情")
async def get_practice_sheet(
    sheet_id: int, db: AsyncSession = Depends(get_db)
) -> PracticeSheetOut:
    """练习卷详情(试卷本体 + 标准答案 + 结构化题目 + 来源快照)"""
    try:
        sheet = await practice_service.get_sheet(db, sheet_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    names = await _class_name_map(db, {sheet.class_id} if sheet.class_id else set())
    return _to_out(sheet, names.get(sheet.class_id) if sheet.class_id else None)


@router.delete("/practice/sheets/{sheet_id}", summary="删除练习卷")
async def delete_practice_sheet(
    sheet_id: int,
    confirm: bool = Query(default=False, description="需显式传 true 二次确认"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除练习卷(不可恢复)"""
    if not confirm:
        raise HTTPException(status_code=400, detail="删除练习卷不可恢复,请确认后重试(confirm=true)")
    try:
        await practice_service.delete_sheet(db, sheet_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"deleted": sheet_id}
