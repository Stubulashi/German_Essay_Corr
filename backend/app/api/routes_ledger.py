"""作业台账路由(日常作业登记与汇总)

- 登记项 CRUD(全局模板 + 班级私有项)与预设模板一键创建;
- 批量登记 upsert、明细分页查询、单条纠错;
- 班级汇总(学生×登记项矩阵)与登记用学生列表(花名册优先)。

错误映射约定:ValueError -> 400(参数/值不合法),LookupError -> 404(目标不存在)。
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.schemas import (
    LedgerBatchRequest,
    LedgerBatchResult,
    LedgerClassSummary,
    LedgerItemCreate,
    LedgerItemOut,
    LedgerItemUpdate,
    LedgerPresetRequest,
    LedgerRecordListResponse,
    LedgerRecordOut,
    LedgerRecordUpdate,
    LedgerStudentOption,
)
from app.services import ledger_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["作业台账"])


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _not_found(e: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------
# 登记项
# ---------------------------------------------------------
@router.get("/ledger/items", response_model=list[LedgerItemOut], summary="登记项列表")
async def list_items(
    class_id: int | None = Query(default=None, description="班级 ID(含全局模板项)"),
    include_archived: bool = Query(default=False, description="是否包含已归档项"),
    db: AsyncSession = Depends(get_db),
) -> list[LedgerItemOut]:
    """列出登记项:全局模板 + 指定班级项(按 sort_order 升序)"""
    items = await ledger_service.list_items(
        db, class_id=class_id, include_global=True, include_archived=include_archived
    )
    return [LedgerItemOut.model_validate(item) for item in items]


@router.post("/ledger/items", response_model=LedgerItemOut, summary="新建登记项")
async def create_item(payload: LedgerItemCreate, db: AsyncSession = Depends(get_db)) -> LedgerItemOut:
    """新建登记项(计分模式:LEVEL 等级 / SCORE 分值 / FLAG 完成度 / STARS 星级)"""
    try:
        item = await ledger_service.create_item(
            db,
            class_id=payload.class_id,
            name=payload.name,
            category=payload.category,
            scoring_mode=payload.scoring_mode,
            config=payload.config,
            sort_order=payload.sort_order,
        )
    except ValueError as e:
        raise _bad_request(e) from e
    return LedgerItemOut.model_validate(item)


@router.post("/ledger/items/presets", response_model=list[LedgerItemOut], summary="一键创建预设登记项")
async def create_presets(
    payload: LedgerPresetRequest, db: AsyncSession = Depends(get_db)
) -> list[LedgerItemOut]:
    """创建教师高频登记维度预设(书面作业/课文背诵/单词听写/课堂表现/作业订正/朗读打卡)"""
    try:
        created = await ledger_service.create_presets(db, payload.class_id)
    except ValueError as e:
        raise _bad_request(e) from e
    return [LedgerItemOut.model_validate(item) for item in created]


@router.put("/ledger/items/{item_id}", response_model=LedgerItemOut, summary="更新登记项")
async def update_item(
    item_id: int, payload: LedgerItemUpdate, db: AsyncSession = Depends(get_db)
) -> LedgerItemOut:
    """更新登记项(重命名/调整计分模式与配置/排序)"""
    try:
        item = await ledger_service.update_item(db, item_id, payload.model_dump(exclude_unset=True))
    except LookupError as e:
        raise _not_found(e) from e
    except ValueError as e:
        raise _bad_request(e) from e
    return LedgerItemOut.model_validate(item)


@router.delete("/ledger/items/{item_id}", summary="删除/归档登记项")
async def delete_item(item_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """删除登记项:无记录时物理删除;有历史记录时仅归档(保留统计口径)"""
    try:
        action = await ledger_service.delete_or_archive_item(db, item_id)
    except LookupError as e:
        raise _not_found(e) from e
    return {"action": action, "item_id": item_id}


# ---------------------------------------------------------
# 登记记录
# ---------------------------------------------------------
@router.post("/ledger/records/batch", response_model=LedgerBatchResult, summary="批量登记")
async def batch_upsert(payload: LedgerBatchRequest, db: AsyncSession = Depends(get_db)) -> LedgerBatchResult:
    """批量登记(upsert):同一登记项 + 同一日期 + 多名学生;value 为空 = 撤销该条"""
    try:
        item = await ledger_service.get_item(db, payload.item_id)
        stats = await ledger_service.batch_upsert(
            db,
            item=item,
            record_date=payload.record_date,
            entries=[entry.model_dump() for entry in payload.entries],
            class_id=payload.class_id,
        )
    except LookupError as e:
        raise _not_found(e) from e
    except ValueError as e:
        raise _bad_request(e) from e
    return LedgerBatchResult(**stats)


@router.get("/ledger/records", response_model=LedgerRecordListResponse, summary="登记明细查询")
async def list_records(
    class_id: int | None = Query(default=None),
    item_id: int | None = Query(default=None),
    student_name: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> LedgerRecordListResponse:
    """按班级/登记项/学生/日期范围筛选登记明细(分页,倒序)"""
    total, items = await ledger_service.list_records(
        db,
        class_id=class_id,
        item_id=item_id,
        student_name=student_name,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return LedgerRecordListResponse(total=total, items=items)


@router.put("/ledger/records/{record_id}", response_model=LedgerRecordOut, summary="单条纠错")
async def update_record(
    record_id: int, payload: LedgerRecordUpdate, db: AsyncSession = Depends(get_db)
) -> LedgerRecordOut:
    """修改单条登记记录(重新归一化 score_value)"""
    try:
        record = await ledger_service.update_record(
            db, record_id, value=payload.value, note=payload.note
        )
        item = await ledger_service.get_item(db, record.item_id)
    except LookupError as e:
        raise _not_found(e) from e
    except ValueError as e:
        raise _bad_request(e) from e
    return LedgerRecordOut(
        id=record.id,
        item_id=record.item_id,
        item_name=item.name,
        class_id=record.class_id,
        student_name=record.student_name,
        student_id=record.student_id,
        value=record.value,
        score_value=record.score_value,
        record_date=record.record_date,
        note=record.note,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.delete("/ledger/records/{record_id}", summary="删除登记记录")
async def delete_record(record_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """删除单条登记记录"""
    try:
        await ledger_service.delete_record(db, record_id)
    except LookupError as e:
        raise _not_found(e) from e
    return {"deleted": record_id}


# ---------------------------------------------------------
# 汇总
# ---------------------------------------------------------
@router.get("/ledger/summary", response_model=LedgerClassSummary, summary="班级台账汇总")
async def class_summary(
    class_id: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> LedgerClassSummary:
    """班级台账汇总:学生×登记项矩阵 + 按项统计(均分/等级分布)"""
    return await ledger_service.build_class_summary(
        db, class_id=class_id, date_from=date_from, date_to=date_to
    )


@router.get("/ledger/students", response_model=list[LedgerStudentOption], summary="登记用学生列表")
async def students_for_class(
    class_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[LedgerStudentOption]:
    """登记用学生列表:班级花名册优先;无花名册时回退学生档案"""
    return await ledger_service.students_for_class(db, class_id)
