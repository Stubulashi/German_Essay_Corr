"""统一统计层服务(三源聚合:批改 / 考试 / 台账;全部只读)

数据原则:
- 各源独立权威(批改 correction_tasks / 考试 exam_papers / 台账 homework_records),
  统计在读时聚合,不向业务表写入;
- 学生对齐复用 analytics_service._student_key(学号优先 / 姓名兜底);
- 综合平均 = 各源"归一百分比"的等权平均;批改的 CEFR 等级仅计数展示,不参与数值平均;
- 空数据返回空结构,由前端展示引导文案。
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as global_settings
from app.models.db_models import (
    ClassRoster,
    CorrectionTask,
    Exam,
    ExamPaper,
    HomeworkItem,
    HomeworkRecord,
)
from app.services.analytics_service import (
    _SCORE_BUCKETS,
    _parse_cefr_level,
    _parse_numeric_score,
    _student_key,
)

logger = logging.getLogger(__name__)

#: 支持的来源
SOURCES = ("correction", "exam", "ledger")


@dataclass
class Assessment:
    """一次评估(三源统一形态)"""

    the_date: date
    source: str  # correction | exam | ledger
    label: str
    raw: str  # 原始分展示文本
    percent: float | None  # 归一百分比(CEFR 等无数值时为 None)


@dataclass
class StudentBucket:
    """学生聚合桶"""

    name: str
    student_id: str | None = None
    entries: list[Assessment] = field(default_factory=list)


async def _load_buckets(
    db: AsyncSession,
    *,
    class_id: int | None,
    date_from: date | None,
    date_to: date | None,
    sources: set[str],
    include_empty_roster: bool = True,
) -> dict[str, StudentBucket]:
    """按学生聚合三类来源的评估记录"""
    buckets: dict[str, StudentBucket] = {}

    def bucket(name: str | None, student_id: str | None) -> StudentBucket | None:
        if not name or name == "未知":
            return None
        key = _student_key(student_id, name)
        item = buckets.get(key)
        if item is None:
            item = StudentBucket(name=name, student_id=student_id)
            buckets[key] = item
        elif student_id and not item.student_id:
            item.student_id = student_id
        return item

    # ---- 批改(已完成任务) ----
    if "correction" in sources:
        stmt = select(CorrectionTask).where(CorrectionTask.status == "COMPLETED")
        if class_id is not None:
            stmt = stmt.where(CorrectionTask.class_id == class_id)
        if date_from is not None:
            stmt = stmt.where(CorrectionTask.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(CorrectionTask.created_at < date_to)
        for task in (await db.execute(stmt)).scalars().all():
            target = bucket(task.student_name, task.student_id)
            if target is None:
                continue
            result = task.result or {}
            score_text = str(result.get("overall_score") or "")
            numeric = _parse_numeric_score(score_text)
            percent = round(numeric[0] * 100.0 / numeric[1], 1) if numeric else None
            if numeric is None and _parse_cefr_level(score_text) is None:
                continue  # 无有效得分文本不计入
            target.entries.append(
                Assessment(
                    the_date=task.created_at.date(),
                    source="correction",
                    label=task.assignment_name or f"批改 #{task.id}",
                    raw=score_text or "—",
                    percent=percent,
                )
            )

    # ---- 考试(已识别考卷) ----
    if "exam" in sources:
        stmt = (
            select(ExamPaper, Exam)
            .join(Exam, ExamPaper.exam_id == Exam.id)
            .where(ExamPaper.ocr_status == "DONE")
        )
        if class_id is not None:
            stmt = stmt.where(Exam.class_id == class_id)
        if date_from is not None:
            stmt = stmt.where(Exam.exam_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Exam.exam_date < date_to)
        for paper, exam in (await db.execute(stmt)).all():
            target = bucket(paper.student_name, paper.student_id)
            if target is None:
                continue
            full = float(exam.full_score or 100.0)
            percent = (
                round(paper.total_score * 100.0 / full, 1)
                if paper.total_score is not None
                else None
            )
            target.entries.append(
                Assessment(
                    the_date=exam.exam_date,
                    source="exam",
                    label=exam.name,
                    raw=f"{paper.total_score:g} / {full:g}" if paper.total_score is not None else "—",
                    percent=percent,
                )
            )

    # ---- 台账(登记记录) ----
    if "ledger" in sources:
        stmt = select(HomeworkRecord, HomeworkItem).join(
            HomeworkItem, HomeworkRecord.item_id == HomeworkItem.id
        )
        if class_id is not None:
            stmt = stmt.where(HomeworkRecord.class_id == class_id)
        if date_from is not None:
            stmt = stmt.where(HomeworkRecord.record_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(HomeworkRecord.record_date < date_to)
        for record, item in (await db.execute(stmt)).all():
            target = bucket(record.student_name, record.student_id)
            if target is None:
                continue
            target.entries.append(
                Assessment(
                    the_date=record.record_date,
                    source="ledger",
                    label=item.name,
                    raw=record.value,
                    percent=record.score_value,
                )
            )

    # ---- 花名册空行(便于发现未登记学生) ----
    if include_empty_roster and class_id is not None:
        members = (
            await db.execute(select(ClassRoster).where(ClassRoster.class_id == class_id))
        ).scalars().all()
        for member in members:
            bucket(member.name, member.student_id)

    # ---- 学生对齐合并(学号优先/姓名兜底) ----
    # 某些来源(如手填台账)可能只有姓名没有学号,会形成"姓名键"桶;
    # 若同名仅存在唯一学号键,则把姓名键桶并入学号键桶(与全系统对齐口径一致);
    # 同名多个学号(重名)时不合并,保持可辨识。
    by_name: dict[str, list[str]] = defaultdict(list)
    for key, bucket_item in buckets.items():
        by_name[bucket_item.name].append(key)
    for name, keys in by_name.items():
        if name not in keys or len(keys) <= 1:
            continue
        id_keys = [key for key in keys if key != name]
        if len(id_keys) == 1:
            primary = buckets[id_keys[0]]
            secondary = buckets.pop(name)
            primary.entries.extend(secondary.entries)
            if not primary.student_id:
                primary.student_id = secondary.student_id

    for item in buckets.values():
        item.entries.sort(key=lambda entry: entry.the_date)
    return buckets


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _percent_summary(percents: list[float]) -> dict:
    """分布统计(平均/最高/最低/及格率/分桶;复用班级分析的分数段口径)"""
    if not percents:
        return {
            "count": 0, "average": None, "highest": None, "lowest": None,
            "pass_rate": None, "buckets": [{"label": label, "count": 0} for label, _, _ in _SCORE_BUCKETS],
        }
    buckets = {label: 0 for label, _, _ in _SCORE_BUCKETS}
    for value in percents:
        for label, low, high in _SCORE_BUCKETS:
            if low <= value < high:
                buckets[label] += 1
                break
    passed = sum(1 for value in percents if value >= float(global_settings.score_pass_line))
    return {
        "count": len(percents),
        "average": _average(percents),
        "highest": max(percents),
        "lowest": min(percents),
        "pass_rate": round(passed * 100.0 / len(percents), 1),
        "buckets": [{"label": label, "count": buckets[label]} for label, _, _ in _SCORE_BUCKETS],
    }


# ---------------------------------------------------------
# 成绩总表
# ---------------------------------------------------------
async def build_gradebook(
    db: AsyncSession,
    *,
    class_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sources: set[str] | None = None,
) -> dict:
    """学生成绩总表(学生 × 三类来源的评估序列 + 聚合列)"""
    active_sources = sources or set(SOURCES)
    buckets = await _load_buckets(
        db, class_id=class_id, date_from=date_from, date_to=date_to, sources=active_sources
    )

    students: list[dict] = []
    for bucket_item in buckets.values():
        per_source: dict[str, list[float]] = defaultdict(list)
        for entry in bucket_item.entries:
            if entry.percent is not None:
                per_source[entry.source].append(entry.percent)
        all_percents = [p for values in per_source.values() for p in values]
        latest = next(
            (entry.percent for entry in reversed(bucket_item.entries) if entry.percent is not None),
            None,
        )
        previous = next(
            (entry.percent for entry in reversed(bucket_item.entries[:-1]) if entry.percent is not None),
            None,
        )
        delta = round(latest - previous, 1) if (latest is not None and previous is not None) else None
        students.append(
            {
                "student_name": bucket_item.name,
                "student_id": bucket_item.student_id,
                "record_count": len(bucket_item.entries),
                "source_average": {source: _average(values) for source, values in per_source.items()},
                "overall_percent": _average(all_percents),
                "latest_percent": latest,
                "delta": delta,
                "entries": [
                    {
                        "date": entry.the_date.isoformat(),
                        "source": entry.source,
                        "label": entry.label,
                        "raw": entry.raw,
                        "percent": entry.percent,
                    }
                    for entry in bucket_item.entries
                ],
            }
        )
    students.sort(key=lambda row: (row["overall_percent"] is None, -(row["overall_percent"] or 0)))
    return {
        "filters": {
            "class_id": class_id,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "sources": sorted(active_sources),
        },
        "student_count": len(students),
        "students": students,
    }


def build_gradebook_csv(gradebook: dict) -> str:
    """总表导出 CSV(长表格式:学生/来源/日期/项目/原始分/百分比;Excel 友好)"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    source_label = {"correction": "作文批改", "exam": "考试", "ledger": "作业台账"}
    writer.writerow(["学生", "学号", "来源", "日期", "项目", "原始分", "归一百分比"])
    for student in gradebook["students"]:
        for entry in student["entries"]:
            writer.writerow([
                student["student_name"],
                student["student_id"] or "",
                source_label.get(entry["source"], entry["source"]),
                entry["date"],
                entry["label"],
                entry["raw"],
                entry["percent"] if entry["percent"] is not None else "",
            ])
    return buffer.getvalue()


