"""班级管理路由(#4 / 方向三)

- GET    /api/classes              班级列表(含任务数统计)
- POST   /api/classes              创建班级(名称唯一)
- DELETE /api/classes/{id}         删除班级(仅当无关联任务时允许)
- POST   /api/classes/{id}/export  导出班级数据包(ZIP,含任务/结果/错因/学生/图片)
- POST   /api/classes/import       导入班级数据包(换电脑/换人迁移)
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.db_models import ClassRoster, CorrectionTask, SchoolClass
from app.models.schemas import (
    ClassCreate,
    ClassImportResult,
    ClassMergeRequest,
    ClassOut,
    RosterImportResult,
    RosterMemberOut,
)
from app.services import class_merge_service
from app.services.class_package_service import (
    ClassPackageError,
    export_class_package,
    import_class_package,
)
from app.services.student_assignment import load_roster, parse_roster_text
from app.services.student_service import upsert_student

logger = logging.getLogger(__name__)

router = APIRouter(tags=["班级"])


@router.get("/classes", response_model=list[ClassOut], summary="班级列表")
async def list_classes(db: AsyncSession = Depends(get_db)) -> list[ClassOut]:
    """查询全部班级,附带各自的任务数(用于筛选器展示)"""
    # 一次查询完成:左连接任务表并按班级分组统计
    stmt = (
        select(SchoolClass, func.count(CorrectionTask.id))
        .outerjoin(CorrectionTask, CorrectionTask.class_id == SchoolClass.id)
        .group_by(SchoolClass.id)
        .order_by(SchoolClass.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        ClassOut(
            id=cls.id,
            name=cls.name,
            note=cls.note,
            task_count=count,
            merged_into_id=cls.merged_into_id,
            merged_at=cls.merged_at,
            created_at=cls.created_at,
        )
        for cls, count in rows
    ]


@router.post("/classes", response_model=ClassOut, summary="创建班级")
async def create_class(payload: ClassCreate, db: AsyncSession = Depends(get_db)) -> ClassOut:
    """创建班级(名称唯一,重复时报 409)"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="班级名称不能为空")

    exists = await db.execute(select(SchoolClass).where(SchoolClass.name == name))
    if exists.scalars().first() is not None:
        raise HTTPException(status_code=409, detail=f"班级已存在:{name}")

    cls = SchoolClass(name=name, note=(payload.note or "").strip() or None)
    db.add(cls)
    await db.commit()
    await db.refresh(cls)
    logger.info("创建班级:%s (id=%s)", cls.name, cls.id)
    return ClassOut(id=cls.id, name=cls.name, note=cls.note, task_count=0, created_at=cls.created_at)


@router.delete("/classes/{class_id}", summary="删除班级")
async def delete_class(class_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """删除班级(存在关联任务时拒绝,保证历史数据完整)"""
    cls = await db.get(SchoolClass, class_id)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"班级 {class_id} 不存在")

    count = (
        await db.execute(
            select(func.count(CorrectionTask.id)).where(CorrectionTask.class_id == class_id)
        )
    ).scalar_one()
    if count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"班级下还有 {count} 个批改任务,无法删除(可先移除任务的班级归属)",
        )

    await db.delete(cls)
    await db.commit()
    logger.info("删除班级:%s (id=%s)", cls.name, class_id)
    return {"deleted": class_id}


# ---------------------------------------------------------
# 班级数据包(方向三:班级作为综合数据库单元)
# ---------------------------------------------------------
@router.post("/classes/{class_id}/export", summary="导出班级数据包(ZIP)")
async def export_class(class_id: int, db: AsyncSession = Depends(get_db)):
    """把班级全部数据(任务/结果/错因/学生/原图)打包为可移植 ZIP"""
    try:
        buffer = await export_class_package(db, class_id)
    except ClassPackageError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="class_package_{stamp}.zip"'},
    )


@router.post("/classes/import", response_model=ClassImportResult, summary="导入班级数据包")
async def import_class(
    file: UploadFile = File(description="class_package_*.zip 数据包"),
    new_name: str | None = Form(default=None, description="可选:为重名班级指定新名称"),
    db: AsyncSession = Depends(get_db),
) -> ClassImportResult:
    """导入班级数据包,重建班级与全部数据(重名班级自动加后缀)"""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        stats = await import_class_package(db, content, new_name=new_name)
    except ClassPackageError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return ClassImportResult(
        class_id=stats.class_id,
        class_name=stats.class_name,
        task_count=stats.task_count,
        error_count=stats.error_count,
        image_count=stats.image_count,
        renamed=stats.renamed,
    )


