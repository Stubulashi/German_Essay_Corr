"""作业台账服务(日常作业登记与汇总)

职责:
- 登记项(HomeworkItem)CRUD:全局模板项(class_id 空)+ 班级私有项;
- 四种计分模式(LEVEL/SCORE/FLAG/STARS)的值校验与归一化(score_value 0-100);
- 批量登记 upsert:唯一键 (item_id, student_name, record_date),重复登记即更新,
  value 为空字符串表示撤销该条记录;
- 班级汇总(学生×登记项矩阵、按项统计)与学生个人汇总(供学生画像读时联动);
- 预设模板一键创建(教师高频登记维度)。

设计约定:
- 写路径仅写 homework_records + 复用 upsert_student(与批改流程同口径,学号优先后姓名兜底);
- 汇总统计以 Python 聚合为主(单机数据量级,清晰优先,与 analytics_service 一致);
- 统计口径跨模式统一到 score_value(0-100 归一),等级档位映射在 config 中可配。
"""

from __future__ import annotations

import copy
import logging
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import HomeworkItem, HomeworkRecord, SchoolClass, Student
from app.models.schemas import (
    LedgerCellOut,
    LedgerClassSummary,
    LedgerItemOut,
    LedgerItemStat,
    LedgerPersonItem,
    LedgerRecordOut,
    LedgerStudentOption,
    LedgerStudentRow,
    LedgerStudentSummary,
)
from app.services.student_service import upsert_student

logger = logging.getLogger(__name__)

#: 支持的计分模式
SCORING_MODES = ("LEVEL", "SCORE", "FLAG", "STARS")
#: 各模式的默认配置(创建登记项时若未提供则补齐)
DEFAULT_LEVELS = [
    {"label": "A", "value": 95},
    {"label": "B", "value": 85},
    {"label": "C", "value": 75},
    {"label": "D", "value": 60},
]
DEFAULT_CONFIGS: dict[str, dict] = {
    "LEVEL": {"levels": DEFAULT_LEVELS},
    "SCORE": {"max": 100, "step": 5},
    "FLAG": {"done": 100, "late": 70, "missing": 0},
    "STARS": {"max": 5},
}

#: 预设模板(教师高频登记维度;一键创建到指定班级或全局)
PRESET_ITEMS: list[dict] = [
    {"name": "书面作业", "category": "书面作业", "scoring_mode": "LEVEL"},
    {"name": "课文背诵", "category": "背诵", "scoring_mode": "LEVEL"},
    {"name": "单词听写", "category": "听写", "scoring_mode": "SCORE", "config": {"max": 100, "step": 5}},
    {"name": "课堂表现", "category": "课堂表现", "scoring_mode": "STARS"},
    {"name": "作业订正", "category": "订正", "scoring_mode": "FLAG"},
    {"name": "朗读打卡", "category": "口语", "scoring_mode": "FLAG"},
]


def default_config(scoring_mode: str) -> dict:
    """按计分模式返回默认配置(返回值可直接存库,深拷贝避免共享可变对象)"""
    base = DEFAULT_CONFIGS.get(scoring_mode, DEFAULT_CONFIGS["LEVEL"])
    return copy.deepcopy(base)