# ---------------------------------------------------------
# 分布统计
# ---------------------------------------------------------
async def build_distribution(
    db: AsyncSession,
    *,
    source: str,
    class_id: int | None = None,
    target_id: int | None = None,
) -> dict:
    """单来源的分布统计(分数段/平均/最高/最低/及格率)

    target_id:source=exam 时限定某场考试;其余来源忽略该参数。
    """
    if source not in SOURCES:
        raise ValueError(f"未知统计来源:{source}(可选 {'/'.join(SOURCES)})")
    buckets = await _load_buckets(
        db, class_id=class_id, date_from=None, date_to=None,
        sources={source}, include_empty_roster=False,
    )
    percents: list[float] = []
    for bucket_item in buckets.values():
        for entry in bucket_item.entries:
            if entry.percent is not None:
                percents.append(entry.percent)
    result = _percent_summary(percents)
    result["source"] = source
    result["target_id"] = target_id if source == "exam" else None
    result["class_id"] = class_id
    return result


# ---------------------------------------------------------
# 高阶报表:排行与趋势
# ---------------------------------------------------------
async def build_rankings(db: AsyncSession, *, class_id: int | None = None) -> dict:
    """排行榜:综合平均 / 考试单场 / 进步榜 / 台账覆盖率"""
    buckets = await _load_buckets(
        db, class_id=class_id, date_from=None, date_to=None, sources=set(SOURCES)
    )

    combined: list[dict] = []
    progress: list[dict] = []
    for bucket_item in buckets.values():
        all_percents = [entry.percent for entry in bucket_item.entries if entry.percent is not None]
        average = _average(all_percents)
        if average is not None:
            combined.append(
                {"student_name": bucket_item.name, "student_id": bucket_item.student_id,
                 "average": average, "count": len(all_percents)}
            )
        scored_entries = [entry for entry in bucket_item.entries if entry.percent is not None]
        if len(scored_entries) >= 2:
            delta = round(scored_entries[-1].percent - scored_entries[-2].percent, 1)
            progress.append(
                {"student_name": bucket_item.name, "student_id": bucket_item.student_id,
                 "delta": delta, "latest": scored_entries[-1].percent,
                 "previous": scored_entries[-2].percent}
            )
    combined.sort(key=lambda row: -row["average"])
    progress.sort(key=lambda row: -row["delta"])

    # 考试单场榜:最近一场考试
    exam_ranking: list[dict] = []
    exam_name: str | None = None
    stmt = select(Exam).order_by(Exam.exam_date.desc(), Exam.id.desc()).limit(1)
    if class_id is not None:
        stmt = stmt.where(Exam.class_id == class_id)
    latest_exam = (await db.execute(stmt)).scalars().first()
    if latest_exam is not None:
        exam_name = latest_exam.name
        full = float(latest_exam.full_score or 100.0)
        papers = (
            await db.execute(
                select(ExamPaper).where(
                    ExamPaper.exam_id == latest_exam.id, ExamPaper.ocr_status == "DONE"
                )
            )
        ).scalars().all()
        for paper in papers:
            if paper.total_score is None:
                continue
            exam_ranking.append(
                {"student_name": paper.student_name, "student_id": paper.student_id,
                 "total_score": paper.total_score, "full_score": full,
                 "percent": round(paper.total_score * 100.0 / full, 1)}
            )
        exam_ranking.sort(key=lambda row: -row["total_score"])

    # 台账覆盖率榜
    coverage: list[dict] = []
    item_stmt = select(HomeworkItem).where(HomeworkItem.archived == 0)
    if class_id is not None:
        item_stmt = item_stmt.where(
            (HomeworkItem.class_id == class_id) | (HomeworkItem.class_id.is_(None))
        )
    item_count = len((await db.execute(item_stmt)).scalars().all())
    if item_count > 0:
        for bucket_item in buckets.values():
            ledger_items = {entry.label for entry in bucket_item.entries if entry.source == "ledger"}
            if not ledger_items:
                continue
            coverage.append(
                {"student_name": bucket_item.name, "student_id": bucket_item.student_id,
                 "covered_items": len(ledger_items), "item_total": item_count,
                 "coverage": round(len(ledger_items) * 100.0 / item_count, 1)}
            )
        coverage.sort(key=lambda row: -row["coverage"])

    return {
        "class_id": class_id,
        "combined": combined[:10],
        "exam_ranking": {"exam_name": exam_name, "rows": exam_ranking[:10]},
        "progress": progress[:5],
        "decline": sorted(progress, key=lambda row: row["delta"])[:5],
        "coverage": coverage[:10],
    }


