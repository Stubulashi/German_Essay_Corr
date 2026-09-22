"""上传时的学生指派服务(方向四)

三种指派方式(上传表单 assign_mode):
- recognize: 不预填,批改后由识别 + 名单对齐回填(现状行为);
- filename:  按文件名匹配花名册(文件名含学号或姓名,如 20260123_李明.jpg);
- order:     按名单顺序匹配(选定花名册起始序号,上传顺序逐一对应)。

此外提供花名册文本解析(导入端点复用)与学生信息规范化工具。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import ClassRoster

logger = logging.getLogger(__name__)

#: 合法学号候选:6-12 位连续数字
_STUDENT_ID_RE = re.compile(r"^\d{6,12}$")
#: 文件名切分符(下划线/连字符/空格/点/逗号/顿号/括号)
_TOKEN_SPLIT_RE = re.compile(r"[_\-\s.,、()（）\[\]]+")
#: 中文姓名候选(2-4 个汉字)
_CHINESE_NAME_RE = re.compile(r"^[\u4e00-\u9fa5]{2,4}$")


@dataclass
class Assignment:
    """一条指派结果"""

    student_name: str | None
    student_id: str | None
    method: str  # filename / order / none
    matched: bool  # 是否命中花名册


def normalize_fullwidth(text: str) -> str:
    """全角字符转半角(数字/字母/标点;NFKC 同时处理常见兼容字符)"""
    return unicodedata.normalize("NFKC", text or "").strip()


def parse_roster_text(text: str) -> list[tuple[str, str | None]]:
    """解析花名册文本(每行 姓名[,学号])

    支持:逗号/制表符/空格分隔;自动跳过期头行(含"姓名"或"name");
    返回 [(姓名, 学号或 None)](保持出现顺序,按姓名去重)。
    """
    results: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = normalize_fullwidth(raw_line)
        if not line:
            continue
        # 期头行跳过
        if "姓名" in line or line.lower().startswith("name"):
            continue
        # 逗号/制表符/空白都可作为分隔(单空格也兼容,便于直接从表格粘贴)
        parts = re.split(r"[,，\t]|\s+", line)
        parts = [p.strip() for p in parts if p.strip()]
        if not parts:
            continue
        name = parts[0]
        student_id: str | None = None
        if len(parts) >= 2 and _STUDENT_ID_RE.match(parts[1]):
            student_id = parts[1]
        # 两侧都有冒号写法兼容:姓名: 张三
        if name.endswith(":") or name.endswith(":"):
            name = name.rstrip(":：").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        results.append((name, student_id))
    return results


async def load_roster(db: AsyncSession, class_id: int | None) -> list[ClassRoster]:
    """加载班级花名册(无班级返回空列表;按 id 升序 = 导入顺序)"""
    if class_id is None:
        return []
    stmt = select(ClassRoster).where(ClassRoster.class_id == class_id).order_by(ClassRoster.id)
    return list((await db.execute(stmt)).scalars().all())


def assign_from_filename(filename: str, roster: list[ClassRoster]) -> Assignment:
    """按文件名匹配花名册

    规则(仅强匹配,不做模糊猜测):
    1. 文件名中的 token 若为 6-12 位数字且与花名册学号精确相等 → 命中;
    2. token 若与花名册姓名精确相等 → 命中;
    3. 整个文件名(去扩展名)包含花名册姓名 → 命中;
    未命中时返回 matched=False 且不带预填(交由识别流程处理)。
    """
    stem = normalize_fullwidth(filename.rsplit(".", 1)[0])
    tokens = [t for t in _TOKEN_SPLIT_RE.split(stem) if t]

    # 1) 学号精确匹配(优先级最高)
    for token in tokens:
        if _STUDENT_ID_RE.match(token):
            for member in roster:
                if member.student_id and member.student_id == token:
                    return Assignment(member.name, member.student_id, "filename", True)

    # 2) 姓名 token 精确匹配
    for token in tokens:
        if _CHINESE_NAME_RE.match(token):
            for member in roster:
                if member.name == token:
                    return Assignment(member.name, member.student_id, "filename", True)

    # 3) 整个文件名包含姓名(如 "高二3班_李明_作文.jpg" 中姓名为子串)
    for member in roster:
        if member.name and member.name in stem:
            return Assignment(member.name, member.student_id, "filename", True)

    return Assignment(None, None, "filename", False)


def assign_from_order(
    roster: list[ClassRoster],
    order_start: int,
    sequence_index: int,
) -> Assignment:
    """按名单顺序匹配

    Args:
        roster:         花名册(按导入顺序)
        order_start:    起始序号(1-based,对应用户选择)
        sequence_index: 本次上传中的第几条(0-based)

    Returns:
        命中返回成员信息;越界返回 matched=False 的空指派。
    """
    pos = (max(1, order_start) - 1) + sequence_index
    if 0 <= pos < len(roster):
        member = roster[pos]
        return Assignment(member.name, member.student_id, "order", True)
    return Assignment(None, None, "order", False)
