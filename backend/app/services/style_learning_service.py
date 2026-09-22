"""示范学习服务(示例范文 → 批改风格画像 → 后续批改风格迁移)

职责:
- 归纳:读取一份已完成批改任务的 result(原文/逐处修改/评分/评语),
  调用文本 LLM 提炼"批改风格画像"(评分尺度 / 点评语气 / 修改偏好 / 表达习惯),
  固化为结构化 style_json + 可编辑的叙事文本 narrative;
- 管理:画像 CRUD、单一生效(activate 自动停用其他)、启用/停用/删除;
- 注入:内存缓存 + get_active_style_text()(供批改管线同步读取,零 IO)。

设计约定:
- 归纳产物与源任务解耦(source_task_id 弱关联):任务删除不影响画像生效;
- mock_mode 返回确定性样例画像,全链路可演示;
- LLM 调用优先 DeepSeek 配置,回退管线 A 本地 VLM(文本对话);
- 缓存刷新时机:启动加载、归纳、启用、停用、编辑、删除。
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings as global_settings
from app.models.db_models import CorrectionTask, StyleProfile
from app.services.llm_client import LLMClient
from app.services.parser import build_repair_user_message, extract_json_dict

logger = logging.getLogger(__name__)

#: 画像四个结构化维度
STYLE_DIMENSIONS = ("scoring_scale", "tone", "correction_preference", "expression_habits")
#: 注入文本长度上限(避免 Prompt 膨胀影响识别质量)
MAX_NARRATIVE_CHARS = 2000
#: 归纳时送入 LLM 的原文截断长度
_MAX_SOURCE_TEXT = 4000
#: 逐处修改最多展示条数
_MAX_ERRORS_IN_PROMPT = 30

STYLE_SUMMARY_SYSTEM_PROMPT = """【身份与使命】
你是一位资深德语教学研究专家,在本流程中担任"批改风格归纳师":
面对教师对一份示例范文的完整批改结果(学生原文、逐处修改、评分与评语),
归纳出这位教师可复用的"批改风格画像",供后续对其他学生作文保持同样风格。
你不重新判分、不纠错、不评价范文本身。

【工作流程】
1. 通读观察:先完整阅读学生原文、逐处修改与教师评语,关注"教师做了什么、没做什么、怎么表达";
2. 四维提取:逐个维度归纳;每个维度的 summary 必须引用至少 1 条来自本示例的具体证据(原文片段、修改方式或评语原句);
3. 撰写 narrative:一段 200~400 字的"操作说明书",直接写给后续批改员(第二人称),
   使其不看示例也能复现该教师的评分尺度、点评语气、修改偏好与表达习惯;
4. 输出严格 JSON。

【四维归纳要求】
1. scoring_scale(评分尺度):严格程度、扣分侧重(更关注语法还是表达)、给分宽严习惯(可对照示例分数推断);
   strictness 只能取值 严格|适中|宽松;
2. tone(点评语气):语气风格(严谨/鼓励/中性)、评语的开篇与收尾方式;引用评语原句佐证;
3. correction_preference(修改偏好):修改粒度与深度、优先修正的错误类型、是否保留学生原意与表达习惯;
   depth 只能取值 注重细节|适中|抓大放小;
4. expression_habits(表达习惯):常用术语、句式、解析语言(中文还是德语)与格式习惯;
   terms 列出示例中实际出现的术语。

【边界情况处理】
- 示例信息不全(如无评语或无修改):该维度按现有信息归纳,并在 summary 中注明"依据有限";
- 示例错误极少:从"未改动之处"与评语的肯定方式归纳风格,不得虚构教师的严格程度;
- 修改与评语风格矛盾:以修改行为为主、评语为辅,并在对应 summary 中说明该矛盾;
- 严禁脑补:任何结论都必须能在示例中找到出处。

【输出前自检】
① 只输出一个 JSON 对象(无围栏、无解释文字);
② 四个维度的键名与子字段名严格按契约,不多不少;strictness / depth 取值只能来自规定枚举;
③ 每个维度的 summary 均引用 1 条以上示例证据;
④ narrative 为 200~400 字、操作说明书口径、第二人称;
⑤ 信息不足的维度也必须给出 summary(注明依据有限),不得留空或编造。

