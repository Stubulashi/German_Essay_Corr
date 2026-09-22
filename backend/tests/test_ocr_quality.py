"""OCR 契约 v2 测试(零修正红线 / 质量字段 / unsicher 标记 / 评分透传兼容)

需求一:OCR 提示词零修正硬约束与自查项;
需求二:recognition_quality / quality_note 字段解析与评分端透传(默认参数零影响);
需求三:[unsicher:…] 标记在识别/评分两侧的规则存在性。
"""

from app.models.schemas import DetailLevel, GradingStandard, OcrExtractionResult
from app.pipelines.prompts import build_grading_prompt, build_grading_user_message, build_ocr_prompt
from app.services.ocr_client import OcrClient


class TestPromptRedlines:
    """提示词红线与标记规则(需求一/三)"""

    def test_ocr_zero_correction_redline(self):
        prompt = build_ocr_prompt()
        assert "复刻笔迹" in prompt
        assert "零修正最高红线" in prompt
        assert "拼写纠正" in prompt and "措辞润色" in prompt and "段落归并" in prompt
        # 反例锚定:学生原文 "ich findet" 必须原样输出
        assert '"ich findet"' in prompt and '"ich finde"' in prompt

    def test_ocr_selfcheck_items(self):
        prompt = build_ocr_prompt()
        assert "逐句反查" in prompt
        assert "修正痕迹扫描" in prompt
        assert "[unsicher:猜测文本]" in prompt

    def test_grading_knows_unsicher_rule(self):
        prompt = build_grading_prompt(GradingStandard.GAOKAO, DetailLevel.MEDIUM)
        assert "[unsicher:" in prompt
        assert "不得基于标记内的猜测内容新增错误条目" in prompt
        assert "[unsicher:…] 标记内的猜测内容未用于新增任何错误条目" in prompt


class TestOcrQualityParse:
    """质量字段解析与旧数据兼容(需求二)"""

    def test_parse_quality_fields(self):
        raw = (
            '{"student_name":"李明","student_id":null,"transcribed_text":"ich findet",'
            '"recognition_quality":"low","quality_note":"多处字迹模糊"}'
        )
        result = OcrClient._parse_ocr_output(raw)
        assert result.recognition_quality == "low"
        assert result.quality_note == "多处字迹模糊"

    def test_invalid_quality_clamped_to_none(self):
        raw = '{"student_name":"未知","transcribed_text":"x","recognition_quality":"super"}'
        assert OcrClient._parse_ocr_output(raw).recognition_quality is None

    def test_legacy_output_defaults_none(self):
        raw = '{"student_name":"王芳","student_id":"01","transcribed_text":"Hallo"}'
        result = OcrClient._parse_ocr_output(raw)
        assert result.recognition_quality is None and result.quality_note is None

    def test_schema_defaults_none(self):
        result = OcrExtractionResult(student_name="未知", transcribed_text="x")
        assert result.recognition_quality is None and result.quality_note is None


class TestGradingUserMessageQuality:
    """评分用户消息:默认零影响 + low 警告注入(需求二)"""

    def test_default_call_has_no_quality_line(self):
        message = build_grading_user_message("Text mit Fehlern.", "李明", None)
        assert "识别质量" not in message

    def test_low_quality_warning(self):
        message = build_grading_user_message(
            "Text.", "李明", "123", quality="low", quality_note="多处模糊"
        )
        assert "识别质量:低" in message
        assert "多处模糊" in message
        assert "不因疑似识别噪声新增错误条目" in message

    def test_high_quality_no_warning(self):
        message = build_grading_user_message(
            "Text.", "李明", None, quality="high", quality_note="清晰"
        )
        assert "识别质量" not in message
