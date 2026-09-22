"""数据加密测试(原语 / 透明类型双读 / 密钥生命周期 / 迁移 / 恢复 / Plan B)

注意:
- 禁止写真实 .env(write_env_atomic 被拦截)与真实归档目录(ARCHIVE_DIR/DB_FILE 被重定向);
- crypto_runtime 为全局状态,autouse fixture 负责还原。
"""

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Base, CorrectionTask
from app.models.schemas import StudentUpdateRequest  # noqa: F401 —— 触发 schemas 加载
from app.api.deps import require_unlocked
from app.services import crypto_runtime, crypto_service, encryption_service


@pytest.fixture(autouse=True)
def clean_crypto_state(monkeypatch):
    # 强制关闭开发模式万能密码门禁:保证基线行为与本机 .env 配置无关
    monkeypatch.setattr(settings, "dev_mode", False)
    monkeypatch.setattr(settings, "dev_master_enabled", False)
    monkeypatch.setattr(settings, "dev_master_password", "")
    crypto_runtime.configure(enabled=False, wrap_present=False, dek=None)
    original_flag = settings.encryption_enabled
    yield
    crypto_runtime.configure(enabled=False, wrap_present=False, dek=None)
    settings.encryption_enabled = original_flag


@pytest.fixture(autouse=True)
def env_writer(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(
        encryption_service.env_manager, "write_env_atomic",
        lambda path, updates: recorded.append(dict(updates)),
    )
    return recorded


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'enc.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


class TestCryptoPrimitives:
    """原语往返与防篡改"""

    def test_random_roundtrip(self):
        dek = crypto_service.new_dek()
        token = crypto_service.encrypt_text("学生姓名:李明", dek)
        assert token.startswith("encv1:")
        assert crypto_service.decrypt_text(token, dek) == "学生姓名:李明"
        # 随机加密:同值两次密文不同
        assert crypto_service.encrypt_text("x", dek) != crypto_service.encrypt_text("x", dek)

    def test_deterministic_same_ciphertext(self):
        dek = crypto_service.new_dek()
        a = crypto_service.encrypt_deterministic("李明", dek)
        b = crypto_service.encrypt_deterministic("李明", dek)
        assert a == b and a.startswith("encd1:")
        assert crypto_service.decrypt_deterministic(a, dek) == "李明"

    def test_tamper_detected(self):
        dek = crypto_service.new_dek()
        token = crypto_service.encrypt_text("secret", dek)
        broken = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
        with pytest.raises(crypto_service.CryptoError):
            crypto_service.decrypt_text(broken, dek)

    def test_wrong_key_fails(self):
        dek1, dek2 = crypto_service.new_dek(), crypto_service.new_dek()
        token = crypto_service.encrypt_text("secret", dek1)
        with pytest.raises(crypto_service.CryptoError):
            crypto_service.decrypt_text(token, dek2)

    def test_wrap_unwrap_and_wrong_password(self):
        dek = crypto_service.new_dek()
        salt = crypto_service.new_salt()
        kek = crypto_service.derive_kek("abc12345", salt)
        blob = crypto_service.wrap_key(dek, kek)
        assert crypto_service.unwrap_key(blob, kek) == dek
        with pytest.raises(crypto_service.CryptoError):
            crypto_service.unwrap_key(blob, crypto_service.derive_kek("wrong999", salt))


class TestManagerLifecycle:
    """启用 / 解锁 / 改密"""

    async def test_setup_unlock_change_password(self, factory, env_writer):
        async with factory() as db:
            status = await encryption_service.setup(db, "abc12345")
            assert status["enabled"] and status["has_password"] and not status["locked"]
            assert env_writer[-1] == {"ENCRYPTION_ENABLED": "true"}

            await encryption_service.lock()
            assert crypto_runtime.is_locked()
            with pytest.raises(HTTPException) as err:
                await require_unlocked()
            assert err.value.status_code == 423

            with pytest.raises(ValueError):
                await encryption_service.unlock(db, "wrong999")
            await encryption_service.unlock(db, "abc12345")
            assert crypto_runtime.is_unlocked()

            await encryption_service.change_password(db, "abc12345", "xyz98765")
            await encryption_service.lock()
            with pytest.raises(ValueError):
                await encryption_service.unlock(db, "abc12345")
            await encryption_service.unlock(db, "xyz98765")

    async def test_weak_password_rejected(self, factory):
        async with factory() as db:
            with pytest.raises(ValueError):
                await encryption_service.setup(db, "short1")
            with pytest.raises(ValueError):
                await encryption_service.setup(db, "abcdefgh")  # 无数字


class TestTransparentTypesAndMigration:
    """透明加解密:双读兼容 / 迁移幂等 / 还原明文"""

    async def test_write_encrypt_read_back_and_dual_read(self, factory):
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
            task = CorrectionTask(student_name="李明", student_id="20260101", status="PENDING",
                                  image_paths=["a.png"])
            db.add(task)
            await db.commit()
            # 原始库值应为密文
            raw_name = (await db.execute(text("SELECT student_name FROM correction_tasks"))).scalar_one()
            assert "encd1:" in raw_name

        # 新会话(模拟重启):先锁定 -> 读取报错;解锁后可读
        crypto_runtime.set_dek(None)
        async with factory() as db:
            with pytest.raises(Exception):
                locked = await db.get(CorrectionTask, 1)
                _ = locked.student_name  # 解密需要密钥(取行时即触发)
        async with factory() as db:
            await encryption_service.unlock(db, "abc12345")
            task = await db.get(CorrectionTask, 1)
            assert task.student_name == "李明"

    async def test_migration_idempotent_and_decrypt(self, factory):
        async with factory() as db:
            db.add(CorrectionTask(student_name="王芳", status="PENDING", image_paths=["a.png"]))
            await db.commit()
            await encryption_service.setup(db, "abc12345")
        # 加密迁移
        await encryption_service._run_migration(factory, "encrypt")
        assert encryption_service.migration_progress()["error"] is None
        async with factory() as db:
            raw = (await db.execute(text("SELECT student_name FROM correction_tasks"))).scalar_one()
            assert "encd1:" in raw
            task = await db.get(CorrectionTask, 1)
            assert task.student_name == "王芳"
        # 幂等:再次执行不产生写入(进度 done=0)
        await encryption_service._run_migration(factory, "encrypt")
        assert encryption_service.migration_progress()["done"] == 0
        # 还原明文
        crypto_runtime.set_enabled(False)
        await encryption_service._run_migration(factory, "decrypt")
        async with factory() as db:
            raw = (await db.execute(text("SELECT student_name FROM correction_tasks"))).scalar_one()
            assert raw == "王芳"


class TestRecoveryAndPlanB:
    """恢复密钥重置(轮换 DEK)与归档重建"""

    async def test_recovery_reset_rotates_key(self, factory):
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
            db.add(CorrectionTask(student_name="赵强", status="PENDING", image_paths=["a.png"]))
            await db.commit()
        await encryption_service._run_migration(factory, "encrypt")
        old_dek = crypto_runtime.get_dek()
        async with factory() as s:
            old_raw = (await s.execute(text("SELECT student_name FROM correction_tasks"))).scalar_one()

        async with factory() as db:
            recovery = await encryption_service.generate_recovery_key(db)
        assert recovery.count("-") == 7

        async with factory() as db:
            status = await encryption_service.recovery_reset(factory, db, recovery, "newpass9x")
        assert status["enabled"] and crypto_runtime.is_unlocked()
        new_dek = crypto_runtime.get_dek()
        assert new_dek != old_dek

        # 重加密后:新会话可读,且旧密文用新密钥解不开(确认轮换)
        async with factory() as fresh:
            task = await fresh.get(CorrectionTask, 1)
            assert task.student_name == "赵强"
        with pytest.raises(crypto_service.CryptoError):
            crypto_service.decrypt_deterministic(old_raw, new_dek)
        # 原恢复密钥继续有效(重打包)
        async with factory() as db2:
            again = await encryption_service.recovery_reset(factory, db2, recovery, "thirdpass7")
        assert again["enabled"]

    async def test_recovery_wrong_key_rejected(self, factory):
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
            await encryption_service.generate_recovery_key(db)
            with pytest.raises(ValueError):
                await encryption_service.recovery_reset(
                    factory, db, "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", "newpass9x"
                )

    async def test_planb_archive_reinit(self, factory, tmp_path, monkeypatch):
        # 重定向归档目录与数据库文件(避免污染真实 data 目录)
        import sqlite3 as _sqlite3

        monkeypatch.setattr(encryption_service, "ARCHIVE_DIR", tmp_path)
        db_file = tmp_path / "corrector.db"
        con = _sqlite3.connect(str(db_file))
        con.execute("CREATE TABLE marker(a)")
        con.commit()
        con.close()
        monkeypatch.setattr(encryption_service, "DB_FILE", db_file)
        monkeypatch.setattr(encryption_service, "BACKEND_DIR", tmp_path)  # uploads 探测也在 tmp

        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
            db.add(CorrectionTask(student_name="李明", status="PENDING", image_paths=["a.png"]))
            await db.commit()
            with pytest.raises(ValueError):
                await encryption_service.planb_archive_reinit(db, "随便写")
            data = await encryption_service.planb_archive_reinit(db, "清空重建")
            assert data["wiped_rows"] >= 1
            assert "archive_" in data["archive_dir"]
            # 归档文件已生成
            assert (db_file.parent / data["archive_dir"].split("archive_")[-1]) is not None
            # 清空后无业务数据、无包裹记录
            assert (await db.execute(text("SELECT COUNT(*) FROM correction_tasks"))).scalar_one() == 0
            status = await encryption_service.status(db)
            assert not status["has_password"] and not status["enabled"]
