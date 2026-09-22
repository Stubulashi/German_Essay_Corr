"""提示词全模式重构测试(六区架构 / 模式差异化 / 边界覆盖 / 契约恒等 / 注入兼容)

覆盖目标(对应重构方案第六节):
1. 契约恒等:全部评分组合内嵌锁定的 JSON 契约原串;各模式自身契约样例逐字存在;
2. 模式差异化:同参组合互不为近重复,各模式持有专属标记,标准/细致度区块整段互异;
3. 边界覆盖:每模式断言其边界关键词集齐;
4. 注入兼容:风格→附录顺序不变,空注入恒等;
5. 用户消息模板:页数/缺失信息/体检/自校验提示齐备。
"""

from app.models.schemas import DetailLevel, GradingStandard
from app.pipelines.prompts import (
    APPENDIX_HEADER,
    STYLE_CONTEXT_HEADER,
    _ERROR_TYPE_CHOICES,
    _GRADING_JSON_SPEC,
    _detail_level_text,
    _grading_standard_text,
    build_exam_ocr_prompt,
    build_exam_user_message,
    build_grading_prompt,
    build_grading_user_message,
    build_ocr_prompt,
    build_ocr_user_message,
    build_pipeline_a_prompt,
    build_pipeline_a_user_message,
)
from app.services.style_learning_service import STYLE_SUMMARY_SYSTEM_PROMPT

STANDARDS = [GradingStandard.GAOKAO, GradingStandard.DSD]
DETAILS = [DetailLevel.LOW, DetailLevel.MEDIUM, DetailLevel.HIGH]


# ---------------------------------------------------------
# 1. 契约恒等
# ---------------------------------------------------------
class TestContractIdentity:
    """输出契约逐字节不变(解析链路安全)"""

    def test_all_grading_combos_embed_locked_json_spec(self):
        for standard in STANDARDS:
            for detail in DETAILS:
                assert _GRADING_JSON_SPEC in build_pipeline_a_prompt(standard, detail)
                assert _GRADING_JSON_SPEC in build_grading_prompt(standard, detail)

    def test_ocr_contract_v2_five_fields(self):
        prompt = build_ocr_prompt()
        for field in (
            '"student_name"',
            '"student_id"',
            '"transcribed_text"',
            '"recognition_quality"',  # OCR 契约 v2
            '"quality_note"',
        ):
            assert field in prompt
        assert "字段仅五个" in prompt
        assert '"total_score"' not in prompt
        assert '"overall_score"' not in prompt  # 转录模式不得暴露评分字段

    def test_exam_contract_and_choices_substituted(self):
        prompt = build_exam_ocr_prompt()
        assert '"total_score": 85.5' in prompt
        assert '"knowledge_tag": "动词位序"' in prompt
        assert _ERROR_TYPE_CHOICES in prompt
        assert "__ERROR_TYPE_CHOICES__" not in prompt  # 占位符必须已被替换

    def test_style_contract_keys_verbatim(self):
        for key in (
            '"scoring_scale"',
            '"tone"',
            '"correction_preference"',
            '"expression_habits"',
            '"narrative"',
        ):
            assert key in STYLE_SUMMARY_SYSTEM_PROMPT

    def test_markdown_report_forbidden_in_grading_modes(self):
        for prompt in (
            build_pipeline_a_prompt(GradingStandard.GAOKAO, DetailLevel.MEDIUM),
            build_grading_prompt(GradingStandard.GAOKAO, DetailLevel.MEDIUM),
        ):
            assert "无 markdown_report 字段" in prompt


# ---------------------------------------------------------
# 2. 模式差异化(要求 1/2)
# ---------------------------------------------------------
class TestModeDifferentiation:
    """模式之间显著可辨,不得近重复"""

    def test_pipeline_a_vs_grading_markers(self):
        for standard in STANDARDS:
            for detail in DETAILS:
                a = build_pipeline_a_prompt(standard, detail)
                g = build_grading_prompt(standard, detail)
                assert a != g
                assert "看图一手批改者" in a and "看图一手批改者" not in g
                assert "只依据给定转录的评卷人" in g and "只依据给定转录的评卷人" not in a
                assert "图文核对" in a and "图文核对" not in g
                assert "转录体检" in g and "转录体检" not in a

    def test_ocr_vs_exam_markers(self):
        ocr = build_ocr_prompt()
        exam = build_exam_ocr_prompt()
        assert "纯转录官" in ocr and "纯转录官" not in exam
        assert "没有评分职责" in ocr
        assert "阅卷机器" in exam and "阅卷机器" not in ocr
        assert "听力主动剔除" in exam and "听力主动剔除" not in ocr

    def test_standard_blocks_fully_distinct(self):
        gaokao = _grading_standard_text(GradingStandard.GAOKAO)
        dsd = _grading_standard_text(GradingStandard.DSD)
        assert gaokao != dsd
        assert len(gaokao) > 250 and len(dsd) > 250  # 整段独立文本,杜绝单行替换式差异
        assert "25 分扣分制" in gaokao and "分数锚点" in gaokao and "CEFR" not in gaokao
        assert "CEFR 能力评估" in dsd and "四维观察" in dsd and "分数锚点" not in dsd

    def test_detail_blocks_fully_distinct(self):
        low = _detail_level_text(DetailLevel.LOW)
        medium = _detail_level_text(DetailLevel.MEDIUM)
        high = _detail_level_text(DetailLevel.HIGH)
        assert len({low, medium, high}) == 3
        for text in (low, medium, high):
            assert len(text) > 200
        assert "红线原则" in low and "最小干预" in low
        assert "均衡覆盖" in medium
        assert "保姆级解析" in high

    def test_detail_field_matrix_alignment(self):
        """字段矩阵与渲染器消费对齐:LOW 允许空修正 / MEDIUM 必填修正 / HIGH 必填解析"""
        low = _detail_level_text(DetailLevel.LOW)
        medium = _detail_level_text(DetailLevel.MEDIUM)
        high = _detail_level_text(DetailLevel.HIGH)
        assert '允许填空字符串 ""' in low
        assert "必填且非空" in medium and "必填且非空" in high
        assert "explanation:必须为 null" in low and "explanation:必须为 null" in medium
        assert "explanation:必填" in high


