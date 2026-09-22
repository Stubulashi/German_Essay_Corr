"""Alembic 迁移环境配置(适配异步引擎与项目 .env 配置)

说明:
- 数据库 URL 从应用配置(app.config.settings)读取,路径解析规则与
  app/db/database.py 保持一致,避免迁移目标与应用运行目标不一致;
- 使用 async_engine_from_config + connection.run_sync 走异步驱动执行迁移;
- render_as_batch=True:SQLite 对 ALTER TABLE 支持有限,批处理模式可安全
  执行加列、改类型等操作。
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 确保 backend/ 目录在 sys.path 中(无论从哪个工作目录调用 alembic)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import BACKEND_DIR, settings  # noqa: E402
from app.models.db_models import Base  # noqa: E402
from app.models.encrypted_types import (  # noqa: E402
    EncryptedDeterministic,
    EncryptedJSON,
    EncryptedText,
)


def compare_type_custom(context, inspected_column, metadata_column, inspected_type, metadata_type):  # noqa: ANN001
    """自定义类型比较:透明加密类型与底层存储等价(避免噪声迁移)

    EncryptedText/EncryptedDeterministic/EncryptedJSON 仅做绑定/读取层加解密,
    存储形态与 TEXT/VARCHAR/JSON 兼容,不产生 ALTER;该回调避开 SQLite 下
    每一次 autogenerate 都误报"类型变更"的问题。
    """
    if isinstance(metadata_type, (EncryptedText, EncryptedDeterministic, EncryptedJSON)):
        return False  # False = 视为无差异
    return None  # 其余类型走 Alembic 默认比较

# Alembic Config 对象(对应 alembic.ini)
config = context.config

# 日志配置(按 alembic.ini 中的 logging 段)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 将应用的真实数据库 URL 注入 alembic(与环境变量/.env 完全同源)
_db_url = settings.database_url
if _db_url.startswith("sqlite") and "./data" in _db_url:
    _db_url = _db_url.replace("./data", str(BACKEND_DIR / "data").replace("\\", "/"))
config.set_main_option("sqlalchemy.url", _db_url)

# 目标元数据:ORM 模型(自动生成的比对基准)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式:仅生成 SQL,不连接数据库"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在线模式实际执行迁移(同步回调,由 run_sync 调用)"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=compare_type_custom,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """在线模式:通过异步引擎执行迁移"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
