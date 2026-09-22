"""教学分析服务单元测试(#2 班级诊断 / #3 学生画像)

使用临时数据库构造"已完成任务 + 错题记录",验证:
- 班级诊断的聚合统计、得分分布与讲评摘要 Markdown;
- 学生画像的时间线、复现错因与平均分;
- 筛选条件(班级/批次)与空数据行为。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_models import Base, CorrectionTask, ErrorRecord, SchoolClass
from app.services.analytics_service import build_class_diagnosis, build_student_profile


@pytest.fixture
async def db_session(tmp_path):
    """临时文件数据库会话"""
    db_file = tmp_path / "analytics.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_data(session) -> None:
    """构造 2 名学生、3 个已完成任务、若干错题的测试数据"""
    cls = SchoolClass(name="高二(3)班德语")
    session.add(cls)
    await session.flush()

    def make_task(student, sid, score, class_id=None):
        return CorrectionTask(
            status="COMPLETED",
            stage="DONE",
            progress=1.0,
            student_name=student,
            student_id=sid,
            class_id=class_id,
            image_paths=["x/1.png"],
            result={
                "student_name": student,
                "overall_score": score,
                "errors": [],
                "highlights": [],
                "markdown_report": "",
                "transcribed_text": "",
                "overall_comment": "",
            },
        )

    t1 = make_task("李明", "S001", "18 / 25", class_id=cls.id)
    t2 = make_task("李明", "S001", "20 / 25", class_id=cls.id)
    t3 = make_task("王芳", "S002", "14 / 25", class_id=cls.id)
    session.add_all([t1, t2, t3])
    await session.flush()

    session.add_all(
        [
            # 李明两次都犯"动词位序"(复现错因)
            ErrorRecord(task_id=t1.id, student_name="李明", student_id="S001",
                        error_type="动词位序", canonical_type="VERB_POSITION",
                        original_text="dass er kommt", corrected_text="dass er kommt(gemacht)"),
            ErrorRecord(task_id=t1.id, student_name="李明", student_id="S001",
                        error_type="名词变格", canonical_type="CASE_DECLENSION",
                        original_text="mit meine", corrected_text="mit meiner"),
            ErrorRecord(task_id=t2.id, student_name="李明", student_id="S001",
                        error_type="动词位序", canonical_type="VERB_POSITION",
                        original_text="weil er ist", corrected_text="weil er ist(修正)"),
            # 王芳一次拼写错误
            ErrorRecord(task_id=t3.id, student_name="王芳", student_id="S002",
                        error_type="拼写", canonical_type="SPELLING",
                        original_text="Ferien", corrected_text="Ferien(拼写)"),
        ]
    )
    await session.commit()
    return cls


class TestClassDiagnosis:
    """班级共性错因诊断测试"""

    async def test_aggregation(self, db_session):
        """聚合统计:样本/学生数/错因频次/百分比"""
        await _seed_data(db_session)
        diag = await build_class_diagnosis(db_session)

        assert diag.task_count == 3
        assert diag.student_count == 2
        assert diag.error_total == 4
        # 动词位序出现 2 次,居首位
        top = diag.category_stats[0]
        assert top.category == "VERB_POSITION"
        assert top.label == "动词位序"
        assert top.count == 2
        assert top.student_count == 1
        assert top.task_count == 2
        assert top.percentage == 50.0
        assert top.examples  # 含典型错例

    async def test_average_score_and_distribution(self, db_session):
        """平均分(同基准)与得分分布"""
        await _seed_data(db_session)
        diag = await build_class_diagnosis(db_session)

        # (18 + 20 + 14) / 3 = 17.3,基准一致时按原始分
        assert diag.average_score == pytest.approx(17.3, abs=0.05)
        assert diag.score_basis == "满分 25"
        assert sum(b.count for b in diag.score_distribution) == 3

    async def test_teaching_summary_markdown(self, db_session):
        """讲评摘要 Markdown 结构"""
        await _seed_data(db_session)
        diag = await build_class_diagnosis(db_session)

        summary = diag.teaching_summary_markdown
        assert "# 班级讲评摘要" in summary
        assert "3 份作文" in summary
        assert "### 高频错因" in summary
        assert "**1. 动词位序**" in summary
        assert "### 讲评建议顺序" in summary
        assert "### 得分分布" in summary

    async def test_filter_by_class(self, db_session):
        """按班级筛选(存在该班任务时统计正常)"""
        cls = await _seed_data(db_session)
        diag = await build_class_diagnosis(db_session, class_id=cls.id)
        assert diag.task_count == 3
        assert diag.filters["class_name"] == "高二(3)班德语"

    async def test_filter_unknown_class_returns_empty(self, db_session):
        """不存在的班级 ID 返回空样本(不报错)"""
        await _seed_data(db_session)
        diag = await build_class_diagnosis(db_session, class_id=99999)
        assert diag.task_count == 0
        assert "暂无已完成" in diag.teaching_summary_markdown


class TestStudentProfile:
    """学生画像与错题本测试"""

    async def test_profile_by_student_id(self, db_session):
        """按学号生成画像:时间线/复现错因/平均分"""
        await _seed_data(db_session)
        profile = await build_student_profile(db_session, student_id="S001")

        assert profile is not None
        assert profile.student_name == "李明"
        assert profile.task_count == 2
        assert profile.error_total == 3
        assert profile.average_score == pytest.approx(19.0, abs=0.05)
        # 复现错因:动词位序在两次任务中都出现
        assert "动词位序" in profile.recurring
        # 时间线按时间正序,含得分与错误数
        assert [t.overall_score for t in profile.timeline] == ["18 / 25", "20 / 25"]
        assert profile.timeline[0].top_category_label == "动词位序"

    async def test_profile_by_name(self, db_session):
        """按姓名查询(无学号学生场景)"""
        await _seed_data(db_session)
        profile = await build_student_profile(db_session, name="王芳")
        assert profile is not None
        assert profile.student_id == "S002"
        assert profile.task_count == 1
        assert profile.recurring == []  # 仅一次出现,不构成复现

    async def test_profile_not_found(self, db_session):
        """不存在的学生返回 None"""
        await _seed_data(db_session)
        assert await build_student_profile(db_session, student_id="NOPE") is None
        assert await build_student_profile(db_session) is None  # 未提供任何条件
