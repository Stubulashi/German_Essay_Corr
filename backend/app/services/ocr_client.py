"""OCR 适配器(管线 B 第一阶段:视觉 / 转录层)

适配器模式:屏蔽不同 OCR 服务的差异,统一输出 `OcrExtractionResult`。
支持三种提供者(通过 .env 的 OCR_PROVIDER 切换 + LOCAL_OCR_ONLY 开关):
- `vlm_openai`: Qwen2.5-VL 等 OpenAI 兼容多模态端点(默认)
- `azure`:      Azure Computer Vision Read API
- `local`:      纯本地 RapidOCR(离线 CPU;LOCAL_OCR_ONLY=true 时启用,
                不调用任何云端/远程 OCR 端点;评分环节不受影响)
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import httpx

from app.config import Settings
from app.models.schemas import OcrExtractionResult
from app.pipelines.base import PipelineConfigError, PipelineNetworkError
from app.pipelines.prompts import build_ocr_prompt, build_ocr_user_message
from app.services.prompt_overrides import get_appendix
from app.services.llm_client import LLMClient, build_image_message_parts
from app.services.ocr_anomaly import raise_if_vision_failed
from app.services.parser import extract_json_dict, normalize_optional_str

logger = logging.getLogger(__name__)


class OcrClient:
    """OCR 统一入口(根据配置分发到具体适配器)"""

    def __init__(self, settings: Settings):
        self._settings = settings

    async def extract(self, image_paths: list[Path]) -> OcrExtractionResult:
        """识别学生信息与转录作文文本

        Args:
            image_paths: 作文图片绝对路径列表(按页序)

        Raises:
            PipelineConfigError: 缺少可用图片文件(不发“无图请求”)
            PipelineNetworkError: 网络错误 / 超时(触发上层故障转移判定)
            PipelineConfigError: 模型自述“未收到图片”(端点不支持视觉)
        """
        existing = [p for p in image_paths if p.exists()]
        if not existing:
            raise PipelineConfigError(
                "任务缺少可用的图片文件(文件缺失或被移动),无法进行识别;"
                "请在队列页重试或重新上传",
                pipeline="OCR-VISION",
            )
        # 纯本地 OCR 模式:短路到本地 RapidOCR(不调用任何云端/远程 OCR 端点)
        if self._settings.local_ocr_only:
            return await self._extract_local_rapid(existing)
        if self._settings.ocr_provider == "azure":
            return await self._extract_azure(existing)
        return await self._extract_vlm_openai(existing)

    # ---------------------------------------------------------
    # 适配器 1:OpenAI 兼容视觉模型(Qwen2.5-VL)
    # ---------------------------------------------------------
    async def _extract_vlm_openai(self, image_paths: list[Path]) -> OcrExtractionResult:
        """通过 OpenAI 兼容多模态端点进行 OCR"""
        s = self._settings
        if not s.ocr_api_key:
            raise PipelineNetworkError(
                "OCR_API_KEY 未配置,无法使用 vlm_openai 模式", pipeline="PIPELINE_B_CLOUD"
            )
        client = LLMClient(
            base_url=s.ocr_base_url,
            api_key=s.ocr_api_key,
            model=s.ocr_model,
            timeout=s.ocr_timeout,
            pipeline_tag="OCR-VLM",
            params_target="ocr",
        )
        # 多页图片一次性提交,由视觉模型整体转录
        content = build_image_message_parts(image_paths, build_ocr_user_message(len(image_paths)))
        messages = [
            {"role": "system", "content": build_ocr_prompt(get_appendix("ocr"))},
            {"role": "user", "content": content},
        ]
        raw = await client.chat(messages)
        result = self._parse_ocr_output(raw)
        # 视觉失败识别:模型自述“未收到图片”→ 可操作的配置错误(不降级为质量低)
        raise_if_vision_failed(result, f"{s.ocr_base_url} / {s.ocr_model}")
        return result

    # ---------------------------------------------------------
    # 适配器 2:Azure Computer Vision Read API
    # ---------------------------------------------------------
    async def _extract_azure(self, image_paths: list[Path]) -> OcrExtractionResult:
        """通过 Azure Read API 进行 OCR(仅转录,姓名/学号从文本头部启发式提取)

        说明:Azure Read API 只做纯文本识别,不输出结构化学生信息。
        姓名与学号默认置空,交给教师在人工复核界面补录(这正是管线 B
        "OCR 复核"环节的价值所在)。
        """
        s = self._settings
        if not s.azure_ocr_endpoint or not s.azure_ocr_key:
            raise PipelineNetworkError(
                "AZURE_OCR_ENDPOINT / AZURE_OCR_KEY 未配置,无法使用 azure 模式",
                pipeline="PIPELINE_B_CLOUD",
            )
        endpoint = s.azure_ocr_endpoint.rstrip("/")
        url = f"{endpoint}/computervision/imageanalysis:analyze"
        params = {"api-version": "2024-02-01", "features": "read"}
        headers = {"Ocp-Apim-Subscription-Key": s.azure_ocr_key}

        all_lines: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=s.ocr_timeout) as client:
                for p in image_paths:
                    resp = await client.post(
                        url,
                        params=params,
                        headers={**headers, "Content-Type": "application/octet-stream"},
                        content=p.read_bytes(),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    # Azure Read 结果结构:readResult.blocks[].lines[].text
                    blocks = data.get("readResult", {}).get("blocks", [])
                    for block in blocks:
                        for line in block.get("lines", []):
                            text = line.get("text", "")
                            if text:
                                all_lines.append(text)
        except httpx.TimeoutException as e:
            raise PipelineNetworkError("Azure OCR 调用超时", pipeline="PIPELINE_B_CLOUD") from e
        except httpx.HTTPError as e:
            raise PipelineNetworkError(
                f"Azure OCR 请求失败:{type(e).__name__} {e}", pipeline="PIPELINE_B_CLOUD"
            ) from e

        transcribed = "\n".join(all_lines).strip()
        if not transcribed:
            raise PipelineNetworkError("Azure OCR 未识别到任何文本", pipeline="PIPELINE_B_CLOUD")
        return OcrExtractionResult(student_name="未知", student_id=None, transcribed_text=transcribed)

    # ---------------------------------------------------------
    # 适配器 3:纯本地 OCR(RapidOCR;LOCAL_OCR_ONLY=true 时启用)
    # ---------------------------------------------------------
    async def _extract_local_rapid(self, image_paths: list[Path]) -> OcrExtractionResult:
        """纯本地识别:整页转录 + 姓名区解析(完全离线,不调用任何云端/远程 OCR 端点)

        - 引擎:本地 RapidOCR(离线 CPU;与“上传后姓名预识别”共用同一实现与单例);
        - 姓名/学号:优先对首页“依据定位点”裁出的姓名区小图识别并复用 parse_identity
          (无定位点依据时退用首页整页文本,宽松解析);
        - 质量:按行平均置信度映射 recognition_quality;
        - 组件缺失时明确报错,绝不静默回退云端(保障“纯本地”语义)。
        """
        # 延迟导入:避免与 name_pre_ocr(顶层引用本模块)形成模块环
        from app.services.name_pre_ocr import parse_identity, rapid_available, recognize_lines

        if not rapid_available():
            raise PipelineConfigError(
                "未安装本地 OCR 组件(rapidocr_onnxruntime),无法使用「本地 OCR 模式」;"
                "可关闭该模式恢复云端识别,或重跑「首次安装 / 一键自检」安装组件",
                pipeline="PIPELINE_B_CLOUD",
            )

        # 1) 逐页整页识别(单页失败跳过,不影响其他页)
        page_texts: list[str] = []
        scores: list[float] = []
        for path in image_paths:
            lines = await asyncio.to_thread(recognize_lines, path)
            if not lines:
                logger.warning("[LOCAL-OCR] 页面识别无文本:%s", path.name)
                continue
            page_texts.append("\n".join(text for text, _score in lines))
            scores.extend(score for _text, score in lines)
        transcribed = "\n".join(page_texts).strip()
        if not transcribed:
            raise PipelineNetworkError("本地 OCR 未识别到任何文本", pipeline="PIPELINE_B_CLOUD")

        # 2) 姓名/学号:优先“依据定位点”裁出的姓名区小图,其次首页整页文本
        from app.services.sheet_align import crop_name_region_ex

        student_name: str | None = None
        student_id: str | None = None
        first = image_paths[0]
        try:
            crop, _meta = await asyncio.to_thread(crop_name_region_ex, first.read_bytes())
        except Exception as error:  # noqa: BLE001 —— 裁剪失败不影响整体转录
            crop = None
            logger.info("[LOCAL-OCR] 姓名区裁剪失败:%s", type(error).__name__)
        if crop is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as handle:
                handle.write(crop)
                tmp_path = Path(handle.name)
            try:
                lines = await asyncio.to_thread(recognize_lines, tmp_path)
                if lines:
                    student_name, student_id = parse_identity("\n".join(text for text, _s in lines))
            finally:
                tmp_path.unlink(missing_ok=True)
        # 逐字段回填:裁图未解析出的字段(如学号写在别处/裁图缺失)用首页整页文本补齐
        if (student_name is None or student_id is None) and page_texts:
            page_name, page_sid = parse_identity(page_texts[0])
            student_name = student_name or page_name
            student_id = student_id or page_sid

        # 3) 质量映射(全行平均置信度;行数不足时如实缺省)
        average = sum(scores) / len(scores) if scores else None
        if average is None:
            quality: str | None = None
        elif average >= 0.92:
            quality = "high"
        elif average >= 0.80:
            quality = "medium"
        else:
            quality = "low"

        return OcrExtractionResult(
            student_name=student_name or "未知",
            student_id=student_id,
            transcribed_text=transcribed,
            recognition_quality=quality,
            quality_note="本地 RapidOCR 离线转录",
        )

    # ---------------------------------------------------------
    # 内部工具
    # ---------------------------------------------------------
    @staticmethod
    def _parse_ocr_output(raw: str) -> OcrExtractionResult:
        """解析视觉模型输出的 JSON(失败时退化为"整段文本即转录"策略)

        视觉模型的输出通常较规整,但为了健壮性:
        若 JSON 解析失败,则把原始输出整体当作转录文本,姓名置为未知。
        """
        try:
            obj = extract_json_dict(raw)
            quality_raw = (normalize_optional_str(obj.get("recognition_quality")) or "").lower()
            quality = quality_raw if quality_raw in {"high", "medium", "low"} else None
            return OcrExtractionResult(
                student_name=str(obj.get("student_name") or "未知").strip() or "未知",
                student_id=normalize_optional_str(obj.get("student_id")),
                transcribed_text=str(obj.get("transcribed_text") or "").strip(),
                recognition_quality=quality,
                quality_note=normalize_optional_str(obj.get("quality_note")),
            )
        except ValueError as e:
            logger.warning("OCR 输出 JSON 解析失败,退化为纯文本转录:%s", e)
            text = raw.strip()
            # 若含代码块围栏,剥离后作为文本
            if "```" in text:
                segments = text.split("```")
                text = max(segments, key=len).strip()
            return OcrExtractionResult(student_name="未知", student_id=None, transcribed_text=text)
