"""开发模式万能密码(兜底解锁)测试

覆盖:
- 门禁:仅「开发模式 + 专用开关 + ≥64 位十六进制长 Hash」同时满足时可用;
- 开发模式正确万能密码:成功解锁并解密已加密字段(审计标注"万能密码",状态不泄露密码);
- 生产模式:该路径不可用,失败结果与普通口令错误完全一致;
- 错误万能密码:与普通错误口令的失败结果不可区分;
- 常量时间比较(hmac.compare_digest)被实际调用;
- 功能关闭时:不产生任何新记录,既有解锁与密文格式逐字节不变。

注意:
- 禁止写真实 .env(write_env_atomic 被拦截)与真实审计文件(audit 被打桩);
- crypto_runtime 为全局状态,autouse fixture 负责还原,并强制关闭开发门禁防本机 .env 干扰。
"""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Base, CorrectionTask
from app.models.schemas import StudentUpdateRequest  # noqa: F401 —— 触发 schemas 加载
from app.services import crypto_runtime, encryption_service

MASTER = "9f4c" * 16  # 64 位十六进制(合规长 Hash)
WRONG_MASTER = "ab12" * 16  # 同格式的错误万能密码
WRONG_PASSWORD_MSG = "口令错误(或密钥数据已损坏)"


@pytest.fixture(autouse=True)
def clean_crypto_state(monkeypatch):
    """隔离全局加密状态,并强制关闭开发门禁(保证基线确定性)"""
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


@pytest.fixture(autouse=True)
def audit_capture(monkeypatch):
    """拦截审计写入(避免污染真实 audit.log),并记录事件供断言"""
    recorded: list[tuple[str, bool, str]] = []

    async def fake_audit(event, *, ok=True, detail="", source="api"):
        recorded.append((event, ok, detail))

    monkeypatch.setattr(encryption_service, "audit", fake_audit)
    return recorded


@pytest.fixture
def dev_master(monkeypatch):
    """打开三重门禁:开发模式 + 专用开关 + 合规长 Hash"""
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "dev_master_enabled", True)
    monkeypatch.setattr(settings, "dev_master_password", MASTER)


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'devmaster.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _setting_keys(db) -> set[str]:
    """当前 app_settings 的全部键(用于断言"不产生新记录")"""
    rows = (await db.execute(text("SELECT key FROM app_settings"))).scalars().all()
    return set(rows)


class TestDevMasterSuccess:
    """开发模式:正确万能密码可用"""

    async def test_unlock_and_decrypt(self, factory, dev_master, audit_capture):
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
            # setup 时(门禁生效)自动登记开发包裹
            assert encryption_service.KEY_DEV_MASTER_WRAP in await _setting_keys(db)
            db.add(CorrectionTask(student_name="李明", student_id="20260101", status="PENDING",
                                  image_paths=["a.png"]))
            await db.commit()
            status = await encryption_service.status(db)
            assert status["dev_master_enabled"] is True
            # 状态接口绝不返回万能密码或其摘要
            assert MASTER not in json.dumps(status)

        await encryption_service.lock()
        assert crypto_runtime.is_locked()

        async with factory() as db:
            await encryption_service.unlock(db, MASTER)
            assert crypto_runtime.is_unlocked()
            task = await db.get(CorrectionTask, 1)
            assert task.student_name == "李明"
            unlocks = [d for e, _, d in audit_capture if e == "encryption.unlock"]
            assert unlocks and "万能密码" in unlocks[-1]

    async def test_selfheal_on_normal_unlock(self, factory, monkeypatch):
        """能力后开:正常解锁时自愈补建开发包裹,之后万能密码可用"""
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
            assert encryption_service.KEY_DEV_MASTER_WRAP not in await _setting_keys(db)

        # 模拟管理员注入 .env 后重启:门禁此时才生效
        monkeypatch.setattr(settings, "dev_mode", True)
        monkeypatch.setattr(settings, "dev_master_enabled", True)
        monkeypatch.setattr(settings, "dev_master_password", MASTER)
        await encryption_service.lock()
        async with factory() as db:
            await encryption_service.unlock(db, "abc12345")  # 正常解锁:自愈登记
            assert encryption_service.KEY_DEV_MASTER_WRAP in await _setting_keys(db)

        await encryption_service.lock()
        async with factory() as db:
            await encryption_service.unlock(db, MASTER)
            assert crypto_runtime.is_unlocked()

    async def test_rotation_refreshes_wrap(self, factory, dev_master):
        """恢复重置轮换 DEK 后:开发包裹随轮换刷新,万能密码仍然有效"""
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
            db.add(CorrectionTask(student_name="王芳", status="PENDING", image_paths=["a.png"]))
            await db.commit()
            recovery = await encryption_service.generate_recovery_key(db)
        async with factory() as db:
            await encryption_service.recovery_reset(factory, db, recovery, "newpass9x")

        await encryption_service.lock()
        async with factory() as db:
            await encryption_service.unlock(db, MASTER)
            task = await db.get(CorrectionTask, 1)
            assert task.student_name == "王芳"


