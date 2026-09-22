"""续写页相邻归组逻辑测试(纯函数,零外部依赖)

覆盖:首页+续写合并、首张续写(孤儿)、链式三页、遇普通页断开、
全首页不合并、孤儿后链式续写等边界。
"""

from app.api.routes_corrections import _group_pages


def _entry(name: str, rel: str, page_type: str = "home") -> tuple[str, str, str]:
    return (name, rel, page_type)


def _rels(groups) -> list[list[str]]:
    return [[rel for _, rel in group] for group in groups]


class TestGroupPages:
    def test_home_then_continuation_merged(self):
        groups, orphans, merged = _group_pages(
            [_entry("a", "r1"), _entry("b", "r2", "continuation")]
        )
        assert _rels(groups) == [["r1", "r2"]]
        assert merged == 1 and orphans == []

    def test_first_page_continuation_is_orphan(self):
        groups, orphans, merged = _group_pages([_entry("a", "r1", "continuation")])
        assert _rels(groups) == [["r1"]]
        assert merged == 0 and orphans == ["a"]

    def test_chain_of_three_pages(self):
        groups, orphans, merged = _group_pages(
            [
                _entry("a", "r1"),
                _entry("b", "r2", "continuation"),
                _entry("c", "r3", "continuation"),
            ]
        )
        assert _rels(groups) == [["r1", "r2", "r3"]]
        assert merged == 2 and orphans == []

    def test_break_on_normal_page(self):
        """普通照片夹在中间 → 新组开始;后续续写归新组(遇非续写即断开)"""
        groups, orphans, merged = _group_pages(
            [
                _entry("a", "r1"),
                _entry("b", "r2", "continuation"),
                _entry("p", "r3"),
                _entry("q", "r4", "continuation"),
            ]
        )
        assert _rels(groups) == [["r1", "r2"], ["r3", "r4"]]
        assert merged == 2 and orphans == []

    def test_all_home_no_merge(self):
        groups, orphans, merged = _group_pages([_entry("a", "r1"), _entry("b", "r2")])
        assert _rels(groups) == [["r1"], ["r2"]]
        assert merged == 0 and orphans == []

    def test_orphan_then_chain(self):
        """首张续写独立成组后,其后的续写页仍链式归入该组"""
        groups, orphans, merged = _group_pages(
            [
                _entry("a", "r1", "continuation"),
                _entry("b", "r2", "continuation"),
            ]
        )
        assert _rels(groups) == [["r1", "r2"]]
        assert orphans == ["a"] and merged == 1
