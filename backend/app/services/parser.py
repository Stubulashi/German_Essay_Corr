"""防御性 LLM 输出解析器

LLM 输出 JSON 时常见的"脏数据"形态及对应处理:
1. 带 Markdown 代码块围栏(```json ... ```) -> 剥离围栏
2. 前后夹杂解释性文字 -> 截取首个 '{' 到最后一个 '}' 之间的内容
3. 尾随逗号、None 值等小瑕疵 -> 轻量清洗后重试解析
4. 字段缺失/类型错误 -> Pydantic 校验报错,由上层发起一次"修复重试"

本模块只负责"文本 -> dict";Pydantic 校验与修复重试编排在管线内完成。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 匹配 Markdown 代码块围栏(```json ... ``` 或 ``` ... ```)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")
# 匹配 JSON 中对象/数组后的尾随逗号,如 {"a":1,} 或 [1,2,]
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def extract_json_dict(raw: str) -> dict[str, Any]:
    """从 LLM 原始输出中提取 JSON 对象

    Args:
        raw: LLM 返回的原始文本

    Returns:
        解析成功后的 dict

    Raises:
        ValueError: 所有清洗策略均无法得到合法 JSON 对象
    """
    if not raw or not raw.strip():
        raise ValueError("LLM 输出为空")

    text = raw.strip()

    # 策略 1:剥离 Markdown 代码块围栏(可能只有一个围栏块)
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate:
            text = candidate

    # 策略 2:截取首个 '{' 到最后一个 '}' 之间的内容(去除前后解释性文字)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        # 特殊场景:LLM 偶尔直接返回顶层数组,给出更准确的诊断信息
        bracket_start = text.find("[")
        if bracket_start != -1:
            try:
                arr = json.loads(text[bracket_start:])
            except (json.JSONDecodeError, RecursionError):
                arr = None
            if isinstance(arr, list):
                raise ValueError(f"解析结果不是 JSON 对象,而是 {type(arr).__name__}")
        raise ValueError(f"输出中未找到 JSON 对象,原始输出前 200 字符:{raw[:200]}")
    candidate = text[start : end + 1]

    # 策略 3:标准解析(RecursionError=超深嵌套输入,与解析失败同义处理)
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, RecursionError):
        # 策略 4:清洗尾随逗号后重试
        cleaned = _TRAILING_COMMA_RE.sub(r"\1", candidate)
        try:
            obj = json.loads(cleaned)
        except (json.JSONDecodeError, RecursionError) as e:
            raise ValueError(
                f"JSON 解析失败:{e};原始输出前 200 字符:{raw[:200]}"
            ) from e

    if not isinstance(obj, dict):
        raise ValueError(f"解析结果不是 JSON 对象,而是 {type(obj).__name__}")
    return obj


# 视为空值的字符串集合(LLM 常把 JSON null 输出成这些文本)
_EMPTY_TOKENS = {"", "null", "none", "无", "n/a", "未知"}


def normalize_optional_str(value: Any) -> str | None:
    """将 LLM 可能输出的 'null' / 'None' / '无' 等空值归一化为 None

    注意:此归一化仅用于可选字段(如 student_id),不用于姓名等必填文本。
    """
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in _EMPTY_TOKENS:
        return None
    return s


def build_repair_user_message(raw_output: str, error_message: str) -> str:
    """构建"修复重试"的用户消息

    当首次输出无法通过 Pydantic 校验时,把原始输出与错误信息发回模型,
    要求其修正为标准 JSON(仅重试一次,避免无限循环)。
    """
    return (
        "你上一次的输出不符合要求,解析/校验失败。\n"
        f"失败原因:{error_message}\n"
        "你上次的原始输出如下:\n"
        "----------------\n"
        f"{raw_output[:4000]}\n"
        "----------------\n"
        "请修正后重新输出:只输出一个合法的 JSON 对象,不要包含任何其他文字或代码块围栏。"
    )
