"""考试统计服务(考卷 OCR 识别 + 班级统计分析 + 报告生成)

职责:
- 考卷图片视觉识别(逐题得分,"除听力外"),人工修订优先;
- 轻量异步处理队列(模式与批改队列 queue_service 一致,但独立实例,零回归);
- 报告聚合统计与确定性 Markdown 渲染,持久化到 exam_reports;
- 学生考试摘要与班级考试概览(供学生画像 / 班级分析读时联动)。

视觉端点选择策略:
1. mock_mode -> 返回确定性样例(与 MockPipeline 的演示行为一致);
2. 优先管线 B 的 OCR 配置(vlm_openai 且已配置);
3. 回退管线 A 的本地 VLM 配置;
4. 均未配置 -> 抛出明确配置错误(写入考卷 ocr_error)。

数据口径:
- 逐题结果只包含"除听力外"条目(Prompt 约束 + 解析兜底过滤);
- knowledge_tag 经 error_taxonomy 归一为 canonical_type 后参与知识点失分聚合;
- 报告为快照式持久化(重新生成即覆盖),统计基于 ocr_status=DONE 的考卷。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, settings as global_settings
from app.models.db_models import Exam, ExamPaper, ExamReport, SchoolClass
from app.models.schemas import (
    ExamKnowledgeStat,
    ExamOverview,
    ExamOverviewItem,
    ExamStudentEntry,
    ExamStudentSummary,
)
from app.pipelines.prompts import build_exam_ocr_prompt, build_exam_user_message
from app.services.error_taxonomy import CATEGORY_TEACHING_TIPS, canonical_label, normalize_error_type
from app.services.llm_client import LLMClient, build_image_message_parts
from app.services.parser import extract_json_dict, normalize_optional_str
from app.services.student_service import upsert_student

logger = logging.getLogger(__name__)

#: 听力板块兜底过滤(即使模型输出听力条目也剔除)
_LISTENING_RE = re.compile(r"hören|hoeren|听力|listening", re.IGNORECASE)
#: 及格线兜底比例(实际口径来自设置中心「教学与报告 · SCORE_PASS_LINE」,热生效)
_PASS_RATIO_FALLBACK = 0.6


def _pass_line_ratio() -> float:
    """当前及格比例(0~1;由设置中心 score_pass_line 决定,非法值回退兜底)"""
    try:
        value = global_settings.score_pass_line
        if 0 <= value <= 100:
            return value / 100.0
    except Exception:  # noqa: BLE001
        pass
    return _PASS_RATIO_FALLBACK

#: 分数分布桶(百分比口径,与班级分析 analytics_service 保持一致)
# 说明:此处按"与 analytics 完全一致的档位"导入,保证全系统分布口径统一
from app.services.analytics_service import _SCORE_BUCKETS, _student_key  # noqa: E402


def _to_float(value) -> float | None:
    """宽容的数值转换(拒绝布尔与不可解析值)"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------
# OCR 输出解析(纯函数,可独立测试)
# ---------------------------------------------------------
def _normalize_question(raw: dict) -> dict | None:
    """规范化单条逐题结果;听力题与缺题号条目返回 None(剔除)"""
    no = str(raw.get("no") or "").strip()
    if not no:
        return None
    part = str(raw.get("part") or "").strip() or None
    if part and _LISTENING_RE.search(part):
        return None  # 听力板块不产生条目
    tag = str(raw.get("knowledge_tag") or "").strip() or None
    return {
        "no": no,
        "part": part,
        "max_score": _to_float(raw.get("max_score")),
        "score": _to_float(raw.get("score")),
        "knowledge_tag": tag,
        "canonical_type": normalize_error_type(tag).value if tag else None,
        "note": str(raw.get("note") or "").strip() or None,
    }


