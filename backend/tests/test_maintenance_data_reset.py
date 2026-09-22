"""数据维护测试(一键清除所有数据 / 一键恢复所有示例数据)

覆盖:
1. 两个维护端点均需显式确认(confirm=true),否则 400;
2. 清除:全部业务表清空、uploads 文件删除、app_settings(配置/密钥)保留、
   示范学习内存缓存同步失效;
3. 恢复:写入完整示例数据(班级/花名册/学生/批改任务与报告/错因/考试与报告/
   台账/练习卷/风格画像/图片),含 1 个待人工复核任务;重复执行结果一致;
4. 与「清除演示数据」的区分:后者仅删演示模式数据(既有测试 test_maintenance_clear_demo 保持通过);
5. 写入后的数据可被统计层直接消费(排行榜/分布不再为空)。

安全约束(必须遵守):
- 路由层用 `settings.upload_path` 定位文件目录,故本模块 autouse fixture 强制把
  `settings.upload_dir` 重定向到临时目录,并断言 upload_path 落在 tmp_path 内 ——
  测试绝不允许触碰 backend/data/uploads 真实目录;
- 审计写入被拦截(不触碰真实 audit.log);示范学习缓存用例后还原。
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import routes_maintenance
from app.config import settings
from app.models.db_models import (
    AppSetting,
    Base,
    ClassMergeLog,
    ClassRoster,
    CorrectionTask,
    ErrorRecord,
    Exam,
    ExamPaper,
    ExamReport,
    HandwritingSample,
    HomeworkItem,
    HomeworkRecord,
    PracticeSheet,
    SchoolClass,
    Student,
    StyleProfile,
)
from app.services import demo_data_service, statistics_service
from app.services import style_learning_service as style_service

#: 清空覆盖的全部业务表(CLEAR_ORDER 口径;与 app_settings 无关)
BUSINESS_MODELS = tuple(model for _key, model in demo_data_service.CLEAR_ORDER)


@pytest.fixture
async def db(tmp_path):
    """独立 SQLite 会话(示例数据落库)"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'demo.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def uploads(tmp_path):
    """独立的 uploads 目录"""
    return tmp_path / "uploads"


@pytest.fixture(autouse=True)
def redirect_upload_dir(monkeypatch, uploads):
    """把 settings.upload_dir 重定向到临时目录(路由层用 settings.upload_path 定位文件)

    安全护栏:断言 upload_path 落在临时目录内,确保测试永不触碰真实数据目录。
    """
    monkeypatch.setattr(settings, "upload_dir", str(uploads))
    assert settings.upload_path == uploads.resolve(), "upload_path 未重定向到临时目录,拒绝执行"


@pytest.fixture(autouse=True)
def audit_recorder(monkeypatch):
    """拦截审计写入并记录事件(不触碰真实 audit.log)"""
    events: list[tuple[str, str]] = []

    async def _fake(event: str, *, ok: bool = True, detail: str = "", source: str = "api") -> None:
        events.append((event, detail))

    monkeypatch.setattr(routes_maintenance, "audit", _fake)
    return events


@pytest.fixture(autouse=True)
def restore_style_cache():
    """快照并还原示范学习内存缓存(模块级全局)"""
    original = style_service._active_text  # noqa: SLF001 —— 测试隔离用
    yield
    style_service._active_text = original  # noqa: SLF001


async def _count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar() or 0


class TestConfirmGuard:
    """两个维护动作均需显式确认(不可恢复)"""

    async def test_clear_all_requires_confirm(self, db, uploads):
        with pytest.raises(Exception):  # HTTPException 400
            await routes_maintenance.clear_all_data(confirm=False, db=db)

    async def test_seed_requires_confirm(self, db, uploads):
        with pytest.raises(Exception):  # HTTPException 400
            await routes_maintenance.seed_demo_data(confirm=False, db=db)


