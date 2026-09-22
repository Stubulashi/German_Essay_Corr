"""视觉端点诊断工具(排查“未收到任何图片”类问题;默认 dry 模式不发网络)

用法:
  python backend/scripts/diag_vision.py            # 只打印将发送的载荷结构(掩码)
  python backend/scripts/diag_vision.py --live     # 对当前配置端点真实发送并打印结果

五种载荷(对齐校方口径:标准 OpenAI 格式 + 直接上传 jpg):
  1) jpg_data_url   —— 线上形态(单张 jpg,与系统当前发送一致);
  2) png_data_url   —— 格式兼容对照(探测端点是否只吃 jpg);
  3) raw_base64_jpg —— 裸 base64(无 data URL 前缀,探测网关改写差异);
  4) dual_jpg       —— 双图(多页作文场景兼容性);
  5) text_only      —— 纯文本对照(区分“视觉不支持”与“网络问题”)。

输出只显示密钥掩码与响应前 160 字;不做任何配置修改。
"""

from __future__ import annotations

import asyncio
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402


def _mask(value: str) -> str:
    if not value:
        return "(空)"
    return value[:6] + "***" + value[-4:] if len(value) > 12 else "***"


def _probe_image_b64(fmt: str) -> str:
    from PIL import Image, ImageDraw

    buffer = io.BytesIO()
    image = Image.new("RGB", (128, 48), "white")
    ImageDraw.Draw(image).text((8, 16), "TEST 123", fill="black")
    image.save(buffer, fmt)
    return base64.b64encode(buffer.getvalue()).decode()


def _payloads(model: str) -> list[tuple[str, dict]]:
    jpg_b64 = _probe_image_b64("JPEG")
    png_b64 = _probe_image_b64("PNG")
    text_part = {"type": "text", "text": "图中写了什么?只回复识别到的文字"}
    jpg_data_url = f"data:image/jpeg;base64,{jpg_b64}"
    png_data_url = f"data:image/png;base64,{png_b64}"
    jpg_part = {"type": "image_url", "image_url": {"url": jpg_data_url}}
    png_part = {"type": "image_url", "image_url": {"url": png_data_url}}
    raw_part = {"type": "image_url", "image_url": {"url": jpg_b64}}
    return [
        ("jpg_data_url(线上形态)", {
            "model": model, "max_tokens": 32,
            "messages": [{"role": "user", "content": [text_part, jpg_part]}],
        }),
        ("png_data_url(格式对照)", {
            "model": model, "max_tokens": 32,
            "messages": [{"role": "user", "content": [text_part, png_part]}],
        }),
        ("raw_base64_jpg", {
            "model": model, "max_tokens": 32,
            "messages": [{"role": "user", "content": [text_part, raw_part]}],
        }),
        ("dual_jpg(双图)", {
            "model": model, "max_tokens": 32,
            "messages": [{"role": "user", "content": [text_part, jpg_part, jpg_part]}],
        }),
        ("text_only(对照)", {
            "model": model, "max_tokens": 8,
            "messages": [{"role": "user", "content": "只回复 OK"}],
        }),
    ]


async def _run_live(model: str, base_url: str, api_key: str) -> None:
    import httpx

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for label, payload in _payloads(model):
            try:
                response = await client.post(url, headers=headers, json=payload)
                body = response.text[:160].replace("\n", " ")
                print(f"  [{label}] HTTP {response.status_code} | {body}")
            except httpx.HTTPError as error:
                print(f"  [{label}] 网络错误:{type(error).__name__}")


def main() -> None:
    live = "--live" in sys.argv
    base_url = settings.ocr_base_url or settings.local_vlm_base_url
    api_key = settings.ocr_api_key or settings.local_vlm_api_key
    model = settings.ocr_model or settings.local_vlm_model
    print("视觉端点诊断")
    print(f"  端点:{base_url}")
    print(f"  模型:{model}")
    print(f"  密钥:{_mask(api_key)}")
    print(f"  模式:{'live(真实发送)' if live else 'dry(仅构造)'}")
    for label, payload in _payloads(model):
        content = payload["messages"][0]["content"]
        if isinstance(content, list):
            image_parts = [part for part in content if isinstance(part, dict) and part.get("type") == "image_url"]
            total = sum(len(part["image_url"]["url"]) for part in image_parts)
            print(f"  [{label}] 载荷就绪:文本+{len(image_parts)} 图(image_url 总长 {total} 字符)")
        else:
            print(f"  [{label}] 载荷就绪:纯文本")
    if live:
        print("真实发送结果:")
        asyncio.run(_run_live(model, base_url, api_key))
    else:
        print("提示:加 --live 参数真实发送(约 5 次请求);对照要点:jpg 成功而 png 失败=端点只吃 jpg;dual_jpg 失败=端点不支持多图;text_only 也失败=网络/鉴权问题。")


if __name__ == "__main__":
    main()
