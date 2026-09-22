"""后端 .env 配置文件的读取与安全更新(设置中心)

职责:
- 原地更新 .env 中指定键的值(保留注释、空行、未知键与原有顺序);
- 新键追加到文件末尾的"设置中心追加"段;
- 值的清洗与转义(拒绝换行/控制字符/引号;含空白或 # 的值用单引号包裹,
  python-dotenv 对单引号值不做转义处理,可安全保留 Windows 路径等反斜杠内容);
- 原子写入(同目录临时文件 + os.replace),避免半写坏文件。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: 允许的键名格式(与 .env 约定一致)
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
#: 匹配 `KEY=` 行(容忍前导空白与 `export `)
_LINE_RE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<rest>.*)$"
)
#: 禁止出现的控制字符(含换行)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
#: 需要单引号包裹的字符(空白或注释符号)
_QUOTE_NEEDED_RE = re.compile(r"[\s#]")

APPEND_SECTION_HEADER = "# ---------- 设置中心追加(由设置面板维护) ----------"


class EnvValueError(ValueError):
    """配置键/值不合法"""


def validate_key(key: str) -> str:
    """校验配置键名(仅大写字母/数字/下划线)"""
    if not _KEY_RE.match(key or ""):
        raise EnvValueError(f"非法配置键名:{key!r}")
    return key


def sanitize_value(value: object) -> str:
    """清洗并转义配置值(返回可直接写入 .env 的字符串)"""
    text = "" if value is None else str(value)
    if _CONTROL_RE.search(text):
        raise EnvValueError("配置值不能包含换行或控制字符")
    if len(text) > 500:
        raise EnvValueError("配置值过长(>500 字符)")
    if '"' in text or "'" in text:
        raise EnvValueError("配置值不能包含引号")
    if not text:
        return ""
    if _QUOTE_NEEDED_RE.search(text):
        return f"'{text}'"
    return text


def render_env_update(text: str, updates: dict[str, str]) -> str:
    """纯函数:在 .env 文本中原地更新键值(保留注释/顺序),未命中的键追加到末尾段"""
    if not updates:
        return text
    for key in updates:
        validate_key(key)
    remaining = {key: sanitize_value(value) for key, value in updates.items()}

    eol = "\r\n" if "\r\n" in text else "\n"
    out: list[str] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if m and m.group("key") in remaining:
            key = m.group("key")
            out.append(f"{m.group('prefix')}{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append(APPEND_SECTION_HEADER)
        for key, value in remaining.items():
            out.append(f"{key}={value}")
    result = eol.join(out)
    if text.endswith(("\n", "\r\n")):
        result += eol
    return result


def write_env_atomic(path: Path, updates: dict[str, str]) -> None:
    """读取现有 .env → 渲染更新 → 原子替换(同目录临时文件 + os.replace)"""
    original = ""
    if path.is_file():
        original = path.read_text(encoding="utf-8")
    rendered = render_env_update(original, updates)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(rendered)
    os.replace(tmp, path)
