"""考试统计模块测试(OCR 输出解析 / 报告聚合 / 联动摘要 / 孤儿自愈)"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_models import Base, Exam, ExamPaper, ExamReport, SchoolClass
from app.services.exam_service import (
    ExamService,
    _mock_exam_payload,
    build_exam_overview,
    build_student_exam_summary,
    generate_exam_report,
    parse_exam_output,
)


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'exam.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class TestParseExamOutput:
    """视觉模型输出容错解析"""

    def test_fenced_and_listening_filtered(self):
        raw = (
            "```json\n"
            '{"student_name": "李明", "student_id": "20260101", "total_score": 24,'
            ' "questions": ['
            '  {"no": "1", "part": "听力", "max_score": 5, "score": 5, "knowledge_tag": null},'
            '  {"no": "2", "part": "Hören Teil 2", "max_score": 5, "score": 3},'
            '  {"no": "3", "part": "语法", "max_score": 5, "score": 4, "knowledge_tag": "动词位序"},'
            '  {"no": "", "part": "语法", "max_score": 5, "score": 5}'
            "]}\n```"
        )
        data = parse_exam_output(raw)
        assert data["student_name"] == "李明"
        assert data["total_score"] == 24
        assert [q["no"] for q in data["questions"]] == ["3"]  # 听力与缺题号条目被剔除
        assert data["questions"][0]["canonical_type"] == "VERB_POSITION"

    def test_total_fallback_sums_scores(self):
        raw = '{"student_name": "未知", "questions": [{"no": "1", "max_score": 5, "score": 4}, {"no": "2", "max_score": 5, "score": 3.5}]}'
        data = parse_exam_output(raw)
        assert data["total_score"] == 7.5
        assert data["student_id"] is None

    def test_mock_payload_deterministic(self):
        first = _mock_exam_payload(1, 2)
        second = _mock_exam_payload(1, 2)
        assert first == second
        assert all(q["part"] != "听力" for q in first["questions"])
        assert first["total_score"] is not None


async def _seed_exam(db):
    cls = SchoolClass(name="高二(3)班")
    db.add(cls)
    await db.flush()
    exam = Exam(class_id=cls.id, name="期中考试", exam_date=__import__("datetime").date(2026, 9, 10), full_score=100)
    db.add(exam)
    await db.flush()
    papers = [
        ExamPaper(
            exam_id=exam.id, student_name="李明", student_id="S001", ocr_status="DONE", total_score=85.0,
            question_results=[
                {"no": "1", "part": "语法", "max_score": 10, "score": 9, "knowledge_tag": "动词位序", "canonical_type": "VERB_POSITION"},
                {"no": "2", "part": "阅读", "max_score": 20, "score": 12, "knowledge_tag": "词汇选择", "canonical_type": "VOCABULARY"},
            ],
        ),
        ExamPaper(
            exam_id=exam.id, student_name="王芳", student_id="S002", ocr_status="DONE", total_score=55.0,
            question_results=[
                {"no": "1", "part": "语法", "max_score": 10, "score": 5, "knowledge_tag": "动词位序", "canonical_type": "VERB_POSITION"},
                {"no": "2", "part": "阅读", "max_score": 20, "score": 18, "knowledge_tag": None, "canonical_type": None},
            ],
        ),
        ExamPaper(exam_id=exam.id, student_name="赵强", ocr_status="PENDING", image_paths=["a.png"]),
    ]
    db.add_all(papers)
    await db.commit()
    return exam, cls


class TestReportGeneration:
    """报告聚合(统计正确性 + Markdown + 持久化)"""

    async def test_generate_report_stats_and_markdown(self, db):
        exam, _cls = await _seed_exam(db)
        report = await generate_exam_report(db, exam.id)

        stats = report.stats
        assert stats["scored_count"] == 2
        assert stats["average"] == 70.0
        assert stats["pass_rate"] == 50.0  # 85 及格 / 55 不及格
        assert stats["score_distribution"] and sum(b["count"] for b in stats["score_distribution"]) == 2
        # 弱题在前:第 2 题(得分率 (12+18)/40=75%)弱于第 1 题(14/20=70%? 实际第1题 14/20=70% 更弱)
        rates = [q["score_rate"] for q in stats["question_stats"]]
        assert rates == sorted(rates)
        # 知识点失分:动词位序 2 次(两名学生均失分)
        knowledge = {k["canonical_type"]: k for k in stats["knowledge_stats"]}
        assert knowledge["VERB_POSITION"]["count"] == 2
        assert knowledge["VERB_POSITION"]["student_count"] == 2

        # Markdown 结构
        assert "# 考试分析报告 - 期中考试" in report.report_markdown
        assert "听力部分不纳入" in report.report_markdown
        assert "逐题分析" in report.report_markdown and "知识点失分" in report.report_markdown
        assert "识别未完成" in report.report_markdown  # 赵强待识别名单

        # 状态与持久化(重新生成覆盖同一行)
        await db.refresh(exam)
        assert exam.status == "REPORTED"
        count = len((await db.execute(select(ExamReport))).scalars().all())
        assert count == 1
        await generate_exam_report(db, exam.id)
        count = len((await db.execute(select(ExamReport))).scalars().all())
        assert count == 1

    async def test_no_done_papers_rejected(self, db):
        exam, _ = await _seed_exam(db)
        for paper in (await db.execute(select(ExamPaper))).scalars().all():
            paper.ocr_status = "PENDING"
        await db.commit()
        with pytest.raises(ValueError):
            await generate_exam_report(db, exam.id)


class TestLinkageSummaries:
    """画像/班级分析联动摘要"""

    async def test_student_exam_summary(self, db):
        await _seed_exam(db)
        summary = await build_student_exam_summary(db, student_name="李明", student_id="S001")
        assert summary.paper_count == 1
        assert summary.average_percent == 85.0
        assert summary.entries[0].exam_name == "期中考试"

    async def test_exam_overview(self, db):
        exam, cls = await _seed_exam(db)
        await generate_exam_report(db, exam.id)
        overview = await build_exam_overview(db, cls.id)
        assert overview.exam_count == 1
        item = overview.items[0]
        assert item.paper_count == 3 and item.average_percent == 70.0
        assert item.pass_rate == 50.0 and item.report_ready is True

    async def test_empty_overview(self, db):
        overview = await build_exam_overview(db, None)
        assert overview.exam_count == 0 and overview.items == []


class TestOrphanRecovery:
    """考卷孤儿自愈"""

    async def test_recover_processing_papers(self, db, tmp_path):
        exam, _ = await _seed_exam(db)
        paper = (await db.execute(select(ExamPaper).where(ExamPaper.ocr_status == "PENDING"))).scalars().first()
        paper.ocr_status = "PROCESSING"
        await db.commit()

        factory = async_sessionmaker(db.bind, expire_on_commit=False)
        service = ExamService(factory, max_concurrent=1)
        recovered = await service.recover_orphan_papers()
        assert paper.id in recovered
        refreshed = await db.get(ExamPaper, paper.id)
        await db.refresh(refreshed)
        assert refreshed.ocr_status == "PENDING"