async def build_trends(
    db: AsyncSession,
    *,
    class_id: int | None = None,
    student: str | None = None,
) -> dict:
    """趋势分析:评估序列 + 月度平均(班级平均与可选学生对比)"""
    buckets = await _load_buckets(
        db, class_id=class_id, date_from=None, date_to=None, sources=set(SOURCES)
    )

    def _entries_of(bucket_item: StudentBucket) -> list[dict]:
        return [
            {
                "date": entry.the_date.isoformat(),
                "source": entry.source,
                "label": entry.label,
                "percent": entry.percent,
            }
            for entry in bucket_item.entries
            if entry.percent is not None
        ]

    selected: StudentBucket | None = None
    if student:
        target_key = None
        for key, bucket_item in buckets.items():
            if student in (bucket_item.name, bucket_item.student_id or ""):
                target_key = key
                break
        if target_key:
            selected = buckets[target_key]

    monthly: dict[str, list[float]] = defaultdict(list)
    for bucket_item in buckets.values():
        for entry in bucket_item.entries:
            if entry.percent is not None:
                monthly[entry.the_date.strftime("%Y-%m")].append(entry.percent)
    monthly_rows = [
        {"month": month, "average": _average(values), "count": len(values)}
        for month, values in sorted(monthly.items())
    ]

    return {
        "class_id": class_id,
        "student_selected": (
            {"student_name": selected.name, "student_id": selected.student_id} if selected else None
        ),
        "monthly": monthly_rows,
        "student_entries": _entries_of(selected) if selected else [],
    }
