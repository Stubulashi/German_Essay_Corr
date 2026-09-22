"""批注定位单元测试(#13 方向一)

验证 report_renderer.locate_error_spans 的定位规则:
- 精确匹配优先,忽略大小写兜底;
- 空片段 / 找不到 / 区间重叠 归入未定位清单;
- 返回区间按位置升序。
"""

from app.models.schemas import ErrorItem
from app.services.report_renderer import locate_error_spans

_TEXT = "Ich habe mit meine Familie nach Berlin gefahren. Nächste Jahr fahre ich wieder."


def _err(fragment: str) -> ErrorItem:
    return ErrorItem(original_text=fragment, corrected_text="修正", error_type="动词位序")


class TestLocateErrorSpans:
    """locate_error_spans 定位规则测试"""

    def test_exact_match(self):
        """精确匹配:区间与片段位置一致"""
        spans, unlocated = locate_error_spans(_TEXT, [_err("mit meine Familie")])
        assert unlocated == []
        assert len(spans) == 1
        span = spans[0]
        assert span.error_index == 0
        assert _TEXT[span.start : span.end] == "mit meine Familie"

    def test_case_insensitive_match(self):
        """忽略大小写兜底匹配"""
        spans, unlocated = locate_error_spans(_TEXT, [_err("nächste jahr")])
        assert unlocated == []
        assert len(spans) == 1
        assert _TEXT[spans[0].start : spans[0].end] == "Nächste Jahr"

    def test_not_found_goes_to_unlocated(self):
        """片段不存在时归入未定位清单"""
        spans, unlocated = locate_error_spans(_TEXT, [_err("完全不在原文里的句子")])
        assert spans == []
        assert unlocated == [0]

    def test_empty_fragment_goes_to_unlocated(self):
        """空片段归入未定位"""
        spans, unlocated = locate_error_spans(_TEXT, [_err("   ")])
        assert spans == []
        assert unlocated == [0]

    def test_overlap_marked_unlocated(self):
        """与已占用区间重叠的匹配归入未定位(保持渲染确定性)"""
        errors = [_err("mit meine Familie"), _err("meine Familie nach")]
        spans, unlocated = locate_error_spans(_TEXT, errors)
        assert len(spans) == 1
        assert spans[0].error_index == 0
        assert unlocated == [1]

    def test_multiple_spans_sorted(self):
        """多条错误区间按位置升序返回"""
        errors = [_err("Nächste Jahr"), _err("mit meine Familie")]
        spans, unlocated = locate_error_spans(_TEXT, errors)
        assert unlocated == []
        assert [s.error_index for s in spans] == [1, 0]  # 按位置排序,而非输入顺序
        assert spans[0].start < spans[1].start

    def test_duplicate_fragment_positions(self):
        """相同片段出现两次:第二条因重叠检测归入未定位"""
        errors = [_err("ich"), _err("ich")]
        spans, unlocated = locate_error_spans(_TEXT.lower(), errors)
        # 两条都会定位到同一位置,第二条应被判定为重叠
        assert len(spans) == 1
        assert len(unlocated) == 1
