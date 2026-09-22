"""错因分类标准化(考点知识库映射)

背景:
LLM 自由生成的 error_type 五花八门(Word Order / 语序错误 / Satzbau 混乱 …),
直接用于统计会让"班级共性错因分析"和"学生错题本"的数据碎片化。

方案:
- 定义固定的考点分类体系(ErrorCategory,约 19 类,覆盖高考/DSD 高频考点);
- 通过"精确键名 → 中文标签 → 别名表 → 子串关键词"四级匹配,
  把任意 error_type 文本归一到唯一类别;
- 无法匹配的归入 OTHER,绝不抛出异常(统计流程永远可用)。

本模块是 #2 班级共性错因分析 与 #3 学生错题本 的数据地基。
"""

from __future__ import annotations

import re
from enum import Enum

from app.models.schemas import ErrorItem


class ErrorCategory(str, Enum):
    """考点分类(存储使用英文键名,展示使用中文标签)"""

    VERB_POSITION = "VERB_POSITION"  # 动词位序(主句第二位/从句末位/倒装)
    SENTENCE_FRAME = "SENTENCE_FRAME"  # 框型结构(Satzklammer:可分动词/完成时框架)
    CASE_DECLENSION = "CASE_DECLENSION"  # 名词变格与词尾
    PREPOSITION = "PREPOSITION"  # 介词搭配(介词 + 要求的格)
    ADJECTIVE_ENDING = "ADJECTIVE_ENDING"  # 形容词词尾
    ARTICLE = "ARTICLE"  # 冠词使用
    PRONOUN = "PRONOUN"  # 代词使用
    VERB_FORM = "VERB_FORM"  # 动词形式(变位/分词/情态动词)
    TENSE = "TENSE"  # 时态与时间表达
    SUBJECT_VERB_AGREEMENT = "SUBJECT_VERB_AGREEMENT"  # 主谓一致
    NEGATION = "NEGATION"  # 否定表达
    PLURAL = "PLURAL"  # 名词复数
    SENTENCE_STRUCTURE = "SENTENCE_STRUCTURE"  # 句子成分缺失/冗余/结构
    WORD_CHOICE = "WORD_CHOICE"  # 词汇选择与用词
    SPELLING = "SPELLING"  # 拼写
    CAPITALIZATION = "CAPITALIZATION"  # 大小写
    PUNCTUATION = "PUNCTUATION"  # 标点
    STYLE_COHERENCE = "STYLE_COHERENCE"  # 篇章结构与连贯性
    OTHER = "OTHER"  # 未分类


# 类别 -> 中文标签(前端展示、报告"薄弱点"统计使用)
CATEGORY_LABELS: dict[ErrorCategory, str] = {
    ErrorCategory.VERB_POSITION: "动词位序",
    ErrorCategory.SENTENCE_FRAME: "框型结构",
    ErrorCategory.CASE_DECLENSION: "名词变格",
    ErrorCategory.PREPOSITION: "介词搭配",
    ErrorCategory.ADJECTIVE_ENDING: "形容词词尾",
    ErrorCategory.ARTICLE: "冠词",
    ErrorCategory.PRONOUN: "代词",
    ErrorCategory.VERB_FORM: "动词形式",
    ErrorCategory.TENSE: "时态",
    ErrorCategory.SUBJECT_VERB_AGREEMENT: "主谓一致",
    ErrorCategory.NEGATION: "否定",
    ErrorCategory.PLURAL: "名词复数",
    ErrorCategory.SENTENCE_STRUCTURE: "句子成分",
    ErrorCategory.WORD_CHOICE: "词汇选择",
    ErrorCategory.SPELLING: "拼写",
    ErrorCategory.CAPITALIZATION: "大小写",
    ErrorCategory.PUNCTUATION: "标点",
    ErrorCategory.STYLE_COHERENCE: "篇章与表达",
    ErrorCategory.OTHER: "其他",
}

# 中文标签 -> 类别(反向索引)
_LABEL_TO_CATEGORY: dict[str, ErrorCategory] = {v: k for k, v in CATEGORY_LABELS.items()}

# 别名表:归一化(小写、去空白/连字符/下划线)后的文本 -> 类别
# 覆盖:历史英文输出、德语术语、中文常见写法
_ALIASES: dict[str, ErrorCategory] = {}


def _register(aliases: list[str], category: ErrorCategory) -> None:
    """将一组别名注册到别名表(统一小写并去除空白、连字符、下划线)"""
    for alias in aliases:
        _ALIASES[_normalize_token(alias)] = category