def parse_exam_output(raw: str) -> dict:
    """解析视觉模型的考卷识别输出

    Returns:
        {student_name, student_id, total_score, questions[]}
        - questions 已剔除听力题与无效条目;
        - total_score 缺失时按逐题得分求和兜底。
    """
    obj = extract_json_dict(raw)
    name = str(obj.get("student_name") or "").strip() or "未知"
    sid = normalize_optional_str(obj.get("student_id"))
    total = _to_float(obj.get("total_score"))
    questions: list[dict] = []
    for item in obj.get("questions") or []:
        if isinstance(item, dict):
            normalized = _normalize_question(item)
            if normalized is not None:
                questions.append(normalized)
    if total is None:
        scored = [q["score"] for q in questions if q["score"] is not None]
        total = round(sum(scored), 2) if scored else None
    return {"student_name": name, "student_id": sid, "total_score": total, "questions": questions}


#: mock 模式的确定性题卷规格(题号, 板块, 满分;听力不出现)
_MOCK_QUESTION_SPEC = [
    ("1", "语法", 5), ("2", "语法", 5), ("3", "词汇", 5), ("4", "词汇", 5),
    ("5", "阅读", 10), ("6", "阅读", 10), ("7", "翻译", 10), ("8", "写作", 15),
]
_WEAK_TAGS = ["动词位序", "名词变格", "介词搭配", "词汇选择", "篇章与表达"]


def _mock_exam_payload(paper_id: int, exam_id: int) -> dict:
    """mock 模式:确定性样例(同一考卷恒等输出,用于 UI 开发与验收)"""
    seed = (paper_id or 0) * 131 + (exam_id or 0) * 17
    questions: list[dict] = []
    total = 0.0
    for i, (no, part, max_score) in enumerate(_MOCK_QUESTION_SPEC):
        score = round(max_score * ((seed + i * 7) % 100) / 100.0 * 2) / 2  # 半分档
        score = min(score, float(max_score))
        total += score
        tag = None
        if score < max_score:
            tag = _WEAK_TAGS[i % len(_WEAK_TAGS)]
        questions.append(
            {
                "no": no,
                "part": part,
                "max_score": float(max_score),
                "score": score,
                "knowledge_tag": tag,
                "canonical_type": normalize_error_type(tag).value if tag else None,
                "note": None,
            }
        )
    return {
        "student_name": "未知",
        "student_id": None,
        "total_score": round(total, 1),
        "questions": questions,
    }


