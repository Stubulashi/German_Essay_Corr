"""设置中心扩展项测试(及格线口径 / 工作台默认值 / 学生版显示项)

覆盖:
- 注册表与视图暴露(教学与报告分组、探索项徽标);
- 更新写回与热生效(与 .env 记录);
- 及格线口径真实生效:统计分布 / 考试报告跟随设置(默认 60 保持历史行为);
- 学生版显示项:显示得分 / 隐藏练习重点(默认与历史行为一致);
- 健康检查暴露工作台默认值。
"""

import pytest

from app.api import routes_health
from app.config import settings
from app.models.db_models import Exam, ExamPaper
from app.models.schemas import DetailLevel, EssayCorrectionResult, GradingStandard, SettingsUpdateRequest
from app.services import exam_service, settings_service, statistics_service
from app.services.report_renderer import render_student_report


@pytest.fixture(autouse=True)
def restore_settings():
    """快照并还原全部注册表字段(隔离用例间的热更新)"""
    attrs = [spec.settings_attr for spec in settings_service.FIELDS]
    snapshot = {attr: getattr(settings, attr) for attr in attrs}
    yield
    for attr, value in snapshot.items():
        setattr(settings, attr, value)


@pytest.fixture(autouse=True)
def env_writer(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(
        settings_service.env_manager, "write_env_atomic",
        lambda path, updates: recorded.append(dict(updates)),
    )
    return recorded


def _result(score: str = "18 / 25") -> EssayCorrectionResult:
    return EssayCorrectionResult.model_validate(
        {
            "student_name": "李明",
            "student_id": "S001",
            "transcribed_text": "Text mit Fehlern.",
            "overall_score": score,
            "overall_comment": "ok",
            "errors": [
                {
                    "original_text": "Fehlern",
                    "corrected_text": "Fehlern(修正)",
                    "error_type": "拼写",
                    "canonical_type": "SPELLING",
                    "explanation": None,
                }
            ],
            "highlights": [],
            "markdown_report": "# 报告",
        }
    )


class TestRegistry:
    """注册表与视图"""

    def test_new_fields_registered(self):
        keys = {spec.key for spec in settings_service.FIELDS}
        assert {
            "DEFAULT_GRADING_STANDARD",
            "DEFAULT_DETAIL_LEVEL",
            "SCORE_PASS_LINE",
            "STUDENT_REPORT_SHOW_SCORE",
            "STUDENT_REPORT_SHOW_TIPS",
        } <= keys
        groups = {spec.group for spec in settings_service.FIELDS}
        assert "teaching" in groups

    def test_view_exposes_teaching_group_and_flags(self):
        view = settings_service.build_view()
        group = next(g for g in view.groups if g.id == "teaching")
        fields = {f.key: f for f in group.fields}
        assert fields["SCORE_PASS_LINE"].value == settings.score_pass_line
        assert fields["STUDENT_REPORT_SHOW_SCORE"].exploratory
        assert fields["SCORE_PASS_LINE"].min_value == 0


class TestApplyAndHotEffect:
    """更新写回与热生效"""

    async def test_score_pass_line_applied(self, db=None):
        # 该用例只依赖设置热生效与 env 记录,复用全局 settings
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        import tempfile
        from pathlib import Path

        from app.models.db_models import Base

        tmp = Path(tempfile.mkdtemp()) / "s.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp.as_posix()}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            result = await settings_service.apply_updates(
                session, SettingsUpdateRequest(score_pass_line=75)
            )
        await engine.dispose()
        assert settings.score_pass_line == 75
        assert "SCORE_PASS_LINE" in result.applied

    def test_statistics_pass_rate_follows_setting(self, monkeypatch):
        monkeypatch.setattr(settings, "score_pass_line", 60)
        assert statistics_service._percent_summary([60.0, 75.0])["pass_rate"] == 100.0
        monkeypatch.setattr(settings, "score_pass_line", 70)
        assert statistics_service._percent_summary([60.0, 75.0])["pass_rate"] == 50.0

    def test_exam_report_pass_line_follows_setting(self, monkeypatch):
        exam = Exam(name="期中", full_score=100.0)
        papers = [ExamPaper(exam_id=1, student_name="李明", ocr_status="DONE", total_score=60.0)]
        monkeypatch.setattr(settings, "score_pass_line", 60)
        stats = exam_service._build_exam_stats(exam, papers, papers)
        assert stats["pass_rate"] == 100.0 and stats["pass_line"] == 60.0
        monkeypatch.setattr(settings, "score_pass_line", 70)
        stats = exam_service._build_exam_stats(exam, papers, papers)
        assert stats["pass_rate"] == 0.0 and stats["pass_line"] == 70.0


class TestStudentReportToggles:
    """学生版显示项(探索项)"""

    def test_default_keeps_history_behavior(self):
        report = render_student_report(_result(), GradingStandard.GAOKAO, DetailLevel.MEDIUM)
        assert "本次得分" not in report  # 默认弱化分数
        assert "### 三、下一步练习重点" in report  # 默认展示练习重点

    def test_show_score_toggle(self):
        report = render_student_report(
            _result(), GradingStandard.GAOKAO, DetailLevel.MEDIUM, show_score=True
        )
        assert "**本次得分:** 18 / 25" in report

    def test_hide_tips_toggle(self):
        report = render_student_report(
            _result(), GradingStandard.GAOKAO, DetailLevel.MEDIUM, show_tips=False
        )
        assert "下一步练习重点" not in report
        assert "### 四、教师寄语" in report  # 其余结构不受影响

    def test_settings_switch_effective_without_explicit_args(self, monkeypatch):
        monkeypatch.setattr(settings, "student_report_show_score", True)
        monkeypatch.setattr(settings, "student_report_show_tips", False)
        report = render_student_report(_result(), GradingStandard.GAOKAO, DetailLevel.MEDIUM)
        assert "**本次得分:**" in report
        assert "下一步练习重点" not in report


class TestHealthExposure:
    """健康检查暴露工作台默认值"""

    async def test_health_contains_workbench_defaults(self):
        response = await routes_health.health()
        assert response.default_grading_standard == settings.default_grading_standard
        assert response.default_detail_level == settings.default_detail_level
