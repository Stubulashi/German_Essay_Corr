"""OCR 异常检测与质量纠偏测试(用户示例样本 / 正常样本 / 边界 / 纠偏)"""

from app.models.schemas import OcrExtractionResult
from app.services.ocr_anomaly import analyze_transcription, reconcile_quality

#: 用户上报的异常样本(畸变词 + 断词粘连 + 标签行 + 自评不符)
SAMPLE = (
    "Anna erklärt, dass das Oktoberfest sehr voll am Abend ist.\n"
    "Deshalb könntest du lieber früher hingehen.\n"
    "Paul sagt, dass du bequeme Schuhe tragen solltest. Weil auf\n"
    "dem Oktoberfest sehr viele Menschen teilnehmen.\n"
    "Sofia empfiehlt, dass gemg Geld mitnimmt. weil Essen und\n"
    "Getränke tenner sein können. Außerdem kann man in vielen\n"
    "Fatzelten nur bar bezahlen. Da musst dich auf etwas Vorberei-\n"
    "ten\n"
    "Max:\n"
    "An der Stelle würde er vorher auf das Wetter schauen.\n"
    "Wenn es kalt ist, bringt ihr warme Kleidung."
)

#: 正常转录(合法德语,含常见辅音簇,不应命中)
CLEAN = (
    "Meine Sommerferien\n\n"
    "In den Sommerferien habe ich mit meiner Familie eine Reise nach Beijing gemacht. "
    "Wir haben viele Sehenswürdigkeiten besucht, zum Beispiel die Große Mauer. "
    "Die Reise war sehr interessant und wir hatten viel Spaß. "
    "Nächstes Jahr möchte ich wieder dorthin fahren."
)


class TestAnalyzeTranscription:
    def test_user_sample_flagged_with_reasons(self):
        report = analyze_transcription(SAMPLE, quality="high")
        assert report.anomalous is True
        joined = "、".join(report.reasons)
        assert "断词" in joined  # 行尾断词粘连
        assert "标签行" in joined  # "Max:" 等
        assert "自评" in joined  # high 与内容不符
        assert report.score >= 4

    def test_clean_text_not_flagged(self):
        report = analyze_transcription(CLEAN, quality="high")
        assert report.anomalous is False
        assert report.score == 0
        assert report.reasons == []

    def test_threshold_override(self):
        strict = analyze_transcription(SAMPLE, quality=None, threshold=100)
        assert strict.anomalous is False
        loose = analyze_transcription(SAMPLE, quality=None, threshold=2)
        assert loose.anomalous is True

    def test_disabled_short_circuit(self):
        report = analyze_transcription(SAMPLE, quality="high", enabled=False)
        assert report.anomalous is False and report.score == 0

    def test_empty_text_not_scored(self):
        assert analyze_transcription("", quality="high").anomalous is False


class TestReconcileQuality:
    def test_high_downgraded_with_note(self):
        ocr = OcrExtractionResult(
            student_name="赖佳莹",
            transcribed_text=SAMPLE,
            recognition_quality="high",
            quality_note="",
        )
        reconcile_quality(ocr, analyze_transcription(SAMPLE, "high"))
        assert ocr.recognition_quality == "low"
        assert "系统检测" in (ocr.quality_note or "")
        assert "断词" in (ocr.quality_note or "")

    def test_existing_note_merged(self):
        ocr = OcrExtractionResult(
            student_name="x",
            transcribed_text=SAMPLE,
            recognition_quality="low",
            quality_note="原有说明",
        )
        reconcile_quality(ocr, analyze_transcription(SAMPLE, "low"))
        assert ocr.recognition_quality == "low"
        assert "原有说明" in (ocr.quality_note or "")
        assert "系统检测" in (ocr.quality_note or "")

    def test_clean_noop(self):
        ocr = OcrExtractionResult(
            student_name="x",
            transcribed_text=CLEAN,
            recognition_quality="high",
            quality_note=None,
        )
        reconcile_quality(ocr, analyze_transcription(CLEAN, "high"))
        assert ocr.recognition_quality == "high"
        assert not ocr.quality_note
