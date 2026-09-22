"""姓名 / 学号解析与名单对齐(方向二:定位器)

把"识别问题"转化为"名单对齐的分类问题":
决策链(从强到弱):
1. 上传时预指派(教师明确指定,最高可信);
2. 花名册学号精确匹配(识别/转录得到的学号与花名册一致);
3. 花名册姓名精确匹配;
4. 转录头部文本包含花名册姓名(抬头区域出现即认定);
5. LLM 识别结果(规范化后);
6. 兜底 "未知"。

明确不做:中文姓名正则"猜测"(低精度会引入错误归属);
学号的正则提取仅作为候选来源之一(6-12 位数字)。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models.db_models import ClassRoster
from app.services.student_assignment import normalize_fullwidth

logger = logging.getLogger(__name__)

#: 抬头区姓名模式(姓名/Name/名字 + 可选冒号)
_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:姓名|名字|Name)[:：\s]*([\u4e00-\u9fa5]{2,4})"),
]
#: 抬头区学号模式(学号/学籍号/编号/No./Nr. + 6-12 位数字)
_ID_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:学号|学籍号|编号|No\.?|Nr\.?)[:：\s]*(\d{6,12})"),
]
#: 抬头扫描行数
_HEAD_LINES = 5


@dataclass
class ResolvedIdentity:
    """解析后的学生身份"""

    student_name: str
    student_id: str | None
    source: str  # preassigned / roster_id / roster_name / llm / unknown


def extract_from_head(transcribed_text: str) -> tuple[str | None, str | None, str]:
    """从转录头部(前 5 行)提取姓名与学号候选

    Returns:
        (姓名或 None, 学号或 None, 规范化后的头部文本)
    """
    lines = (transcribed_text or "").splitlines()[:_HEAD_LINES]
    head = normalize_fullwidth("\n".join(lines))
    name: str | None = None
    student_id: str | None = None
    for pattern in _NAME_PATTERNS:
        m = pattern.search(head)
        if m:
            name = m.group(1)
            break
    for pattern in _ID_PATTERNS:
        m = pattern.search(head)
        if m:
            student_id = m.group(1)
            break
    return name, student_id, head


def resolve_identity(
    *,
    preassigned_name: str | None,
    preassigned_id: str | None,
    llm_name: str | None,
    llm_id: str | None,
    transcribed_text: str,
    roster: list[ClassRoster],
) -> ResolvedIdentity:
    """按决策链解析最终学生身份(纯函数,可独立测试)"""
    # 1. 预指派(上传时教师明确指定)
    if preassigned_name and preassigned_name.strip() and preassigned_name != "未知":
        return ResolvedIdentity(preassigned_name.strip(), _clean_id(preassigned_id), "preassigned")

    head_name, head_id, head_text = extract_from_head(transcribed_text)
    candidate_ids = [cid for cid in (_clean_id(llm_id), head_id) if cid]
    candidate_names = [n for n in (_clean_name(llm_name), head_name) if n]

    # 2. 花名册学号精确匹配
    for cid in candidate_ids:
        for member in roster:
            if member.student_id and member.student_id == cid:
                return ResolvedIdentity(member.name, member.student_id, "roster_id")

    # 3. 花名册姓名精确匹配
    for cname in candidate_names:
        for member in roster:
            if member.name == cname:
                return ResolvedIdentity(member.name, member.student_id, "roster_name")

    # 4. 抬头文本包含花名册姓名(如 "姓名:李明" 之外的直接书写)
    for member in roster:
        if member.name and member.name in head_text:
            return ResolvedIdentity(member.name, member.student_id, "roster_name")

    # 5. LLM 识别结果(规范化后)
    fallback_name = candidate_names[0] if candidate_names else None
    if fallback_name:
        return ResolvedIdentity(fallback_name, candidate_ids[0] if candidate_ids else None, "llm")

    # 6. 兜底
    return ResolvedIdentity("未知", candidate_ids[0] if candidate_ids else None, "unknown")


def _clean_name(value: str | None) -> str | None:
    """姓名清洗:去空白;'未知/None 等空值'归一为 None"""
    if value is None:
        return None
    text = normalize_fullwidth(str(value))
    if not text or text in ("未知", "None", "null"):
        return None
    return text


def _clean_id(value: str | None) -> str | None:
    """学号清洗:全角转半角、去空格;非 6-12 位数字交由调用方宽容处理(保留原值)"""
    if value is None:
        return None
    text = normalize_fullwidth(str(value)).replace(" ", "")
    if not text or text in ("未知", "None", "null"):
        return None
    return text