def _normalize_token(text: str) -> str:
    """归一化匹配键:小写、去除空格/连字符/下划线/中英文标点"""
    text = text.strip().lower()
    return re.sub(r"[\s\-_/、,。,.()()]+", "", text)


# ---------- 静态映射定义 ----------
_register(
    ["word order", "verb position", "wortstellung", "satzstellung", "verbposition",
     "语序", "语序错误", "倒装"],
    ErrorCategory.VERB_POSITION,
)
_register(
    ["sentence frame", "satzklammer", "verb bracket", "frame structure",
     "框型结构", "框形结构", "动词框架", "可分动词"],
    ErrorCategory.SENTENCE_FRAME,
)
_register(
    ["case", "case ending", "case declension", "kasus", "deklination", "derkasus",
     "名词变格", "变格", "变格词尾", "名词词尾", "格的错误", "格"],
    ErrorCategory.CASE_DECLENSION,
)
_register(
    ["preposition", "präposition", "praeposition", "介词搭配", "介词错误", "介词"],
    ErrorCategory.PREPOSITION,
)
_register(
    ["adjective ending", "adjektivendung", "adjective declension",
     "形容词词尾", "形容词变格", "形容词变化"],
    ErrorCategory.ADJECTIVE_ENDING,
)
_register(
    ["article", "artikel", "冠词", "定冠词", "不定冠词"],
    ErrorCategory.ARTICLE,
)
_register(
    ["pronoun", "pronomen", "代词", "代词使用"],
    ErrorCategory.PRONOUN,
)
_register(
    ["verb form", "verbform", "konjugation", "partizip", "verb conjugation",
     "动词形式", "动词变位", "变位", "分词", "情态动词"],
    ErrorCategory.VERB_FORM,
)
_register(
    ["tense", "tempus", "时态", "时态错误", "时间表达"],
    ErrorCategory.TENSE,
)
_register(
    ["subject-verb agreement", "kongruenz", "主谓一致", "一致性"],
    ErrorCategory.SUBJECT_VERB_AGREEMENT,
)
_register(
    ["negation", "negierung", "否定", "否定表达", "nichtkein"],
    ErrorCategory.NEGATION,
)
_register(
    ["plural", "pluralform", "名词复数", "复数形式", "复数"],
    ErrorCategory.PLURAL,
)
_register(
    ["sentence structure", "satzbau", "成分缺失", "句子结构", "句子成分", "成分残缺"],
    ErrorCategory.SENTENCE_STRUCTURE,
)
_register(
    ["vocabulary", "word choice", "wortwahl", "lexik", "vokabel",
     "词汇选择", "词汇", "用词", "选词", "搭配"],
    ErrorCategory.WORD_CHOICE,
)
_register(
    ["spelling", "rechtschreibung", "拼写", "拼写错误"],
    ErrorCategory.SPELLING,
)
_register(
    ["capitalization", "capitalisation", "großschreibung", "grossschreibung",
     "lowercase", "uppercase", "kleinschreibung",
     "大小写", "首字母大写", "大写", "小写"],
    ErrorCategory.CAPITALIZATION,
)
_register(
    ["punctuation", "interpunktion", "zeichensetzung", "标点", "标点符号"],
    ErrorCategory.PUNCTUATION,
)
_register(
    ["style", "coherence", "stil", "kohärenz", "kohaerenz", "structure",
     "篇章", "连贯性", "篇章结构", "表达", "篇章与表达"],
    ErrorCategory.STYLE_COHERENCE,
)
_register(["other", "sonstiges", "unclassified", "其他", "其它"], ErrorCategory.OTHER)

# 子串扫描用的别名列表:按长度降序,保证更长、更具体的别名优先命中
# (例如 "lowercaseerror" 应先命中 "lowercase" 相关长词,而非被 "case" 误匹配)
_SUBSTRING_ALIASES: list[tuple[str, ErrorCategory]] = sorted(
    _ALIASES.items(), key=lambda kv: -len(kv[0])
)


