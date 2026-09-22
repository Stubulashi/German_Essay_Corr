"""大模型超参数管理与自动适配测试(装配 / 三态 / 画像 / 400 剔除自愈 / 设置中心)

覆盖:
1. 未配置默认:请求体与历史行为逐字节一致(temperature/stream/response_format);
2. 三态:具体值 / omit(不发送,payload 无该键)/ auto(自动适配);
3. 模型画像:kimi-k3 不发 temperature;OpenAI 推理系列剔除采样类参数;
4. 400 自愈:解析参数名 -> 剔除 -> 重试一次 -> 记录(内存+落库);仍失败按现状抛错;
5. 学习记录:后续同端点+模型直接不发该参数(手填值亦被拦截);
6. 设置中心:保存热生效 / .env 记录 / 非法值拒绝 / 恢复默认 / 一键回退 / 视图记录;
7. 自定义参数(EXTRA_PARAMS)解析与并入。

注意:所有用例禁止触碰真实 .env 与真实 audit.log(autouse fixture 统一拦截)。
"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import AppSetting, Base
from app.models.schemas import SettingsUpdateRequest
from app.services import audit_service, llm_params, settings_service
from app.services import llm_client as llm_client_module
from app.services.llm_client import LLMClient
from app.services.settings_service import SettingsValidationError

#: 报错现场原始响应体(kimi-k3 拒绝 temperature)
KIMI_K3_ERROR = (
    '{"error":{"code":"invalid_parameter_error","param":null,'
    "\"message\":\"Parameter 'temperature'=0.2 is not supported for kimi-k3 model.\","
    '"type":"invalid_request_error"},"id":"chatcmpl-1c569bda"}'
)
_OK_PAYLOAD = {"choices": [{"message": {"content": "OK"}}]}

_PARAM_ATTRS = [
    f"{prefix}_{param}"
    for prefix in ("local_vlm", "ocr", "deepseek")
    for param in (*llm_params.PARAM_KEYS, "extra_params")
]


@pytest.fixture(autouse=True)
def restore_settings():
    """快照并还原超参配置(隔离用例间的热更新)"""
    snapshot = {attr: getattr(settings, attr) for attr in _PARAM_ATTRS}
    yield
    for attr, value in snapshot.items():
        setattr(settings, attr, value)


@pytest.fixture(autouse=True)
def isolate_learned(monkeypatch):
    """清空学习记录与落库工厂(用例间隔离)"""
    monkeypatch.setattr(llm_params, "_learned", {})
    monkeypatch.setattr(llm_params, "_session_factory", None)


@pytest.fixture(autouse=True)
def env_writer(monkeypatch):
    """拦截 .env 写入并记录内容"""
    recorded: list[dict] = []
    monkeypatch.setattr(
        settings_service.env_manager, "write_env_atomic",
        lambda path, updates: recorded.append(dict(updates)),
    )
    return recorded


@pytest.fixture(autouse=True)
def audit_recorder(monkeypatch):
    """拦截审计写入(不触碰真实 audit.log)并记录事件"""
    events: list[tuple[str, str]] = []

    async def _fake(event: str, *, ok: bool = True, detail: str = "", source: str = "api") -> None:
        events.append((event, detail))

    monkeypatch.setattr(audit_service, "audit", _fake)
    return events


class _FakeResponse:
    """脚本化响应(status/text/json/raise_for_status 与 httpx 行为一致)"""

    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://fake/chat/completions")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("bad status", request=request, response=response)

    def json(self) -> dict:
        return self._payload


class _ScriptedAsyncClient:
    """按序返回预设响应的假异步客户端(记录每次请求载荷)"""

    responses: list[tuple[int, dict, str]] = []
    payloads: list[dict] = []

    def __init__(self, **kwargs) -> None:  # noqa: ARG002
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):  # noqa: ARG002
        _ScriptedAsyncClient.payloads.append(dict(kwargs.get("json") or {}))
        status, payload, text = _ScriptedAsyncClient.responses.pop(0)
        return _FakeResponse(status, payload, text)


class _HttpScript:
    """测试用 HTTP 脚本:按序设定响应,查看每次请求载荷"""

    def __init__(self) -> None:
        _ScriptedAsyncClient.responses = []
        _ScriptedAsyncClient.payloads = []

    def enqueue(self, status: int, payload: dict | None = None, text: str = "") -> None:
        _ScriptedAsyncClient.responses.append((status, payload or {}, text))

    @property
    def payloads(self) -> list[dict]:
        return _ScriptedAsyncClient.payloads


@pytest.fixture
def http_script(monkeypatch) -> _HttpScript:
    """安装脚本化 HTTP 客户端"""
    monkeypatch.setattr(llm_client_module.httpx, "AsyncClient", _ScriptedAsyncClient)
    return _HttpScript()


@pytest.fixture
async def db(tmp_path):
    """独立 SQLite 会话(设置中心集成用例)"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'llm_params.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------
