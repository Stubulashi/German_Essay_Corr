"""班级合并服务(两个班级及其相互匹配的学生数据合并)

核心约定:
- 合并 = 把"来源班级"的全部业务数据(任务 / 花名册 / 台账 / 考试)改挂到"目标班级",
  不删除任何业务记录;错因记录经由任务关联自动跟随,无需搬运;
- 学生/花名册匹配:先按学号,后按姓名(与全系统 upsert_student 口径一致);
  同名成员保留目标班级记录,来源缺失的学号用于补全,学号冲突以目标为准且明细记入合并日志;
- 台账登记项同名冲突:来源项下的全部登记记录改挂到目标同名项,空置的来源项删除;
- 来源标注:来源班级标记 merged_into_id / merged_at(班级本体保留,不做删除),
  每次成功合并写入 class_merge_logs(来源、目标、各项统计、花名册冲突明细);
- 失败回滚:全部改动在单个数据库事务中执行,任一异常即整体回滚(原子性,不会半完成)。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    ClassMergeLog,
    ClassRoster,
    CorrectionTask,
    ErrorRecord,
    Exam,
    ExamPaper,
    HomeworkItem,
    HomeworkRecord,
    SchoolClass,
)

logger = logging.getLogger(__name__)


class ClassMergeError(Exception):
    """合并业务错误(路由层转 400;包含合并失败回滚提示)"""


async def _validate_pair(
    db: AsyncSession, source_id: int, target_id: int
) -> tuple[SchoolClass, SchoolClass]:
    """校验合并对:存在、不相同、均未参与过合并"""
    if source_id == target_id:
        raise ClassMergeError("来源班级与目标班级不能相同")
    source = await db.get(SchoolClass, source_id)
    if source is None:
        raise LookupError(f"来源班级 {source_id} 不存在")
    target = await db.get(SchoolClass, target_id)
    if target is None:
        raise LookupError(f"目标班级 {target_id} 不存在")
    if source.merged_into_id is not None:
        raise ClassMergeError(f"来源班级「{source.name}」已经并入了其他班级,不能重复合并")
    if target.merged_into_id is not None:
        raise ClassMergeError(f"目标班级「{target.name}」本身已被并入其他班级,不能作为合并目标")
    return source, target


async def _collect_counts(db: AsyncSession, source_id: int) -> dict:
    """统计来源班级的全部业务数据量(预览与日志共用)"""
    task_ids = select(CorrectionTask.id).where(CorrectionTask.class_id == source_id)
    exam_ids = select(Exam.id).where(Exam.class_id == source_id)
    return {
        "tasks": (
            await db.execute(
                select(func.count(CorrectionTask.id)).where(CorrectionTask.class_id == source_id)
            )
        ).scalar_one(),
        "error_records": (
            await db.execute(
                select(func.count(ErrorRecord.id)).where(ErrorRecord.task_id.in_(task_ids))
            )
        ).scalar_one(),
        "roster": (
            await db.execute(
                select(func.count(ClassRoster.id)).where(ClassRoster.class_id == source_id)
            )
        ).scalar_one(),
        "homework_items": (
            await db.execute(
                select(func.count(HomeworkItem.id)).where(HomeworkItem.class_id == source_id)
            )
        ).scalar_one(),
        "homework_records": (
            await db.execute(
                select(func.count(HomeworkRecord.id)).where(HomeworkRecord.class_id == source_id)
            )
        ).scalar_one(),
        "exams": (
            await db.execute(select(func.count(Exam.id)).where(Exam.class_id == source_id))
        ).scalar_one(),
        "exam_papers": (
            await db.execute(select(func.count(ExamPaper.id)).where(ExamPaper.exam_id.in_(exam_ids)))
        ).scalar_one(),
    }


async def _build_roster_plan(db: AsyncSession, source_id: int, target_id: int) -> dict:
    """花名册匹配方案(同名以目标为准;展示每个来源成员的处置方式)"""
    source_members = (
        await db.execute(
            select(ClassRoster).where(ClassRoster.class_id == source_id).order_by(ClassRoster.id)
        )
    ).scalars().all()
    target_members = {
        m.name: m
        for m in (
            await db.execute(select(ClassRoster).where(ClassRoster.class_id == target_id))
        ).scalars().all()
    }
    plan: list[dict] = []
    counts = {"move": 0, "fill_id": 0, "duplicate": 0, "conflict": 0}
    for member in source_members:
        target_member = target_members.get(member.name)
        if target_member is None:
            status = "move"  # 目标班级没有该成员:随班级改挂
        elif member.student_id and not target_member.student_id:
            status = "fill_id"  # 目标缺学号:用来源学号补全
        elif member.student_id and target_member.student_id and member.student_id != target_member.student_id:
            status = "conflict"  # 学号冲突:以目标为准(明细记入日志)
        else:
            status = "duplicate"  # 完全重复:去重
        counts[status] += 1
        plan.append(
            {
                "name": member.name,
                "source_student_id": member.student_id,
                "target_student_id": target_member.student_id if target_member else None,
                "status": status,
            }
        )
    return {"total": len(source_members), **counts, "plan": plan}


async def _build_homework_plan(db: AsyncSession, source_id: int, target_id: int) -> dict:
    """台账登记项匹配方案(同名项:记录改挂目标项;不同名:随班级改挂)"""
    source_items = (
        await db.execute(
            select(HomeworkItem).where(HomeworkItem.class_id == source_id).order_by(HomeworkItem.id)
        )
    ).scalars().all()
    target_names = {
        name
        for (name,) in (
            await db.execute(select(HomeworkItem.name).where(HomeworkItem.class_id == target_id))
        ).all()
    }
    plan: list[dict] = []
    move = merge_records = 0
    for item in source_items:
        status = "merge_records" if item.name in target_names else "move"
        if status == "merge_records":
            merge_records += 1
        else:
            move += 1
        plan.append({"id": item.id, "name": item.name, "scoring_mode": item.scoring_mode, "status": status})
    return {"total": len(source_items), "move": move, "merge_records": merge_records, "plan": plan}


async def build_merge_preview(db: AsyncSession, source_id: int, target_id: int) -> dict:
    """合并预览:校验合并对并给出数据规模与冲突明细(只读,不产生任何改动)"""
    source, target = await _validate_pair(db, source_id, target_id)
    return {
        "source": {"id": source.id, "name": source.name, "note": source.note},
        "target": {"id": target.id, "name": target.name, "note": target.note},
        "counts": await _collect_counts(db, source_id),
        "roster": await _build_roster_plan(db, source_id, target_id),
        "homework_items": await _build_homework_plan(db, source_id, target_id),
    }


# ---------------------------------------------------------
# 合并执行(单事务;任一步骤异常 -> 整体回滚)
# ---------------------------------------------------------
async def _move_tasks(db: AsyncSession, source_id: int, target_id: int) -> int:
    """批改任务改挂目标班级(错因记录经任务关联自动跟随)"""
    result = await db.execute(
        update(CorrectionTask)
        .where(CorrectionTask.class_id == source_id)
        .values(class_id=target_id)
    )
    return result.rowcount or 0


async def _merge_roster(db: AsyncSession, source_id: int, target_id: int) -> dict:
    """花名册合并:新成员改挂;同名成员按"补全学号 -> 去重/冲突以目标为准"处理"""
    source_members = (
        await db.execute(select(ClassRoster).where(ClassRoster.class_id == source_id))
    ).scalars().all()
    target_members = {
        m.name: m
        for m in (
            await db.execute(select(ClassRoster).where(ClassRoster.class_id == target_id))
        ).scalars().all()
    }
    moved = filled = deduplicated = conflicts = 0
    for member in source_members:
        target_member = target_members.get(member.name)
        if target_member is None:
            member.class_id = target_id
            moved += 1
            continue
        if member.student_id and not target_member.student_id:
            target_member.student_id = member.student_id
            filled += 1
        elif member.student_id and target_member.student_id and member.student_id != target_member.student_id:
            conflicts += 1
        else:
            deduplicated += 1
        await db.delete(member)
    return {"moved": moved, "filled_id": filled, "deduplicated": deduplicated, "conflicts": conflicts}


async def _merge_homework(db: AsyncSession, source_id: int, target_id: int) -> dict:
    """台账合并:同名登记项的记录改挂目标项(空置来源项删除);其余项与记录随班级改挂"""
    source_items = (
        await db.execute(select(HomeworkItem).where(HomeworkItem.class_id == source_id))
    ).scalars().all()
    target_items = {
        item.name: item
        for item in (
            await db.execute(select(HomeworkItem).where(HomeworkItem.class_id == target_id))
        ).scalars().all()
    }
    moved_items = merged_items = 0
    for item in source_items:
        target_item = target_items.get(item.name)
        if target_item is None:
            item.class_id = target_id
            moved_items += 1
        else:
            # 记录改挂到目标同名项,随后删除已空置的来源项
            await db.execute(
                update(HomeworkRecord)
                .where(HomeworkRecord.item_id == item.id)
                .values(item_id=target_item.id)
            )
            await db.delete(item)
            merged_items += 1
    # 统一更新登记记录的班级归属(含刚改挂到同名项的记录)
    result = await db.execute(
        update(HomeworkRecord)
        .where(HomeworkRecord.class_id == source_id)
        .values(class_id=target_id)
    )
    return {"moved_items": moved_items, "merged_items": merged_items, "moved_records": result.rowcount or 0}


async def _move_exams(db: AsyncSession, source_id: int, target_id: int) -> int:
    """考试改挂目标班级(考卷/报告经考试关联自动跟随)"""
    result = await db.execute(
        update(Exam).where(Exam.class_id == source_id).values(class_id=target_id)
    )
    return result.rowcount or 0


async def _mark_source_merged(db: AsyncSession, source: SchoolClass, target: SchoolClass) -> None:
    """来源班级标记为已并入(保留班级本体,不做删除,保证可追溯)"""
    source.merged_into_id = target.id
    source.merged_at = datetime.now(timezone.utc)


async def execute_merge(db: AsyncSession, source_id: int, target_id: int) -> dict:
    """执行班级合并(原子性:失败整体回滚)

    Returns:
        {source, target, moved, roster, homework, log_id}

    Raises:
        LookupError:      班级不存在
        ClassMergeError:  业务校验失败,或执行异常(已回滚)
    """
    source, target = await _validate_pair(db, source_id, target_id)
    counts = await _collect_counts(db, source_id)
    roster_plan = await _build_roster_plan(db, source_id, target_id)

    try:
        moved = {
            "tasks": await _move_tasks(db, source_id, target_id),
            "exams": await _move_exams(db, source_id, target_id),
        }
        roster_stats = await _merge_roster(db, source_id, target_id)
        homework_stats = await _merge_homework(db, source_id, target_id)
        await _mark_source_merged(db, source, target)

        log = ClassMergeLog(
            source_class_id=source.id,
            source_class_name=source.name,
            target_class_id=target.id,
            target_class_name=target.name,
            stats={
                "counts_before": counts,
                "moved": moved,
                "roster": roster_stats,
                "homework": homework_stats,
                # 学号冲突明细(以目标为准;来源侧信息保留在日志中)
                "roster_conflicts": [
                    item for item in roster_plan["plan"] if item["status"] == "conflict"
                ],
            },
        )
        db.add(log)
        await db.commit()
    except ClassMergeError:
        await db.rollback()
        raise
    except Exception as e:  # noqa: BLE001 —— 任何异常都必须完整回滚并给出明确提示
        await db.rollback()
        logger.exception("班级合并失败,已回滚:%s -> %s", source_id, target_id)
        raise ClassMergeError(f"合并失败,数据已完整回滚(未产生半完成状态):{e}") from e

    logger.info(
        "班级合并完成:%s(%s) -> %s(%s);任务 %s 个、考试 %s 场",
        source.name, source.id, target.name, target.id, moved["tasks"], moved["exams"],
    )
    return {
        "source": {"id": source.id, "name": source.name},
        "target": {"id": target.id, "name": target.name},
        "moved": moved,
        "roster": roster_stats,
        "homework": homework_stats,
        "log_id": log.id,
    }


async def list_merge_logs(db: AsyncSession, limit: int = 20) -> list[dict]:
    """合并日志列表(倒序,供追溯展示)"""
    logs = (
        await db.execute(
            select(ClassMergeLog).order_by(ClassMergeLog.id.desc()).limit(max(1, min(limit, 200)))
        )
    ).scalars().all()
    return [
        {
            "id": log.id,
            "source_class_id": log.source_class_id,
            "source_class_name": log.source_class_name,
            "target_class_id": log.target_class_id,
            "target_class_name": log.target_class_name,
            "stats": log.stats,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
