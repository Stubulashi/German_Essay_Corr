"""重新批改服务(已完成任务原地重跑的准备逻辑)

语义(与需求逐条对应):
- 复用任务已保存的原图,不新建任务、不重复落盘;
- 仅应用教师显式覆盖的参数(白名单),未提供项一律沿用任务原配置;
- 快照上一次结果摘要(总分/错因数)供完成后的差异提示;
- 先清理该任务旧错题记录(重建语义,防止下游统计重复计数);
- 状态机回退 PENDING/UPLOADED 并清 OCR/故障转移痕迹,重走 OCR → GRADING → RENDERING → DONE;
- 教师编辑版报告(edited_report)与教师寄语(teacher_message)保留、不被覆盖;
- 递增 recorrect_count 并记录 last_recorrect_at(追溯与新旧对比)。

入队由路由层完成(本服务保持无队列依赖,便于独立测试)。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db_models import CorrectionTask, ErrorRecord, SchoolClass, utc_now

logger = logging.getLogger(__name__)


class RecorrectError(ValueError):
    """重新批改前置校验失败(路由层转 400)"""


#: 允许覆盖的字段白名单(与 TaskRecorrectRequest 对齐)
_ALLOWED_FIELDS = {
    "pipeline_choice",
    "grading_standard",
    "detail_level",
    "require_ocr_review",
    "student_name",
    "student_id",
    "class_id",
    "assignment_name",
    "topic",
}


#: 每任务互斥锁(单进程部署;防止并发双发导致的双重入队与追溯计数错乱)
_task_locks: dict[int, asyncio.Lock] = {}


def _task_lock(task_id: int) -> asyncio.Lock:
    """获取(或创建)某任务的互斥锁"""
    lock = _task_locks.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _task_locks[task_id] = lock
    return lock


async def apply_recorrect(db: AsyncSession, task: CorrectionTask, updates: dict) -> CorrectionTask:
    """校验并准备重新批改(原地修改任务并提交;入队由调用方完成)

    并发双发保护:按任务加互斥锁,并在锁内重新同步任务状态——
    后到的请求将看到 PENDING 而被拒(400),避免双重入队与计数互覆。
    """
    async with _task_lock(task.id):
        await db.refresh(task)  # 锁外可能读到过期状态,锁内重新同步
        return await _apply_recorrect_locked(db, task, updates)


async def _apply_recorrect_locked(
    db: AsyncSession, task: CorrectionTask, updates: dict
) -> CorrectionTask:
    """重新批改主体逻辑(调用方已持有该任务的互斥锁)"""
    if task.status != "COMPLETED":
        raise RecorrectError(
            f"仅已完成(COMPLETED)的任务支持重新批改;当前状态:{task.status}"
        )

    # 1. 原图校验:复用既有图片(至少存在一张,否则无法重跑)
    existing = [
        rel for rel in (task.image_paths or []) if (settings.upload_path / rel).exists()
    ]
    if not existing:
        raise RecorrectError("原始图片文件缺失,无法重新批改(请删除任务后重新上传)")

    # 2. 快照上一次结果摘要(完成后差异提示:总分 / 错因数)
    result = task.result or {}
    task.prev_overall_score = str(result.get("overall_score") or "").strip() or None
    task.prev_error_count = len(result.get("errors") or [])

    # 3. 应用显式覆盖项(白名单;其余字段保持原值)
    for field, value in updates.items():
        if field not in _ALLOWED_FIELDS:
            continue
        if field == "class_id":
            if value is not None:
                cls = await db.get(SchoolClass, value)
                if cls is None:
                    raise RecorrectError(f"班级 {value} 不存在")
            task.class_id = value
        elif field == "student_name":
            name = (value or "").strip()
            if name:  # 姓名留空视为不修改(避免误置"未知"),如需清空请用"学生信息纠错"
                task.student_name = name
        elif field in ("student_id", "assignment_name", "topic"):
            text = value.strip() if isinstance(value, str) else value
            setattr(task, field, text or None)
        elif field == "require_ocr_review":
            task.require_ocr_review = 1 if value else 0
        elif value is not None:  # pipeline_choice / grading_standard / detail_level
            setattr(task, field, value)

    # 4. 清理旧错题记录(重建语义;批改完成时会按新结果重新写入)
    await db.execute(delete(ErrorRecord).where(ErrorRecord.task_id == task.id))

    # 5. 状态机回退(清 OCR 与故障转移痕迹;保留 result 供重跑期间继续查看旧报告)
    task.ocr_result = None
    task.pipeline_used = None
    task.fallback_triggered = 0
    task.status = "PENDING"
    task.stage = "UPLOADED"
    task.progress = 0.0
    task.error_message = None

    # 6. 追溯计数与时间
    task.recorrect_count = (task.recorrect_count or 0) + 1
    task.last_recorrect_at = utc_now()

    await db.commit()
    await db.refresh(task)
    logger.info(
        "任务 %s 已准备重新批改(第 %s 次;上次:%s / %s 处错因)",
        task.id,
        task.recorrect_count,
        task.prev_overall_score or "—",
        task.prev_error_count,
    )
    return task
