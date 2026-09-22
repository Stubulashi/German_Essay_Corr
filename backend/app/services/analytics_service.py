"""教学分析服务(#2 班级共性错因分析 / #3 学生错题本)

职责:
- 基于 error_records 的标准化考点分类(canonical_type)做聚合统计;
- 生成"讲评摘要"Markdown(高频错因 Top N + 典型错例 + 讲评建议顺序 + 得分分布);
- 生成学生画像与错题本(时间线、复现错因、个体错因分布)。

设计说明:
- 统计以 Python 聚合为主(教师单机数据量级为数百~数千条,清晰优先);
- 所有统计维度均依赖 #1 的错因标准化,保证"Word Order"与"动词位序"不会分裂计数。
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import CorrectionTask, ErrorRecord, SchoolClass
from app.models.schemas import (
    ClassDiagnosis,
    ErrorCategoryStat,
    ErrorExample,
    ScoreBucket,
    StudentProfile,
    StudentTimelineItem,
)
from app.services.error_taxonomy import CATEGORY_TEACHING_TIPS, canonical_label

logger = logging.getLogger(__name__)

# 匹配 "18 / 25" 形式的数值得分
_NUMERIC_SCORE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
# 匹配 CEFR 等级(A1~C2)
_CEFR_RE = re.compile(r"\b(A1|A2|B1|B2|C1|C2)\b", re.IGNORECASE)

# 得分分布桶(按得分百分比)
_SCORE_BUCKETS: list[tuple[str, float, float]] = [
    ("低于 40%", 0.0, 40.0),
    ("40-60%", 40.0, 60.0),
    ("60-75%", 60.0, 75.0),
    ("75-90%", 75.0, 90.0),
    ("90% 以上", 90.0, 100.01),
]


def _parse_numeric_score(score_text: str | None) -> tuple[float, float] | None:
    """解析 "18 / 25" 形式的得分,返回 (得分, 满分);无法解析返回 None"""
    if not score_text:
        return None
    m = _NUMERIC_SCORE_RE.search(score_text)
    if not m:
        return None
    got, full = float(m.group(1)), float(m.group(2))
    if full <= 0:
        return None
    return got, full


def _parse_cefr_level(score_text: str | None) -> str | None:
    """从得分文本中提取 CEFR 等级(如 'B1 Pass' -> 'B1')"""
    if not score_text:
        return None
    m = _CEFR_RE.search(score_text)
    return m.group(1).upper() if m else None


def _student_key(student_id: str | None, student_name: str | None) -> str:
    """学生去重键:优先学号,其次姓名"""
    return student_id.strip() if student_id and student_id.strip() else (student_name or "未知")


def _aggregate_category_stats(
    records: list[ErrorRecord],
    task_to_student: dict[int, str],
    total_errors: int,
    max_examples: int = 3,
) -> list[ErrorCategoryStat]:
    """将错题记录聚合为各考点分类统计(按频次降序)

    Args:
        records:         错题记录列表
        task_to_student: 任务 ID -> 学生去重键
        total_errors:    错因总条数(用于百分比计算)
        max_examples:    每类保留的典型错例数量
    """
    # category -> 计数 / 任务集合 / 学生集合 / 错例池
    counts: Counter[str] = Counter()
    task_sets: dict[str, set[int]] = defaultdict(set)
    student_sets: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, list[ErrorExample]] = defaultdict(list)
    seen_example_students: dict[str, set[str]] = defaultdict(set)

    for rec in records:
        category = rec.canonical_type or "OTHER"
        counts[category] += 1
        task_sets[category].add(rec.task_id)
        student_key = _student_key(rec.student_id, rec.student_name)
        student_sets[category].add(student_key)
        # 典型错例:优先收录不同学生的样本,最多 max_examples 条
        if len(examples[category]) < max_examples and student_key not in seen_example_students[category]:
            examples[category].append(
                ErrorExample(
                    student_name=rec.student_name or "未知",
                    original_text=rec.original_text,
                    corrected_text=rec.corrected_text,
                )
            )
            seen_example_students[category].add(student_key)

    stats: list[ErrorCategoryStat] = []
    for category, count in counts.most_common():
        stats.append(
            ErrorCategoryStat(
                category=category,
                label=canonical_label(category),
                count=count,
                task_count=len(task_sets[category]),
                student_count=len(student_sets[category]),
                percentage=round(count * 100.0 / total_errors, 1) if total_errors else 0.0,
                examples=examples[category],
            )
        )
    return stats


def _render_teaching_summary(
    title: str,
    task_count: int,
    student_count: int,
    error_total: int,
    average_score: float | None,
    score_basis: str | None,
    score_distribution: list[ScoreBucket],
    level_distribution: list[ScoreBucket],
    category_stats: list[ErrorCategoryStat],
) -> str:
    """渲染讲评摘要 Markdown(后端确定性渲染,复用渲染器思路)"""
    lines: list[str] = [f"# 班级讲评摘要 - {title}", ""]

    # ---- 样本概览 ----
    overview = f"**样本:** {task_count} 份作文 · 涉及 {student_count} 名学生 · 错因合计 {error_total} 条"
    lines.append(overview)
    if average_score is not None and score_basis:
        lines.append(f"**平均分:** {average_score:g} / {score_basis}")
    lines.append("")

    if task_count == 0:
        lines.append("_当前筛选条件下暂无已完成的批改数据,请先完成批改后查看。_")
        return "\n".join(lines)

    # ---- 高频错因 TOP ----
    lines.append("---")
    lines.append("### 高频错因")
    lines.append("")
    if not category_stats:
        lines.append("_本次样本未发现明显语法错误,值得肯定!_")
    else:
        for i, stat in enumerate(category_stats[:8], start=1):
            lines.append(
                f"**{i}. {stat.label}** —— 出现 {stat.count} 次 · 涉及 {stat.student_count} 人 · 占比 {stat.percentage}%"
            )
            if stat.examples:
                ex = stat.examples[0]
                lines.append(f"> 典型错例:{ex.student_name}:❌ {ex.original_text} ❌ → ✅ {ex.corrected_text} ✅")
            lines.append("")

    # ---- 讲评建议顺序 ----
    if category_stats:
        lines.append("### 讲评建议顺序")
        lines.append("")
        for i, stat in enumerate(category_stats[:5], start=1):
            tip = CATEGORY_TEACHING_TIPS.get(stat.category, CATEGORY_TEACHING_TIPS["OTHER"])
            lines.append(f"{i}. **{stat.label}**(涉及 {stat.student_count} 人):{tip}")
        lines.append("")

    # ---- 得分分布 ----
    if score_distribution:
        lines.append("### 得分分布")
        lines.append("")
        for bucket in score_distribution:
            lines.append(f"* {bucket.label}:{bucket.count} 人")
        lines.append("")
    if level_distribution:
        lines.append("### CEFR 等级分布")
        lines.append("")
        for bucket in level_distribution:
            lines.append(f"* {bucket.label}:{bucket.count} 份")
        lines.append("")

    return "\n".join(lines)


async def build_class_diagnosis(
    db: AsyncSession,
    *,
    class_id: int | None = None,
    batch_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    with_ledger: bool = False,
    with_exam: bool = False,
) -> ClassDiagnosis:
    """构建班级共性错因诊断报告(#2)

    筛选优先级为"与"关系;不传任何条件则统计全部已完成任务。
    with_ledger / with_exam 为 true 时附加台账概览与考试统计概览(跨模块联动)。
    """
    # ---------- 1. 查询已完成任务 ----------
    task_stmt = select(CorrectionTask).where(CorrectionTask.status == "COMPLETED")
    if class_id is not None:
        task_stmt = task_stmt.where(CorrectionTask.class_id == class_id)
    if batch_id:
        task_stmt = task_stmt.where(CorrectionTask.batch_id == batch_id)
    if date_from:
        task_stmt = task_stmt.where(CorrectionTask.created_at >= date_from)
    if date_to:
        task_stmt = task_stmt.where(CorrectionTask.created_at < date_to)

    tasks = (await db.execute(task_stmt)).scalars().all()

    # ---------- 2. 筛选回显(含班级名) ----------
    class_name: str | None = None
    if class_id is not None:
        cls = await db.get(SchoolClass, class_id)
        class_name = cls.name if cls else None
    filters_echo = {
        "class_id": class_id,
        "class_name": class_name,
        "batch_id": batch_id,
        "date_from": date_from.strftime("%Y-%m-%d") if date_from else None,
        "date_to": date_to.strftime("%Y-%m-%d") if date_to else None,
    }
    title = class_name or (f"批次 {batch_id}" if batch_id else "全部数据")

    # ---------- 跨模块联动概览(作业台账 / 考试统计) ----------
    ledger_overview = None
    exam_overview = None
    if with_ledger:
        from app.services.ledger_service import build_class_summary as build_ledger_class_summary

        ledger_overview = await build_ledger_class_summary(
            db,
            class_id=class_id,
            date_from=date_from.date() if date_from else None,
            date_to=(date_to - timedelta(days=1)).date() if date_to else None,
        )
    if with_exam:
        from app.services.exam_service import build_exam_overview

        exam_overview = await build_exam_overview(db, class_id)

    if not tasks:
        return ClassDiagnosis(
            filters=filters_echo,
            task_count=0,
            student_count=0,
            error_total=0,
            teaching_summary_markdown=_render_teaching_summary(
                title, 0, 0, 0, None, None, [], [], []
            ),
            ledger_overview=ledger_overview,
            exam_overview=exam_overview,
        )

    # ---------- 3. 得分解析与分布 ----------
    scores: list[tuple[float, float]] = []
    score_buckets: Counter[str] = Counter()
    level_counter: Counter[str] = Counter()
    denominator_counts: Counter[float] = Counter()
    student_set: set[str] = set()
    for task in tasks:
        student_set.add(_student_key(task.student_id, task.student_name))
        score_text = (task.result or {}).get("overall_score")
        numeric = _parse_numeric_score(score_text)
        if numeric:
            got, full = numeric
            scores.append((got, full))
            denominator_counts[full] += 1
            pct = got * 100.0 / full
            for label, lo, hi in _SCORE_BUCKETS:
                if lo <= pct < hi:
                    score_buckets[label] += 1
                    break
        else:
            level = _parse_cefr_level(score_text)
            if level:
                level_counter[level] += 1

    average_score: float | None = None
    score_basis: str | None = None
    if scores:
        # 若满分基准一致,按原始分显示平均(更贴近教师直觉);否则换算百分制
        if len(denominator_counts) == 1:
            full = next(iter(denominator_counts))
            average_score = round(sum(g for g, _ in scores) / len(scores), 1)
            score_basis = f"满分 {full:g}"
        else:
            average_score = round(sum(g * 100.0 / f for g, f in scores) / len(scores), 1)
            score_basis = "百分制"

    score_distribution = [
        ScoreBucket(label=label, count=score_buckets.get(label, 0))
        for label, _, _ in _SCORE_BUCKETS
    ]
    level_distribution = [ScoreBucket(label=k, count=v) for k, v in sorted(level_counter.items())]

    # ---------- 4. 错因聚合 ----------
    task_ids = [t.id for t in tasks]
    task_to_student = {t.id: _student_key(t.student_id, t.student_name) for t in tasks}
    rec_stmt = select(ErrorRecord).where(ErrorRecord.task_id.in_(task_ids))
    records = (await db.execute(rec_stmt)).scalars().all()
    category_stats = _aggregate_category_stats(records, task_to_student, len(records))

    # ---------- 5. 讲评摘要 ----------
    summary = _render_teaching_summary(
        title=title,
        task_count=len(tasks),
        student_count=len(student_set),
        error_total=len(records),
        average_score=average_score,
        score_basis=score_basis,
        score_distribution=score_distribution,
        level_distribution=level_distribution,
        category_stats=category_stats,
    )

    return ClassDiagnosis(
        filters=filters_echo,
        task_count=len(tasks),
        student_count=len(student_set),
        error_total=len(records),
        average_score=average_score,
        score_basis=score_basis,
        score_distribution=score_distribution,
        level_distribution=level_distribution,
        category_stats=category_stats,
        teaching_summary_markdown=summary,
        ledger_overview=ledger_overview,
        exam_overview=exam_overview,
    )


async def build_student_profile(
    db: AsyncSession,
    *,
    student_id: str | None = None,
    name: str | None = None,
) -> StudentProfile | None:
    """构建学生画像与错题本(#3)

    Args:
        student_id: 按学号精确匹配(优先)
        name:       按姓名匹配(学号缺失的学生)

    Returns:
        StudentProfile;未找到该学生的批改记录时返回 None
    """
    # ---------- 1. 定位学生已完成任务 ----------
    task_stmt = select(CorrectionTask).where(CorrectionTask.status == "COMPLETED")
    if student_id:
        task_stmt = task_stmt.where(CorrectionTask.student_id == student_id)
    elif name:
        task_stmt = task_stmt.where(CorrectionTask.student_name == name)
    else:
        return None
    tasks = (await db.execute(task_stmt)).scalars().all()
    if not tasks:
        return None

    tasks.sort(key=lambda t: t.created_at)
    first_task = tasks[0]
    resolved_name = first_task.student_name or name or "未知"
    resolved_id = first_task.student_id or student_id

    # ---------- 2. 得分 ----------
    scores: list[tuple[float, float]] = []
    denominator_counts: Counter[float] = Counter()
    for task in tasks:
        numeric = _parse_numeric_score((task.result or {}).get("overall_score"))
        if numeric:
            scores.append(numeric)
            denominator_counts[numeric[1]] += 1
    average_score: float | None = None
    score_basis: str | None = None
    if scores:
        if len(denominator_counts) == 1:
            full = next(iter(denominator_counts))
            average_score = round(sum(g for g, _ in scores) / len(scores), 1)
            score_basis = f"满分 {full:g}"
        else:
            average_score = round(sum(g * 100.0 / f for g, f in scores) / len(scores), 1)
            score_basis = "百分制"

    # ---------- 3. 错因聚合 ----------
    task_ids = [t.id for t in tasks]
    task_to_student = {t.id: _student_key(t.student_id, t.student_name) for t in tasks}
    rec_stmt = select(ErrorRecord).where(ErrorRecord.task_id.in_(task_ids))
    records = (await db.execute(rec_stmt)).scalars().all()
    category_stats = _aggregate_category_stats(records, task_to_student, len(records))

    # 复现错因:同一考点在 ≥2 个不同任务中出现(按涉及任务数降序)
    category_task_sets: dict[str, set[int]] = defaultdict(set)
    for rec in records:
        category_task_sets[rec.canonical_type or "OTHER"].add(rec.task_id)
    recurring = [
        canonical_label(cat)
        for cat, tids in sorted(category_task_sets.items(), key=lambda kv: -len(kv[1]))
        if len(tids) >= 2
    ]

    # ---------- 4. 时间线 ----------
    errors_per_task: Counter[int] = Counter(rec.task_id for rec in records)
    top_category_per_task: dict[int, str] = {}
    task_category_counts: dict[int, Counter[str]] = defaultdict(Counter)
    for rec in records:
        task_category_counts[rec.task_id][rec.canonical_type or "OTHER"] += 1
    for tid, counter in task_category_counts.items():
        top_category_per_task[tid] = canonical_label(counter.most_common(1)[0][0])

    timeline: list[StudentTimelineItem] = []
    for task in tasks:
        timeline.append(
            StudentTimelineItem(
                kind="correction",
                task_id=task.id,
                created_at=task.created_at,
                overall_score=(task.result or {}).get("overall_score", ""),
                error_count=errors_per_task.get(task.id, 0),
                top_category_label=top_category_per_task.get(task.id),
                assignment_name=task.assignment_name,
            )
        )

    # ---------- 跨模块联动:作业台账 / 考试记录(读时聚合,各源独立权威) ----------
    from app.services.exam_service import build_student_exam_summary
    from app.services.ledger_service import build_student_summary as build_ledger_student_summary

    homework_summary = await build_ledger_student_summary(
        db, student_name=resolved_name, student_id=resolved_id
    )
    exam_summary = await build_student_exam_summary(
        db, student_name=resolved_name, student_id=resolved_id
    )
    for entry in exam_summary.entries:
        timeline.append(
            StudentTimelineItem(
                kind="exam",
                exam_id=entry.exam_id,
                # 注意:SQLite 读回的 datetime 为 naive,此处保持一致以便统一排序
                created_at=datetime.combine(entry.exam_date, time.min),
                overall_score=(
                    f"{entry.total_score:g} / {entry.full_score:g}" if entry.total_score is not None else "—"
                ),
                error_count=0,
                top_category_label=None,
                assignment_name=entry.exam_name,
            )
        )
    timeline.sort(key=lambda item: item.created_at.replace(tzinfo=None))

    has_ledger = homework_summary.record_count > 0
    has_exam = exam_summary.paper_count > 0
    return StudentProfile(
        student_name=resolved_name,
        student_id=resolved_id,
        task_count=len(tasks),
        first_seen=first_task.created_at,
        last_seen=tasks[-1].created_at,
        average_score=average_score,
        score_basis=score_basis,
        error_total=len(records),
        category_stats=category_stats,
        recurring=recurring,
        timeline=timeline,
        homework_summary=homework_summary if has_ledger else None,
        exam_summary=exam_summary if has_exam else None,
    )