class TestDevMasterGate:
    """门禁完备性:生产/未启用/不合规时一律不可用且失败结果一致"""

    async def test_wrong_master_indistinguishable(self, factory, dev_master):
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
        await encryption_service.lock()
        async with factory() as db:
            with pytest.raises(ValueError) as normal_fail:
                await encryption_service.unlock(db, "wrong999")
            with pytest.raises(ValueError) as master_fail:
                await encryption_service.unlock(db, WRONG_MASTER)
            # 两条路径的失败信息逐字一致(不泄露"这是万能密码")
            assert str(normal_fail.value) == str(master_fail.value) == WRONG_PASSWORD_MSG
            assert crypto_runtime.is_locked()

    async def test_gate_requires_all_conditions(self, factory, monkeypatch):
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
        await encryption_service.lock()

        cases = [
            (False, True, MASTER),  # dev_mode 关
            (True, False, MASTER),  # 专用开关关
            (True, True, MASTER[:32]),  # 密钥长度不足
            (True, True, "zz" * 32),  # 非十六进制
            (True, True, ""),  # 未配置
        ]
        for dev_mode, enabled, key in cases:
            monkeypatch.setattr(settings, "dev_mode", dev_mode)
            monkeypatch.setattr(settings, "dev_master_enabled", enabled)
            monkeypatch.setattr(settings, "dev_master_password", key)
            assert encryption_service.dev_master_available() is False
            async with factory() as db:
                assert (await encryption_service.status(db))["dev_master_enabled"] is False
                with pytest.raises(ValueError) as err:
                    await encryption_service.unlock(db, MASTER)
                assert str(err.value) == WRONG_PASSWORD_MSG
                assert crypto_runtime.is_locked()

    async def test_no_wrap_keeps_original_error(self, factory, dev_master):
        """未设置口令时:保持既有"尚未设置加密口令"错误(不走万能密码回退)"""
        async with factory() as db:
            with pytest.raises(ValueError) as err:
                await encryption_service.unlock(db, MASTER)
            assert str(err.value) == "尚未设置加密口令"

    async def test_production_unavailable(self, factory, monkeypatch):
        """生产模式(dev_mode=false):即便另两项已配置,路径不可用且不暴露"""
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
        monkeypatch.setattr(settings, "dev_master_enabled", True)
        monkeypatch.setattr(settings, "dev_master_password", MASTER)
        await encryption_service.lock()

        assert encryption_service.dev_master_available() is False
        async with factory() as db:
            assert (await encryption_service.status(db))["dev_master_enabled"] is False
            with pytest.raises(ValueError) as err:
                await encryption_service.unlock(db, MASTER)
            assert str(err.value) == WRONG_PASSWORD_MSG
            assert crypto_runtime.is_locked()

    async def test_constant_time_compare_used(self, factory, dev_master, monkeypatch):
        calls: list[int] = []
        real_compare = encryption_service.hmac.compare_digest

        def counting_compare(a, b):
            calls.append(1)
            return real_compare(a, b)

        monkeypatch.setattr(encryption_service.hmac, "compare_digest", counting_compare)
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
        await encryption_service.lock()
        async with factory() as db:
            await encryption_service.unlock(db, MASTER)
        assert calls, "万能密码校验必须使用 hmac.compare_digest 常量时间比较"
        assert crypto_runtime.is_unlocked()


class TestFeatureOffUnchanged:
    """功能关闭:既有行为逐字节不变"""

    async def test_no_new_records_and_same_ciphertext(self, factory):
        async with factory() as db:
            await encryption_service.setup(db, "abc12345")
            # 仅产生既有包裹记录,无开发包裹
            assert await _setting_keys(db) == {encryption_service.KEY_KEYWRAP}
            db.add(CorrectionTask(student_name="赵强", status="PENDING", image_paths=["a.png"]))
            await db.commit()
            raw = (await db.execute(text("SELECT student_name FROM correction_tasks"))).scalar_one()
            assert raw.startswith("encd1:")

        await encryption_service.lock()
        async with factory() as db:
            await encryption_service.unlock(db, "abc12345")
            assert crypto_runtime.is_unlocked()
            task = await db.get(CorrectionTask, 1)
            assert task.student_name == "赵强"
            # 门禁关闭:正常解锁也不写任何新记录
            assert await _setting_keys(db) == {encryption_service.KEY_KEYWRAP}
