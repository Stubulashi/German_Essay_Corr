"""设置中心测试(注册表 / 脱敏 / 更新校验 / developer 门控 / 探索项回退)

注意:
- 所有用例禁止触碰真实 .env(run 时 monkeypatch env_manager.write_env_atomic 为记录器);
- 用例可能热改全局 settings 单例,autouse fixture 负责完整还原。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, settings
from app.models.db_models import Base
from app.models.schemas import SettingsUpdateRequest
from app.services import env_manager, settings_service
from app.services.settings_service import DeveloperOnlyError, SettingsValidationError


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
    """拦截 .env 写入并记录内容"""
    recorded: list[dict] = []
    monkeypatch.setattr(
        settings_service.env_manager, "write_env_atomic",
        lambda path, updates: recorded.append(dict(updates)),
    )
    return recorded


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'settings.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class TestEnvManager:
    """env 文本更新纯函数"""

    def test_preserve_comments_and_update_in_place(self):
        text = "# 头部注释\nMOCK_MODE=false  # 演示模式\nA=1\n"
        result = env_manager.render_env_update(text, {"MOCK_MODE": "true"})
        assert "# 头部注释" in result
        assert "MOCK_MODE=true" in result  # 行内注释随值被替换(值部分整体替换)

    def test_append_new_keys(self):
        text = "A=1\n"
        result = env_manager.render_env_update(text, {"NEW_KEY": "v"})
        assert "A=1" in result
        assert env_manager.APPEND_SECTION_HEADER in result
        assert "NEW_KEY=v" in result

    def test_reject_control_chars_and_quotes(self):
        with pytest.raises(env_manager.EnvValueError):
            env_manager.sanitize_value("bad\nvalue")
        with pytest.raises(env_manager.EnvValueError):
            env_manager.sanitize_value('say "hi"')

    def test_quote_whitespace_value(self):
        assert env_manager.sanitize_value("C:\\my data\\uploads") == "'C:\\my data\\uploads'"


class TestRegistryAndView:
    """注册表与视图(脱敏)"""

    def test_registry_keys_match_settings_attrs(self):
        for spec in settings_service.FIELDS:
            assert spec.settings_attr in Settings.model_fields, spec.key

    def test_view_masks_secrets(self, monkeypatch):
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-abcdef123456")
        view = settings_service.build_view()
        field = next(
            f for group in view.groups for f in group.fields if f.key == "DEEPSEEK_API_KEY"
        )
        assert field.sensitive and field.has_value
        assert field.value is None
        assert "abcdef12345" not in (field.masked or "")
        assert field.masked.startswith("sk-")

    def test_view_metadata_flags(self):
        view = settings_service.build_view()
        fields = {f.key: f for group in view.groups for f in group.fields}
        assert fields["DATABASE_URL"].audience == "developer"
        assert fields["IMAGE_GRAYSCALE"].exploratory
        assert fields["SERVER_PORT"].restart_required
        assert fields["MOCK_MODE"].value in (True, False)


class TestApplyUpdates:
    """更新流程:写回记录 / 热生效 / 校验 / developer 门控"""

    async def test_hot_apply_and_boolean_env(self, db, env_writer, monkeypatch):
        monkeypatch.setattr(settings, "mock_mode", False)
        result = await settings_service.apply_updates(db, SettingsUpdateRequest(mock_mode=True))
        assert result.applied == ["MOCK_MODE"]
        assert settings.mock_mode is True
        assert env_writer == [{"MOCK_MODE": "true"}]
        assert result.restart_required == []

    async def test_invalid_url_rejected(self, db):
        with pytest.raises(SettingsValidationError):
            await settings_service.apply_updates(
                db, SettingsUpdateRequest(local_vlm_base_url="ftp://bad")
            )

    async def test_number_range_rejected(self, db):
        with pytest.raises(SettingsValidationError):
            await settings_service.apply_updates(db, SettingsUpdateRequest(server_port=99))

    async def test_developer_field_gated(self, db, monkeypatch):
        monkeypatch.setattr(settings, "dev_mode", False)
        with pytest.raises(DeveloperOnlyError):
            await settings_service.apply_updates(
                db, SettingsUpdateRequest(max_concurrent_tasks=4)
            )
        monkeypatch.setattr(settings, "dev_mode", True)
        result = await settings_service.apply_updates(
            db, SettingsUpdateRequest(max_concurrent_tasks=4)
        )
        assert "MAX_CONCURRENT_TASKS" in result.restart_required

    async def test_secret_three_states(self, db, env_writer, monkeypatch):
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-old")
        # 设置新值
        await settings_service.apply_updates(db, SettingsUpdateRequest(deepseek_api_key="sk-new"))
        assert settings.deepseek_api_key == "sk-new"
        # 清除
        result = await settings_service.apply_updates(db, SettingsUpdateRequest(deepseek_api_key=""))
        assert settings.deepseek_api_key == ""
        assert result.cleared == ["DEEPSEEK_API_KEY"]


class TestExploratoryRollback:
    """探索项快照:恢复默认 / 一键回退"""

    async def test_reset_captures_snapshot_then_rollback_restores(self, db, env_writer, monkeypatch):
        monkeypatch.setattr(settings, "image_grayscale", False)
        # 第一次修改:记录快照(修改前值 false)
        await settings_service.apply_updates(db, SettingsUpdateRequest(image_grayscale=True))
        assert settings.image_grayscale is True
        meta = await settings_service.snapshot_meta(db)
        assert meta["available"] and meta["keys"] == ["IMAGE_GRAYSCALE"]

        # 恢复默认
        await settings_service.reset_exploratory(db, "IMAGE_GRAYSCALE")
        assert settings.image_grayscale is False

        # 一键回退:还原到修改前(false),快照清空
        result = await settings_service.rollback_exploratory(db)
        assert result.applied == ["IMAGE_GRAYSCALE"]
        assert settings.image_grayscale is False
        assert (await settings_service.snapshot_meta(db))["available"] is False

    async def test_reset_rejects_non_exploratory(self, db):
        with pytest.raises(SettingsValidationError):
            await settings_service.reset_exploratory(db, "MOCK_MODE")

    async def test_rollback_without_snapshot(self, db):
        with pytest.raises(SettingsValidationError):
            await settings_service.rollback_exploratory(db)

    async def test_second_change_keeps_first_snapshot(self, db, monkeypatch):
        monkeypatch.setattr(settings, "deepseek_use_reasoning", False)
        await settings_service.apply_updates(db, SettingsUpdateRequest(deepseek_use_reasoning=True))
        await settings_service.apply_updates(db, SettingsUpdateRequest(deepseek_use_reasoning=False))
        await settings_service.apply_updates(db, SettingsUpdateRequest(deepseek_use_reasoning=True))
        await settings_service.rollback_exploratory(db)
        assert settings.deepseek_use_reasoning is False  # 回到最早快照


class TestConnectivityValidation:
    """连通性测试参数校验(不发真实网络请求)"""

    async def test_unknown_override_rejected(self):
        with pytest.raises(SettingsValidationError):
            await settings_service.test_connectivity("deepseek", {"page_size": 10})
