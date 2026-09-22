"""全局状态聚合测试(真实自检/缓存/进度条目)

checkup 为真实执行:上传目录写测失败时,短语必须如实变化(无安慰剂式提示)。
"""

import io
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Base, CorrectionTask
from app.services import status_service


class _QueueStub:
    pending_count = 2
    recent_avg_seconds = 10.0


async def _make_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 's.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


class TestCheckupRealExecution:
    async def test_checkup_ok_reflects_storage(self, tmp_path, monkeypatch):
        status_service._checkup_cache = None
        engine, factory = await _make_db(tmp_path)
        upload_dir = tmp_path / "uploads"
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
        async with factory() as session:
            data = await status_service.run_checkup(session)
        assert data["ok"] is True
        assert "存储可写" in data["summary"]
        await engine.dispose()

    async def test_checkup_flags_unwritable_storage(self, tmp_path, monkeypatch):
        """真实路径:上传目录被一个同名文件占位 → 短语必须变为真实警示"""
        status_service._checkup_cache = None
        engine, factory = await _make_db(tmp_path)
        blocker = tmp_path / "blocked_uploads"
        blocker.write_text("占位文件", encoding="utf-8")  # 目录路径被文件占据
        monkeypatch.setattr(settings, "upload_dir", str(blocker))
        async with factory() as session:
            data = await status_service.run_checkup(session)
        assert data["ok"] is False
        assert any("上传目录不可写" in item for item in data["problems"])
        await engine.dispose()

    async def test_checkup_cache_within_ttl(self, tmp_path, monkeypatch):
        status_service._checkup_cache = None
        engine, factory = await _make_db(tmp_path)
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
        async with factory() as session:
            first = await status_service.run_checkup(session)
        async with factory() as session:
            second = await status_service.run_checkup(session)
        assert first["checked_at"] == second["checked_at"]  # 10s 缓存内同一结果
        await engine.dispose()


class TestOverview:
    async def test_overview_active_grading_progress(self, tmp_path, monkeypatch):
        from app.models.db_models import SchoolClass

        status_service._checkup_cache = None
        engine, factory = await _make_db(tmp_path)
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
        async with factory() as session:
            session.add(SchoolClass(name="页面测试班"))
            await session.commit()
            session.add(
                CorrectionTask(
                    student_name="未知",
                    status="PROCESSING",
                    stage="GRADING",
                    progress=0.6,
                    image_paths=[],
                    result=None,
                )
            )
            await session.commit()
        async with factory() as session:
            data = await status_service.build_overview(session, _QueueStub())
        grading = [item for item in data["active"] if item["kind"] == "grading"]
        assert grading, "应包含批改队列条目"
        item = grading[0]
        assert item["percent"] == 60.0  # 处理中任务真实 progress(0.6)
        assert item["eta_seconds"] == 30  # (pending 2 + processing 1) × 均耗 10s
        assert "自检" not in item["status_text"]  # 阶段文案独立于自检短语
        assert data["checkup"]["summary"]
        await engine.dispose()

    async def test_overview_without_queue_stub(self, tmp_path, monkeypatch):
        """queue 为 None 时聚合仍需工作(容错)"""
        status_service._checkup_cache = None
        engine, factory = await _make_db(tmp_path)
        monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
        async with factory() as session:
            data = await status_service.build_overview(session, None)
        assert "checkup" in data and isinstance(data["active"], list)
        await engine.dispose()