# ---------------------------------------------------------
# 班级合并(把来源班级及其学生数据整体并入目标班级)
# ---------------------------------------------------------
@router.get("/classes/merge/preview", summary="班级合并预览")
async def merge_preview(
    source_id: int = Query(description="来源班级 ID"),
    target_id: int = Query(description="目标班级 ID"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """预览合并影响:数据规模 + 花名册匹配方案 + 台账项处理方案(只读,不产生改动)"""
    try:
        return await class_merge_service.build_merge_preview(db, source_id, target_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except class_merge_service.ClassMergeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/classes/merge", summary="执行班级合并")
async def merge_classes(payload: ClassMergeRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """执行合并(需 confirm=true 二次确认)

    - 来源班级的任务/花名册/台账/考试全部改挂目标班级,来源班级标记为"已并入";
    - 花名册同名冲突以目标为准(来源学号保留在合并日志中);
    - 全部改动在单个事务内完成,失败自动完整回滚。
    """
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="请先确认合并影响再执行(confirm=true)")
    try:
        return await class_merge_service.execute_merge(
            db, payload.source_class_id, payload.target_class_id
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except class_merge_service.ClassMergeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/classes/merge/logs", summary="班级合并日志")
async def merge_logs(
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """查看历史合并记录(来源/目标/统计/学号冲突明细,供追溯)"""
    return await class_merge_service.list_merge_logs(db, limit=limit)


# ---------------------------------------------------------
# 班级花名册(方向四:上传指派与识别对齐的基础)
# ---------------------------------------------------------
async def _get_class_or_404(db: AsyncSession, class_id: int) -> SchoolClass:
    """按 ID 获取班级(不存在时 404)"""
    cls = await db.get(SchoolClass, class_id)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"班级 {class_id} 不存在")
    return cls


@router.get(
    "/classes/{class_id}/roster",
    response_model=list[RosterMemberOut],
    summary="查看班级花名册",
)
async def get_class_roster(class_id: int, db: AsyncSession = Depends(get_db)) -> list[RosterMemberOut]:
    """返回班级花名册(按导入顺序,即“按名单顺序指派”所用的顺序)"""
    await _get_class_or_404(db, class_id)
    roster = await load_roster(db, class_id)
    return [RosterMemberOut.model_validate(m) for m in roster]


@router.get(
    "/classes/rosters/{roster_id}/qr.png",
    summary="学生身份二维码 PNG(标准答题卷打印用)",
)
async def get_roster_qr_png(roster_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    """按花名册成员生成身份二维码 PNG(纯本地生成;内容确定性推导,无需入库)

    - 内容格式 V1|<学号>|<姓名>(与识别链 qr_identity 同源);H 级纠错;
    - 组件缺失(未安装 qrcode)→ 503;成员不存在 → 404。
    """
    from app.services.qr_identity import build_qr_payload, render_qr_png

    member = await db.get(ClassRoster, roster_id)
    if member is None:
        raise HTTPException(status_code=404, detail="花名册成员不存在")
    payload = build_qr_payload(member.student_id, member.name)
    image = render_qr_png(payload)
    if image is None:
        raise HTTPException(
            status_code=503,
            detail="二维码生成组件未安装(qrcode);请重跑「首次安装 / 一键自检」",
        )
    return Response(content=image, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.post(
    "/classes/{class_id}/roster/import",
    response_model=RosterImportResult,
    summary="导入班级花名册",
)
async def import_class_roster(
    class_id: int,
    text: str = Form(default="", description="粘贴的名单文本(每行 姓名,学号)"),
    file: UploadFile | None = File(default=None, description="名单文件(CSV/TXT,可选)"),
    db: AsyncSession = Depends(get_db),
) -> RosterImportResult:
    """导入班级花名册(每行 `姓名,学号`;支持粘贴文本或上传文件)

    - 学号可选;重复姓名自动去重(已存在时仅补全学号);
    - 同时 upsert 学生档案,使“学生错题本”页立即出现名单学生。
    """
    cls = await _get_class_or_404(db, class_id)

    # 合并两个来源:表单粘贴 + 上传文件(文件优先按 UTF-8,失败尝试 GBK)
    raw_text = text or ""
    if file is not None:
        content = await file.read()
        if content:
            for encoding in ("utf-8-sig", "gbk"):
                try:
                    raw_text += "\n" + content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

    members = parse_roster_text(raw_text)
    if not members:
        raise HTTPException(
            status_code=400,
            detail="没有解析到有效的名单内容(格式:每行 姓名,学号 或 姓名)",
        )

    # 现有花名册(姓名 -> 成员)
    existing = {m.name: m for m in await load_roster(db, class_id)}
    imported = 0
    updated = 0
    for name, student_id in members:
        member = existing.get(name)
        if member is None:
            session_obj = ClassRoster(class_id=class_id, name=name, student_id=student_id)
            db.add(session_obj)
            existing[name] = session_obj
            imported += 1
        elif student_id and member.student_id != student_id:
            member.student_id = student_id
            updated += 1
        # 同步学生档案(与批改流程共用规则)
        await upsert_student(db, name, student_id)

    await db.commit()
    logger.info(
        "花名册导入:%s(新增 %s,更新 %s,共 %s 人)", cls.name, imported, updated, len(existing)
    )
    return RosterImportResult(imported=imported, updated=updated, total=len(existing))
