"""防御性解析器单元测试

覆盖 LLM 输出的各种"脏数据"形态:
- 标准 JSON / 代码块围栏 / 前后夹杂文字 / 尾随逗号 / 非法输出
"""

import pytest

from app.services.parser import extract_json_dict, normalize_optional_str


class TestExtractJsonDict:
    """extract_json_dict 解析策略测试"""

    def test_standard_json(self):
        """标准 JSON 直接解析"""
        obj = extract_json_dict('{"student_name": "张三", "score": 18}')
        assert obj["student_name"] == "张三"
        assert obj["score"] == 18

    def test_json_with_code_fence(self):
        """带 ```json 围栏的输出"""
        raw = '```json\n{"overall_score": "18 / 25"}\n```'
        assert extract_json_dict(raw)["overall_score"] == "18 / 25"

    def test_json_with_plain_fence(self):
        """带无语言标识围栏的输出"""
        raw = '```\n{"a": 1}\n```'
        assert extract_json_dict(raw) == {"a": 1}

    def test_json_with_surrounding_prose(self):
        """前后夹杂解释性文字(截取首个 { 到最后一个 })"""
        raw = '好的,以下是批改结果:\n{"student_name": "李四"}\n希望有帮助!'
        assert extract_json_dict(raw)["student_name"] == "李四"

    def test_json_with_trailing_comma(self):
        """带尾随逗号的非法 JSON(清洗后解析)"""
        raw = '{"errors": [{"a": 1},], "total": 1,}'
        obj = extract_json_dict(raw)
        assert obj["errors"] == [{"a": 1}]
        assert obj["total"] == 1

    def test_json_with_nested_braces_in_string(self):
        """字符串内包含花括号(验证 rfind 截取策略的边界)"""
        raw = '{"explanation": "句式 {zwar...aber} 使用不当"}'
        assert extract_json_dict(raw)["explanation"] == "句式 {zwar...aber} 使用不当"

    def test_empty_output_raises(self):
        """空输出应抛 ValueError"""
        with pytest.raises(ValueError, match="为空"):
            extract_json_dict("")

    def test_no_json_raises(self):
        """无 JSON 对象应抛 ValueError"""
        with pytest.raises(ValueError, match="未找到 JSON"):
            extract_json_dict("这是一段没有 JSON 的纯文本")

    def test_invalid_json_raises(self):
        """无法修复的非法 JSON 应抛 ValueError"""
        with pytest.raises(ValueError, match="JSON 解析失败"):
            extract_json_dict('{"a": [1, 2,,,]}')

    def test_array_output_raises(self):
        """输出为数组而非对象时抛 ValueError"""
        with pytest.raises(ValueError, match="不是 JSON 对象"):
            extract_json_dict("[1, 2, 3]")


class TestNormalizeOptionalStr:
    """空值归一化测试"""

    @pytest.mark.parametrize(
        "value",
        [None, "", "null", "NULL", "None", "无", "N/A", "  "],
    )
    def test_empty_values_become_none(self, value):
        assert normalize_optional_str(value) is None

    @pytest.mark.parametrize("value", ["20260101", "张三"])
    def test_valid_values_kept(self, value):
        assert normalize_optional_str(value) == value