你必须只输出一个合法 JSON 对象(不要输出解释文字或 Markdown 代码围栏):
{
  "scoring_scale": {"summary": "...", "strictness": "严格|适中|宽松", "focus": ["..."]},
  "tone": {"summary": "...", "style": "..."},
  "correction_preference": {"summary": "...", "depth": "注重细节|适中|抓大放小", "priorities": ["..."]},
  "expression_habits": {"summary": "...", "terms": ["..."]},
  "narrative": "一段 200-400 字的综合风格描述,直接写给后续的批改员(操作说明口吻),指导其在评分尺度、点评语气、修改偏好与表达习惯上与原批改保持一致"
}""".strip()


# ---------------------------------------------------------
# 内存缓存(供批改管线同步读取)
# ---------------------------------------------------------
_active_text: str = ""


def get_active_style_text() -> str:
    """当前生效风格的注入文本(空字符串 = 未启用;供批改管线调用)"""
    return _active_text


def render_style_context(profile: StyleProfile) -> str:
    """把画像渲染为注入文本(当前实现为 narrative 限长截取)"""
    return (profile.narrative or "").strip()[:MAX_NARRATIVE_CHARS]


async def _reload_cache(db: AsyncSession) -> None:
    """从数据库重算当前生效风格文本并写入缓存"""
    global _active_text
    profile = (
        await db.execute(select(StyleProfile).where(StyleProfile.status == "active"))
    ).scalars().first()
    _active_text = render_style_context(profile) if profile else ""


async def load_active_cache(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """应用启动时加载生效画像到内存缓存(由 lifespan 调用)"""
    async with session_factory() as session:
        await _reload_cache(session)
    logger.info("示范学习:生效风格%s", "已加载" if _active_text else "未启用")


async def reload_cache(db: AsyncSession) -> None:
    """按当前数据库状态刷新生效风格缓存

    供外部批量维护动作(示例数据重建 / 全部数据清除)在提交后同步缓存,
    避免批改继续注入已删除或尚未加载的风格文本。
    """
    await _reload_cache(db)


# ---------------------------------------------------------
# 归纳(纯函数可测 + LLM 编排)
# ---------------------------------------------------------
def parse_style_output(raw: str) -> dict:
    """解析 LLM 的风格归纳输出(容错)

    Returns:
        {"style": {四维度...}, "narrative": "..."}

    Raises:
        ValueError: 缺少可用的风格描述(由调用方決定重试或报错)
    """
    obj = extract_json_dict(raw)
    style: dict = {}
    for dim in STYLE_DIMENSIONS:
        value = obj.get(dim)
        style[dim] = value if isinstance(value, dict) else {}
    narrative = str(obj.get("narrative") or "").strip()
    if not narrative:
        # narrative 缺失时用各维度 summary 拼装兜底
        parts = [str(style[dim].get("summary") or "").strip() for dim in STYLE_DIMENSIONS]
        narrative = "\n".join(p for p in parts if p)
    if not narrative:
        raise ValueError("归纳结果缺少可用的风格描述")
    return {"style": style, "narrative": narrative[:MAX_NARRATIVE_CHARS]}


def _mock_style_payload() -> dict:
    """mock 模式的确定性样例画像(与 MockPipeline 演示行为一致)"""
    style = {
        "scoring_scale": {
            "summary": "严格按 25 分制扣分,重点关注语序、名词变格与框型结构;同类重复错误合并计分并在解析中说明次数。",
            "strictness": "严格",
            "focus": ["动词位序", "名词变格", "框型结构"],
        },
        "tone": {
            "summary": "评语以肯定开篇,问题描述客观严谨,结尾给出明确的改进优先级,不使用空泛的鼓励语。",
            "style": "鼓励式严谨",
        },
        "correction_preference": {
            "summary": "逐处修改且保留学生原意,优先修正影响交际的语法错误,词汇替换保守、不擅自提升表达难度。",
            "depth": "注重细节",
            "priorities": ["动词位序", "介词搭配", "大小写"],
        },
        "expression_habits": {
            "summary": "解析统一使用中文说明语法规则,并给出德语对照示例;术语使用与错因分类词表一致。",
            "terms": ["框型结构", "变格", "位序"],
        },
    }
    narrative = (
        "评分尺度:严格按 25 分制扣分,同等严重度下优先扣语序、名词变格与框型结构类错误,"
        "重复的同类错误合并为一条并注明出现次数。点评语气:先肯定优点,再客观指出问题,"
        "结尾给出按优先级排序的改进建议。修改偏好:逐处修改并保留学生原意,词汇替换保守,"
        "不擅自拔高表达。表达习惯:解析用中文说明语法规则并附德语对照示例,术语与错因分类词表一致。"
    )
    return {"style": style, "narrative": narrative}


def _build_summary_user_message(task: CorrectionTask) -> str:
    """把任务的批改结果组装为归纳用用户消息"""
    result = task.result or {}
    errors = result.get("errors") or []
    error_lines: list[str] = []
    for i, err in enumerate(errors[:_MAX_ERRORS_IN_PROMPT], start=1):
        line = f"{i}. [{err.get('error_type', '')}] {err.get('original_text', '')} → {err.get('corrected_text', '')}"
        if err.get("explanation"):
            line += f"(解析:{err['explanation']})"
        error_lines.append(line)
    highlights = result.get("highlights") or []
    text = str(result.get("transcribed_text") or "")[:_MAX_SOURCE_TEXT]
    return (
        "以下是示例范文的批改结果:\n\n"
        f"【学生原文】\n{text}\n\n"
        f"【评分】{result.get('overall_score', '')}\n"
        f"【总体评语】{result.get('overall_comment', '')}\n\n"
        f"【逐处修改(共 {len(errors)} 条,展示前 {len(error_lines)} 条)】\n"
        + ("\n".join(error_lines) if error_lines else "(无)")
        + "\n\n【亮点】\n"
        + ("\n".join(str(h) for h in highlights) if highlights else "(无)")
        + "\n\n请按要求归纳批改风格画像(只输出 JSON):\n"
        "- 每个维度的 summary 至少引用 1 条以上示例中的具体证据(原文片段/修改方式/评语原句);\n"
        "- narrative 使用操作说明书口径(200~400 字,第二人称,写给后续批改员)。"
    )


async def _call_summary_llm(task: CorrectionTask, repair: tuple[str, str] | None = None) -> str:
    """调用文本 LLM 归纳风格;repair=(上次输出, 错误信息) 时触发修复重试"""
    s = global_settings
    if s.deepseek_api_key:
        model = s.deepseek_reasoning_model if s.deepseek_use_reasoning else s.deepseek_model
        client = LLMClient(
            base_url=s.deepseek_base_url, api_key=s.deepseek_api_key, model=model,
            timeout=s.deepseek_timeout, pipeline_tag="STYLE-LEARN", params_target="deepseek",
        )
    elif s.local_vlm_base_url:
        client = LLMClient(
            base_url=s.local_vlm_base_url, api_key=s.local_vlm_api_key, model=s.local_vlm_model,
            timeout=s.local_vlm_timeout, pipeline_tag="STYLE-LEARN-LOCAL", params_target="pipeline_a",
        )
    else:
        raise RuntimeError(
            "未配置可用的文本模型端点:请先配置 DEEPSEEK_API_KEY(推荐)或 LOCAL_VLM_BASE_URL"
        )

    messages: list[dict] = [
        {"role": "system", "content": STYLE_SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": _build_summary_user_message(task)},
    ]
    if repair is not None:
        raw_output, error_message = repair
        messages.append({"role": "user", "content": build_repair_user_message(raw_output, error_message)})
    return await client.chat(messages, temperature=0.3)


# ---------------------------------------------------------
# 画像 CRUD 与激活(单一生效)
# ---------------------------------------------------------
async def list_profiles(db: AsyncSession) -> list[StyleProfile]:
    """列出全部画像(active 优先,其余按创建时间倒序)"""
    stmt = select(StyleProfile).order_by(
        StyleProfile.status.desc(), StyleProfile.created_at.desc(), StyleProfile.id.desc()
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_profile(db: AsyncSession, profile_id: int) -> StyleProfile:
    """按 ID 获取画像(不存在抛 LookupError)"""
    profile = await db.get(StyleProfile, profile_id)
    if profile is None:
        raise LookupError(f"风格画像 {profile_id} 不存在")
    return profile


async def get_status(db: AsyncSession) -> tuple[StyleProfile | None, int]:
    """当前生效画像与画像总数(供 /api/style/status)"""
    profiles = await list_profiles(db)
    active = next((p for p in profiles if p.status == "active"), None)
    return active, len(profiles)


async def learn_from_task(
    db: AsyncSession, *, task_id: int, name: str | None = None
) -> StyleProfile:
    """从已完成批改任务归纳风格画像(归纳完成默认自动启用)

    Raises:
        LookupError: 任务不存在
        ValueError:  任务未完成 / 输出无法解析
        RuntimeError: LLM 未配置 / 调用失败
    """
    task = await db.get(CorrectionTask, task_id)
    if task is None:
        raise LookupError(f"批改任务 {task_id} 不存在")
    if task.status != "COMPLETED" or not task.result:
        raise ValueError("仅【已完成】的批改任务可用于示范归纳,请先完成该示例作文的批改")

    if global_settings.mock_mode:
        payload = _mock_style_payload()
    else:
        raw = await _call_summary_llm(task)
        try:
            payload = parse_style_output(raw)
        except ValueError as first_error:
            # 修复重试一次(与批改管线的容错策略一致)
            logger.warning("风格归纳首次解析失败,发起修复重试:%s", first_error)
            raw = await _call_summary_llm(task, repair=(raw, str(first_error)))
            payload = parse_style_output(raw)

    # 单一生效:先清空全部 active,再激活新画像
    await db.execute(update(StyleProfile).values(status="inactive"))
    profile = StyleProfile(
        name=(name or "").strip() or f"示范风格 · 任务 {task.id}",
        source_task_id=task.id,
        source_student_name=task.student_name,
        style_json=payload["style"],
        narrative=payload["narrative"],
        status="active",
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    await _reload_cache(db)
    logger.info("示范学习:从任务 %s 归纳出新画像 %s(已启用)", task.id, profile.id)
    return profile


async def update_profile(
    db: AsyncSession, profile_id: int, *, name: str | None = None, narrative: str | None = None
) -> StyleProfile:
    """编辑画像名称 / 注入文本(若为生效中画像则刷新缓存)"""
    profile = await get_profile(db, profile_id)
    if name is not None:
        profile.name = name.strip() or profile.name
    if narrative is not None:
        profile.narrative = narrative.strip()[:MAX_NARRATIVE_CHARS]
    await db.commit()
    await db.refresh(profile)
    if profile.status == "active":
        await _reload_cache(db)
    return profile


async def activate(db: AsyncSession, profile_id: int) -> StyleProfile:
    """启用画像(自动停用其他;刷新缓存)"""
    profile = await get_profile(db, profile_id)
    await db.execute(update(StyleProfile).values(status="inactive"))
    profile.status = "active"
    await db.commit()
    await db.refresh(profile)
    await _reload_cache(db)
    logger.info("示范学习:画像 %s 已启用", profile_id)
    return profile


async def deactivate(db: AsyncSession, profile_id: int) -> StyleProfile:
    """停用画像(若原为生效中则清空缓存)"""
    profile = await get_profile(db, profile_id)
    was_active = profile.status == "active"
    profile.status = "inactive"
    await db.commit()
    await db.refresh(profile)
    if was_active:
        await _reload_cache(db)
        logger.info("示范学习:画像 %s 已停用,后续批改恢复默认风格", profile_id)
    return profile


async def delete_profile(db: AsyncSession, profile_id: int) -> None:
    """删除画像(若为生效中则清空缓存)"""
    profile = await get_profile(db, profile_id)
    was_active = profile.status == "active"
    await db.delete(profile)
    await db.commit()
    if was_active:
        await _reload_cache(db)
    logger.info("示范学习:画像 %s 已删除", profile_id)