# 1. 参数装配(纯函数)
# ---------------------------------------------------------
class TestAssemble:
    """assemble:基线 / 三态 / 画像 / 学习记录 / 自定义参数"""

    def test_default_matches_legacy_payload(self):
        """未配置任何新键 -> 与历史请求体逐字节一致"""
        plan = llm_params.assemble("pipeline_a", "some-model", "http://x/v1")
        assert plan.params == {
            "temperature": 0.2,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        assert plan.omitted == {}

    def test_legacy_target_ignores_param_config(self):
        """未指定目标(legacy):不读取超参配置(调用点/画像/学习记录仍生效)"""
        settings.ocr_temperature = "0.9"
        plan = llm_params.assemble("", "some-model", "http://x/v1")
        assert plan.params["temperature"] == 0.2
        assert "top_p" not in plan.params

    def test_optional_params_not_sent_by_default(self):
        """auto 档:可选参数(top_p/seed/stop/penalties)画像未声明时不发送"""
        plan = llm_params.assemble("ocr", "qwen2.5-vl", "http://x/v1")
        for key in ("top_p", "seed", "stop", "presence_penalty", "frequency_penalty"):
            assert key not in plan.params

    def test_manual_value_overrides_and_caller_priority(self):
        """具体值 > 调用点显式值 > 历史默认"""
        settings.local_vlm_temperature = "0.5"
        assert llm_params.assemble(
            "pipeline_a", "m", "http://x/v1", caller_temperature=0.4
        ).params["temperature"] == 0.5
        settings.local_vlm_temperature = "auto"
        assert llm_params.assemble(
            "pipeline_a", "m", "http://x/v1", caller_temperature=0.4
        ).params["temperature"] == 0.4
        assert llm_params.assemble(
            "pipeline_a", "m", "http://x/v1"
        ).params["temperature"] == 0.2

    def test_omit_removes_key_entirely(self):
        """显式"不发送":payload 中不含该键(而非 0/空值/null)"""
        settings.local_vlm_temperature = "omit"
        settings.ocr_response_format = "omit"
        settings.deepseek_stream = "omit"
        plan = llm_params.assemble("pipeline_a", "m", "http://x/v1")
        assert "temperature" not in plan.params
        assert plan.omitted["temperature"] == "omit(设置中心)"
        plan = llm_params.assemble("ocr", "m", "http://x/v1")
        assert "response_format" not in plan.params
        plan = llm_params.assemble("deepseek", "m", "http://x/v1")
        assert "stream" not in plan.params

    def test_manual_values_converted(self):
        """具体值类型转换(数值/列表/枚举/布尔)"""
        settings.ocr_stop = "请用中文,结束"
        settings.ocr_response_format = "text"
        settings.ocr_stream = "false"
        settings.ocr_max_tokens = "2048"
        plan = llm_params.assemble("ocr", "m", "http://x/v1")
        assert plan.params["stop"] == ["请用中文", "结束"]
        assert plan.params["response_format"] == {"type": "text"}
        assert plan.params["stream"] is False
        assert plan.params["max_tokens"] == 2048

    def test_profile_kimi_k3_skips_temperature(self):
        """内置画像:kimi-k3 不发 temperature(即使配置为具体值)"""
        plan = llm_params.assemble("ocr", "kimi-k3", "http://x/v1")
        assert "temperature" not in plan.params
        assert plan.omitted["temperature"] == "模型画像"
        settings.ocr_temperature = "0.3"
        plan = llm_params.assemble("ocr", "kimi-k3", "http://x/v1")
        assert "temperature" not in plan.params

    def test_profile_openai_reasoning_family(self):
        """内置画像:OpenAI 推理系列剔除采样类参数"""
        settings.deepseek_temperature = "0.2"
        settings.deepseek_top_p = "0.9"
        plan = llm_params.assemble("deepseek", "o3-mini", "http://x/v1")
        assert "temperature" not in plan.params and "top_p" not in plan.params

    def test_extra_params_merged(self):
        """自定义参数:解析并并入请求体"""
        settings.local_vlm_extra_params = "min_p=0.05;repetition_penalty=1.05;flags=a,b"
        plan = llm_params.assemble("pipeline_a", "m", "http://x/v1")
        assert plan.params["min_p"] == 0.05
        assert plan.params["repetition_penalty"] == 1.05
        assert plan.params["flags"] == ["a", "b"]

    def test_extra_params_same_name_as_fixed_ignored(self):
        """自定义参数与固定超参重名:以固定超参为准"""
        settings.local_vlm_temperature = "0.7"
        settings.local_vlm_extra_params = "temperature=0.1"
        plan = llm_params.assemble("pipeline_a", "m", "http://x/v1")
        assert plan.params["temperature"] == 0.7


# ---------------------------------------------------------
# 2. 设置中心取值校验(纯函数)
# ---------------------------------------------------------
class TestEntryValidation:
    """validate_entry:三态与取值范围"""

    def test_numeric_normalized(self):
        assert llm_params.validate_entry("temperature", "0.20") == "0.2"
        assert llm_params.validate_entry("max_tokens", "2048") == "2048"
        assert llm_params.validate_entry("seed", "-5") == "-5"
        assert llm_params.validate_entry("temperature", "") == "auto"
        assert llm_params.validate_entry("top_p", "omit") == "omit"

    @pytest.mark.parametrize(
        ("param", "raw"),
        [
            ("temperature", "3"),
            ("temperature", "abc"),
            ("top_p", "1.5"),
            ("presence_penalty", "-3"),
            ("max_tokens", "0"),
            ("seed", "1.5"),
            ("stop", "a;b"),
            ("response_format", "json"),
            ("stream", "true"),
            ("extra_params", "min_p"),
        ],
    )
    def test_invalid_values_rejected(self, param, raw):
        with pytest.raises(ValueError):
            llm_params.validate_entry(param, raw)

    def test_extra_params_protected_key_rejected(self):
        with pytest.raises(ValueError):
            llm_params.validate_entry("extra_params", "messages=hi")


class TestParseUnsupportedParam:
    """parse_unsupported_param:从 400 响应体解析参数名"""

    def test_kimi_style_message(self):
        assert llm_params.parse_unsupported_param(KIMI_K3_ERROR) == "temperature"

    def test_unsupported_parameter_style(self):
        body = '{"error":{"message":"Unsupported parameter: \'logprobs\'"}}'
        assert llm_params.parse_unsupported_param(body) == "logprobs"

    def test_irrelevant_body_returns_none(self):
        assert llm_params.parse_unsupported_param('{"error":{"message":"invalid api key"}}') is None

    def test_protected_key_never_parsed(self):
        body = '{"error":{"message":"Parameter \'messages\' is not supported"}}'
        assert llm_params.parse_unsupported_param(body) is None


# ---------------------------------------------------------
# 3. 运行时 400 剔除自愈(含重试与学习记录)
# ---------------------------------------------------------
class TestRuntimeStrip:
    """LLMClient.chat:400 参数不支持 -> 剔除并重试一次 -> 记录"""

    async def test_default_payload_unchanged(self, http_script):
        """未指定目标:请求体与历史行为一致"""
        http_script.enqueue(200, _OK_PAYLOAD)
        client = LLMClient("http://x/v1", "k", "some-model", pipeline_tag="TEST")
        assert await client.chat([{"role": "user", "content": "hi"}]) == "OK"
        payload = http_script.payloads[0]
        assert payload["temperature"] == 0.2
        assert payload["stream"] is False
        assert payload["response_format"] == {"type": "json_object"}

    async def test_kimi_k3_no_longer_fails(self, http_script):
        """kimi-k3:画像直接不发 temperature,首发即成功(不再出现 400)"""
        http_script.enqueue(200, _OK_PAYLOAD)
        client = LLMClient("http://x/v1", "k", "kimi-k3", params_target="ocr")
        assert await client.chat([{"role": "user", "content": "hi"}]) == "OK"
        assert len(http_script.payloads) == 1
        assert "temperature" not in http_script.payloads[0]

    async def test_strip_and_retry_once(self, http_script, audit_recorder):
        """未知不支持模型的 400:剔除参数 -> 重试成功 -> 记录 + 审计"""
        http_script.enqueue(400, text=KIMI_K3_ERROR)
        http_script.enqueue(200, _OK_PAYLOAD)
        client = LLMClient("http://x/v1", "k", "mystery-model", params_target="ocr")
        assert await client.chat([{"role": "user", "content": "hi"}]) == "OK"
        assert "temperature" in http_script.payloads[0]
        assert "temperature" not in http_script.payloads[1]
        assert llm_params.learned_for("http://x/v1", "mystery-model") == {"temperature"}
        assert any(event == "llm.param_stripped" for event, _ in audit_recorder)
        assert any(event == "llm.request" for event, _ in audit_recorder)

    async def test_learned_record_applied_to_next_request(self, http_script):
        """学习记录生效:同端点+模型后续请求直接不再发送该参数"""
        llm_params._learned["http://x/v1|mystery-model"] = {"temperature"}
        http_script.enqueue(200, _OK_PAYLOAD)
        client = LLMClient("http://x/v1", "k", "mystery-model", params_target="ocr")
        await client.chat([{"role": "user", "content": "hi"}])
        assert "temperature" not in http_script.payloads[0]

    async def test_learned_record_blocks_manual_value(self, http_script):
        """学习记录优先级最高:手填值亦被拦截(避免每个任务先失败一次)"""
        llm_params._learned["http://x/v1|mystery-model"] = {"temperature"}
        settings.ocr_temperature = "0.6"
        http_script.enqueue(200, _OK_PAYLOAD)
        client = LLMClient("http://x/v1", "k", "mystery-model", params_target="ocr")
        await client.chat([{"role": "user", "content": "hi"}])
        assert "temperature" not in http_script.payloads[0]

    async def test_second_failure_raises_pipeline_error(self, http_script):
        """剔除重试仍失败 -> 按现状抛 PipelineNetworkError(不无限重试)"""
        from app.pipelines.base import PipelineNetworkError

        http_script.enqueue(400, text=KIMI_K3_ERROR)
        http_script.enqueue(400, text=KIMI_K3_ERROR)
        client = LLMClient("http://x/v1", "k", "mystery-model", params_target="ocr")
        with pytest.raises(PipelineNetworkError) as exc:
            await client.chat([{"role": "user", "content": "hi"}])
        assert "服务返回错误状态 400" in str(exc.value)
        assert len(http_script.payloads) == 2

    async def test_non_param_400_not_retried(self, http_script):
        """非参数类 400(如密钥错误)不重试,维持现状行为"""
        from app.pipelines.base import PipelineNetworkError

        http_script.enqueue(400, text='{"error":{"message":"invalid api key"}}')
        client = LLMClient("http://x/v1", "k", "m", params_target="ocr")
        with pytest.raises(PipelineNetworkError):
            await client.chat([{"role": "user", "content": "hi"}])
        assert len(http_script.payloads) == 1

    async def test_unknown_named_param_not_in_payload_not_retried(self, http_script):
        """端点指出的参数不在本次请求体中:不剔除不重试"""
        from app.pipelines.base import PipelineNetworkError

        settings.ocr_temperature = "omit"
        body = '{"error":{"message":"Parameter \'temperature\' is not supported"}}'
        http_script.enqueue(400, text=body)
        client = LLMClient("http://x/v1", "k", "m", params_target="ocr")
        with pytest.raises(PipelineNetworkError):
            await client.chat([{"role": "user", "content": "hi"}])
        assert len(http_script.payloads) == 1

    async def test_record_persists_and_clears(self, monkeypatch, tmp_path):
        """学习记录落库(app_settings)与清除"""
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'learn.db').as_posix()}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(llm_params, "_session_factory", factory)

        await llm_params.record_unsupported("http://x/v1", "kimi-k3", "temperature")
        async with factory() as session:
            row = await session.get(AppSetting, llm_params.LEARNED_KEY)
            assert row is not None and "temperature" in (row.value or "")
            cleared = await llm_params.clear_learned(session)
        assert cleared == 1
        async with factory() as session:
            assert await session.get(AppSetting, llm_params.LEARNED_KEY) is None
        assert llm_params.learned_snapshot() == {}
        await engine.dispose()


