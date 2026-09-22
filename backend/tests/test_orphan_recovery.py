"""孤儿任务自愈集成测试

验证 CorrectionService.recover_orphaned_tasks 的恢复逻辑:
- 仅 PROCESSING 状态的任务被重置为 PENDING 并返回;
- WAITING_REVIEW(人工复核中)与 COMPLETED 的任务不受影响。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models.db_models import Base, CorrectionTask
from app.services.correction_service import CorrectionService


@pytest.fixture
async def session_factory(tmp_path):
    """临时文件数据库 + 会话工厂(内存库在多次连接间不共享,故用文件库)"""
    db_file = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class TestOrphanRecovery:
    """孤儿任务自愈测试"""

    async def test_recovers_processing_tasks_only(self, session_factory):
        """PROCESSING 任务被恢复,其他状态不受影响"""
        async with session_factory() as session:
            session.add_all(
                [
                    CorrectionTask(status="PROCESSING", stage="GRADING", progress=0.6,
                                   student_name="A", image_paths=["a/1.png"]),
                    CorrectionTask(status="WAITING_REVIEW", stage="OCR", progress=0.5,
                                   student_name="B", image_paths=["b/1.png"]),
                    CorrectionTask(status="COMPLETED", stage="DONE", progress=1.0,
                                   student_name="C", image_paths=["c/1.png"]),
                ]
            )
            await session.commit()

        service = CorrectionService(session_factory=session_factory, settings=Settings())
        recovered = await service.recover_orphaned_tasks()

        assert len(recovered) == 1  # 仅那个 PROCESSING 任务

        async with session_factory() as session:
            from sqlalchemy import select

            tasks = (await session.execute(select(CorrectionTask))).scalars().all()
            by_status = {t.status: t for t in tasks}
            assert "PROCESSING" not in by_status  # 已被重置
            recovered_task = by_status["PENDING"]
            assert recovered_task.stage == "UPLOADED"
            assert recovered_task.progress == 0.0
            assert recovered_task.error_message is None
            # 复核中/已完成任务未被改动
            assert by_status["WAITING_REVIEW"].stage == "OCR"
            assert by_status["COMPLETED"].progress == 1.0

    async def test_no_orphans_returns_empty(self, session_factory):
        """无中断任务时返回空列表"""
        service = CorrectionService(session_factory=session_factory, settings=Settings())
        assert await service.recover_orphaned_tasks() == []