# 考点分类 -> 讲评/练习建议(供班级讲评摘要与学生版报告复用)
CATEGORY_TEACHING_TIPS: dict[str, str] = {
    ErrorCategory.VERB_POSITION.value: "复习动词位置规则(主句第二位、从句末位、倒装),专项练习 dass/weil/obwohl 等从句",
    ErrorCategory.SENTENCE_FRAME.value: "复习框型结构(Satzklammer):可分动词前缀与情态动词/完成时的句框",
    ErrorCategory.CASE_DECLENSION.value: "系统温习名词变格表(第一/三/四格)及形容词、冠词词尾变化",
    ErrorCategory.PREPOSITION.value: "整理高频介词搭配表,强调每个介词要求的格(an+Dat./Akk. 等)",
    ErrorCategory.ADJECTIVE_ENDING.value: "复习形容词词尾变化三张表(弱/强/混合变化)",
    ErrorCategory.ARTICLE.value: "复习定冠词/不定冠词/零冠词的使用场景与名词性属",
    ErrorCategory.PRONOUN.value: "复习人称代词、物主代词、关系代词的变格",
    ErrorCategory.VERB_FORM.value: "复习变位规则、强变化动词表与情态动词用法",
    ErrorCategory.TENSE.value: "复习现在完成时/过去时的时间表达与助动词选择",
    ErrorCategory.SUBJECT_VERB_AGREEMENT.value: "强调主语与动词的数的一致,注意复合主语的用法",
    ErrorCategory.NEGATION.value: "复习 nicht/kein 的区分与位置规则",
    ErrorCategory.PLURAL.value: "复习名词复数类型(-e/-er/-en/-s/无变化)",
    ErrorCategory.SENTENCE_STRUCTURE.value: "梳理句子主干成分,练习避免成分残缺与冗余",
    ErrorCategory.WORD_CHOICE.value: "积累高频词固定搭配与近义词辨析",
    ErrorCategory.SPELLING.value: "整理高频拼写错误词表,强化听写练习",
    ErrorCategory.CAPITALIZATION.value: "强调名词首字母大写的识别训练",
    ErrorCategory.PUNCTUATION.value: "复习逗号在从句与并列句中的使用规则",
    ErrorCategory.STYLE_COHERENCE.value: "训练段落结构与衔接词使用(zwar...aber、einerseits...andererseits 等)",
    ErrorCategory.OTHER.value: "结合具体错例做个别说明",
}


def normalize_error_type(raw: str | None) -> ErrorCategory:
    """将任意 error_type 文本归一到固定分类体系

    匹配优先级:
    1. 英文键名精确匹配(如 "VERB_POSITION");
    2. 中文标签精确匹配(如 "名词变格");
    3. 别名表精确匹配(覆盖历史英文/德语输出);
    4. 子串关键词扫描(如 "Word Order issue" 包含 "wordorder");
    5. 兜底 OTHER。

    本函数永不抛异常(输入始终有确定输出),保证统计流程稳定。
    """
    if raw is None:
        return ErrorCategory.OTHER
    text = str(raw).strip()
    if not text:
        return ErrorCategory.OTHER

    # 1. 英文键名精确匹配
    upper = text.upper().replace(" ", "_")
    for category in ErrorCategory:
        if category.value == upper:
            return category

    # 2. 中文标签精确匹配
    if text in _LABEL_TO_CATEGORY:
        return _LABEL_TO_CATEGORY[text]

    # 3. 别名表精确匹配
    token = _normalize_token(text)
    if token in _ALIASES:
        return _ALIASES[token]

    # 4. 子串关键词扫描(长别名优先,保证确定性与准确率)
    for alias_token, category in _SUBSTRING_ALIASES:
        if len(alias_token) >= 3 and alias_token in token:
            return category

    return ErrorCategory.OTHER


def canonical_label(value: str | ErrorCategory | None) -> str:
    """类别键名(或任意文本)-> 中文标签(供报告与统计展示)"""
    if isinstance(value, ErrorCategory):
        return CATEGORY_LABELS.get(value, "其他")
    if value:
        for category in ErrorCategory:
            if category.value == str(value).upper():
                return CATEGORY_LABELS[category]
        if str(value) in _LABEL_TO_CATEGORY:
            return str(value)
    return "其他"


def annotate_errors(errors: list[ErrorItem]) -> None:
    """为错误列表原地填充 canonical_type(已有值则保留)

    在渲染报告与落库前调用,保证:
    - report_renderer 的"薄弱点"统计使用规范化标签;
    - error_records 的分类聚合可靠。
    """
    for err in errors:
        if not err.canonical_type:
            err.canonical_type = normalize_error_type(err.error_type).value


def category_label_of(err: ErrorItem) -> str:
    """获取某条错误的中文分类标签(优先 canonical_type,兜底实时归一)"""
    if err.canonical_type:
        return canonical_label(err.canonical_type)
    return canonical_label(normalize_error_type(err.error_type))
