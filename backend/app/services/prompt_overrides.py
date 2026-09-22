"""提示词微调附录(设置中心)

- 三个可编辑位:pipeline_a(管线 A 综合批改) / ocr(管线 B 识别) / grading(管线 B 评分);
- "有限编辑":仅允许追加一段"教师自定义补充要求",基础模板不可改
  (保证双管线输出 JSON 结构统一与安全约束不被破坏);
- 存储:app_settings(key = prompt_pipeline_a / prompt_ocr / prompt_grading);
- 内存缓存 + 启动加载,批改管线同步读取(get_appendix)。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.db_models import AppSetting

logger = logging.getLogger(__name__)

#: 可编辑的提示词位置
KINDS = ("pipeline_a", "ocr", "grading")
_KEY_PREFIX = "prompt_"
MAX_APPENDIX_CHARS = 2000

_cache: dict[str, str] = {kind: "" for kind in KINDS}


def get_appendix(kind: str) -> str:
    """当前追加指令(空串 = 无;供批改管线同步读取)"""
    return _cache.get(kind, "")


def sanitize_appendix(text: str) -> str:
    """清洗追加指令:去首尾空白;拒绝控制字符(换行/制表符除外);限长"""
    value = (text or "").strip()
    for ch in value:
        if ch in "\n\t":
            continue
        if ord(ch) < 32 or ord(ch) == 127:
            raise ValueError("自定义要求不能包含控制字符")
    return value[:MAX_APPENDIX_CHARS]


async def load_appendix_cache(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """应用启动时加载全部附录(由 lifespan 调用)"""
    async with session_factory() as session:
        for kind in KINDS:
            row = await session.get(AppSetting, _KEY_PREFIX + kind)
            _cache[kind] = (row.value or "").strip() if row is not None else ""
    active = [kind for kind in KINDS if _cache[kind]]
    logger.info("提示词微调附录已加载:%s", active or "无")


async def update_appendices(db: AsyncSession, updates: dict[str, str | None]) -> dict[str, str]:
    """更新附录(仅传入的 kind 生效;空串 = 清除)并同步缓存"""
    for kind, text in updates.items():
        if kind not in KINDS:
            raise ValueError(f"未知的提示词位置:{kind}")
        value = sanitize_appendix(text or "")
        row = await db.get(AppSetting, _KEY_PREFIX + kind)
        if row is None:
            db.add(AppSetting(key=_KEY_PREFIX + kind, value=value))
        else:
            row.value = value
        _cache[kind] = value
    await db.commit()
    return {kind: _cache[kind] for kind in KINDS}


def build_previews() -> dict[str, str]:
    """拼装三份"最终系统提示词"(含示范学习风格与附录)供只读预览"""
    # 延迟导入避免模块级循环(style_learning_service 会导入 prompts)
    from app.models.schemas import DetailLevel, GradingStandard
    from app.pipelines import prompts as prompt_lib
    from app.services.style_learning_service import get_active_style_text

    style_text = get_active_style_text()
    return {
        "pipeline_a": prompt_lib.build_pipeline_a_prompt(
            GradingStandard.GAOKAO,
            DetailLevel.MEDIUM,
            style_context=style_text,
            appendix=_cache["pipeline_a"],
        ),
        "ocr": prompt_lib.build_ocr_prompt(_cache["ocr"]),
        "grading": prompt_lib.build_grading_prompt(
            GradingStandard.GAOKAO,
            DetailLevel.MEDIUM,
            style_context=style_text,
            appendix=_cache["grading"],
        ),
    }
