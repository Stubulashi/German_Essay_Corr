"""LLM HTTP 客户端(OpenAI 兼容协议)

统一封装对以下端点的调用(均为 OpenAI Chat Completions 兼容格式):
- 管线 A:本地 VLM(vLLM / Ollama)
- 管线 B 步骤 1:Qwen2.5-VL 兼容端点
- 管线 B 步骤 3:DeepSeek API

职责:
- 组装多模态消息(文本 + base64 图像)
- 统一超时与错误映射(httpx 异常 -> PipelineNetworkError,供故障转移判定)
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.pipelines.base import PipelineNetworkError
from app.services import audit_service, llm_params

logger = logging.getLogger(__name__)

# 常见图片扩展名 -> MIME 类型(用于 data URL 组装)
_IMAGE_MIME_FALLBACK = "image/jpeg"

#: 单图发送护栏:超过该体积(字节)或长边(px) → 归一化为 JPEG 并等比缩小
#: 背景:部分网关对超大 body 静默截断/丢弃图片,表现为模型自述"未收到图片"
_MAX_INLINE_BYTES = 3_500_000
_MAX_INLINE_SIDE = 2400
_JPEG_QUALITY = 88


def _normalize_for_vision(raw: bytes) -> tuple[bytes, str]:
    """视觉输入归一化(校方端点口径:OpenAI 格式 + 直接上传 jpg)

    规则:
    1. JPEG 且体积/尺寸在护栏内 → 原样返回(零重编码损失);
    2. 其它格式(PNG/WEBP/BMP/HEIC 解码后)或超限 JPEG → 转 RGB JPEG(透明白底);
    3. 转码失败 → 空 MIME 表示回退(调用方按原字节+原扩展名发送,不阻断)。
    """
    try:
        from io import BytesIO

        from PIL import Image, ImageOps

        with Image.open(BytesIO(raw)) as source:
            fmt = (source.format or "").upper()
            width, height = source.size
            within_guard = (
                fmt == "JPEG"
                and len(raw) <= _MAX_INLINE_BYTES
                and max(width, height) <= _MAX_INLINE_SIDE
            )
            if within_guard:
                return raw, "image/jpeg"

            image = ImageOps.exif_transpose(source)
            if image.mode != "RGB":
                # 透明通道 → 白底合成(直接 convert 会把透明处变黑,影响识别)
                mask = image.split()[-1] if image.mode in ("RGBA", "LA", "PA") else None
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=mask)
                image = background
            if max(image.size) > _MAX_INLINE_SIDE:
                ratio = _MAX_INLINE_SIDE / max(image.size)
                image = image.resize(
                    (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                    Image.LANCZOS,
                )
            buffer = BytesIO()
            image.save(buffer, "JPEG", quality=_JPEG_QUALITY, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except Exception as error:  # noqa: BLE001 —— 归一化失败绝不阻断发送
        logger.warning("视觉输入归一化失败,按原字节发送:%s", error)
        return raw, ""


def image_to_data_url(image_path: Path) -> str:
    """将本地图片转换为 base64 data URL(供多模态消息使用)

    2026-09-20 调优(校方端点口径:标准 OpenAI 格式 + 直接 jpg):
    - 一律以 JPEG 发送(非 jpg 源自动转码,PNG 透明转白底);
    - 超体积/超尺寸图自动缩边重编码,防网关静默丢弃导致“未收到图片”;
    - 失败回退原字节(与旧版行为一致,不影响本地 vLLM/Ollama 等宽容端点)。
    """
    raw = image_path.read_bytes()
    normalized, mime = _normalize_for_vision(raw)
    if not mime:
        mime, _ = mimetypes.guess_type(str(image_path))
        if mime is None or not mime.startswith("image/"):
            mime = _IMAGE_MIME_FALLBACK
    b64 = base64.b64encode(normalized).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_image_message_parts(image_paths: list[Path], prompt_text: str) -> list[dict[str, Any]]:
    """组装 OpenAI 多模态消息的 content 数组(文本 + 多张图片)

    Args:
        image_paths: 图片绝对路径列表(按页序)
        prompt_text: 附带的文本指令(通常为任务说明)
    """
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
    for p in image_paths:
        parts.append({"type": "image_url", "image_url": {"url": image_to_data_url(p)}})
    return parts


def _compact(value: Any) -> str:
    """超参取值压缩为单行文本(供日志与审计)"""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        pipeline_tag: str = "",
        params_target: str = "",
    ):
        """
        Args:
            base_url:     端点地址(如 http://127.0.0.1:8000/v1)
            api_key:      API Key(本地端点可为任意占位值)
            model:        模型名
            timeout:      请求超时(秒)
            pipeline_tag: 管线标识(用于异常与日志定位)
            params_target: 超参目标(pipeline_a/ocr/deepseek);空 = legacy(仅画像与学习记录生效)
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.pipeline_tag = pipeline_tag
        self.params_target = params_target

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = True,
    ) -> str:
        """发起一次 Chat Completion 调用,返回模型输出的文本内容

        Args:
            messages:            消息列表(支持多模态 content 数组)
            temperature:         调用点显式温度(None = 由超参设置/自动适配决定,历史默认 0.2)
            max_tokens:          调用点显式最大生成数(None = 由超参设置/自动适配决定)
            response_format_json: 是否请求 JSON 输出模式(设置中心可强制/省略;不支持时解析器兜底)
        """
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        plan = llm_params.assemble(
            target=self.params_target,
            model=self.model,
            base_url=self.base_url,
            caller_temperature=temperature,
            caller_max_tokens=max_tokens,
            caller_response_format_json=response_format_json,
        )
        payload.update(plan.params)
        await self._trace_params(plan)

        url = f"{self.base_url}/chat/completions"
        try:
            data = await self._send_with_param_retry(payload)
        except httpx.TimeoutException as e:
            raise PipelineNetworkError(
                f"调用超时(>{self.timeout}s):{url}", pipeline=self.pipeline_tag
            ) from e
        except httpx.HTTPStatusError as e:
            # 4xx/5xx:部分场景(如 API Key 无效)属于配置问题,但统一按网络层错误抛出,
            # 由上层决定是否故障转移;错误正文保留尾部 300 字符便于排查
            body = e.response.text[-300:] if e.response is not None else ""
            raise PipelineNetworkError(
                f"服务返回错误状态 {e.response.status_code if e.response else '?'}:{body}",
                pipeline=self.pipeline_tag,
            ) from e
        except httpx.HTTPError as e:
            raise PipelineNetworkError(
                f"网络请求失败:{type(e).__name__} {e}", pipeline=self.pipeline_tag
            ) from e

        try:
            message = data["choices"][0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise PipelineNetworkError(
                f"响应结构异常(缺少 choices[0].message.content):{str(data)[:300]}",
                pipeline=self.pipeline_tag,
            ) from e

        # 内容为空:区分“思考型模型仅输出推理段”与一般空响应,给出可操作指引
        if content is None or (isinstance(content, str) and not content.strip()):
            reasoning = ""
            if isinstance(message, dict):
                reasoning = str(message.get("reasoning_content") or "").strip()
            if reasoning:
                raise PipelineNetworkError(
                    "模型仅返回了思考内容(reasoning_content),正式内容为空;"
                    "当前模型疑似思考型/非视觉模型,请改用非思考模式或视觉模型",
                    pipeline=self.pipeline_tag,
                )
            raise PipelineNetworkError("模型返回内容为空", pipeline=self.pipeline_tag)

        logger.debug("[%s] LLM 原始输出前 200 字符:%s", self.pipeline_tag, str(content)[:200])
        return str(content)

    # ---------------------------------------------------------
    # 发送与容错(400 参数不支持 -> 剔除并重试一次)
    # ---------------------------------------------------------
    async def _send_with_param_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发送请求;若 400 明确指向某参数不支持,剔除该参数并重试一次

        仍失败(或无法解析出参数名)时原样抛出,由 chat 按既有分类映射为 PipelineNetworkError。
        """
        try:
            return await self._post_json(payload)
        except httpx.HTTPStatusError as e:
            param = self._unsupported_param(e)
            if param is None or param not in payload:
                raise
            payload.pop(param, None)
            await self._record_stripped_param(param, e)
            logger.warning(
                "[%s] 端点拒绝参数 %s(%s),已剔除并重试一次",
                self.pipeline_tag, param, self.base_url,
            )
            return await self._post_json(payload)

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        """单次 HTTP 调用(抛出 httpx 原生异常,由上层统一映射)"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def _unsupported_param(self, error: httpx.HTTPStatusError) -> str | None:
        """从 400 响应体解析出"不支持的参数名"(仅接受可安全剔除的键)"""
        response = error.response
        if response is None or response.status_code != 400:
            return None
        param = llm_params.parse_unsupported_param(response.text)
        return param if param and param not in ("model", "messages") else None

    async def _record_stripped_param(self, param: str, error: httpx.HTTPStatusError) -> None:
        """记录被自动剔除的参数(内存+落库)并写入审计(便于追溯)"""
        await llm_params.record_unsupported(self.base_url, self.model, param)
        body = error.response.text[-160:] if error.response is not None else ""
        await audit_service.audit(
            "llm.param_stripped",
            detail=f"{self.pipeline_tag} {self.base_url} {self.model} 剔除参数 {param};端点原文:{body}",
        )

    async def _trace_params(self, plan: llm_params.ParamPlan) -> None:
        """每次请求最终实际发送的超参(日志 + 审计,便于排查与追溯)"""
        sent = " ".join(f"{key}={_compact(value)}" for key, value in plan.params.items())
        stripped = " ".join(f"{key}({reason})" for key, reason in plan.omitted.items())
        suffix = f" 剔除=[{stripped}]" if stripped else ""
        logger.info(
            "[%s] 请求超参: target=%s model=%s 发送=[%s]%s",
            self.pipeline_tag, self.params_target or "legacy", self.model, sent, suffix,
        )
        await audit_service.audit(
            "llm.request",
            detail=f"{self.pipeline_tag or 'LLM'} {self.model} 发送=[{sent}]{suffix}",
        )
