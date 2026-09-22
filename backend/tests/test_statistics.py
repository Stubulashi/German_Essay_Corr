"""统一统计层测试(三源聚合 / 分布 / 排行 / 趋势 / CSV / 空数据)"""

from datetime import date, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_models import (
    Base,
    ClassRoster,
    CorrectionTask,
    Exam,
    ExamPaper,
    HomeworkItem,
    HomeworkRecord,
    SchoolClass,
)
from app.services import statistics_service


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'stats.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db):
    """三源数据:批改(李明 18/25)· 考试(李明 80/100、王芳 60/100)· 台账(李明 A、王芳 B)"""
    cls = SchoolClass(name="高二(3)班")
    db.add(cls)
    await db.flush()
    db.add_all([
        ClassRoster(class_id=cls.id, name="李明", student_id="S001"),
        ClassRoster(class_id=cls.id, name="王芳", student_id="S002"),
    ])
    db.add(
        CorrectionTask(
            class_id=cls.id, student_name="李明", student_id="S001", status="COMPLETED",
            stage="DONE", progress=1.0, image_paths=["a.png"],
            result={"overall_score": "18 / 25", "errors": [], "highlights": [], "transcribed_text": "x",
                    "overall_comment": "", "student_name": "李明", "markdown_report": ""},
        )
    )
    exam = Exam(class_id=cls.id, name="期中考试", exam_date=date(2026, 9, 10), full_score=100)
    db.add(exam)
    await db.flush()
    db.add_all([
        ExamPaper(exam_id=exam.id, student_name="李明", student_id="S001", ocr_status="DONE", total_score=80.0),
        ExamPaper(exam_id=exam.id, student_name="王芳", student_id="S002", ocr_status="DONE", total_score=60.0),
    ])
    item = HomeworkItem(class_id=cls.id, name="书面作业", scoring_mode="LEVEL", config={})
    db.add(item)
    await db.flush()
    db.add_all([
        HomeworkRecord(item_id=item.id, class_id=cls.id, student_name="李明", value="A",
                       score_value=95.0, record_date=date(2026, 9, 1)),
        HomeworkRecord(item_id=item.id, class_id=cls.id, student_name="王芳", value="B",
                       score_value=85.0, record_date=date(2026, 9, 2)),
    ])
    await db.commit()
    return cls


class TestGradebook:
    """成绩总表"""

    async def test_cross_source_aggregation(self, db):
        cls = await _seed(db)
        gradebook = await statistics_service.build_gradebook(db, class_id=cls.id)
        rows = {row["student_name"]: row for row in gradebook["students"]}
        assert set(rows) == {"李明", "王芳"}

        liming = rows["李明"]
        assert liming["record_count"] == 3
        assert liming["source_average"]["correction"] == 72.0  # 18/25
        assert liming["source_average"]["exam"] == 80.0
        assert liming["source_average"]["ledger"] == 95.0
        assert liming["overall_percent"] == 82.3  # (72+80+95)/3
        # 评估序列按日期升序,含三类来源
        assert [e["source"] for e in liming["entries"]] == ["ledger", "exam", "correction"]

        wangfang = rows["王芳"]
        assert wangfang["source_average"]["exam"] == 60.0
        assert wangfang["overall_percent"] == 72.5  # (60+85)/2

    async def test_sources_filter(self, db):
        cls = await _seed(db)
        gradebook = await statistics_service.build_gradebook(db, class_id=cls.id, sources={"ledger"})
        rows = {row["student_name"]: row for row in gradebook["students"]}
        assert set(rows["李明"]["source_average"]) == {"ledger"}

    async def test_empty_gradebook(self, db):
        gradebook = await statistics_service.build_gradebook(db, class_id=None)
        assert gradebook["student_count"] == 0
        assert gradebook["students"] == []

    async def test_csv_export(self, db):
        cls = await _seed(db)
        gradebook = await statistics_service.build_gradebook(db, class_id=cls.id)
        csv_text = statistics_service.build_gradebook_csv(gradebook)
        assert "归一百分比" in csv_text
        assert "李明" in csv_text and "考试" in csv_text


class TestDistribution:
    """分布统计"""

    async def test_exam_distribution(self, db):
        cls = await _seed(db)
        result = await statistics_service.build_distribution(db, source="exam", class_id=cls.id)
        assert result["count"] == 2
        assert result["average"] == 70.0
        assert result["highest"] == 80.0 and result["lowest"] == 60.0
        assert result["pass_rate"] == 100.0  # 及格线为 >=60,60 分恰好及格
        assert sum(b["count"] for b in result["buckets"]) == 2

    async def test_unknown_source_rejected(self, db):
        with pytest.raises(ValueError):
            await statistics_service.build_distribution(db, source="guess")


class TestRankingsAndTrends:
    """排行与趋势"""

    async def test_rankings(self, db):
        cls = await _seed(db)
        result = await statistics_service.build_rankings(db, class_id=cls.id)
        assert result["combined"][0]["student_name"] == "李明"  # 82.3 > 72.5
        assert result["exam_ranking"]["exam_name"] == "期中考试"
        assert result["exam_ranking"]["rows"][0]["student_name"] == "李明"
        # 台账覆盖率:1 个进行中的登记项,两名学生均覆盖
        assert {row["student_name"] for row in result["coverage"]} == {"李明", "王芳"}
        assert all(row["coverage"] == 100.0 for row in result["coverage"])

    async def test_trends_monthly(self, db):
        cls = await _seed(db)
        result = await statistics_service.build_trends(db, class_id=cls.id, student="李明")
        assert result["student_selected"]["student_name"] == "李明"
        assert len(result["student_entries"]) == 3
        assert result["monthly"] and all(m["count"] >= 1 for m in result["monthly"])
