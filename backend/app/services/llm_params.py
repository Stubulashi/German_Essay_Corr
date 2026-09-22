"""大模型超参数管理与自动适配(请求组装层)

背景:
- 各 OpenAI 兼容端点对 Chat Completions 超参的支持集合不同,固定发送
  temperature 等采样参数会被部分模型(如 kimi-k3)以 400 直接拒绝;
- 本模块是超参装配的唯一入口:设置中心三态配置(具体值 / auto 自动 / omit 不发送)
  + 内置模型画像 + 运行时"400 剔除学习"记录,共同决定每次请求最终发送哪些参数。

三态取值约定(每个超参一个配置项,默认 auto):
- auto:按能力档自动决定(即"自动模式";未做任何新配置时与历史行为逐字节一致);
- omit:强制不发送该键(payload 中完全省略,不发 0 / 空值 / null);
- 具体值(如 0.2 / 42 / json_object / 逗号分隔列表):强制发送。

自动档规则(assemble 按序执行):
1. 基线集合 = 历史行为:temperature(调用点值或 0.2)、stream=false、
   response_format=json_object(调用点请求 JSON 时);max_tokens 仅在有值时发送;
2. 遍历三态配置:omit -> 剔除;具体值 -> 覆盖;auto -> 保持基线
   (top_p / penalties / seed / stop / 自定义键等可选参数,画像未声明时不发送);
3. 应用内置模型画像(MODEL_PROFILES,按模型名正则匹配);
4. 应用学习记录(端点+模型级,优先级最高:即使手填值也会被剔除,
   避免"每个任务先失败一次";设置中心"自动适配记录"可见并可清除);
5. 自定义参数 EXTRA_PARAMS 的同名键以九项固定配置为准(避免两处配置打架)。

持久化:学习记录存 app_settings(key = llm_unsupported_params,JSON 形如
{"<base_url>|<model>": ["temperature"]}),启动时装载到内存供同步读取(零 IO),
记录时异步落库;落库失败仅告警,内存仍生效。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings as global_settings
from app.models.db_models import AppSetting

logger = logging.getLogger(__name__)

#: 三个可配置目标(与设置中心三个分组一一对应)
TARGETS = ("pipeline_a", "ocr", "deepseek")

#: 目标 -> .env 键前缀(与 config.py 属性名前缀一致)
TARGET_PREFIX: dict[str, str] = {
    "pipeline_a": "local_vlm",
    "ocr": "ocr",
    "deepseek": "deepseek",
}

#: 可配置的超参数(键名与请求体键同名)
PARAM_KEYS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "max_tokens",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "stop",
    "response_format",
    "stream",
)

#: 三态特殊令牌(其余内容按具体值解析)
AUTO = "auto"
OMIT = "omit"

#: 历史默认参数集(未做任何新配置时的行为,保证对既有端点零影响)
LEGACY_DEFAULTS: dict[str, Any] = {"temperature": 0.2, "stream": False}

#: 学习记录在 app_settings 中的键名
LEARNED_KEY = "llm_unsupported_params"

#: 请求体中永不可剔除的键
_PROTECTED_KEYS = frozenset({"model", "messages"})

#: 数值型超参的取值范围(超出即拒绝保存)
NUMBER_RANGES: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 1.0),
    "presence_penalty": (-2.0, 2.0),
    "frequency_penalty": (-2.0, 2.0),
}

#: 自定义参数键名格式
_EXTRA_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class ModelProfile:
    """模型画像:按模型名正则声明"明确不支持"的参数集合"""

    pattern: str
    unsupported: frozenset[str]
    note: str = ""


#: 内置模型画像(单一事实来源;新增模型族在此追加一行即可)
MODEL_PROFILES: tuple[ModelProfile, ...] = (
    ModelProfile(
        r"kimi-k3",
        frozenset({"temperature"}),
        "Kimi K3 拒绝 temperature(Parameter 'temperature' is not supported)",
    ),
    ModelProfile(
        r"^(o1|o3|o4|gpt-5)",
        frozenset({"temperature", "top_p", "presence_penalty", "frequency_penalty", "seed"}),
        "OpenAI 推理系列不接受采样类参数",
    ),
)


@dataclass(frozen=True)
class ParamPlan:
    """一次请求的参数装配结果(供请求组装、日志、审计与测试断言)"""

    params: dict[str, Any] = field(default_factory=dict)  # 最终写入请求体的参数(不含 model/messages)
    omitted: dict[str, str] = field(default_factory=dict)  # 被剔除的键 -> 原因(omit/画像/学习记录)


# ---------------------------------------------------------
# 配置读取与校验(供设置中心保存时调用)
# ---------------------------------------------------------
def _entry(target: str, param: str) -> str:
    """读取某目标某超参的三态配置值(空串视为 auto)"""
    prefix = TARGET_PREFIX.get(target)
    if prefix is None:
        return AUTO
    text = str(getattr(global_settings, f"{prefix}_{param}", AUTO) or "").strip()
    return text or AUTO


def _format_number(value: float) -> str:
    """数值归一化(整数去掉小数点,如 0.20 -> 0.2、1.0 -> 1)"""
    return str(int(value)) if float(value).is_integer() else str(value)


def validate_entry(param: str, raw: Any) -> str:
    """校验并归一化设置中心提交的超参与自定义参数取值

    Raises:
        ValueError: 取值不合法(设置中心转为 SettingsValidationError)
    """
    text = str(raw or "").strip()
    if not text or text.lower() == AUTO:
        return AUTO
    if text.lower() == OMIT:
        return OMIT
    lowered = text.lower()

    if param in NUMBER_RANGES:
        try:
            number = float(text)
        except ValueError as error:
            raise ValueError(f"取值必须是 {AUTO} / {OMIT} / 数字") from error
        low, high = NUMBER_RANGES[param]
        if not low <= number <= high:
            raise ValueError(f"数值范围 {low:g} ~ {high:g}")
        return _format_number(number)

    if param == "max_tokens":
        if not re.fullmatch(r"\d+", text):
            raise ValueError(f"取值必须是 {AUTO} / {OMIT} / 正整数")
        if int(text) < 1:
            raise ValueError("必须 ≥ 1")
        return str(int(text))

    if param == "seed":
        if not re.fullmatch(r"-?\d+", text):
            raise ValueError(f"取值必须是 {AUTO} / {OMIT} / 整数")
        return str(int(text))

    if param == "stop":
        if ";" in text:
            raise ValueError("停止序列以英文逗号分隔,不能包含分号")
        if not parse_stop(text):
            raise ValueError(f"取值必须是 {AUTO} / {OMIT} / 至少一个停止序列(英文逗号分隔)")
        return text

    if param == "response_format":
        if lowered not in ("json_object", "text"):
            raise ValueError(f"取值必须是 {AUTO} / {OMIT} / json_object / text")
        return lowered

    if param == "stream":
        if lowered != "false":
            # 系统按非流式读取响应,开启流式将导致解析失败,故不提供 true
            raise ValueError(f"取值必须是 {AUTO} / {OMIT} / false(系统按非流式读取响应)")
        return lowered

    if param == "extra_params":
        parse_extra_params(text)  # 语法自检(不合法时抛 ValueError)
        return text

    return text


def parse_stop(text: str) -> list[str]:
    """解析停止序列(英文逗号分隔;空段忽略)"""
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def parse_extra_params(text: str) -> dict[str, Any]:
    """解析自定义参数文本:``key=value;key2=value2``

    取值规则:
    - true / false -> 布尔;整数 -> int;其余数字 -> float;
    - 含英文逗号 -> 字符串数组(供序列类参数使用);
    - 其余按原字符串(不能含引号,受 .env 写入约束)。
    """
    result: dict[str, Any] = {}
    for chunk in str(text or "").split(";"):
        item = chunk.strip()
        if not item:
            continue
        key, sep, raw = item.partition("=")
        key, raw = key.strip(), raw.strip()
        if not sep:
            raise ValueError(f"自定义参数缺少“=”:{item}")
        if not _EXTRA_KEY_RE.fullmatch(key):
            raise ValueError(f"自定义参数键名非法:{key}")
        if key in _PROTECTED_KEYS:
            raise ValueError(f"自定义参数不允许覆盖 {key}")
        if not raw:
            raise ValueError(f"自定义参数 {key} 缺少取值")
        result[key] = _parse_scalar(raw)
    return result


def _parse_scalar(raw: str) -> Any:
    """自定义参数取值 -> Python 值"""
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    if "," in raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return raw


# ---------------------------------------------------------
# 模型画像
# ---------------------------------------------------------
def profile_unsupported(model: str) -> frozenset[str]:
    """按模型名匹配内置画像,返回明确不支持的参数集(无匹配返回空集)"""
    name = (model or "").strip()
    for profile in MODEL_PROFILES:
        if re.search(profile.pattern, name, re.IGNORECASE):
            return profile.unsupported
    return frozenset()


def parse_unsupported_param(body: str) -> str | None:
    """从端点 400 错误正文中解析出"不支持的参数名"(解析不出返回 None)

    覆盖形如:
    - ``Parameter 'temperature'=0.2 is not supported for kimi-k3 model.``
    - ``Unsupported parameter: 'top_p'``
    """
    text = str(body or "")
    lowered = text.lower()
    if "not supported" not in lowered and "unsupported" not in lowered:
        return None
    patterns = (
        re.compile(r"Parameter\s+[\"']([A-Za-z0-9_.\-]+)[\"']", re.IGNORECASE),
        re.compile(r"Unsupported\s+parameter\s*:?\s*[\"']?([A-Za-z0-9_.\-]+)", re.IGNORECASE),
        re.compile(r"[\"']([A-Za-z0-9_.\-]+)[\"']\s+is\s+not\s+supported", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match and match.group(1) not in _PROTECTED_KEYS:
            return match.group(1)
    return None


# ---------------------------------------------------------
# 学习记录(端点+模型级;内存同步读取 + 异步落库)
# ---------------------------------------------------------
_learned: dict[str, set[str]] = {}
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _record_key(base_url: str, model: str) -> str:
    """学习记录键:``端点|模型``(去尾斜杠,便于跨调用点命中同一端点)"""
    return f"{(base_url or '').rstrip('/')}|{model or ''}"


def learned_for(base_url: str, model: str) -> set[str]:
    """该端点+模型已被判定不支持的参数集"""
    return set(_learned.get(_record_key(base_url, model), ()))


def learned_snapshot() -> dict[str, list[str]]:
    """全部学习记录(供设置中心展示;键 = 端点|模型)"""
    return {key: sorted(value) for key, value in sorted(_learned.items()) if value}


async def load_learned_cache(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """应用启动时装载学习记录到内存(由 lifespan 调用)"""
    global _learned, _session_factory
    _session_factory = session_factory
    async with session_factory() as session:
        row = await session.get(AppSetting, LEARNED_KEY)
    data: dict[str, list[str]] = {}
    if row is not None and row.value:
        try:
            raw = json.loads(row.value)
            if isinstance(raw, dict):
                data = {
                    str(key): [str(item) for item in value]
                    for key, value in raw.items()
                    if isinstance(value, list) and value
                }
        except ValueError:
            logger.warning("自动适配记录解析失败,按空记录处理")
    _learned = {key: set(value) for key, value in data.items()}
    if _learned:
        logger.info("大模型自动适配记录已加载:%s", learned_snapshot())


async def record_unsupported(base_url: str, model: str, param: str) -> None:
    """记录"该端点+模型不支持某参数"(内存立即生效;落库失败仅告警)"""
    key = _record_key(base_url, model)
    bucket = _learned.setdefault(key, set())
    if param in bucket:
        return
    bucket.add(param)
    logger.warning("自动适配:记录 %s 不支持参数 %s(后续请求不再发送)", key, param)
    if _session_factory is None:
        return
    try:
        async with _session_factory() as session:
            row = await session.get(AppSetting, LEARNED_KEY)
            payload = json.dumps(learned_snapshot(), ensure_ascii=False)
            if row is None:
                session.add(AppSetting(key=LEARNED_KEY, value=payload))
            else:
                row.value = payload
            await session.commit()
    except Exception as error:  # noqa: BLE001 —— 记录落库失败不允许影响批改
        logger.warning("自动适配记录落库失败(内存仍生效):%s", error)


async def clear_learned(db: AsyncSession) -> int:
    """清空全部学习记录(设置中心入口;返回被清除的记录条数)"""
    global _learned
    count = len(_learned)
    _learned = {}
    row = await db.get(AppSetting, LEARNED_KEY)
    if row is not None:
        await db.delete(row)
        await db.commit()
    logger.info("大模型自动适配记录已清空(%s 条)", count)
    return count


# ---------------------------------------------------------
# 参数装配(请求组装层唯一入口)
# ---------------------------------------------------------
def _convert_entry(param: str, entry: str) -> Any:
    """把具体值文本转换为请求体取值"""
    if param == "max_tokens" or param == "seed":
        return int(entry)
    if param in NUMBER_RANGES:
        return float(entry)
    if param == "stop":
        return parse_stop(entry)
    if param == "response_format":
        return {"type": entry}
    if param == "stream":
        return entry.lower() == "true"
    return entry


def assemble(
    target: str,
    model: str,
    base_url: str,
    caller_temperature: float | None = None,
    caller_max_tokens: int | None = None,
    caller_response_format_json: bool = True,
) -> ParamPlan:
    """装配一次请求最终发送的超参集合

    规则顺序:基线(历史行为)-> 三态配置 -> 自定义参数 -> 模型画像 -> 学习记录。
    target 不在 TARGETS 内(如 legacy/测试)时跳过全部配置读取,仅保留
    画像 / 学习记录 / 调用点取值,保证未迁移调用点行为与历史一致。

    Args:
        target:                    参数目标(管线 A / OCR / DeepSeek;空 = legacy)
        model:                     模型名(用于画像匹配)
        base_url:                  端点地址(用于学习记录命中)
        caller_temperature:        调用点显式温度(None = 未指定,用历史默认 0.2)
        caller_max_tokens:         调用点显式最大生成数(None = 不发送)
        caller_response_format_json: 调用点是否请求 JSON 输出模式
    """
    params: dict[str, Any] = {}
    omitted: dict[str, str] = {}

    def put(key: str, value: Any) -> None:
        params[key] = value
        omitted.pop(key, None)

    def drop(key: str, reason: str) -> None:
        if key in params:
            params.pop(key)
            omitted[key] = reason

    # ---------- 1. 基线(历史行为:未做任何新配置时与此完全一致) ----------
    put(
        "temperature",
        caller_temperature if caller_temperature is not None else LEGACY_DEFAULTS["temperature"],
    )
    put("stream", LEGACY_DEFAULTS["stream"])
    if caller_response_format_json:
        put("response_format", {"type": "json_object"})
    if caller_max_tokens is not None:
        put("max_tokens", caller_max_tokens)

    # ---------- 2. 三态配置 ----------
    if target in TARGET_PREFIX:
        for key in PARAM_KEYS:
            entry = _entry(target, key)
            if entry == AUTO:
                continue
            if entry == OMIT:
                drop(key, "omit(设置中心)")
                continue
            put(key, _convert_entry(key, entry))

        extra_entry = _entry(target, "extra_params")
        if extra_entry not in (AUTO, OMIT):
            for key, value in parse_extra_params(extra_entry).items():
                if key in PARAM_KEYS:
                    logger.warning("自定义参数 %s 与固定超参重名,已忽略(以固定超参设置为准)", key)
                    continue
                put(key, value)

    # ---------- 3. 内置模型画像 ----------
    for key in profile_unsupported(model):
        drop(key, "模型画像")

    # ---------- 4. 学习记录(优先级最高:含手填值,避免每次任务先失败一次) ----------
    for key in learned_for(base_url, model):
        drop(key, "自动适配记录")

    return ParamPlan(params=params, omitted=omitted)