# ---------------------------------------------------------
# 4. 设置中心集成(注册表 / 热生效 / 回退 / 视图)
# ---------------------------------------------------------
class TestSettingsIntegration:
    """设置中心:三态超参的保存 / 校验 / 恢复默认 / 一键回退 / 视图"""

    def test_registry_entries_metadata(self):
        """注册表条目:探索项(支持恢复默认/回退)、热生效、控件类型"""
        view = settings_service.build_view()
        fields = {f.key: f for group in view.groups for f in group.fields}
        for key in ("LOCAL_VLM_TEMPERATURE", "OCR_TEMPERATURE", "DEEPSEEK_STOP"):
            assert fields[key].exploratory and not fields[key].restart_required
            assert fields[key].control == "text"
        assert fields["OCR_RESPONSE_FORMAT"].control == "select"
        assert fields["OCR_STREAM"].choices == ["auto", "false", "omit"]

    async def test_save_hot_apply_and_env(self, db, env_writer):
        result = await settings_service.apply_updates(
            db, SettingsUpdateRequest(ocr_temperature="omit")
        )
        assert result.applied == ["OCR_TEMPERATURE"]
        assert settings.ocr_temperature == "omit"
        assert env_writer == [{"OCR_TEMPERATURE": "omit"}]

    async def test_save_invalid_value_rejected(self, db):
        with pytest.raises(SettingsValidationError):
            await settings_service.apply_updates(
                db, SettingsUpdateRequest(ocr_temperature="3.5")
            )
        with pytest.raises(SettingsValidationError):
            await settings_service.apply_updates(
                db, SettingsUpdateRequest(deepseek_stream="true")
            )

    async def test_reset_and_rollback(self, db, env_writer):
        await settings_service.apply_updates(db, SettingsUpdateRequest(ocr_top_p="0.9"))
        assert settings.ocr_top_p == "0.9"
        meta = await settings_service.snapshot_meta(db)
        assert meta["available"] and "OCR_TOP_P" in meta["keys"]

        result = await settings_service.reset_exploratory(db, "OCR_TOP_P")
        assert result.applied == ["OCR_TOP_P"]
        assert settings.ocr_top_p == "auto"

        rolled = await settings_service.rollback_exploratory(db)
        assert rolled.applied == ["OCR_TOP_P"]
        assert settings.ocr_top_p == "auto"
        assert (await settings_service.snapshot_meta(db))["available"] is False

    async def test_view_exposes_learned_params(self, monkeypatch):
        monkeypatch.setattr(
            llm_params, "_learned", {"http://x/v1|kimi-k3": {"temperature"}}
        )
        view = settings_service.build_view()
        assert view.llm_learned_params == {"http://x/v1|kimi-k3": ["temperature"]}
