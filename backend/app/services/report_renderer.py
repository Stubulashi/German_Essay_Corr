"""Markdown 报告统一渲染器(双管线一致性的关键组件)

设计决策:
- LLM 只负责产出结构化 JSON 字段(评分、错误清单、评语等);
- Markdown 报告由本模块**确定性渲染**,不依赖 LLM 自由发挥;
- 因此无论走管线 A 还是管线 B,`markdown_report` 的结构永远一致,
  前端渲染与教师阅读体验完全统一。

渲染规则(按规格):
- 报告头:学生姓名/学号、运行管线、考试标准、细致度、综合得分
- 总体评价:overall_comment
- 详细批改:逐条错误渲染(按细致度裁剪层级)
  - LOW:    仅"原句"(错误片段以 ❌ 标注)
  - MEDIUM: "原句" + "修改"(修正内容以 ✅ 标注)
  - HIGH:   "原句" + "修改" + "错因解析"
- 词汇与句型亮点/短板:亮点来自 highlights;短板由错误类型频次统计生成
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.models.schemas import (
    DetailLevel,
    ErrorItem,
    EssayCorrectionResult,
    GradingStandard,
    PipelineChoice,
)
from app.services.error_taxonomy import (
    CATEGORY_LABELS,
    CATEGORY_TEACHING_TIPS,
    annotate_errors,
    category_label_of,
)

# 中文标签 -> 分类键 的反查表(供学生版报告生成练习建议)
_LABEL_TO_CATEGORY_KEY: dict[str, str] = {
    label: key for key, label in CATEGORY_LABELS.items()
}

# 枚举 -> 中文展示文案映射
_STANDARD_LABELS: dict[GradingStandard, str] = {
    GradingStandard.GAOKAO: "高考",
    GradingStandard.DSD: "DSD",
}

_DETAIL_LABELS: dict[DetailLevel, str] = {
    DetailLevel.LOW: "低",
    DetailLevel.MEDIUM: "中",
    DetailLevel.HIGH: "高",
}

_DEFAULT_PIPELINE_NAMES: dict[PipelineChoice, str] = {
    PipelineChoice.PIPELINE_A_LOCAL: "本地 Qwen3.8-27B",
    PipelineChoice.PIPELINE_B_CLOUD: "云端 DeepSeek",
}


def _find_fragment(transcribed_text: str, fragment: str) -> int:
    """在转录全文中查找错误片段位置

    规则(全项目唯一实现,报告渲染与批注定位共用):
    1. 优先精确匹配;
    2. 失败时忽略大小写匹配;
    3. 仍未找到返回 -1。
    """
    idx = transcribed_text.find(fragment)
    if idx == -1:
        idx = transcribed_text.lower().find(fragment.lower())
    return idx


@dataclass
class ErrorSpan:
    """错误片段在转录全文中的定位区间(闭开区间 [start, end))"""

    start: int
    end: int
    error_index: int


def locate_error_spans(
    transcribed_text: str, errors: list[ErrorItem]
) -> tuple[list[ErrorSpan], list[int]]:
    """定位全部错误片段在转录全文中的区间(#13 批注视图与报告共用)

    - 匹配规则与报告渲染完全一致(_find_fragment:精确 → 忽略大小写);
    - 重叠处理:与已占用区间重叠的匹配视为未定位,避免渲染冲突;
    - 返回:(按位置升序的区间列表, 未能定位的错误下标列表)。

    未能定位的条目由调用方以清单形式兜底展示,保证信息不丢失。
    """
    spans: list[ErrorSpan] = []
    unlocated: list[int] = []
    occupied: list[tuple[int, int]] = []
    for index, err in enumerate(errors):
        fragment = (err.original_text or "").strip()
        if not fragment:
            unlocated.append(index)
            continue
        pos = _find_fragment(transcribed_text, fragment)
        if pos == -1:
            unlocated.append(index)
            continue
        end = pos + len(fragment)
        # 与已占用区间重叠 → 归入未定位(保持渲染确定性)
        if any(not (end <= s or pos >= e) for s, e in occupied):
            unlocated.append(index)
            continue
        occupied.append((pos, end))
        spans.append(ErrorSpan(start=pos, end=end, error_index=index))
    spans.sort(key=lambda span: span.start)
    return spans, unlocated


def _locate_error_context(transcribed_text: str, fragment: str) -> str:
    """在转录全文中定位错误片段,并以 ❌ 包裹展示(附带少量上下文)

    定位失败时降级为直接展示错误片段本身,保证渲染永不中断。
    """
    fragment = fragment.strip()
    if not fragment:
        return "❌ (片段缺失) ❌"
    # 优先精确匹配,其次忽略大小写匹配
    idx = _find_fragment(transcribed_text, fragment)
    if idx == -1:
        return f"❌ {fragment} ❌"
    # 截取前后各 30 字符作为上下文
    ctx_start = max(0, idx - 30)
    ctx_end = min(len(transcribed_text), idx + len(fragment) + 30)
    before = transcribed_text[ctx_start:idx]
    after = transcribed_text[idx + len(fragment) : ctx_end]
    prefix = "…" if ctx_start > 0 else ""
    suffix = "…" if ctx_end < len(transcribed_text) else ""
    return f"{prefix}{before}❌**{transcribed_text[idx:idx + len(fragment)]}**❌{after}{suffix}"


#: 错因解析拆分标记(单步推理 / 另注 / 补充说明 / 附注)
_EXPLANATION_SPLIT_RE = re.compile(r"(单步推理|另注|补充说明|附注)[:：]?")


@dataclass
class ExplanationParts:
    """错因解析的展示拆分(仅呈现层;不改 explanation 数据本身)"""

    rule: str
    steps: list[str]
    notes: list[str]


def _split_explanation(text: str) -> ExplanationParts:
    """把错因解析拆分为 规则 / 推理 / 说明 三段(启发式)

    以"单步推理/另注/补充说明/附注"为切分标记;无任何标记时整体作为规则段,
    保证零信息损失。拆分仅用于 Markdown 展示排版。
    """
    raw = (text or "").strip()
    if not raw:
        return ExplanationParts(rule="", steps=[], notes=[])
    segments = _EXPLANATION_SPLIT_RE.split(raw)
    rule = segments[0].strip(" 。;；\n")
    steps: list[str] = []
    notes: list[str] = []
    for index in range(1, len(segments) - 1, 2):
        label = segments[index]
        body = (segments[index + 1] or "").strip(" 。;；\n")
        if not body:
            continue
        (steps if label == "单步推理" else notes).append(body)
    if not rule and not steps and not notes:
        rule = raw
    return ExplanationParts(rule=rule, steps=steps, notes=notes)


def _render_weakness(errors_types: list[str]) -> str:
    """根据规范化考点分类的频次生成"薄弱点"描述

    例如:"动词位序(出现 3 次)、名词变格(出现 2 次)"
    """
    if not errors_types:
        return "暂未发现高频语法薄弱点"
    counter = Counter(errors_types)
    parts = [
        f"{name}(出现 {count} 次)" if count > 1 else str(name)
        for name, count in counter.most_common(5)
    ]
    return "、".join(parts)


def render_markdown_report(
    result: EssayCorrectionResult,
    pipeline_choice: PipelineChoice,
    pipeline_display_name: str,
    standard: GradingStandard,
    detail: DetailLevel,
) -> str:
    """将标准化批改结果渲染为规格 Markdown 报告

    Args:
        result:                标准化批改结果(markdown_report 字段会被覆盖)
        pipeline_choice:       教师选择的管线(display_name 为空时用于兜底展示名)
        pipeline_display_name: 实际执行管线的展示名(如"本地 Qwen3.8-27B")
        standard:              评分标准
        detail:                批改细致度

    Returns:
        完整 Markdown 报告字符串
    """
    display = pipeline_display_name or _DEFAULT_PIPELINE_NAMES.get(pipeline_choice, "未知管线")
    standard_label = _STANDARD_LABELS.get(standard, standard.value)
    detail_label = _DETAIL_LABELS.get(detail, detail.value)

    # 学生标题:姓名 + 学号(若有)
    name_part = result.student_name or "未知"
    title = f"# 德语作文批改报告 - {name_part}"
    if result.student_id:
        title += f" / {result.student_id}"

    lines: list[str] = [
        title,
        f"**运行管线:** {display} | **考试标准:** {standard_label} | **细致度:** {detail_label}",
        f"**综合得分:** {result.overall_score or '（未给出）'}",
        "",
        "---",
        "### 📝 总体评价",
        result.overall_comment or "（无）",
        "",
        "### 🔍 详细批改",
    ]

    # ---------- 详细批改:逐条错误 ----------
    if not result.errors:
        lines.append("_本次批改未发现明显语法错误,继续保持!_")
    else:
        for i, err in enumerate(result.errors, start=1):
            if i > 1:
                lines.append("")
                lines.append("---")
            lines.append("")
            lines.append(f"**错误 {i} · {err.error_type}**")
            # 原句:在转录全文中定位错误片段并标注 ❌(独立引用块)
            lines.append("> **原句**")
            lines.append(">")
            lines.append(f"> {_locate_error_context(result.transcribed_text, err.original_text)}")
            # 修改:细致度为 MEDIUM / HIGH 时展示(独立引用块)
            if detail in (DetailLevel.MEDIUM, DetailLevel.HIGH) and err.corrected_text:
                lines.append("")
                lines.append("> **修正**")
                lines.append(">")
                lines.append(f"> ✅ {err.corrected_text} ✅")
            # 错因解析:仅细致度 HIGH 展示;内部按 规则/推理/说明 拆条展示
            if detail == DetailLevel.HIGH and err.explanation:
                parts = _split_explanation(err.explanation)
                lines.append("")
                lines.append("> **错因解析**")
                lines.append(">")
                if parts.rule:
                    lines.append(f"> - **规则**:{parts.rule}")
                for step in parts.steps:
                    lines.append(f"> - **推理**:{step}")
                for note in parts.notes:
                    lines.append(f"> - **说明**:{note}")

    # ---------- 亮点与短板 ----------
    lines.append("")
    lines.append("### 📊 词汇与句型亮点/短板")
    lines.append("* **亮点:**")
    if result.highlights:
        for h in result.highlights:
            lines.append(f"  * {h}")
    else:
        lines.append("  * 暂无明显亮点,鼓励学生积累精彩句型")
    lines.append(f"* **薄弱点:** {_render_weakness([category_label_of(e) for e in result.errors])}")
    lines.append("")

    return "\n".join(lines)


def finalize_result(
    result: EssayCorrectionResult,
    pipeline_choice: PipelineChoice,
    pipeline_display_name: str,
    standard: GradingStandard,
    detail: DetailLevel,
) -> EssayCorrectionResult:
    """填充 result.markdown_report 字段并返回(原地修改)

    这一步是双管线统一性的最后一道关卡:所有管线的输出都必须经过本函数。
    同时在渲染前完成错因分类标准化(canonical_type 填充),保证:
    - 报告"薄弱点"使用规范化考点标签;
    - 落库的 error_records 分类聚合可靠。
    """
    # 错因分类标准化(为已有 canonical_type 的条目跳过)
    annotate_errors(result.errors)

    result.markdown_report = render_markdown_report(
        result=result,
        pipeline_choice=pipeline_choice,
        pipeline_display_name=pipeline_display_name,
        standard=standard,
        detail=detail,
    )
    return result


# =============================================================
# 学生版报告(#11:教师版 / 学生版双输出)
# =============================================================


DEFAULT_TEACHER_MESSAGE = "认真订正就是最好的复习,期待你的下一篇作文更进一步!"


def render_student_report(
    result: EssayCorrectionResult,
    standard: GradingStandard,
    detail: DetailLevel,
    teacher_message: str | None = None,
    show_score: bool | None = None,
    show_tips: bool | None = None,
) -> str:
    """渲染学生版报告(订正单)

    与教师版的差异:
    - 默认弱化分数(不展示得分,由教师自行公布;设置中心可开启"学生版显示得分");
    - 以"订正清单"为主体(原句 → 修正 → 小提示);
    - 附"做得好的地方"与"下一步练习重点"(可关闭,设置中心探索项);
    - 尾部教师寄语:优先使用教师自定义(teacher_message),为空时用系统默认。

    show_score / show_tips 为 None 时读取设置中心当前值(热生效)。
    """
    # 保证错因分类已填充(独立调用时的防御)
    annotate_errors(result.errors)

    # 学生版显示项(设置中心「教学与报告」;默认与历史行为一致)
    from app.config import settings as global_settings  # 延迟导入避免模块环

    if show_score is None:
        show_score = global_settings.student_report_show_score
    if show_tips is None:
        show_tips = global_settings.student_report_show_tips

    name = result.student_name or "同学"
    title = f"# 德语作文订正单 - {name}"
    if result.student_id:
        title += f" / {result.student_id}"

    standard_label = _STANDARD_LABELS.get(standard, standard.value)
    lines: list[str] = [title]
    if show_score and result.overall_score:
        lines.append(f"**本次得分:** {result.overall_score}")
    lines.extend(
        [
            f"**批改标准:** {standard_label} · 请对照下方清单逐条订正,并重写相关句子。",
            "",
            "---",
            "### 一、你的订正清单",
        ]
    )

    if not result.errors:
        lines.append("_本次批改没有发现明显错误,为你点赞!_")
    else:
        for i, err in enumerate(result.errors, start=1):
            if i > 1:
                lines.append("")
                lines.append("---")
            lines.append("")
            lines.append(f"**{i}. {category_label_of(err)}**")
            lines.append("> **原文**")
            lines.append(">")
            lines.append(f"> {_locate_error_context(result.transcribed_text, err.original_text)}")
            # 细致度非 LOW 时展示修正(独立引用块)
            if detail in (DetailLevel.MEDIUM, DetailLevel.HIGH) and err.corrected_text:
                lines.append("")
                lines.append("> **修正**")
                lines.append(">")
                lines.append(f"> ✅ {err.corrected_text} ✅")
            # HIGH 附小提示;内部按 规则/推理/说明 拆条展示
            if detail == DetailLevel.HIGH and err.explanation:
                parts = _split_explanation(err.explanation)
                lines.append("")
                lines.append("> **小提示**")
                lines.append(">")
                if parts.rule:
                    lines.append(f"> - **规则**:{parts.rule}")
                for step in parts.steps:
                    lines.append(f"> - **推理**:{step}")
                for note in parts.notes:
                    lines.append(f"> - **说明**:{note}")

    # ---- 做得好的地方 ----
    lines.append("")
    lines.append("### 二、做得好的地方")
    if result.highlights:
        for h in result.highlights:
            lines.append(f"* {h}")
    else:
        lines.append("* 继续保持书写与表达的完整性,每一次练习都是积累")

    # ---- 下一步练习重点(基于本次错因类别的练习建议;设置中心可关闭) ----
    if show_tips:
        lines.append("")
        lines.append("### 三、下一步练习重点")
        if result.errors:
            label_counter = Counter(category_label_of(e) for e in result.errors)
            for i, (label, count) in enumerate(label_counter.most_common(3), start=1):
                # 标签 -> 分类键 -> 练习建议
                category_key = _LABEL_TO_CATEGORY_KEY.get(label, "OTHER")
                tip = CATEGORY_TEACHING_TIPS.get(category_key, CATEGORY_TEACHING_TIPS["OTHER"])
                suffix = f"(本次出现 {count} 次)" if count > 1 else ""
                lines.append(f"{i}. **{label}**{suffix}:{tip}")
        else:
            lines.append("1. 尝试在下一篇作文中使用 1~2 个新学的连接词或从句结构")

    # ---- 教师寄语(教师可编辑,空则系统默认) ----
    lines.append("")
    lines.append("### 四、教师寄语")
    lines.append((teacher_message or "").strip() or DEFAULT_TEACHER_MESSAGE)
    lines.append("")

    return "\n".join(lines)
