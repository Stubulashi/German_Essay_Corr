"""跨模块联动测试(学生画像三源聚合 / 班级分析联动参数 / 向后兼容)"""

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_models import (
    Base,
    CorrectionTask,
    Exam,
    ExamPaper,
    HomeworkItem,
    HomeworkRecord,
    SchoolClass,
)
from app.services.analytics_service import build_class_diagnosis, build_student_profile


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'link.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, *, with_extra: bool = True):
    cls = SchoolClass(name="高二(3)班")
    db.add(cls)
    await db.flush()
    db.add(
        CorrectionTask(
            class_id=cls.id, student_name="李明", student_id="S001", status="COMPLETED",
            stage="DONE", progress=1.0, image_paths=["a.png"],
            result={
                "student_name": "李明", "student_id": "S001", "transcribed_text": "text",
                "overall_score": "18 / 25", "overall_comment": "ok",
                "errors": [{"original_text": "x", "corrected_text": "y", "error_type": "动词位序",
                            "canonical_type": "VERB_POSITION", "explanation": None}],
                "highlights": [], "markdown_report": "# 报告",
            },
        )
    )
    if with_extra:
        exam = Exam(class_id=cls.id, name="期中考试", exam_date=date(2026, 9, 10), full_score=100)
        db.add(exam)
        await db.flush()
        db.add(
            ExamPaper(exam_id=exam.id, student_name="李明", student_id="S001",
                      ocr_status="DONE", total_score=80.0,
                      question_results=[{"no": "1", "max_score": 10, "score": 8,
                                         "knowledge_tag": "动词位序", "canonical_type": "VERB_POSITION"}]),
        )
        item = HomeworkItem(class_id=cls.id, name="书面作业", scoring_mode="LEVEL", config={})
        db.add(item)
        await db.flush()
        db.add(
            HomeworkRecord(item_id=item.id, class_id=cls.id, student_name="李明", value="A",
                           score_value=95.0, record_date=date(2026, 9, 1)),
        )
    await db.commit()
    return cls


class TestStudentProfileLinkage:
    """学生画像:作业台账 + 考试记录读时聚合"""

    async def test_profile_includes_three_sources(self, db):
        await _seed(db, with_extra=True)
        profile = await build_student_profile(db, student_id="S001")
        assert profile is not None
        # 台账摘要
        assert profile.homework_summary is not None
        assert profile.homework_summary.record_count == 1
        assert profile.homework_summary.items[0].name == "书面作业"
        # 考试摘要
        assert profile.exam_summary is not None
        assert profile.exam_summary.average_percent == 80.0
        assert profile.exam_summary.knowledge_stats[0].label == "动词位序"
        # 时间线含两类来源,且按时间升序
        kinds = [item.kind for item in profile.timeline]
        assert "correction" in kinds and "exam" in kinds
        dates = [item.created_at for item in profile.timeline]
        assert dates == sorted(dates)
        exam_item = next(item for item in profile.timeline if item.kind == "exam")
        assert exam_item.exam_id is not None and exam_item.overall_score == "80 / 100"

    async def test_profile_without_extra_sources_backward_compatible(self, db):
        await _seed(db, with_extra=False)
        profile = await build_student_profile(db, student_id="S001")
        assert profile is not None
        assert profile.homework_summary is None
        assert profile.exam_summary is None
        assert [item.kind for item in profile.timeline] == ["correction"]


class TestClassDiagnosisLinkage:
    """班级分析:with_ledger / with_exam 参数"""

    async def test_default_returns_no_overviews(self, db):
        cls = await _seed(db, with_extra=True)
        diagnosis = await build_class_diagnosis(db, class_id=cls.id)
        assert diagnosis.ledger_overview is None
        assert diagnosis.exam_overview is None
        assert diagnosis.task_count == 1  # 既有统计口径不变

    async def test_with_flags_returns_overviews(self, db):
        cls = await _seed(db, with_extra=True)
        diagnosis = await build_class_diagnosis(db, class_id=cls.id, with_ledger=True, with_exam=True)
        assert diagnosis.ledger_overview is not None
        assert diagnosis.ledger_overview.record_count == 1
        assert diagnosis.exam_overview is not None
        assert diagnosis.exam_overview.exam_count == 1
        assert diagnosis.exam_overview.items[0].average_percent == 80.0

    async def test_empty_class_with_flags(self, db):
        cls = await _seed(db, with_extra=False)
        # 换一个完全没有数据的班级
        empty = SchoolClass(name="空班级")
        db.add(empty)
        await db.commit()
        diagnosis = await build_class_diagnosis(db, class_id=empty.id, with_ledger=True, with_exam=True)
        assert diagnosis.task_count == 0
        assert diagnosis.ledger_overview is not None and diagnosis.ledger_overview.record_count == 0
        assert diagnosis.exam_overview is not None and diagnosis.exam_overview.exam_count == 0