# ---------------------------------------------------------
# 3. 边界覆盖(要求 3)
# ---------------------------------------------------------
class TestBoundaryCoverage:
    """各模式边界情况描述完整无遗漏"""

    def test_pipeline_a_boundaries(self):
        prompt = build_pipeline_a_prompt(GradingStandard.GAOKAO, DetailLevel.MEDIUM)
        for token in ("多页", "[unleserlich]", "未知", "非德语", "空白页", "涂改"):
            assert token in prompt

    def test_ocr_boundaries(self):
        prompt = build_ocr_prompt()
        for token in (
            "[unleserlich]", "[leer]", "[nicht-deutsch]", "[Seitenreihenfolge prüfen]", "涂改",
            "[unsicher:", "复刻笔迹",  # 需求一/三:零修正红线与低把握标记
        ):
            assert token in prompt

    def test_grading_boundaries(self):
        prompt = build_grading_prompt(GradingStandard.GAOKAO, DetailLevel.MEDIUM)
        for token in ("转录体检", "[unleserlich]", "截断", "乱码", "不虚构"):
            assert token in prompt

    def test_exam_boundaries(self):
        prompt = build_exam_ocr_prompt()
        for token in ("听力", "自校验", "0.5", "题号按顺序推断", "null"):
            assert token in prompt

    def test_selfcheck_section_present_everywhere(self):
        for prompt in (
            build_pipeline_a_prompt(GradingStandard.GAOKAO, DetailLevel.MEDIUM),
            build_grading_prompt(GradingStandard.GAOKAO, DetailLevel.MEDIUM),
            build_ocr_prompt(),
            build_exam_ocr_prompt(),
            STYLE_SUMMARY_SYSTEM_PROMPT,
        ):
            assert "【输出前自检】" in prompt


# ---------------------------------------------------------
# 4. 注入兼容(要求 5)
# ---------------------------------------------------------
class TestInjectionCompat:
    """示范学习风格 → 教师附录:顺序与空值恒等"""

    def test_style_then_appendix_order(self):
        prompt = build_grading_prompt(
            GradingStandard.GAOKAO,
            DetailLevel.MEDIUM,
            style_context="保持严格评分尺度",
            appendix="评语最后附一句德语鼓励语",
        )
        assert prompt.index(STYLE_CONTEXT_HEADER) < prompt.index(APPENDIX_HEADER)
        assert "保持严格评分尺度" in prompt and "德语鼓励语" in prompt

    def test_empty_injections_are_identity(self):
        base = build_pipeline_a_prompt(GradingStandard.DSD, DetailLevel.HIGH)
        with_empty = build_pipeline_a_prompt(
            GradingStandard.DSD, DetailLevel.HIGH, style_context="", appendix=""
        )
        assert base == with_empty
        assert STYLE_CONTEXT_HEADER not in base and APPENDIX_HEADER not in base

    def test_headers_verbatim(self):
        assert STYLE_CONTEXT_HEADER.startswith("【示范学习参考")
        assert APPENDIX_HEADER.startswith("【教师自定义补充要求")


# ---------------------------------------------------------
# 5. 用户消息模板
# ---------------------------------------------------------
class TestUserMessages:
    """各模式用户消息携带对应的任务提醒与边界提示"""

    def test_pipeline_a_message_page_hint(self):
        single = build_pipeline_a_user_message(1)
        multi = build_pipeline_a_user_message(3)
        assert "图片数量:1 张" in single and "这是完整的一篇作文" in single
        assert "图片数量:3 张" in multi and "按页序拼接" in multi
        assert "[unleserlich]" in single

    def test_ocr_message(self):
        message = build_ocr_user_message(2)
        assert "图片数量:2 张" in message
        assert "不修正" in message and "[unleserlich]" in message

    def test_grading_message_missing_info_and_checkup(self):
        message = build_grading_user_message("Text mit Fehlern.", "", None)
        assert "转录体检" in message
        assert "未知" in message and "（无）" in message
        assert "逐字取自" in message

    def test_exam_message(self):
        message = build_exam_user_message()
        assert "自校验" in message and "听力" in message and "null" in message