class TestClearAllData:
    """一键清除所有数据"""

    async def test_clear_all_removes_business_data_and_files(self, db, uploads, audit_recorder):
        counts = await demo_data_service.seed_demo_data(db, uploads)
        # 追加系统级元数据与边缘业务数据(清空后前者保留、后者删除)
        db.add(AppSetting(key="keep_me", value="1"))
        db.add(ClassMergeLog(source_class_id=1, source_class_name="A", target_class_id=2, target_class_name="B"))
        db.add(HandwritingSample(class_id=1, image_path="x.jpg", status="pending_bind"))
        await db.commit()

        result = await routes_maintenance.clear_all_data(confirm=True, db=db)

        assert counts["tasks"] == 24  # 写入的「已完成任务」计数
        assert result["tasks"] == 25  # 清除的实际行数:24 已完成 + 1 待复核
        assert result["errors"] == counts["errors"]
        assert result["classes"] == 2 and result["students"] == 12
        assert result["exams"] == 1 and result["exam_papers"] == 6
        assert result["files"] == counts["files"]
        assert result["bytes"] > 0
        for model in BUSINESS_MODELS:
            assert await _count(db, model) == 0, model.__name__
        # 系统配置与密钥保留
        assert await db.get(AppSetting, "keep_me") is not None
        # 上传文件全部删除(目录内不残留任何文件与子目录)
        assert [p for p in uploads.rglob("*")] == []
        # 审计事件与示范学习缓存同步
        assert any(event == "maintenance.clear_all_data" for event, _ in audit_recorder)
        assert style_service.get_active_style_text() == ""

    async def test_clear_all_on_empty_db_is_noop(self, db, uploads, audit_recorder):
        result = await routes_maintenance.clear_all_data(confirm=True, db=db)
        assert result["tasks"] == 0 and result["files"] == 0
        assert await _count(db, CorrectionTask) == 0
        assert any(event == "maintenance.clear_all_data" for event, _ in audit_recorder)


class TestSeedDemoData:
    """一键恢复所有示例数据(先清空再写入)"""

    async def test_seed_writes_full_dataset(self, db, uploads, audit_recorder):
        counts = await routes_maintenance.seed_demo_data(confirm=True, db=db)

        assert counts["classes"] == 2 and counts["roster"] == 12 and counts["students"] == 12
        assert counts["tasks"] == 24 and counts["errors"] > 0
        assert counts["exams"] == 1 and counts["exam_papers"] == 6
        assert counts["ledger_items"] == 6 and counts["ledger_records"] > 0
        assert counts["practice_sheets"] == 1 and counts["style_profiles"] == 1
        assert counts["files"] == 25

        assert await _count(db, SchoolClass) == 2
        assert await _count(db, ClassRoster) == 12
        assert await _count(db, Student) == 12
        assert await _count(db, CorrectionTask) == 25  # 24 已完成 + 1 待人工复核
        assert await _count(db, ErrorRecord) == counts["errors"]
        assert await _count(db, ExamReport) == 1
        assert await _count(db, HomeworkItem) == 6
        assert await _count(db, PracticeSheet) == 1
        assert await _count(db, StyleProfile) == 1
        assert any(event == "maintenance.seed_demo_data" for event, _ in audit_recorder)

        # 任务与报告同构:已完成任务带渲染后的 Markdown 报告;错因已标准化
        completed = (
            await db.execute(select(CorrectionTask).where(CorrectionTask.status == "COMPLETED"))
        ).scalars().all()
        assert len(completed) == 24
        for task in completed:
            assert task.result and task.result.get("markdown_report")
            assert task.image_paths and (uploads / task.image_paths[0]).is_file()
        waiting = (
            await db.execute(select(CorrectionTask).where(CorrectionTask.status == "WAITING_REVIEW"))
        ).scalars().all()
        assert len(waiting) == 1 and waiting[0].ocr_result and waiting[0].result is None
        canon = {row for (row,) in (await db.execute(select(ErrorRecord.canonical_type))).all()}
        assert "OTHER" not in canon

        # 考试报告与练习卷内容齐备
        report = (await db.execute(select(ExamReport))).scalars().first()
        assert report is not None and "考试分析报告" in report.report_markdown
        exam = (await db.execute(select(Exam))).scalars().first()
        assert exam is not None and exam.status == "REPORTED"
        sheet = (await db.execute(select(PracticeSheet))).scalars().first()
        assert sheet is not None and sheet.worksheet_markdown and sheet.answer_markdown

        # 示范学习画像生效(注入缓存)
        profile = (await db.execute(select(StyleProfile))).scalars().first()
        assert profile is not None and profile.status == "active"
        assert style_service.get_active_style_text()

        # 统计层可直接消费(排行榜/分布非空)
        rankings = await statistics_service.build_rankings(db)
        assert rankings.get("combined")
        distribution = await statistics_service.build_distribution(db, source="correction")
        assert distribution.get("average") is not None

    async def test_seed_replaces_existing_data_and_keeps_settings(self, db, uploads):
        """重复执行结果一致;写入前先清空(既有业务数据被替换,app_settings 保留)"""
        db.add(AppSetting(key="keep_me", value="1"))
        db.add(
            CorrectionTask(
                student_name="老数据", student_id="999", status="COMPLETED", image_paths=["old.jpg"],
            )
        )
        await db.commit()

        first = await demo_data_service.seed_demo_data(db, uploads)
        second = await demo_data_service.seed_demo_data(db, uploads)

        assert first == second  # 确定性数据:两次结果一致
        assert await _count(db, CorrectionTask) == 25
        names = (await db.execute(select(CorrectionTask.student_name))).all()
        assert ("老数据",) not in names
        assert await db.get(AppSetting, "keep_me") is not None
