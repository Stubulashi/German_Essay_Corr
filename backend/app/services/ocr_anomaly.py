"""OCR 转录异常检测与质量自评纠偏(兜底机制)

用途:检测"字段结构正常但内容异常"的 OCR 输出(畸变词/行尾断词粘连/标签行混入/自评不符),
驱动管线自愈(重跑识别或整管线重试)与降级(质量纠偏/强制复核/可见提示)。

设计要点:
- 纯函数、零 IO:加权评分 + 可解释 reasons,便于单测与审计;
- 权重为内部可解释常量(下表),阈值/开关/次数由设置中心配置
  (OCR_ANOMALY_ENABLED / THRESHOLD / MAX_RETRY / FORCE_REVIEW);
- 仅执行流程与展示层增强,不改任何输出 JSON 契约与字段含义。

信号与权重:
- 行尾断词粘连 "词-\n词"        每个 +2(封顶 4)——OCR 把排版断词原样保留的强信号;
- 标签行/非正文行("Max:"等)     每项 +2(封顶 4)——角色标签、批注混入正文;
- 畸变词/数字混排(启发式)       每个 +1(封顶 3)——≥4 连续辅音且非合法德语簇等;
- 占位符密度过高(≥200 字文本)   +2——[unleserlich*]/[unsicher:*] 密度 >1/100 字;
- 自评不符(quality=high 且命中上述任一) +3。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.config import Settings
from app.models.schemas import OcrExtractionResult
from app.pipelines.base import PipelineConfigError
from app.services.audit_service import audit

logger = logging.getLogger(__name__)

# ---------- 信号权重(可解释常量;调整需同步文档与测试) ----------
_W_HYPHEN_JOIN = 2
_W_HYPHEN_JOIN_CAP = 4
_W_LABEL_LINE = 2
_W_LABEL_LINE_CAP = 4
_W_DISTORTED = 1
_W_DISTORTED_CAP = 3
_W_PLACEHOLDER = 2
_W_QUALITY_MISMATCH = 3

#: 行尾断词粘连:字母 + 连字符 + 换行 + 小写字母(如 "Vorberei-\nten")
_HYPHEN_JOIN_RE = re.compile(r"[A-Za-zÄÖÜäöüß]-\n[a-zäöüß]")
#: 独立标签行:单个首字母大写的词 + 冒号独占一行(如 "Max:")
_LABEL_LINE_RE = re.compile(r"^\s*[A-ZÄÖÜ][a-zäöüß]{1,14}\s*:\s*$", re.MULTILINE)
#: 口语化批注/旁注行
_META_LINE_RE = re.compile(
    r"^\s*(?:An der Stelle|Anmerkung|Kommentar|Randbemerkung)\b", re.MULTILINE
)
#: 不确定标记([unleserlich] / [unleserlich×n] / [unsicher:…])
_PLACEHOLDER_RE = re.compile(r"\[unleserlich(?:×\d+)?\]|\[unsicher:[^\]]*\]")
_TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")
#: ≥4 连续辅音(命中且不含合法德语簇时判为畸变候选)
_CONS_CLUSTER_RE = re.compile(r"[bcdfghjklmnpqrstvwxyzß]{4,}")
#: 含字母/数字的词样 token(数字混排检测用)
#: 旧实现用多段回溯正则,对长纯字母串存在灾难性回溯(O(n²) 卡死);
#: 改为一次线性 findall + 逐 token 检查,语义等价且线性安全。
_WORDLIKE_RE = re.compile(r"[A-Za-zÄÖÜäöüß\d]+")
#: 合法德语辅音簇白名单(出现在连续辅音串中即视为正常)
_LEGAL_CLUSTERS = (
    "sch", "chr", "spr", "str", "spl", "schn", "schm", "schr", "schw",
    "tz", "pf", "ck", "ch", "ph", "th", "sp", "st",
)


@dataclass
class AnomalyReport:
    """异常检测报告(可审计、可展示)"""

    score: int = 0
    reasons: list[str] = field(default_factory=list)
    anomalous: bool = False


def _is_distorted_token(token: str) -> bool:
    """启发式畸变词:含 ≥4 连续辅音且整段不含合法德语簇"""
    lowered = token.lower()
    for match in _CONS_CLUSTER_RE.finditer(lowered):
        run = match.group(0)
        if any(cluster in run for cluster in _LEGAL_CLUSTERS):
            continue
        return True
    return False


def analyze_transcription(
    text: str,
    quality: str | None = None,
    *,
    threshold: int | None = None,
    enabled: bool | None = None,
) -> AnomalyReport:
    """对转录文本进行异常加权评分

    Args:
        text:      转录文本(OCR 输出)
        quality:   模型自评质量(high|medium|low,可空)
        threshold: 命中阈值(默认读设置 OCR_ANOMALY_THRESHOLD)
        enabled:   是否启用(默认读设置 OCR_ANOMALY_ENABLED)

    Returns:
        AnomalyReport(score / reasons / anomalous)
    """
    from app.config import settings as global_settings  # 延迟导入避免模块环

    if enabled is None:
        enabled = global_settings.ocr_anomaly_enabled
    if threshold is None:
        threshold = global_settings.ocr_anomaly_threshold
    if not enabled:
        return AnomalyReport()

    raw = (text or "").strip()
    if not raw:
        # 空文本由既有"内容为空"链路处理,此处不重复计分
        return AnomalyReport()

    score = 0
    reasons: list[str] = []

    hyphen_joins = len(_HYPHEN_JOIN_RE.findall(raw))
    if hyphen_joins:
        score += min(hyphen_joins * _W_HYPHEN_JOIN, _W_HYPHEN_JOIN_CAP)
        reasons.append(f"行尾断词粘连 ×{hyphen_joins}")

    label_lines = len(_LABEL_LINE_RE.findall(raw)) + len(_META_LINE_RE.findall(raw))
    if label_lines:
        score += min(label_lines * _W_LABEL_LINE, _W_LABEL_LINE_CAP)
        reasons.append(f"标签行/非正文行 ×{label_lines}")

    distorted = sum(1 for token in _TOKEN_RE.findall(raw) if _is_distorted_token(token))
    distorted += sum(
        1
        for token in _WORDLIKE_RE.findall(raw)
        if any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token)
    )
    if distorted:
        score += min(distorted * _W_DISTORTED, _W_DISTORTED_CAP)
        reasons.append(f"畸变词/数字混排 ×{distorted}")

    if len(raw) >= 200:
        placeholders = len(_PLACEHOLDER_RE.findall(raw))
        density = placeholders * 100 / len(raw)
        if density > 1.0:
            score += _W_PLACEHOLDER
            reasons.append(f"占位符密度过高({density:.1f}/100 字)")

    strong_hit = bool(reasons)
    if (quality or "").strip().lower() == "high" and strong_hit:
        score += _W_QUALITY_MISMATCH
        reasons.append("自评质量(high)与内容异常不符")

    return AnomalyReport(score=score, reasons=reasons, anomalous=score >= threshold)


def reconcile_quality(ocr: OcrExtractionResult, report: AnomalyReport) -> None:
    """按检测结果纠偏自评质量(原地修改;字段含义与枚举不变)

    - 异常时:recognition_quality 覆盖为 "low";quality_note 合并检测缘由(追加,不覆盖既有说明);
    - 正常时:不做任何修改。
    """
    if not report.anomalous:
        return
    reasons = "、".join(report.reasons) or "内容异常"
    note = f"系统检测:转录可能存在畸变({reasons}),建议人工核对。"
    existing = (ocr.quality_note or "").strip()
    if note not in existing:
        ocr.quality_note = f"{existing}\n{note}".strip() if existing else note
    if (ocr.recognition_quality or "").strip().lower() != "low":
        ocr.recognition_quality = "low"


async def run_ocr_with_guard(
    produce: Callable[[], Awaitable[OcrExtractionResult]],
    *,
    settings: Settings,
    image_count: int,
    pipeline_tag: str,
) -> tuple[OcrExtractionResult, AnomalyReport]:
    """执行识别并在预算内自动重试/质量纠偏(管线 A/B 共用,日志与审计口径一致)

    流程:produce → 检测 → (预算内)审计并重试 → 超预算 reconcile 质量并审计降级。
    返回最终 OcrExtractionResult(异常状态经 quality 字段表达)与最后一次检测报告。
    """
    attempt = 0
    while True:
        ocr = await produce()
        report = analyze_transcription(ocr.transcribed_text, ocr.recognition_quality)
        if not report.anomalous:
            return ocr, report
        if attempt >= settings.ocr_anomaly_max_retry:
            reconcile_quality(ocr, report)
            logger.warning(
                "[%s] OCR 异常重试用尽,质量已纠偏为 low(信号:%s)",
                pipeline_tag,
                "、".join(report.reasons),
            )
            await audit(
                "ocr.anomaly_degraded",
                ok=True,
                detail=f"得分 {report.score};信号:{'、'.join(report.reasons)};质量已纠偏",
                source="worker",
            )
            return ocr, report
        attempt += 1
        logger.warning(
            "[%s] OCR 转录异常(得分 %s:%s),自动重跑识别(第 %s 次)",
            pipeline_tag,
            report.score,
            "、".join(report.reasons),
            attempt,
        )
        await audit(
            "ocr.anomaly_retry",
            ok=True,
            detail=(
                f"图像 {image_count} 页;得分 {report.score};"
                f"信号:{'、'.join(report.reasons)};第 {attempt} 次重试"
            ),
            source="worker",
        )


#: 视觉调用失败特征(模型/网关“没看到图”的自述;命中即判为端点不支持视觉,而非识别质量低)
_VISION_FAILURE_PATTERNS: tuple[str, ...] = (
    "未收到任何图片",
    "没有收到图片",
    "收到任何图片",
    "未接收到图片",
    "无法查看图片",
    "无法看到图片",
    "无法查看图像",
    "未提供图片",
    "图片缺失",
    "无法进行转录",
    "no image",
    "cannot see",
    "can't see",
    "unable to view",
    "image was not provided",
)


def raise_if_vision_failed(ocr: OcrExtractionResult, endpoint_label: str) -> None:
    """识别结果自述“未收到图片”时抛出可操作的配置错误(不再降级为“识别质量低”)

    扫描 transcribed_text 与 quality_note;命中特征短语即判为目标端点/模型
    不支持图片输入(或图片未送达),直接报错让教师看到真实原因与替代路径。
    """
    text = f"{ocr.transcribed_text or ''}\n{ocr.quality_note or ''}".lower()
    for pattern in _VISION_FAILURE_PATTERNS:
        if pattern.lower() in text:
            raise PipelineConfigError(
                "目标端点/模型疑似不支持图片输入:"
                f"模型回复中出现“{pattern}”。请改用支持视觉的模型"
                "(如 qwen-vl-plus / qwen2.5-vl),或为识别配置独立视觉端点;"
                f"当前端点:{endpoint_label}",
                pipeline="OCR-VISION",
            )
