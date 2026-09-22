"""练习卷生成服务(依据历史作业错因数据生成练习卷 + 标准答案)

数据来源(只读溯源,不写回任何统计表):
- correction_tasks(COMPLETED)的 result.errors / topic / assignment_name / overall_score;
- error_records 的 canonical_type 聚合(与错题本/班级分析同源表)。

生成流程:
1. 解析来源(scope=selected/all + 作业引用)→ 命中任务集合(空 → 明确报错);
2. 组装证据摘要(错因分布 / 典型错句去重限条 / 主题列表);
3. 调用 LLM 命题(复用 DeepSeek 配置;mock 返回确定性样例)生成试卷/答案 Markdown + 结构化题目;
4. 解析容错(extract_json_dict + 修复重试一次),校验契约后落库 practice_sheets。
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as global_settings
from app.models.db_models import CorrectionTask, ErrorRecord, PracticeSheet, SchoolClass
from app.models.schemas import PracticeGenerateRequest
from app.pipelines.prompts import PRACTICE_SYSTEM_PROMPT, build_practice_user_message
from app.services.error_taxonomy import canonical_label, normalize_error_type
from app.services.llm_client import LLMClient
from app.services.parser import build_repair_user_message, extract_json_dict

logger = logging.getLogger(__name__)

#: 题型集合(唯一来源:GET /practice/options 与本服务共用)
QUESTION_TYPES: tuple[tuple[str, str], ...] = (
    ("grammar", "语法填空"),
    ("vocabulary", "词汇选择"),
    ("correction", "句子改错"),
    ("translation", "中译德"),
    ("reading", "阅读理解"),
    ("cloze", "完形填空"),
    ("writing", "写作小任务"),
)
TYPE_LABELS: dict[str, str] = dict(QUESTION_TYPES)
ALLOWED_TYPES: set[str] = set(TYPE_LABELS)
_LABEL_TO_KEY: dict[str, str] = {label: key for key, label in QUESTION_TYPES}

#: 数量边界(options 与请求校验共用)
MIN_COUNT = 5
MAX_COUNT = 50
DEFAULT_COUNT = 10

#: 证据规模上限(控制 Prompt 体积与生成耗时)
_MAX_TASKS = 200
_MAX_ERROR_SAMPLES = 40
_MAX_TOPICS = 15


class PracticeError(ValueError):
    """练习卷生成的前置校验/生成失败(路由层转 400/502)"""


# ---------------------------------------------------------
# 来源聚合(一次/多次/全选的可选项)
# ---------------------------------------------------------
async def build_sources(db: AsyncSession, class_id: int | None = None) -> list[dict]:
    """按 (class_id, assignment_name) 聚合可选作业来源(任务数/日期区间/高频错因 Top3)"""
    stmt = select(CorrectionTask).where(CorrectionTask.status == "COMPLETED")
    if class_id is not None:
        stmt = stmt.where(CorrectionTask.class_id == class_id)
    tasks = list((await db.execute(stmt.order_by(CorrectionTask.created_at.desc()))).scalars().all())

    groups: dict[tuple[int | None, str | None], dict] = {}
    for task in tasks:
        key = (task.class_id, task.assignment_name)
        item = groups.setdefault(
            key,
            {
                "class_id": task.class_id,
                "name": task.assignment_name,
                "task_count": 0,
                "dates": [],
                "task_ids": [],
            },
        )
        item["task_count"] += 1
        if task.created_at:
            item["dates"].append(task.created_at.date())
        item["task_ids"].append(task.id)

    # 错因分布:error_records 聚合(同源表,只读)
    per_task_category: dict[int, Counter] = defaultdict(Counter)
    all_ids = [tid for item in groups.values() for tid in item["task_ids"]]
    if all_ids:
        rows = (
            await db.execute(
                select(ErrorRecord.task_id, ErrorRecord.canonical_type).where(
                    ErrorRecord.task_id.in_(all_ids)
                )
            )
        ).all()
        for task_id, canonical in rows:
            per_task_category[task_id][canonical or "OTHER"] += 1

    # 班级名称映射
    class_ids = {item["class_id"] for item in groups.values() if item["class_id"] is not None}
    class_names: dict[int, str] = {}
    if class_ids:
        for cls in (
            await db.execute(select(SchoolClass).where(SchoolClass.id.in_(class_ids)))
        ).scalars().all():
            class_names[cls.id] = cls.name

    results: list[dict] = []
    for item in groups.values():
        counter: Counter = Counter()
        for tid in item["task_ids"]:
            counter.update(per_task_category.get(tid, Counter()))
        results.append(
            {
                "class_id": item["class_id"],
                "class_name": class_names.get(item["class_id"]) if item["class_id"] else None,
                "name": item["name"],
                "task_count": item["task_count"],
                "date_from": min(item["dates"]) if item["dates"] else None,
                "date_to": max(item["dates"]) if item["dates"] else None,
                "top_categories": [canonical_label(ctype) for ctype, _ in counter.most_common(3)],
            }
        )
    results.sort(key=lambda row: row["date_to"] or date.min, reverse=True)
    return results


def resolve_tasks(
    tasks: list[CorrectionTask], assignments: list, scope: str
) -> list[CorrectionTask]:
    """来源选择解析:scope=all 命中全部;selected 按 (class_id, name) 精确匹配"""
    if scope == "all":
        return list(tasks)
    wanted = {(ref.class_id, (ref.name or None)) for ref in assignments}
    if not wanted:
        raise PracticeError("请至少选择一次作业,或将生成范围切换为「全部作业」")
    return [
        task
        for task in tasks
        if (task.class_id, (task.assignment_name or None)) in wanted
    ]


# ---------------------------------------------------------
# 证据摘要与解析(纯函数可测)
# ---------------------------------------------------------
def build_evidence(tasks: list[CorrectionTask]) -> dict:
    """从命中任务中提取命题证据:错因分布 / 典型错句(去重限条) / 主题列表"""
    counter: Counter = Counter()
    samples: list[str] = []
    seen: set[tuple] = set()
    topics: list[str] = []
    for task in tasks:
        result = task.result or {}
        for error in result.get("errors") or []:
            canonical = error.get("canonical_type") or normalize_error_type(
                error.get("error_type")
            ).value
            counter[canonical_label(canonical)] += 1
            original = str(error.get("original_text") or "").strip()
            corrected = str(error.get("corrected_text") or "").strip()
            if not original or "[unleserlich" in original:
                continue
            key = (canonical, original, corrected)
            if key in seen or len(samples) >= _MAX_ERROR_SAMPLES:
                continue
            seen.add(key)
            samples.append(
                f"{len(samples) + 1}. [{canonical_label(canonical)}] {original} → {corrected or '(未提供修正,保留原句待重写)'}"
            )
        topic = (task.topic or "").strip() or (task.assignment_name or "").strip()
        if topic and topic not in topics and len(topics) < _MAX_TOPICS:
            topics.append(topic)
    return {
        "category_distribution": counter.most_common(12),
        "error_samples": samples,
        "topics": topics,
    }


def _normalize_type(raw: str) -> str:
    """题型归一:键名/中文标签均接受;未知值保留原文(前端按回退展示)"""
    text = (raw or "").strip()
    if text in ALLOWED_TYPES:
        return text
    if text in _LABEL_TO_KEY:
        return _LABEL_TO_KEY[text]
    return text or "grammar"


def parse_practice_output(raw: str) -> dict:
    """解析 LLM 命题输出(容错);缺少可用题目内容时抛 ValueError(触发修复重试)"""
    obj = extract_json_dict(raw)
    questions: list[dict] = []
    for index, question in enumerate(obj.get("questions") or [], start=1):
        if not isinstance(question, dict):
            continue
        stem = str(question.get("stem") or "").strip()
        answer = str(question.get("answer") or "").strip()
        if not stem or not answer:
            continue
        explanation = question.get("explanation")
        questions.append(
            {
                "type": _normalize_type(str(question.get("type") or "")),
                "no": str(question.get("no") or index),
                "stem": stem,
                "answer": answer,
                "explanation": (str(explanation).strip() or None) if explanation else None,
            }
        )
    worksheet = str(obj.get("worksheet_markdown") or "").strip()
    answers = str(obj.get("answer_markdown") or "").strip()
    title = str(obj.get("title") or "").strip()
    if not questions and not worksheet:
        raise ValueError("练习卷输出缺少 questions 与 worksheet_markdown,无法使用")
    return {
        "title": title,
        "questions": questions,
        "worksheet_markdown": worksheet,
        "answer_markdown": answers,
    }


def render_markdown(title: str, questions: list[dict], *, answers: bool) -> str:
    """确定性兜底渲染(LLM 未提供 Markdown 时使用;与 questions 同源)"""
    if answers:
        lines = [f"# {title} · 标准答案", ""]
        for question in questions:
            lines.append(f"**{question['no']}.** {question['answer']}")
            if question.get("explanation"):
                lines.append(f"> 解析:{question['explanation']}")
    else:
        lines = [f"# {title}", "", "## 试题", ""]
        for question in questions:
            lines.append(f"**{question['no']}.** {question['stem']}")
    return "\n".join(lines)


def _mock_practice_payload(question_types: list[str], count: int) -> dict:
    """mock 模式确定性样例(全链路可演示、可断言)"""
    questions: list[dict] = []
    for index in range(1, min(count, MAX_COUNT) + 1):
        qtype = question_types[(index - 1) % len(question_types)]
        label = TYPE_LABELS.get(qtype, qtype)
        questions.append(
            {
                "type": qtype,
                "no": str(index),
                "stem": f"【演示 · {label}】第 {index} 题:Ergänzen Sie die richtige Form. (Demo-Modus)",
                "answer": f"Antwort {index}",
                "explanation": f"演示模式解析:{label} · 示例考点 {index}。",
            }
        )
    title = f"演示练习卷({len(question_types)} 类题型 / {count} 题)"
    return {
        "title": title,
        "questions": questions,
        "worksheet_markdown": render_markdown(title, questions, answers=False),
        "answer_markdown": render_markdown(title, questions, answers=True),
    }


# ---------------------------------------------------------
# LLM 编排
# ---------------------------------------------------------
def _make_client() -> LLMClient:
    config = global_settings
    if config.deepseek_api_key:
        model = (
            config.deepseek_reasoning_model
            if config.deepseek_use_reasoning
            else config.deepseek_model
        )
        return LLMClient(
            base_url=config.deepseek_base_url,
            api_key=config.deepseek_api_key,
            model=model,
            timeout=config.deepseek_timeout,
            pipeline_tag="PRACTICE",
            params_target="deepseek",
        )
    if config.local_vlm_base_url:
        return LLMClient(
            base_url=config.local_vlm_base_url,
            api_key=config.local_vlm_api_key,
            model=config.local_vlm_model,
            timeout=config.local_vlm_timeout,
            pipeline_tag="PRACTICE-LOCAL",
            params_target="pipeline_a",
        )
    raise PracticeError(
        "未配置可用的文本模型端点:请先在设置中心配置 DEEPSEEK_API_KEY(推荐)或 LOCAL_VLM_BASE_URL"
    )


def _model_name() -> str:
    config = global_settings
    if config.deepseek_api_key:
        return (
            config.deepseek_reasoning_model
            if config.deepseek_use_reasoning
            else config.deepseek_model
        )
    return config.local_vlm_model


async def _generate_with_repair(user_message: str) -> dict:
    """命题调用 + 解析容错(失败触发一次修复重试)"""
    client = _make_client()
    messages: list[dict] = [
        {"role": "system", "content": PRACTICE_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    raw = await client.chat(messages, temperature=0.4)
    try:
        return parse_practice_output(raw)
    except ValueError as error:
        messages.append(
            {"role": "user", "content": build_repair_user_message(raw, str(error))}
        )
        repaired = await client.chat(messages, temperature=0.4)
        return parse_practice_output(repaired)


# ---------------------------------------------------------
# 生成与 CRUD
# ---------------------------------------------------------
async def _fetch_completed(db: AsyncSession) -> list[CorrectionTask]:
    stmt = (
        select(CorrectionTask)
        .where(CorrectionTask.status == "COMPLETED")
        .order_by(CorrectionTask.created_at.desc())
        .limit(_MAX_TASKS)
    )
    return list((await db.execute(stmt)).scalars().all())


def _validate_request(payload: PracticeGenerateRequest) -> list[str]:
    """校验题型白名单与数量边界;返回去重后的题型列表"""
    types = list(dict.fromkeys(payload.question_types))
    unknown = [item for item in types if item not in ALLOWED_TYPES]
    if unknown:
        raise PracticeError(
            f"未知题型:{', '.join(unknown)}(可选:{', '.join(ALLOWED_TYPES)})"
        )
    if not (MIN_COUNT <= payload.count <= MAX_COUNT):
        raise PracticeError(f"题目数量必须在 {MIN_COUNT}~{MAX_COUNT} 之间")
    return types


async def generate_sheet(db: AsyncSession, payload: PracticeGenerateRequest):
    """生成练习卷(同步端点;失败不落库,返回可读错误)"""
    types = _validate_request(payload)
    tasks = await _fetch_completed(db)
    selected = resolve_tasks(tasks, payload.assignments, payload.scope)
    if not selected:
        raise PracticeError("所选范围内没有已完成任务,无法生成练习卷(请先完成批改或调整范围)")

    evidence = build_evidence(selected)
    if global_settings.mock_mode:
        parsed = _mock_practice_payload(types, payload.count)
        model_name = "mock"
    else:
        user_message = build_practice_user_message(
            question_types=types,
            type_labels=TYPE_LABELS,
            count=payload.count,
            category_distribution=evidence["category_distribution"],
            error_samples=evidence["error_samples"],
            topics=evidence["topics"],
        )
        parsed = await _generate_with_repair(user_message)
        model_name = _model_name()

    questions = parsed["questions"]
    if not questions and not parsed["worksheet_markdown"]:
        raise PracticeError("生成结果缺少题目内容,请重试")
    fallback_title = f"{selected[0].created_at.date() if selected[0].created_at else date.today()} 错因专项练习卷"
    title = (payload.title or parsed["title"] or fallback_title).strip()[:128]
    worksheet = parsed["worksheet_markdown"] or render_markdown(title, questions, answers=False)
    answers = parsed["answer_markdown"] or render_markdown(title, questions, answers=True)

    source_meta = {
        "scope": payload.scope,
        "assignments": (
            [{"class_id": ref.class_id, "name": ref.name} for ref in payload.assignments]
            if payload.scope == "selected"
            else []
        ),
        "task_count": len(selected),
        "category_distribution": [
            {"label": label, "count": number} for label, number in evidence["category_distribution"]
        ],
        "topics": evidence["topics"],
    }
    sheet = PracticeSheet(
        class_id=payload.class_id,
        title=title,
        source=source_meta,
        params={"question_types": types, "count": payload.count},
        content={"questions": questions},
        worksheet_markdown=worksheet,
        answer_markdown=answers,
        model=model_name,
    )
    db.add(sheet)
    await db.commit()
    await db.refresh(sheet)
    logger.info(
        "练习卷 %s 已生成(题型 %s,题数 %s,来源任务 %s 篇)",
        sheet.id,
        types,
        payload.count,
        len(selected),
    )
    return sheet


async def list_sheets(db: AsyncSession, class_id: int | None = None) -> list[PracticeSheet]:
    stmt = select(PracticeSheet).order_by(PracticeSheet.created_at.desc(), PracticeSheet.id.desc())
    if class_id is not None:
        stmt = stmt.where(PracticeSheet.class_id == class_id)
    return list((await db.execute(stmt)).scalars().all())


async def get_sheet(db: AsyncSession, sheet_id: int) -> PracticeSheet:
    sheet = await db.get(PracticeSheet, sheet_id)
    if sheet is None:
        raise LookupError(f"练习卷 {sheet_id} 不存在")
    return sheet


async def delete_sheet(db: AsyncSession, sheet_id: int) -> None:
    sheet = await get_sheet(db, sheet_id)
    await db.delete(sheet)
    await db.commit()
    logger.info("练习卷 %s 已删除", sheet_id)