# ---------------------------------------------------------
# 报告聚合(纯函数,可独立测试)
# ---------------------------------------------------------
def _build_exam_stats(exam: Exam, done: list[ExamPaper], all_papers: list[ExamPaper]) -> dict:
    """聚合考试统计(概览/分布/逐题/知识点失分/待处理名单)"""
    full = float(exam.full_score or 100.0)
    scored = [(p, float(p.total_score)) for p in done if p.total_score is not None]
    values = [v for _, v in scored]
    average = round(sum(values) / len(values), 1) if values else None
    highest = max(values) if values else None
    lowest = min(values) if values else None
    pass_line = full * _pass_line_ratio()
    passed = sum(1 for v in values if v >= pass_line)
    pass_rate = round(passed * 100.0 / len(values), 1) if values else None

    buckets: Counter[str] = Counter()
    for v in values:
        pct = v * 100.0 / full
        for label, lo, hi in _SCORE_BUCKETS:
            if lo <= pct < hi:
                buckets[label] += 1
                break
    score_distribution = [
        {"label": label, "count": buckets.get(label, 0)} for label, _, _ in _SCORE_BUCKETS
    ]

    # ---- 逐题聚合(按 题号+板块 分组,先按得分率升序 = 弱题在前) ----
    q_data: dict[tuple[str, str], dict] = {}
    for p in done:
        student = _student_key(p.student_id, p.student_name)
        for q in p.question_results or []:
            key = (str(q.get("no") or ""), str(q.get("part") or ""))
            entry = q_data.setdefault(
                key, {"max": [], "scores": [], "wrong": 0, "students_wrong": set()}
            )
            mx, sc = _to_float(q.get("max_score")), _to_float(q.get("score"))
            if mx is not None:
                entry["max"].append(mx)
            if sc is not None:
                entry["scores"].append(sc)
            if mx is not None and sc is not None and sc < mx:
                entry["wrong"] += 1
                entry["students_wrong"].add(student)
    question_stats: list[dict] = []
    for (no, part), entry in q_data.items():
        avg_max = sum(entry["max"]) / len(entry["max"]) if entry["max"] else None
        avg_score = sum(entry["scores"]) / len(entry["scores"]) if entry["scores"] else None
        rate = (
            round(avg_score * 100.0 / avg_max, 1)
            if (avg_max and avg_score is not None)
            else None
        )
        question_stats.append(
            {
                "no": no,
                "part": part or None,
                "avg_max": round(avg_max, 1) if avg_max else None,
                "avg_score": round(avg_score, 2) if avg_score is not None else None,
                "score_rate": rate,
                "wrong_count": entry["wrong"],
                "students_wrong": len(entry["students_wrong"]),
            }
        )
    question_stats.sort(
        key=lambda r: (r["score_rate"] if r["score_rate"] is not None else 101.0, -r["wrong_count"])
    )

    # ---- 知识点失分聚合 ----
    k_data: dict[str, dict] = {}
    for p in done:
        student = _student_key(p.student_id, p.student_name)
        for q in p.question_results or []:
            mx, sc = _to_float(q.get("max_score")), _to_float(q.get("score"))
            ctype = q.get("canonical_type")
            if ctype and mx is not None and sc is not None and sc < mx:
                entry = k_data.setdefault(ctype, {"count": 0, "students": set()})
                entry["count"] += 1
                entry["students"].add(student)
    knowledge_stats = [
        {
            "canonical_type": ctype,
            "label": canonical_label(ctype),
            "count": entry["count"],
            "student_count": len(entry["students"]),
            "tip": CATEGORY_TEACHING_TIPS.get(ctype, CATEGORY_TEACHING_TIPS.get("OTHER", "")),
        }
        for ctype, entry in sorted(k_data.items(), key=lambda kv: -kv[1]["count"])
    ]

    return {
        "full_score": full,
        "paper_count": len(all_papers),
        "scored_count": len(values),
        "average": average,
        "highest": highest,
        "lowest": lowest,
        "pass_line": round(pass_line, 1),
        "pass_rate": pass_rate,
        "score_distribution": score_distribution,
        "question_stats": question_stats,
        "knowledge_stats": knowledge_stats,
        "pending_papers": [p.student_name for p in all_papers if p.ocr_status != "DONE"],
        "failed_papers": [p.student_name for p in all_papers if p.ocr_status == "FAILED"],
    }