def normalize_score_value(scoring_mode: str, config: dict, value: str) -> float | None:
    """把登记值按计分模式归一化为 0-100 的 score_value

    - LEVEL:按 config.levels 的 label->value 映射;
    - SCORE:数值/满分×100(限幅到 [0, max]);
    - FLAG:done/late/missing 三态映射(取 config 中对应分值);
    - STARS:星数/上限×100(限幅)。

    Returns:
        归一化分值;值不合法(无法解析/不在档位内)返回 None
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        if scoring_mode == "LEVEL":
            levels = config.get("levels") or DEFAULT_LEVELS
            for level in levels:
                if str(level.get("label")) == text:
                    return float(level.get("value", 0))
            return None
        if scoring_mode == "SCORE":
            num = float(text)
            maximum = float(config.get("max", 100) or 100)
            if maximum <= 0:
                return None
            return round(min(max(num, 0.0), maximum) / maximum * 100, 1)
        if scoring_mode == "FLAG":
            flag_map = {
                "done": config.get("done", 100),
                "late": config.get("late", 70),
                "missing": config.get("missing", 0),
            }
            if text not in flag_map:
                return None
            return float(flag_map[text])
        if scoring_mode == "STARS":
            num = float(text)
            maximum = float(config.get("max", 5) or 5)
            if maximum <= 0:
                return None
            return round(min(max(num, 0.0), maximum) / maximum * 100, 1)
    except (TypeError, ValueError):
        return None
    return None


def _validate_value_or_raise(scoring_mode: str, config: dict, value: str) -> float | None:
    """校验登记值;非法时抛出 ValueError(路由层转 400)"""
    score = normalize_score_value(scoring_mode, config, value)
    if score is None:
        raise ValueError(f"登记值不合法:{value!r}(计分模式 {scoring_mode})")
    return score


# ---------------------------------------------------------
# 登记项 CRUD
# ---------------------------------------------------------
async def list_items(
    db: AsyncSession,
    *,
    class_id: int | None = None,
    include_global: bool = True,
    include_archived: bool = False,
) -> list[HomeworkItem]:
    """列出登记项(默认:全局模板 + 指定班级项,按 sort_order 升序)"""
    stmt = select(HomeworkItem)
    if class_id is not None:
        if include_global:
            stmt = stmt.where(
                (HomeworkItem.class_id == class_id) | (HomeworkItem.class_id.is_(None))
            )
        else:
            stmt = stmt.where(HomeworkItem.class_id == class_id)
    elif not include_global:
        stmt = stmt.where(HomeworkItem.class_id.is_not(None))
    if not include_archived:
        stmt = stmt.where(HomeworkItem.archived == 0)
    stmt = stmt.order_by(HomeworkItem.sort_order, HomeworkItem.id)
    return list((await db.execute(stmt)).scalars().all())


async def _ensure_name_unique(
    db: AsyncSession, *, class_id: int | None, name: str, exclude_id: int | None = None
) -> None:
    """同一作用域(班级或全局)内名称唯一"""
    stmt = select(HomeworkItem).where(HomeworkItem.name == name)
    stmt = stmt.where(
        HomeworkItem.class_id == class_id if class_id is not None else HomeworkItem.class_id.is_(None)
    )
    if exclude_id is not None:
        stmt = stmt.where(HomeworkItem.id != exclude_id)
    if (await db.execute(stmt)).scalars().first() is not None:
        raise ValueError(f"登记项已存在:{name}")


async def create_item(
    db: AsyncSession,
    *,
    class_id: int | None,
    name: str,
    category: str | None = None,
    scoring_mode: str = "LEVEL",
    config: dict | None = None,
    sort_order: int = 0,
) -> HomeworkItem:
    """创建登记项(计分模式与配置校验;名称同作用域唯一)"""
    if scoring_mode not in SCORING_MODES:
        raise ValueError(f"不支持的计分模式:{scoring_mode}")
    if class_id is not None:
        cls = await db.get(SchoolClass, class_id)
        if cls is None:
            raise ValueError(f"班级 {class_id} 不存在")
    name = name.strip()
    await _ensure_name_unique(db, class_id=class_id, name=name)
    merged_config = {**default_config(scoring_mode), **(config or {})}
    item = HomeworkItem(
        class_id=class_id,
        name=name,
        category=(category or "").strip() or None,
        scoring_mode=scoring_mode,
        config=merged_config,
        sort_order=sort_order,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def update_item(db: AsyncSession, item_id: int, fields: dict) -> HomeworkItem:
    """更新登记项(传 None 的字段保持不变;修改计分模式时补齐默认配置)"""
    item = await db.get(HomeworkItem, item_id)
    if item is None:
        raise LookupError(f"登记项 {item_id} 不存在")
    if fields.get("name") is not None:
        new_name = str(fields["name"]).strip()
        await _ensure_name_unique(db, class_id=item.class_id, name=new_name, exclude_id=item.id)
        item.name = new_name
    if fields.get("category") is not None:
        item.category = str(fields["category"]).strip() or None
    if fields.get("sort_order") is not None:
        item.sort_order = int(fields["sort_order"])
    if fields.get("scoring_mode") is not None:
        mode = str(fields["scoring_mode"])
        if mode not in SCORING_MODES:
            raise ValueError(f"不支持的计分模式:{mode}")
        item.scoring_mode = mode
        item.config = {**default_config(mode), **({} if fields.get("config") is None else fields["config"])}
    elif fields.get("config") is not None:
        item.config = {**item.config, **fields["config"]}
    await db.commit()
    await db.refresh(item)
    return item


async def delete_or_archive_item(db: AsyncSession, item_id: int) -> str:
    """删除登记项:无记录时物理删除;有记录时归档(保留历史统计)

    Returns:
        "deleted" 或 "archived"
    """
    item = await db.get(HomeworkItem, item_id)
    if item is None:
        raise LookupError(f"登记项 {item_id} 不存在")
    has_records = (
        await db.execute(
            select(HomeworkRecord.id).where(HomeworkRecord.item_id == item_id).limit(1)
        )
    ).scalars().first() is not None
    if has_records:
        item.archived = 1
        await db.commit()
        return "archived"
    await db.delete(item)
    await db.commit()
    return "deleted"


async def create_presets(db: AsyncSession, class_id: int | None) -> list[HomeworkItem]:
    """一键创建预设登记项(同作用域已存在的同名项自动跳过)"""
    if class_id is not None:
        cls = await db.get(SchoolClass, class_id)
        if cls is None:
            raise ValueError(f"班级 {class_id} 不存在")
    # 仅检查同作用域(该班级或全局)的重名,避免误跨作用域去重
    scope_stmt = select(HomeworkItem).where(
        HomeworkItem.class_id == class_id
        if class_id is not None
        else HomeworkItem.class_id.is_(None)
    )
    existing = {item.name for item in (await db.execute(scope_stmt)).scalars().all()}
    created: list[HomeworkItem] = []
    for index, preset in enumerate(PRESET_ITEMS):
        if preset["name"] in existing:
            continue
        item = HomeworkItem(
            class_id=class_id,
            name=preset["name"],
            category=preset.get("category"),
            scoring_mode=preset["scoring_mode"],
            config={**default_config(preset["scoring_mode"]), **preset.get("config", {})},
            sort_order=index,
        )
        db.add(item)
        created.append(item)
    await db.commit()
    for item in created:
        await db.refresh(item)
    return created


# ---------------------------------------------------------
# 登记记录(批量 upsert / 明细 / 纠错)
# ---------------------------------------------------------
async def get_item(db: AsyncSession, item_id: int) -> HomeworkItem:
    """获取登记项(不存在抛 LookupError)"""
    item = await db.get(HomeworkItem, item_id)
    if item is None:
        raise LookupError(f"登记项 {item_id} 不存在")
    return item


async def batch_upsert(
    db: AsyncSession,
    *,
    item: HomeworkItem,
    record_date: date,
    entries: list[dict],
    class_id: int | None = None,
) -> dict:
    """批量登记(upsert)

    Args:
        item:        登记项(提供计分模式与配置)
        record_date: 登记日期
        entries:     [{student_name, student_id?, value, note?}];value 为空 = 撤销该条
        class_id:    登记归属班级(优先于登记项默认归属;用于全局模板项的记录归属)

    Returns:
        {created, updated, deleted, student_synced}
    """
    record_class = class_id if class_id is not None else item.class_id
    created = updated = deleted = student_synced = 0
    for entry in entries:
        name = str(entry.get("student_name", "")).strip()
        if not name:
            continue
        student_id = (str(entry.get("student_id") or "").strip()) or None
        raw_value = str(entry.get("value", "") or "").strip()
        note = (str(entry.get("note") or "").strip()) or None

        existing = (
            await db.execute(
                select(HomeworkRecord).where(
                    HomeworkRecord.item_id == item.id,
                    HomeworkRecord.student_name == name,
                    HomeworkRecord.record_date == record_date,
                )
            )
        ).scalars().first()

        if not raw_value:  # 空值 = 撤销
            if existing is not None:
                await db.delete(existing)
                deleted += 1
            continue

        score_value = _validate_value_or_raise(item.scoring_mode, item.config or {}, raw_value)
        if existing is not None:
            existing.value = raw_value
            existing.score_value = score_value
            existing.note = note
            if student_id:
                existing.student_id = student_id
            if class_id is not None:
                existing.class_id = class_id
            updated += 1
        else:
            db.add(
                HomeworkRecord(
                    item_id=item.id,
                    class_id=record_class,
                    student_name=name,
                    student_id=student_id,
                    value=raw_value,
                    score_value=score_value,
                    record_date=record_date,
                    note=note,
                )
            )
            created += 1
        # 学生档案同步(与批改/花名册同规则)
        await upsert_student(db, name, student_id)
        student_synced += 1

    await db.commit()
    logger.info(
        "台账批量登记:item=%s date=%s 新增 %s / 更新 %s / 撤销 %s",
        item.id, record_date, created, updated, deleted,
    )
    return {"created": created, "updated": updated, "deleted": deleted, "student_synced": student_synced}


async def list_records(
    db: AsyncSession,
    *,
    class_id: int | None = None,
    item_id: int | None = None,
    student_name: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[int, list[LedgerRecordOut]]:
    """明细查询(返回总数与当前页;带登记项名称便于展示)"""
    from sqlalchemy import func

    conditions = []
    if class_id is not None:
        conditions.append(HomeworkRecord.class_id == class_id)
    if item_id is not None:
        conditions.append(HomeworkRecord.item_id == item_id)
    if student_name:
        conditions.append(HomeworkRecord.student_name == student_name)
    if date_from:
        conditions.append(HomeworkRecord.record_date >= date_from)
    if date_to:
        conditions.append(HomeworkRecord.record_date <= date_to)

    total = (
        await db.execute(select(func.count(HomeworkRecord.id)).where(*conditions))
    ).scalar_one()

    stmt = (
        select(HomeworkRecord, HomeworkItem.name)
        .join(HomeworkItem, HomeworkRecord.item_id == HomeworkItem.id)
        .where(*conditions)
        .order_by(HomeworkRecord.record_date.desc(), HomeworkRecord.id.desc())
        .limit(min(max(limit, 1), 1000))
        .offset(max(offset, 0))
    )
    rows = (await db.execute(stmt)).all()
    items = [
        LedgerRecordOut(
            id=rec.id,
            item_id=rec.item_id,
            item_name=item_name,
            class_id=rec.class_id,
            student_name=rec.student_name,
            student_id=rec.student_id,
            value=rec.value,
            score_value=rec.score_value,
            record_date=rec.record_date,
            note=rec.note,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )
        for rec, item_name in rows
    ]
    return total, items


async def update_record(
    db: AsyncSession, record_id: int, *, value: str, note: str | None
) -> HomeworkRecord:
    """单条纠错(重新归一化 score_value)"""
    record = await db.get(HomeworkRecord, record_id)
    if record is None:
        raise LookupError(f"登记记录 {record_id} 不存在")
    item = await get_item(db, record.item_id)
    raw = (value or "").strip()
    if not raw:
        raise ValueError("登记值不能为空(如需撤销请删除该条记录)")
    record.value = raw
    record.score_value = _validate_value_or_raise(item.scoring_mode, item.config or {}, raw)
    record.note = (note or "").strip() or None
    await db.commit()
    await db.refresh(record)
    return record


async def delete_record(db: AsyncSession, record_id: int) -> None:
    """删除单条登记记录"""
    record = await db.get(HomeworkRecord, record_id)
    if record is None:
        raise LookupError(f"登记记录 {record_id} 不存在")
    await db.delete(record)
    await db.commit()


# ---------------------------------------------------------
# 汇总(班级 / 学生)
# ---------------------------------------------------------
async def students_for_class(db: AsyncSession, class_id: int | None) -> list[LedgerStudentOption]:
    """登记用学生列表:花名册优先;无花名册时回退学生档案表"""
    from app.services.student_assignment import load_roster

    if class_id is not None:
        roster = await load_roster(db, class_id)
        if roster:
            return [
                LedgerStudentOption(name=m.name, student_id=m.student_id) for m in roster
            ]
    students = (await db.execute(select(Student).order_by(Student.id))).scalars().all()
    return [LedgerStudentOption(name=s.name, student_id=s.student_id) for s in students]


async def build_class_summary(
    db: AsyncSession,
    *,
    class_id: int | None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_empty_students: bool = True,
) -> LedgerClassSummary:
    """班级台账汇总:学生×项矩阵 + 按项统计

    - 学生集合 = 记录涉及的学生 ∪ 花名册学生(include_empty_students 时);
    - 单元格值 = 学生在时间范围内该项最近一次登记;同时附记录条数。
    """
    class_name: str | None = None
    if class_id is not None:
        cls = await db.get(SchoolClass, class_id)
        class_name = cls.name if cls else None

    # 1. 拉取时间范围内的记录(附带登记项)
    stmt = (
        select(HomeworkRecord, HomeworkItem)
        .join(HomeworkItem, HomeworkRecord.item_id == HomeworkItem.id)
        .order_by(HomeworkRecord.record_date, HomeworkRecord.id)
    )
    if class_id is not None:
        stmt = stmt.where(HomeworkRecord.class_id == class_id)
    if date_from:
        stmt = stmt.where(HomeworkRecord.record_date >= date_from)
    if date_to:
        stmt = stmt.where(HomeworkRecord.record_date <= date_to)
    rows = (await db.execute(stmt)).all()

    # 2. 汇总学生行与按项统计
    students_map: dict[str, LedgerStudentRow] = {}
    item_stats: dict[int, dict] = {}
    all_scores: dict[str, list[float]] = defaultdict(list)

    def _row(name: str, student_id: str | None) -> LedgerStudentRow:
        if name not in students_map:
            students_map[name] = LedgerStudentRow(student_name=name, student_id=student_id)
        elif student_id and not students_map[name].student_id:
            students_map[name].student_id = student_id
        return students_map[name]

    for rec, item in rows:
        row = _row(rec.student_name, rec.student_id)
        row.record_count += 1
        if row.latest_date is None or rec.record_date > row.latest_date:
            row.latest_date = rec.record_date
        if rec.score_value is not None:
            all_scores[rec.student_name].append(rec.score_value)
        # 单元格:同项取最近一次
        key = str(item.id)
        cell = row.by_item.get(key)
        if cell is None or rec.record_date >= cell.record_date:
            row.by_item[key] = LedgerCellOut(
                value=rec.value,
                score_value=rec.score_value,
                record_date=rec.record_date,
                count=(cell.count + 1) if cell else 1,
            )
        else:
            cell.count += 1
        # 按项统计
        stat = item_stats.setdefault(
            item.id,
            {
                "name": item.name,
                "scoring_mode": item.scoring_mode,
                "count": 0,
                "students": set(),
                "scores": [],
                "distribution": Counter(),
            },
        )
        stat["count"] += 1
        stat["students"].add(rec.student_name)
        if rec.score_value is not None:
            stat["scores"].append(rec.score_value)
        stat["distribution"][rec.value] += 1

    # 3. 花名册学生补空行(便于发现漏登)
    if class_id is not None and include_empty_students:
        for option in await students_for_class(db, class_id):
            _row(option.name, option.student_id)

    # 4. 生均分
    for name, row in students_map.items():
        scores = all_scores.get(name)
        if scores:
            row.overall_avg = round(sum(scores) / len(scores), 1)

    # 5. 登记项列表(班级/全局在用的未归档项)
    involved_ids = {rec.item_id for rec, _ in rows}
    items_all = await list_items(db, class_id=class_id)
    items = [item for item in items_all if item.id in involved_ids or item.archived == 0]

    def _stat_out(item: HomeworkItem) -> LedgerItemStat:
        stat = item_stats.get(item.id)
        if stat is None:
            return LedgerItemStat(
                item_id=item.id, name=item.name, scoring_mode=item.scoring_mode, count=0, student_count=0
            )
        scores: list[float] = stat["scores"]
        return LedgerItemStat(
            item_id=item.id,
            name=item.name,
            scoring_mode=item.scoring_mode,
            count=stat["count"],
            student_count=len(stat["students"]),
            avg_score_value=round(sum(scores) / len(scores), 1) if scores else None,
            distribution=dict(stat["distribution"]),
        )

    student_rows = sorted(
        students_map.values(),
        key=lambda r: (-(r.overall_avg or -1), r.student_name),
    )
    return LedgerClassSummary(
        class_id=class_id,
        class_name=class_name,
        date_from=date_from,
        date_to=date_to,
        record_count=len(rows),
        student_count=len(students_map),
        items=[LedgerItemOut.model_validate(item) for item in items],
        item_stats=[_stat_out(item) for item in items],
        students=student_rows,
    )


async def build_student_summary(
    db: AsyncSession,
    *,
    student_name: str | None,
    student_id: str | None,
    class_id: int | None = None,
    recent_limit: int = 10,
) -> LedgerStudentSummary:
    """学生个人台账摘要(供学生画像读时聚合)

    匹配规则(读时聚合):学号精确 **或** 姓名一致——与全系统"学号优先、姓名兜底"
    对齐口径一致;台账记录常缺学号(手填/全局登记项),仅按学号匹配会漏聚合。
    代价:班级内存在重名且学号不同时可能过聚合(教师可补录学号后消除)。
    """
    if not student_id and not student_name:
        return LedgerStudentSummary()

    stmt = select(HomeworkRecord, HomeworkItem).join(
        HomeworkItem, HomeworkRecord.item_id == HomeworkItem.id
    )
    matched: list = []
    if student_id:
        matched.append(HomeworkRecord.student_id == student_id)
    if student_name:
        matched.append(HomeworkRecord.student_name == student_name)
    stmt = stmt.where(or_(*matched) if len(matched) > 1 else matched[0])
    if class_id is not None:
        stmt = stmt.where(HomeworkRecord.class_id == class_id)
    stmt = stmt.order_by(HomeworkRecord.record_date.desc(), HomeworkRecord.id.desc())
    rows = (await db.execute(stmt)).all()
    if not rows:
        return LedgerStudentSummary()

    # 按项聚合
    per_item: dict[int, dict] = {}
    all_scores: list[float] = []
    for rec, item in rows:
        entry = per_item.setdefault(
            item.id,
            {
                "name": item.name,
                "scoring_mode": item.scoring_mode,
                "count": 0,
                "scores": [],
                "latest_value": rec.value,
                "latest_date": rec.record_date,
            },
        )
        entry["count"] += 1
        if rec.score_value is not None:
            entry["scores"].append(rec.score_value)
            all_scores.append(rec.score_value)

    items = [
        LedgerPersonItem(
            item_id=item_id,
            name=entry["name"],
            scoring_mode=entry["scoring_mode"],
            count=entry["count"],
            avg_score_value=round(sum(entry["scores"]) / len(entry["scores"]), 1)
            if entry["scores"]
            else None,
            latest_value=entry["latest_value"],
            latest_date=entry["latest_date"],
        )
        for item_id, entry in sorted(per_item.items(), key=lambda kv: -kv[1]["count"])
    ]
    recent = [
        LedgerRecordOut(
            id=rec.id,
            item_id=rec.item_id,
            item_name=item.name,
            class_id=rec.class_id,
            student_name=rec.student_name,
            student_id=rec.student_id,
            value=rec.value,
            score_value=rec.score_value,
            record_date=rec.record_date,
            note=rec.note,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )
        for rec, item in rows[:recent_limit]
    ]
    return LedgerStudentSummary(
        record_count=len(rows),
        average_score_value=round(sum(all_scores) / len(all_scores), 1) if all_scores else None,
        items=items,
        recent=recent,
    )
