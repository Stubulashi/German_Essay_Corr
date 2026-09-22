"""清除演示数据测试(仅删演示标记任务/错因;二次确认守卫;真实数据零触碰)"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import routes_maintenance
from app.models.db_models import Base, CorrectionTask, ErrorRecord
from app.pipelines.mock import MOCK_STUDENT_ID, MOCK_STUDENT_NAME


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'demo.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db) -> tuple[int, int]:
    """一条演示任务 + 一条真实任务(各带错因)"""
    demo = CorrectionTask(
        student_name=MOCK_STUDENT_NAME, student_id=MOCK_STUDENT_ID, status="COMPLETED", stage="DONE",
        progress=1.0, image_paths=[], pipeline_used="PIPELINE_A_LOCAL",
    )
    real = CorrectionTask(
        student_name="王小明", student_id="S001", status="COMPLETED", stage="DONE",
        progress=1.0, image_paths=[], pipeline_used="PIPELINE_B_CLOUD",
    )
    db.add_all([demo, real])
    await db.flush()
    db.add_all([
        ErrorRecord(
            task_id=demo.id, student_name=MOCK_STUDENT_NAME, error_type="名词变格",
            canonical_type="CASE_DECLENSION", original_text="a", corrected_text="b",
        ),
        ErrorRecord(
            task_id=real.id, student_name="王小明", error_type="时态",
            canonical_type="TENSE", original_text="c", corrected_text="d",
        ),
    ])
    await db.commit()
    return demo.id, real.id


class TestClearDemoData:
    async def test_confirm_guard(self, db):
        await _seed(db)
        with pytest.raises(Exception):  # 未确认 -> 400
            await routes_maintenance.clear_demo_data(confirm=False, db=db)

    async def test_removes_only_demo_data(self, db):
        demo_id, real_id = await _seed(db)
        result = await routes_maintenance.clear_demo_data(confirm=True, db=db)
        assert result["tasks"] == 1
        remaining = (await db.execute(select(CorrectionTask))).scalars().all()
        assert [task.id for task in remaining] == [real_id]
        errors = (await db.execute(select(ErrorRecord))).scalars().all()
        assert [error.task_id for error in errors] == [real_id]
        assert demo_id not in [task.id for task in remaining]

    async def test_second_run_is_noop(self, db):
        await _seed(db)
        await routes_maintenance.clear_demo_data(confirm=True, db=db)
        again = await routes_maintenance.clear_demo_data(confirm=True, db=db)
        assert again["tasks"] == 0
