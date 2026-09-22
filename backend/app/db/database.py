"""数据库连接与会话管理(异步 SQLite)+ Alembic 迁移

提供:
- `engine`:      全局异步引擎
- `init_db()`:   应用启动时执行 Alembic 迁移(替代 create_all,支持增量升级)
- `get_session()`: 依赖注入用的会话工厂(FastAPI Depends)
"""

import asyncio
import logging
import sqlite3
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import BACKEND_DIR, settings

logger = logging.getLogger(__name__)

# 将 DATABASE_URL 中的相对路径解析为基于 backend/ 目录的绝对路径,
# 避免因启动时工作目录不同导致数据库文件位置漂移
_db_url = settings.database_url
if _db_url.startswith("sqlite") and "./data" in _db_url:
    _db_url = _db_url.replace("./data", str(BACKEND_DIR / "data").replace("\\", "/"))

# 全局异步引擎(SQLite + aiosqlite)
# timeout=30:多工作协程并发写入时的锁等待上限,避免 "database is locked"
engine = create_async_engine(
    _db_url,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},
)

# 异步会话工厂(expire_on_commit=False 便于 commit 后继续读取对象属性)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _sqlite_file_path() -> Path | None:
    """从数据库 URL 中提取 SQLite 文件路径(非 SQLite 返回 None)"""
    prefix = "sqlite+aiosqlite:///"
    if _db_url.startswith(prefix):
        return Path(_db_url[len(prefix):])
    return None


def _run_migrations_sync() -> None:
    """同步执行 Alembic 迁移(在线程池中调用,避免阻塞事件循环)

    处理策略:
    - 版本守卫:库内版本号必须能在当前程序的迁移脚本中找到,
      否则说明数据来自更新版本的程序,拒绝启动(避免旧程序损坏新数据);
    - 全新数据库:直接 upgrade head(迁移从头建表);
    - 已有业务表但无 alembic_version(引入 Alembic 前创建的库):
      先将基线版本 stamp 到库上,再 upgrade head 应用后续增量迁移。
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    script = ScriptDirectory.from_config(cfg)

    db_file = _sqlite_file_path()
    if db_file is not None and db_file.exists() and db_file.stat().st_size > 0:
        # 读取库内表清单与当前迁移版本(一次连接完成)
        conn = sqlite3.connect(str(db_file))
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            db_version: str | None = None
            if "alembic_version" in tables:
                row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
                db_version = row[0] if row else None
        finally:
            conn.close()

        # ---------- 版本守卫:旧程序不得打开新库 ----------
        if db_version:
            try:
                script.get_revision(db_version)
            except Exception as e:
                raise RuntimeError(
                    f"检测到数据库由更新版本的程序创建(库版本 {db_version},当前程序无法识别)。"
                    "为避免数据损坏,已拒绝启动。请把“批改器”目录整体升级到最新版本后再使用。"
                ) from e

        # ---------- 存量库兼容:标记基线后增量升级 ----------
        if "correction_tasks" in tables and "alembic_version" not in tables:
            bases = script.get_bases()
            if bases:
                baseline = sorted(bases)[0]
                logger.info("检测到 Alembic 之前的存量数据库,标记基线版本 %s", baseline)
                command.stamp(cfg, baseline)

    logger.info("执行数据库迁移(upgrade head)...")
    command.upgrade(cfg, "head")


async def init_db() -> None:
    """应用启动时调用:确保数据目录存在、执行迁移并启用 WAL"""
    settings.data_path.mkdir(parents=True, exist_ok=True)
    # Alembic command API 为同步接口,放入线程池执行
    await asyncio.to_thread(_run_migrations_sync)
    async with engine.begin() as conn:
        # WAL 模式下读写并发更友好(批改结果回写与前端轮询查询并存)
        await conn.execute(text("PRAGMA journal_mode=WAL"))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入:每个请求一个独立会话"""
    async with SessionLocal() as session:
        yield session