def _render_exam_report(exam: Exam, class_name: str | None, stats: dict) -> str:
    """确定性渲染考试分析报告 Markdown"""
    lines: list[str] = [f"# 考试分析报告 - {exam.name}", ""]
    meta_parts: list[str] = []
    if class_name:
        meta_parts.append(f"**班级**:{class_name}")
    meta_parts.extend(
        [
            f"**考试日期**:{exam.exam_date.isoformat()}",
            f"**科目**:{exam.subject}",
            f"**满分**:{stats['full_score']:g}",
            f"**参考(已识别)**:{stats['scored_count']} 人",
        ]
    )
    lines.append(" · ".join(meta_parts))
    lines.append("")
    lines.append("> 说明:听力部分不纳入本报告统计;逐题与知识点分析均基于「除听力外」的得分数据。")
    lines.append("")

    if stats["scored_count"] == 0:
        lines.append("_暂无可用于统计的得分数据,请先完成考卷识别。_")
        return "\n".join(lines)

    # ---- 一、成绩概览 ----
    lines.append("## 一、成绩概览")
    lines.append("")
    if stats["average"] is not None:
        lines.append(f"- 平均分:**{stats['average']:g}** / {stats['full_score']:g}")
    if stats["highest"] is not None:
        lines.append(f"- 最高分:{stats['highest']:g} · 最低分:{stats['lowest']:g}")
    if stats["pass_rate"] is not None:
        lines.append(f"- 及格率(≥{stats['pass_line']:g} 分 / {global_settings.score_pass_line}%):**{stats['pass_rate']}%**")
    lines.append("")
    lines.append("### 分数分布")
    lines.append("")
    for bucket in stats["score_distribution"]:
        lines.append(f"* {bucket['label']}:{bucket['count']} 人")
    lines.append("")

    # ---- 二、逐题分析(弱题在前) ----
    weak_questions = [q for q in stats["question_stats"] if q["score_rate"] is not None][:10]
    if weak_questions:
        lines.append("## 二、逐题分析(得分率由低到高,失分最多的题在前)")
        lines.append("")
        for i, q in enumerate(weak_questions, start=1):
            part = f"({q['part']})" if q["part"] else ""
            max_hint = f",满分 {q['avg_max']:g}" if q["avg_max"] else ""
            lines.append(
                f"{i}. **第 {q['no']} 题{part}**{max_hint}:平均得分 "
                f"{q['avg_score'] if q['avg_score'] is not None else '-'},"
                f"得分率 {q['score_rate']}%,失分 {q['wrong_count']} 人次"
            )
        lines.append("")

    # ---- 三、知识点失分与讲评建议 ----
    if stats["knowledge_stats"]:
        lines.append("## 三、知识点失分与讲评建议")
        lines.append("")
        for i, k in enumerate(stats["knowledge_stats"][:8], start=1):
            lines.append(
                f"{i}. **{k['label']}** —— 失分 {k['count']} 次 · 涉及 {k['student_count']} 人"
            )
            if k["tip"]:
                lines.append(f"   - 讲评建议:{k['tip']}")
        lines.append("")

    # ---- 四、待处理名单 ----
    if stats["pending_papers"] or stats["failed_papers"]:
        lines.append("## 四、待处理名单")
        lines.append("")
        if stats["failed_papers"]:
            lines.append(f"- 识别失败:{'、'.join(stats['failed_papers'])}(可在考试详情页重试)")
        if stats["pending_papers"]:
            lines.append(f"- 识别未完成:{'、'.join(stats['pending_papers'])}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------
# 考试服务(独立异步队列 + 处理流水线)
# ---------------------------------------------------------
class ExamService:
    """考卷识别与统计服务(独立 worker 池,与批改队列互不干扰)"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings | None = None,
        max_concurrent: int = 1,
    ):
        self._session_factory = session_factory
        self._settings = settings or global_settings
        self._max_concurrent = max(1, max_concurrent)
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False

    @property
    def pending_count(self) -> int:
        """当前排队中的考卷数"""
        return self._queue.qsize()

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self._max_concurrent):
            worker = asyncio.create_task(self._worker_loop(i), name=f"exam-worker-{i}")
            self._workers.append(worker)
        logger.info("考试识别队列已启动(并发数:%s)", self._max_concurrent)

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("考试识别队列已停止")

    async def enqueue(self, paper_id: int) -> None:
        """考卷入队(立即返回,不阻塞请求)"""
        await self._queue.put(paper_id)
        logger.debug("考卷 %s 已入队(当前排队:%s)", paper_id, self._queue.qsize())

    async def _worker_loop(self, worker_index: int) -> None:
        while self._running:
            try:
                paper_id = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self.process_paper(paper_id)
            except asyncio.CancelledError:
                logger.warning("考试工作协程 %s 被取消,考卷 %s 处理中断", worker_index, paper_id)
                break
            except Exception:
                logger.exception("考试工作协程 %s 处理考卷 %s 时发生未捕获异常", worker_index, paper_id)
            finally:
                self._queue.task_done()

    async def recover_orphan_papers(self) -> list[int]:
        """服务重启后恢复中断的考卷识别(重置 PROCESSING -> PENDING 并返回 ID 列表)"""
        async with self._session_factory() as session:
            stmt = select(ExamPaper).where(ExamPaper.ocr_status == "PROCESSING")
            papers = (await session.execute(stmt)).scalars().all()
            ids: list[int] = []
            for paper in papers:
                paper.ocr_status = "PENDING"
                paper.ocr_error = None
                ids.append(paper.id)
            if ids:
                await session.commit()
                logger.warning("检测到 %s 份中断考卷,已重置为待识别:%s", len(ids), ids)
        return ids

    # ---------- 单份考卷处理 ----------
    async def process_paper(self, paper_id: int) -> None:
        """处理一份考卷(永不向外抛异常,保证队列稳定)"""
        async with self._session_factory() as session:
            paper = await session.get(ExamPaper, paper_id)
            if paper is None:
                logger.error("考卷 %s 不存在,跳过处理", paper_id)
                return
            paper.ocr_status = "PROCESSING"
            paper.ocr_error = None
            exam_id = paper.exam_id
            image_rels = list(paper.image_paths or [])
            await session.commit()

        try:
            if self._settings.mock_mode:
                data = _mock_exam_payload(paper_id, exam_id)
            else:
                image_paths = [self._settings.upload_path / rel for rel in image_rels]
                missing = [p for p in image_paths if not p.exists()]
                if missing:
                    raise FileNotFoundError(f"考卷图片缺失:{missing[0].name}")
                raw = await self._call_vision(image_paths)
                data = parse_exam_output(raw)
        except Exception as e:  # noqa: BLE001 —— 任何失败都写入考卷状态
            logger.error("考卷 %s 识别失败:%s", paper_id, e)
            await self._mark_failed(paper_id, str(e))
            await self._refresh_exam_status(exam_id)
            return

        await self._apply_result(paper_id, data)
        await self._refresh_exam_status(exam_id)
        logger.info(
            "考卷 %s 识别完成:学生=%s,总分=%s,题数=%s",
            paper_id, data.get("student_name"), data.get("total_score"), len(data.get("questions") or []),
        )

    def _pick_vision_endpoint(self) -> tuple[str, str, str, float, str]:
        """选择视觉端点:优先管线 B OCR(vlm_openai),回退管线 A 本地 VLM"""
        s = self._settings
        if s.ocr_provider == "vlm_openai" and s.ocr_api_key and s.ocr_base_url:
            return s.ocr_base_url, s.ocr_api_key, s.ocr_model, s.ocr_timeout, "EXAM-OCR"
        if s.local_vlm_base_url:
            return s.local_vlm_base_url, s.local_vlm_api_key, s.local_vlm_model, s.local_vlm_timeout, "EXAM-OCR-LOCAL"
        raise RuntimeError(
            "未配置可用的视觉模型端点:请先配置 OCR_BASE_URL/OCR_API_KEY(管线 B)或 LOCAL_VLM_BASE_URL(管线 A)"
        )

    async def _call_vision(self, image_paths: list[Path]) -> str:
        """调用视觉模型识别考卷"""
        base_url, api_key, model, timeout, tag = self._pick_vision_endpoint()
        # 超参目标:OCR 端点走 ocr 配置,本地 VLM 回退走管线 A 配置
        client = LLMClient(
            base_url=base_url, api_key=api_key, model=model, timeout=timeout, pipeline_tag=tag,
            params_target="ocr" if tag == "EXAM-OCR" else "pipeline_a",
        )
        content = build_image_message_parts(image_paths, build_exam_user_message())
        messages = [
            {"role": "system", "content": build_exam_ocr_prompt()},
            {"role": "user", "content": content},
        ]
        return await client.chat(messages)

    async def _mark_failed(self, paper_id: int, message: str) -> None:
        async with self._session_factory() as session:
            paper = await session.get(ExamPaper, paper_id)
            if paper is None:
                return
            paper.ocr_status = "FAILED"
            paper.ocr_error = message[:500]
            await session.commit()

    async def _apply_result(self, paper_id: int, data: dict) -> None:
        """把识别结果写入考卷(人工修订优先;同名冲突时保留原有姓名)"""
        async with self._session_factory() as session:
            paper = await session.get(ExamPaper, paper_id)
            if paper is None:
                return
            if paper.teacher_edited:
                # 教师已人工修订:不覆盖结果,仅标记识别结束
                paper.ocr_status = "DONE"
                paper.ocr_error = None
                await session.commit()
                return

            name = str(data.get("student_name") or "").strip()
            sid = data.get("student_id")
            if name and name != "未知" and name != paper.student_name:
                conflict = (
                    await session.execute(
                        select(ExamPaper).where(
                            ExamPaper.exam_id == paper.exam_id,
                            ExamPaper.student_name == name,
                            ExamPaper.id != paper.id,
                        )
                    )
                ).scalars().first()
                if conflict is None:
                    paper.student_name = name
                else:
                    logger.warning("考卷 %s 识别姓名 %s 与同场考试其他考卷冲突,保留原姓名", paper_id, name)
            if sid and not paper.student_id:
                paper.student_id = str(sid)
            paper.total_score = data.get("total_score")
            paper.question_results = data.get("questions") or []
            paper.ocr_status = "DONE"
            paper.ocr_error = None
            if paper.student_name and paper.student_name != "未知":
                await upsert_student(session, paper.student_name, paper.student_id)
            await session.commit()

    async def _refresh_exam_status(self, exam_id: int) -> None:
        """根据考卷状态刷新考试状态(PENDING/PROCESSING -> PROCESSING;全部完成 -> READY)"""
        async with self._session_factory() as session:
            exam = await session.get(Exam, exam_id)
            if exam is None:
                return
            statuses = {
                s for (s,) in (
                    await session.execute(select(ExamPaper.ocr_status).where(ExamPaper.exam_id == exam_id))
                ).all()
            }
            if not statuses:
                return
            if "PENDING" in statuses or "PROCESSING" in statuses:
                exam.status = "PROCESSING"
            elif "DONE" in statuses:
                exam.status = "READY"
            await session.commit()


# ---------------------------------------------------------
# 报告生成与联动摘要
# ---------------------------------------------------------
async def generate_exam_report(db: AsyncSession, exam_id: int) -> ExamReport:
    """生成(或重新生成)考试分析报告并持久化

    Raises:
        LookupError: 考试不存在
        ValueError:  暂无已识别完成的考卷
    """
    exam = await db.get(Exam, exam_id)
    if exam is None:
        raise LookupError(f"考试 {exam_id} 不存在")
    papers = (
        await db.execute(select(ExamPaper).where(ExamPaper.exam_id == exam_id))
    ).scalars().all()
    done = [p for p in papers if p.ocr_status == "DONE"]
    if not done:
        raise ValueError("暂无识别完成的考卷,无法生成报告(请先上传考卷并完成识别)")

    class_name: str | None = None
    if exam.class_id is not None:
        cls = await db.get(SchoolClass, exam.class_id)
        class_name = cls.name if cls else None

    stats = _build_exam_stats(exam, done, papers)
    markdown = _render_exam_report(exam, class_name, stats)

    report = (
        await db.execute(select(ExamReport).where(ExamReport.exam_id == exam_id))
    ).scalars().first()
    if report is None:
        report = ExamReport(exam_id=exam_id)
        db.add(report)
    report.report_markdown = markdown
    report.stats = stats
    report.generated_at = datetime.now(timezone.utc)
    exam.status = "REPORTED"
    await db.commit()
    await db.refresh(report)
    logger.info("考试 %s 报告已生成(%s 份考卷)", exam_id, stats["scored_count"])
    return report


async def build_student_exam_summary(
    db: AsyncSession, *, student_name: str | None, student_id: str | None
) -> ExamStudentSummary:
    """学生考试记录摘要(供学生画像读时聚合;无记录返回空结构)

    匹配规则(读时聚合):学号精确 **或** 姓名一致(与全系统对齐口径一致;
    考卷可能存在仅有姓名或仅有学号的录入情形)。
    """
    if not student_id and not student_name:
        return ExamStudentSummary()
    stmt = (
        select(ExamPaper, Exam)
        .join(Exam, ExamPaper.exam_id == Exam.id)
        .where(ExamPaper.ocr_status == "DONE")
    )
    matched: list = []
    if student_id:
        matched.append(ExamPaper.student_id == student_id)
    if student_name:
        matched.append(ExamPaper.student_name == student_name)
    stmt = stmt.where(or_(*matched) if len(matched) > 1 else matched[0])
    rows = (await db.execute(stmt.order_by(Exam.exam_date, Exam.id))).all()
    if not rows:
        return ExamStudentSummary()

    entries: list[ExamStudentEntry] = []
    percents: list[float] = []
    k_data: dict[str, dict] = {}
    for paper, exam in rows:
        percent: float | None = None
        full = float(exam.full_score or 100.0)
        if paper.total_score is not None:
            percent = round(paper.total_score * 100.0 / full, 1)
            percents.append(percent)
        entries.append(
            ExamStudentEntry(
                exam_id=exam.id,
                exam_name=exam.name,
                exam_date=exam.exam_date,
                total_score=paper.total_score,
                full_score=full,
                percent=percent,
                class_id=exam.class_id,
            )
        )
        for q in paper.question_results or []:
            mx, sc = _to_float(q.get("max_score")), _to_float(q.get("score"))
            ctype = q.get("canonical_type")
            if ctype and mx is not None and sc is not None and sc < mx:
                k_data.setdefault(ctype, {"count": 0})["count"] += 1

    knowledge = [
        ExamKnowledgeStat(
            canonical_type=ctype,
            label=canonical_label(ctype),
            count=entry["count"],
            student_count=1,
            tip=CATEGORY_TEACHING_TIPS.get(ctype),
        )
        for ctype, entry in sorted(k_data.items(), key=lambda kv: -kv[1]["count"])
    ]
    return ExamStudentSummary(
        paper_count=len(rows),
        average_percent=round(sum(percents) / len(percents), 1) if percents else None,
        entries=entries,
        knowledge_stats=knowledge,
    )


async def build_exam_overview(db: AsyncSession, class_id: int | None) -> ExamOverview:
    """班级考试统计概览(供班级分析联动;无数据返回空结构)"""
    stmt = select(Exam).order_by(Exam.exam_date.desc(), Exam.id.desc())
    if class_id is not None:
        stmt = stmt.where(Exam.class_id == class_id)
    exams = (await db.execute(stmt)).scalars().all()
    if not exams:
        return ExamOverview(exam_count=0, items=[])

    exam_ids = [e.id for e in exams]
    papers = (
        await db.execute(select(ExamPaper).where(ExamPaper.exam_id.in_(exam_ids)))
    ).scalars().all()
    report_ids = {
        r for (r,) in (
            await db.execute(select(ExamReport.exam_id).where(ExamReport.exam_id.in_(exam_ids)))
        ).all()
    }
    by_exam: dict[int, list[ExamPaper]] = defaultdict(list)
    for p in papers:
        by_exam[p.exam_id].append(p)

    items: list[ExamOverviewItem] = []
    for exam in exams:
        exam_papers = by_exam.get(exam.id, [])
        done = [p for p in exam_papers if p.ocr_status == "DONE" and p.total_score is not None]
        full = float(exam.full_score or 100.0)
        percents = [p.total_score * 100.0 / full for p in done]
        average = round(sum(percents) / len(percents), 1) if percents else None
        ratio = _pass_line_ratio()
        pass_rate = (
            round(sum(1 for x in percents if x >= ratio * 100.0) * 100.0 / len(percents), 1)
            if percents
            else None
        )
        items.append(
            ExamOverviewItem(
                exam_id=exam.id,
                name=exam.name,
                exam_date=exam.exam_date,
                paper_count=len(exam_papers),
                average_percent=average,
                pass_rate=pass_rate,
                report_ready=exam.id in report_ids,
            )
        )
    return ExamOverview(exam_count=len(exams), items=items)
