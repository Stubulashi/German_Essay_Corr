"""错因分类标准化单元测试

覆盖:
- 英文键名 / 中文标签 / 英文别名 / 德语别名 的归一化;
- 子串扫描与长别名优先;
- 空值与未知输入的兜底行为;
- annotate_errors 与 category_label_of 的填充与展示。
"""

import pytest

from app.models.schemas import ErrorItem
from app.services.error_taxonomy import (
    ErrorCategory,
    annotate_errors,
    canonical_label,
    category_label_of,
    normalize_error_type,
)


class TestNormalizeErrorType:
    """normalize_error_type 四级匹配测试"""

    def test_exact_key_match(self):
        """英文键名精确匹配"""
        assert normalize_error_type("VERB_POSITION") == ErrorCategory.VERB_POSITION
        assert normalize_error_type("verb_position") == ErrorCategory.VERB_POSITION

    def test_exact_chinese_label_match(self):
        """中文标签精确匹配(新 Prompt 的输出格式)"""
        assert normalize_error_type("动词位序") == ErrorCategory.VERB_POSITION
        assert normalize_error_type("名词变格") == ErrorCategory.CASE_DECLENSION
        assert normalize_error_type("形容词词尾") == ErrorCategory.ADJECTIVE_ENDING

    def test_english_alias_match(self):
        """历史英文输出别名匹配"""
        assert normalize_error_type("Word Order") == ErrorCategory.VERB_POSITION
        assert normalize_error_type("Case Ending") == ErrorCategory.CASE_DECLENSION
        assert normalize_error_type("Vocabulary") == ErrorCategory.WORD_CHOICE
        assert normalize_error_type("Punctuation") == ErrorCategory.PUNCTUATION
        assert normalize_error_type("Spelling") == ErrorCategory.SPELLING

    def test_german_alias_match(self):
        """德语术语别名匹配"""
        assert normalize_error_type("Satzbau") == ErrorCategory.SENTENCE_STRUCTURE
        assert normalize_error_type("Wortstellung") == ErrorCategory.VERB_POSITION
        assert normalize_error_type("Rechtschreibung") == ErrorCategory.SPELLING
        assert normalize_error_type("Satzklammer") == ErrorCategory.SENTENCE_FRAME

    def test_substring_scan(self):
        """子串关键词扫描"""
        assert normalize_error_type("Word Order issue") == ErrorCategory.VERB_POSITION
        assert normalize_error_type("介词搭配错误") == ErrorCategory.PREPOSITION

    def test_long_alias_priority(self):
        """长别名优先:lowercase error 不应被 case 误匹配为名词变格"""
        # "lowercase" 会移除空格后进入子串扫描,长别名优先保证命中大小写
        result = normalize_error_type("lowercase error")
        assert result in (ErrorCategory.CAPITALIZATION, ErrorCategory.OTHER)

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_input_fallback(self, value):
        """空输入兜底为 OTHER(永不抛异常)"""
        assert normalize_error_type(value) == ErrorCategory.OTHER

    def test_unknown_input_fallback(self):
        """未知输入兜底为 OTHER"""
        assert normalize_error_type("完全无法识别的类型") == ErrorCategory.OTHER


class TestCanonicalLabel:
    """类别 -> 中文标签映射测试"""

    def test_key_to_label(self):
        assert canonical_label("VERB_POSITION") == "动词位序"
        assert canonical_label(ErrorCategory.CASE_DECLENSION) == "名词变格"

    def test_label_passthrough(self):
        assert canonical_label("名词变格") == "名词变格"

    def test_unknown_to_other(self):
        assert canonical_label("未知键名") == "其他"
        assert canonical_label(None) == "其他"


class TestAnnotateErrors:
    """annotate_errors 原地填充测试"""

    def test_fills_canonical_type(self):
        errors = [
            ErrorItem(original_text="a", corrected_text="b", error_type="Word Order"),
            ErrorItem(original_text="c", corrected_text="d", error_type="名词变格"),
        ]
        annotate_errors(errors)
        assert errors[0].canonical_type == "VERB_POSITION"
        assert errors[1].canonical_type == "CASE_DECLENSION"

    def test_preserves_existing_value(self):
        """已有 canonical_type 的条目不覆盖"""
        errors = [ErrorItem(original_text="a", corrected_text="b", error_type="词汇", canonical_type="OTHER")]
        annotate_errors(errors)
        assert errors[0].canonical_type == "OTHER"

    def test_category_label_of(self):
        err = ErrorItem(original_text="a", corrected_text="b", error_type="Word Order")
        annotate_errors([err])
        assert category_label_of(err) == "动词位序"
