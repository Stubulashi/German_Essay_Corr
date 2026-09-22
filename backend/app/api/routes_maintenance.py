"""维护路由(存量图片压缩 / 数据维护;设置管理员 + 数据解锁双重校验)

- POST /api/maintenance/compress-images         启动存量图片批量压缩(后台执行)
- GET  /api/maintenance/compress-images/status  压缩进度(供设置中心轮询)
- POST /api/maintenance/clear-demo-data        清除演示模式(Mock)产生的数据(真实数据不受影响)
- POST /api/maintenance/clear-all-data         一键清除所有数据(全部业务数据 + 上传文件)
- POST /api/maintenance/seed-demo-data         一键恢复所有示例数据(先清空再写入)

说明:图片压缩路径与文件名不变、原子替换、幂等可重跑;数据维护类动作均不可恢复,
需 confirm=true(前端强制二次确认),执行结果与审计记录一一对应。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_settings_admin, require_unlocked
from app.config import settings
from app.db.database import get_db
from app.models.db_models import (
    CorrectionTask,
    ErrorRecord,
    ExamPaper,
    HomeworkRecord,
    Student,
)
from app.pipelines.mock import MOCK_STUDENT_ID, MOCK_STUDENT_NAME
from app.services import demo_data_service, image_compress_service
from app.services.audit_service import audit

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["维护"],
    dependencies=[Depends(require_settings_admin), Depends(require_unlocked)],
)


@router.post("/maintenance/compress-images", summary="压缩存量图片(后台执行)")
async def compress_stored_images() -> dict:
    """对 upload 目录下全部存档图片执行就地压缩(幂等;可随时重跑)"""
    try:
        await image_compress_service.start_compress(
            settings.upload_path,
            max_side=settings.image_compress_max_side,
            quality=settings.image_compress_quality,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"started": True, "progress": image_compress_service.progress()}


@router.get("/maintenance/compress-images/status", summary="存量压缩进度")
async def compress_status() -> dict:
    """当前/最近一次批量压缩的进度快照(running/done/total/changed/字节量)"""
    return image_compress_service.progress()


@router.post("/maintenance/clear-demo-data", summary="清除演示模式产生的数据")
async def clear_demo_data(
    confirm: bool = Query(default=False, description="需显式传 true 二次确认"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """仅删除「演示模式(Mock)」产生的任务/错因/图片与孤儿学生(按演示样例固定身份识别);真实数据绝不受影响"""
    if not confirm:
        raise HTTPException(status_code=400, detail="清除演示数据不可恢复,请确认后重试(confirm=true)")
    tasks = (
        await db.execute(
            select(CorrectionTask).where(
                and_(
                    CorrectionTask.student_name == MOCK_STUDENT_NAME,
                    CorrectionTask.student_id == MOCK_STUDENT_ID,
                )
            )
        )
    ).scalars().all()
    task_ids = [task.id for task in tasks]
    name_pairs = {(task.student_name, task.student_id) for task in tasks if task.student_name}
    removed_files = 0
    for task in tasks:
        for rel in task.image_paths or []:
            path = settings.upload_path / rel
            if path.exists():
                try:
                    path.unlink()
                    removed_files += 1
                except OSError:
                    pass
    if task_ids:
        await db.execute(delete(ErrorRecord).where(ErrorRecord.task_id.in_(task_ids)))
        await db.execute(delete(CorrectionTask).where(CorrectionTask.id.in_(task_ids)))
    students_removed = 0
    for name, student_id in name_pairs:
        remaining = (
            await db.execute(
                select(func.count()).select_from(CorrectionTask).where(CorrectionTask.student_name == name)
            )
        ).scalar() or 0
        if remaining:
            continue
        exams = (
            await db.execute(
                select(func.count()).select_from(ExamPaper).where(ExamPaper.student_name == name)
            )
        ).scalar() or 0
        ledgers = (
            await db.execute(
                select(func.count()).select_from(HomeworkRecord).where(HomeworkRecord.student_name == name)
            )
        ).scalar() or 0
        if exams or ledgers:
            continue
        statement = delete(Student).where(Student.name == name)
        if student_id:
            statement = statement.where(Student.student_id == student_id)
        result = await db.execute(statement)
        students_removed += result.rowcount or 0
    await db.commit()
    await audit(
        "maintenance.clear_demo_data",
        detail=f"删除演示任务 {len(task_ids)} 个、学生 {students_removed} 条、图片 {removed_files} 张",
    )
    logger.info("清除演示数据:任务 %s、学生 %s、图片 %s", len(task_ids), students_removed, removed_files)
    return {"tasks": len(task_ids), "students": students_removed, "files": removed_files}


#: 统计计数 -> 审计/日志用中文标签
_SUMMARY_LABELS: tuple[tuple[str, str], ...] = (
    ("classes", "班级"),
    ("roster", "花名册"),
    ("students", "学生"),
    ("tasks", "任务"),
    ("errors", "错因"),
    ("exams", "考试"),
    ("exam_papers", "考卷"),
    ("exam_reports", "考试报告"),
    ("ledger_items", "台账登记项"),
    ("ledger_records", "台账记录"),
    ("practice_sheets", "练习卷"),
    ("style_profiles", "风格画像"),
    ("handwriting_samples", "手写样本"),
    ("handwriting_models", "手写模型"),
    ("merge_logs", "合并日志"),
    ("files", "图片文件"),
    ("bytes", "字节"),
)


def _summary(counts: dict) -> str:
    """把计数结果压成一行中文摘要(审计/日志共用)"""
    return "、".join(
        f"{label} {counts[key]}" for key, label in _SUMMARY_LABELS if counts.get(key)
    ) or "无数据变化"


@router.post("/maintenance/clear-all-data", summary="一键清除所有数据")
async def clear_all_data(
    confirm: bool = Query(default=False, description="需显式传 true 二次确认"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """清空全部业务数据(任务/错因/学生/班级/考试/台账/练习卷/示范学习等)与上传文件

    - 不可恢复:需显式 confirm=true(前端强制二次确认);
    - 保留系统配置与密钥(app_settings:设置中心配置、提示词附录、加密密钥包裹、自动适配记录);
    - 与「清除演示数据」区分:后者仅删演示模式(Mock)产生的数据。
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="清除所有数据不可恢复,请确认后重试(confirm=true)")
    counts = await demo_data_service.clear_all_business_data(db, settings.upload_path)
    await audit("maintenance.clear_all_data", detail=_summary(counts))
    logger.info("一键清除所有数据完成:%s", _summary(counts))
    return counts


@router.post("/maintenance/seed-demo-data", summary="一键恢复所有示例数据")
async def seed_demo_data(
    confirm: bool = Query(default=False, description="需显式传 true 二次确认"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """先清空全部业务数据,再写入一整套可直接演示的示例数据(覆盖现有数据,不可恢复)

    - 覆盖范围:示例班级与花名册、学生档案、批改任务与报告/错因(含 1 个待复核任务)、
      考试与报告、台账登记项与记录、练习卷、示范学习画像与示例答卷图片;
    - 与「清除演示数据」区分:后者仅删演示模式(Mock)产生的数据,不写入任何内容。
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="恢复示例数据会先清空现有全部数据,请确认后重试(confirm=true)",
        )
    counts = await demo_data_service.seed_demo_data(db, settings.upload_path)
    await audit("maintenance.seed_demo_data", detail=_summary(counts))
    logger.info("一键恢复示例数据完成:%s", _summary(counts))
    return counts
