"""上传后姓名预识别(后台轻量服务)

目标:教师上传后,在批改开始/完成之前,尽快把"姓名/学号"从姓名区小图里认出来,
在队列页/审阅页即时展示(第 7 项"上传后即刻显示"的落地实现)。

流程:
任务创建后 schedule → worker 取任务 → `sheet_align.crop_name_region_ex` **依据定位点**裁姓名区
(无定位点依据 → 整图识别,不做启发式裁切)
→ 引擎链(演示短路 → 已配置识别端点(OCR 位优先,其次本地 VLM) → 本地 RapidOCR 兜底)
→ `parse_identity` 解析(中文/拼音姓名 + 学号;含花名册同班唯一命中纠错)
→ **条件更新**(仅 result 为空且任务活跃时写入;绝不覆盖正式批改结果)→ 审计 name.pre_ocr
(全分支可观测:跳过/无定位点/未识别/写入均留痕,含裁切依据 basis)。

设计约束:
- 失败全静默:任何异常都不影响上传与批改主链路;临时文件用后即删;
- 可选组件:rapidocr_onnxruntime 缺失时自动跳过本地引擎(仅用已配置端点);
- MOCK_MODE:直接返回演示常量(不走网络),供演示与冒烟;
- 本地 RapidOCR:完全离线、CPU 可跑,PP-OCR 字符集原生覆盖中文+拉丁字母(拼音)+数字。
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from collections import deque
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import select, update

from app.config import settings
from app.db.database import SessionLocal
from app.models.db_models import ClassRoster, CorrectionTask
from app.models.schemas import OcrExtractionResult
from app.pipelines.mock import MOCK_STUDENT_ID, MOCK_STUDENT_NAME
from app.pipelines.prompts import build_ocr_prompt, build_ocr_user_message
from app.services.audit_service import audit
from app.services.llm_client import LLMClient, build_image_message_parts
from app.services.ocr_anomaly import raise_if_vision_failed
from app.services.ocr_client import OcrClient
from app.services.qr_identity import decode_qr_identity
from app.services.prompt_overrides import get_appendix
from app.services.sheet_align import crop_name_region_ex

try:  # 可选组件:本地离线 OCR(缺失时仅用端点引擎)
    from rapidocr_onnxruntime import RapidOCR as _RapidOCR
except Exception:  # noqa: BLE001
    _RapidOCR = None

logger = logging.getLogger(__name__)

#: 允许被条件更新的任务状态(批改完成/失败等一律不碰);
#: WAITING_REVIEW:管线 B 人工复核挂起时 result 仍为空——姓名预识别同样可先行展示,
#: 正式批改完成时以正式结果覆盖(语义:预识别先行展示,正式结果为准)
_ACTIVE_STATUSES = ("PENDING", "PROCESSING", "UPLOADED", "WAITING_REVIEW")

#: 解析用模式
_LATIN_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]{2,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")
_ID_KEYWORDS = ("学号", "id", "nummer", "matrikel")
_NAME_KEYWORDS = ("姓名", "name")
_STOPWORDS = {
    "klasse", "name", "datum", "nummer", "thema", "id", "matrikel", "pinyin",
    "alter", "aler", "age", "jahre",  # 个人信息关键词(含 Alter 常见 OCR 截断变体)
    "班级", "姓名", "学号", "日期", "题目", "名字", "拼音",
}


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text in ("未知", "无", "null", "none"):
        return None
    return text


def parse_identity(
    text: str | None, *, require_keyword: bool = False
) -> tuple[str | None, str | None]:
    """从姓名区 OCR 文本中解析 (姓名, 学号);任一可为 None

    - 姓名:关键词(姓名/Name)邻近优先;支持中文(2-4 字)与拼音(2-4 个拉丁词,Title Case);
    - 学号:关键词(学号/ID/Nummer)邻近优先,否则取 6-12 位数字串;
    - require_keyword=True 时仅当出现「姓名/Name」关键词才解析姓名
      (用于整页文本场景,避免把正文句词误当姓名);默认 False 保持既有行为。
    """
    if not text:
        return None, None
    lowered = text.lower()

    student_id: str | None = None
    for keyword in _ID_KEYWORDS:
        index = lowered.find(keyword)
        if index == -1:
            continue
        window = text[index : index + 40]
        match = re.search(r"[A-Za-z]{0,2}\d{4,12}", window)
        if match:
            student_id = match.group(0).upper()
            break
    if student_id is None:
        match = re.search(r"\d{6,12}", text)
        if match:
            student_id = match.group(0)

    name: str | None = None
    for keyword in _NAME_KEYWORDS:
        index = lowered.find(keyword)
        if index == -1:
            continue
        window = text[index : index + 60]
        name = _name_from_window(window)
        if name:
            break
    if name is None and not require_keyword:
        name = _name_from_window(text)
    return name, student_id


def _name_from_window(window: str) -> str | None:
    """在片段中提取姓名:中文 2-4 字优先,其次 2-4 个拉丁词(拼音)"""
    for run in _CJK_RUN_RE.findall(window):
        if run not in _STOPWORDS and not any(word in run for word in ("班级", "姓名", "学号", "日期", "题目")):
            return run
    words = [w for w in _LATIN_WORD_RE.findall(window) if w.lower() not in _STOPWORDS]
    if 2 <= len(words) <= 4:
        return " ".join(word.capitalize() for word in words)
    if len(words) == 1 and len(words[0]) >= 4:  # 如 "LiMing"
        return words[0]
    return None


#: 年龄解析关键词(前后小窗内找 1-3 位数字;仅用于个人信息探测)
#: "aler" 为 "Alter" 的常见 OCR 截断变体(实测)
_AGE_KEYWORDS = ("alter", "aler", "age", "jahre", "年龄", "岁")
_AGE_MIN, _AGE_MAX = 1, 120


def parse_age(text: str | None) -> str | None:
    """从 OCR 文本中解析年龄(关键词邻近;1-120 合理性过滤;无关键词不猜)

    - 支持「Alter: 16」「Age 15」「年龄 17 岁」「16 岁」等写法;
    - 数字取关键词前后各 20 字符窗口内的首个 1-3 位整数;
    - 全文无年龄关键词时不做数字扫描(避免把学号/日期误当年龄)。
    """
    if not text:
        return None
    lowered = text.lower()
    for keyword in _AGE_KEYWORDS:
        start = 0
        while True:
            index = lowered.find(keyword, start)
            if index == -1:
                break
            window = text[max(0, index - 20) : index + len(keyword) + 20]
            match = re.search(r"\d{1,3}", window)
            if match:
                value = int(match.group(0))
                if _AGE_MIN <= value <= _AGE_MAX:
                    return str(value)
            start = index + len(keyword)
    return None


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s.·-]+", "", (value or "")).lower()


async def _enhance_identity(
    class_id: int | None, name: str | None, student_id: str | None, crop_bytes: bytes | None
) -> tuple[str | None, str | None]:
    """花名册匹配 + 按档位的本地手写模型近邻增强(light/medium/precise)

    - light:仅花名册精确/包含匹配(现状行为);
    - medium/precise:额外用姓名区图特征与班级样本库近邻判别:
      OCR 与模型一致 → 采用并补全学号;OCR 未命名/未命中花名册 → 采用模型判定;
      凡失败均静默回退到花名册结果。
    """
    name, student_id = await _correct_with_roster(class_id, name, student_id)
    if class_id is None or crop_bytes is None:
        return name, student_id
    try:
        from app.services.handwriting_service import (
            effective_level,
            load_model_entries,
            match_candidate,
        )

        level = effective_level()
        if level == "light":
            return name, student_id
        entries = await load_model_entries(class_id)
        if not entries:
            return name, student_id
        model_match = match_candidate(crop_bytes, entries, level)
        if model_match is None:
            return name, student_id
        model_name, model_sid = model_match
        if name and _normalize_name(name) == _normalize_name(model_name):
            return name, student_id or (model_sid or None)
        if not name:
            return model_name, student_id or (model_sid or None)
        return name, student_id
    except Exception:  # noqa: BLE001 —— 模型增强失败静默回退
        return name, student_id


async def _correct_with_roster(
    class_id: int | None, name: str | None, student_id: str | None
) -> tuple[str | None, str | None]:
    """花名册同班唯一命中纠错:规范姓名写法;学号仅在识别为空时补全;任何异常静默忽略"""
    if class_id is None or not name:
        return name, student_id
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(ClassRoster.name, ClassRoster.student_id).where(
                        ClassRoster.class_id == class_id
                    )
                )
            ).all()
    except Exception as error:  # noqa: BLE001 —— 加密锁定/解密失败等一律忽略
        logger.info("花名册纠错跳过:%s", type(error).__name__)
        return name, student_id
    if not rows:
        return name, student_id

    target = _normalize_name(name)
    exact = [(r[0], r[1]) for r in rows if _normalize_name(r[0]) == target]
    if len(exact) == 1:
        canonical_name, roster_id = exact[0]
        return canonical_name, student_id or (roster_id or None)
    contained = [
        (r[0], r[1])
        for r in rows
        if target and (target in _normalize_name(r[0]) or _normalize_name(r[0]) in target)
    ]
    if len(contained) == 1:
        canonical_name, roster_id = contained[0]
        return canonical_name, student_id or (roster_id or None)
    return name, student_id


async def _extract_via_endpoint(
    base_url: str, api_key: str, model: str, image_path: Path, tag: str
) -> OcrExtractionResult:
    """用已配置端点对姓名区小图做一次 OCR(复用既有 OCR 提示词与解析,契约零改)"""
    client = LLMClient(
        base_url=base_url, api_key=api_key, model=model, timeout=60.0, pipeline_tag=tag,
        params_target="ocr" if "ocr" in tag.lower() else "pipeline_a",
    )
    content = build_image_message_parts([image_path], build_ocr_user_message(1))
    messages: list[dict] = [
        {"role": "system", "content": build_ocr_prompt(get_appendix("ocr"))},
        {"role": "user", "content": content},
    ]
    raw = await client.chat(messages)
    result = OcrClient._parse_ocr_output(raw)
    raise_if_vision_failed(result, f"{base_url} / {model}")
    return result


_rapid_engine = None


def rapid_available() -> bool:
    """本地 RapidOCR 组件是否可用(纯本地 OCR 模式的前置条件)"""
    return _RapidOCR is not None


def recognize_lines(image_path: Path) -> list[tuple[str, float]] | None:
    """本地 RapidOCR(离线 CPU)识别文本行;返回 [(文本, 置信度)];缺组件/失败返回 None

    与姓名预识别共用同一引擎单例(内存只加载一次);
    纯本地 OCR 模式(ocr_client)复用本函数。
    """
    global _rapid_engine
    if _RapidOCR is None:
        return None
    try:
        if _rapid_engine is None:
            _rapid_engine = _RapidOCR()
        result, _elapsed = _rapid_engine(str(image_path))
        if not result:
            return None
        lines: list[tuple[str, float]] = []
        for item in result:
            if len(item) > 2:
                text = str(item[1]).strip()
                if text:
                    lines.append((text, float(item[2])))
            elif len(item) > 1 and str(item[1]).strip():
                lines.append((str(item[1]).strip(), 0.0))
        return lines or None
    except Exception as error:  # noqa: BLE001
        logger.info("本地 RapidOCR 识别失败:%s", type(error).__name__)
        return None


def _extract_via_local_rapid(image_path: Path) -> str | None:
    """本地 RapidOCR 识别文本行(纯文本;缺组件/失败返回 None;委托 recognize_lines)

    保留本函数名与行为:既有调用与测试桩均以本函数为接口。
    """
    lines = recognize_lines(image_path)
    if not lines:
        return None
    return "\n".join(text for text, _score in lines)


#: 整图识别输入的长边上限(无定位点回退路径;控制传输体积与耗时)
_FULL_IMAGE_MAX_SIDE = 1600


def _scaled_full_image(content: bytes) -> bytes | None:
    """无定位点回退:生成用于识别的整图 JPEG(长边超限缩小;失败返回 None)"""
    try:
        with Image.open(BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            side = max(image.size)
            if side > _FULL_IMAGE_MAX_SIDE:
                ratio = _FULL_IMAGE_MAX_SIDE / side
                image = image.resize(
                    (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                    Image.LANCZOS,
                )
            buffer = BytesIO()
            image.save(buffer, "JPEG", quality=88, optimize=True)
            return buffer.getvalue()
    except Exception as error:  # noqa: BLE001
        logger.info("整图准备失败:%s", type(error).__name__)
        return None


def probe_personal_info(content: bytes) -> dict:
    """单张图片的个人信息探测(纯内存;强制使用本地引擎,不触任何端点)

    供工作台「强制性姓名识别」选文件即时探测调用,严格顺序:
    0. 二维码优先(纯本地解码;不依赖 OCR 组件):命中即产出身份并结束;
    1. 先依定位点裁剪姓名区(crop_name_region_ex:无检测通过 → 不裁);
    2. 裁剪产出后立即识别——识别输入恒为**裁剪产物**;无定位点依据时
       回退整图(长边 1600)继续识别(不中断);
    返回 {"name","age","student_id","status","note","basis"}:
    - status = ok(至少解析出姓名/年龄/学号之一) | empty(识别成功但无个人信息)
             | error(环境/组件/解码问题;note 说明原因)
    - basis = qr(二维码解码) | canonical/landmarks(依定位点裁剪) | full-image(无定位点整图回退)
    """
    result: dict = {
        "name": None,
        "age": None,
        "student_id": None,
        "status": "error",
        "note": "",
        "basis": None,
    }
    # 0) 二维码优先(纯本地;解码异常/未命中均静默转入既有链路)
    try:
        qr_identity = decode_qr_identity(content)
    except Exception:  # noqa: BLE001
        qr_identity = None
    if qr_identity and (qr_identity.get("name") or qr_identity.get("student_id")):
        result.update(
            name=qr_identity.get("name"),
            student_id=qr_identity.get("student_id"),
            status="ok",
            basis="qr",
        )
        logger.debug("个人识别探测:输入依据 qr → 二维码直接解码")
        return result
    if not rapid_available():
        result["note"] = "missing-component"
        return result
    try:
        crop, meta = crop_name_region_ex(content)
        if crop is not None:
            target: bytes | None = crop
            strict_name = False  # 依定位点裁出的姓名区:宽松解析
            result["basis"] = str(meta.get("basis") or "landmarks")
        else:
            target = _scaled_full_image(content)
            strict_name = True  # 整页文本:仅关键词命中才取名(防正文误报)
            result["basis"] = "full-image"
        if target is None:
            result["note"] = "decode-failed"
            return result
        logger.debug(
            "个人识别探测:输入依据 %s → %s",
            result["basis"],
            "裁剪小图" if crop is not None else "整图回退",
        )

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as handle:
                handle.write(target)
                tmp_path = Path(handle.name)
            lines = recognize_lines(tmp_path)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        text = "\n".join(text_line for text_line, _score in lines) if lines else ""
        if not text.strip():
            result["status"] = "empty"
            result["note"] = "no-text"
            return result
        name, student_id = parse_identity(text, require_keyword=strict_name)
        age = parse_age(text)
        result.update(name=name, age=age, student_id=student_id)
        result["status"] = "ok" if (name or age or student_id) else "empty"
        return result
    except Exception as error:  # noqa: BLE001 —— 探测失败静默为 error 状态(不阻断任何流程)
        logger.info("个人识别探测失败:%s", type(error).__name__)
        result["note"] = f"exception:{type(error).__name__}"
        return result


async def run_pre_ocr(task_id: int) -> None:
    """对单个任务执行姓名预识别(由服务 worker 调用;全程静默降级)

    识别输入选择(裁切严格依据定位点):
    - 定位点命中(规范画布 / 可找平照片)→ 用定位点几何裁出的姓名区小图;
    - 无定位点依据 → 整图识别(不裁;不做与定位点无关的启发式裁切)。
    """
    if not settings.name_pre_ocr_enabled:
        return
    started = time.perf_counter()
    try:
        async with SessionLocal() as session:
            task = await session.get(CorrectionTask, task_id)
            if task is None:
                return
            if task.result is not None or task.status not in _ACTIVE_STATUSES:
                await audit(
                    "name.pre_ocr",
                    ok=True,
                    detail=f"task {task_id};跳过(状态 {task.status},无需预识别)",
                    source="worker",
                )
                return
            paths = [settings.upload_path / p for p in (task.image_paths or [])]
            class_id = task.class_id
        first = next((p for p in paths if p.exists()), None)
        if first is None:
            await audit(
                "name.pre_ocr",
                ok=True,
                detail=f"task {task_id};跳过(图片文件缺失)",
                source="worker",
            )
            return

        # 二维码优先(纯本地解码;不依赖 OCR 组件):命中即写入身份并结束
        first_bytes = first.read_bytes()
        qr_identity = await asyncio.to_thread(decode_qr_identity, first_bytes)
        if qr_identity and (qr_identity.get("name") or qr_identity.get("student_id")):
            qr_values: dict[str, str] = {}
            qr_name = _clean(qr_identity.get("name"))
            qr_sid = _clean(qr_identity.get("student_id"))
            if qr_name:
                qr_values["student_name"] = qr_name
            if qr_sid:
                qr_values["student_id"] = qr_sid
            async with SessionLocal() as session:
                qr_result = await session.execute(
                    update(CorrectionTask)
                    .where(
                        CorrectionTask.id == task_id,
                        CorrectionTask.status.in_(_ACTIVE_STATUSES),
                    )
                    .values(**qr_values)
                )
                await session.commit()
            elapsed = int((time.perf_counter() - started) * 1000)
            await audit(
                "name.pre_ocr",
                ok=True,
                detail=(
                    f"task {task_id};来源 qr;定位点依据 qr(二维码解码);"
                    f"写入字段 {'/'.join(qr_values)};命中 {qr_result.rowcount};耗时 {elapsed}ms"
                ),
                source="worker",
            )
            return

        # 裁切严格依据检测到的定位点;无定位点依据返回 None(不做启发式裁切)
        crop, crop_meta = await asyncio.to_thread(crop_name_region_ex, first_bytes)
        basis = str(crop_meta.get("basis") or "none")
        main_points = crop_meta.get("main_points") or []
        if crop is not None:
            engine_input: bytes | None = crop
        else:
            engine_input = await asyncio.to_thread(_scaled_full_image, first_bytes)
            basis = "full-image"
        if engine_input is None:
            await audit(
                "name.pre_ocr",
                ok=True,
                detail=f"task {task_id};跳过(无可用识别输入;定位点依据 {basis})",
                source="worker",
            )
            return

        name: str | None = None
        student_id: str | None = None
        source = ""
        if settings.mock_mode:
            name, student_id, source = MOCK_STUDENT_NAME, MOCK_STUDENT_ID, "mock"
        else:
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as handle:
                    handle.write(engine_input)
                    tmp_path = Path(handle.name)
                candidates: list[tuple[str, str, str, str]] = []
                if settings.local_ocr_only:
                    # 纯本地 OCR 模式:跳过全部端点,仅以本地引擎为准(确保完全离线)
                    logger.info("本地 OCR 模式:姓名预识别跳过端点,仅使用本地引擎")
                else:
                    if settings.ocr_provider == "vlm_openai" and settings.ocr_base_url and settings.ocr_model:
                        candidates.append(
                            (settings.ocr_base_url, settings.ocr_api_key or "", settings.ocr_model, "ocr-endpoint")
                        )
                    if settings.local_vlm_base_url and settings.local_vlm_model:
                        candidates.append(
                            (
                                settings.local_vlm_base_url,
                                settings.local_vlm_api_key or "",
                                settings.local_vlm_model,
                                "vlm-endpoint",
                            )
                        )
                for base_url, api_key, model, tag in candidates:
                    try:
                        ocr = await _extract_via_endpoint(base_url, api_key, model, tmp_path, tag)
                        name, student_id, source = _clean(ocr.student_name), _clean(ocr.student_id), tag
                        if name or student_id:
                            break
                    except Exception as error:  # noqa: BLE001 —— 换下一引擎
                        logger.info("预识别端点(%s)失败:%s", tag, type(error).__name__)
                if not name and not student_id and _RapidOCR is not None:
                    text = await asyncio.to_thread(_extract_via_local_rapid, tmp_path)
                    if text:
                        name, student_id = parse_identity(text)
                        source = "local-rapidocr"
            finally:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)

        name, student_id = await _enhance_identity(class_id, name, student_id, crop)
        basis_note = f"定位点依据 {basis}" + (f"(块 {len(main_points)}/4)" if main_points else "")
        if not name and not student_id:
            await audit(
                "name.pre_ocr",
                ok=True,
                detail=f"task {task_id};未识别到姓名/学号(来源 {source or 'none'};{basis_note})",
                source="worker",
            )
            return

        values: dict[str, str] = {}
        if name:
            values["student_name"] = name
        if student_id:
            values["student_id"] = student_id
        async with SessionLocal() as session:
            # 原子条件更新:仅活跃状态可写。
            # 注意:result 列为 SQLAlchemy JSON 类型(None 默认序列化为 'null' 文本而非 SQL NULL),
            # 不能用 result IS NULL 做条件;“批改已写结果”与“状态流转”在同一事务提交,
            # 故以状态白名单作为防覆盖条件(读取阶段另已预检 result 非空即退出)。
            result = await session.execute(
                update(CorrectionTask)
                .where(
                    CorrectionTask.id == task_id,
                    CorrectionTask.status.in_(_ACTIVE_STATUSES),
                )
                .values(**values)
            )
            await session.commit()
        elapsed = int((time.perf_counter() - started) * 1000)
        await audit(
            "name.pre_ocr",
            ok=True,
            detail=(
                f"task {task_id};来源 {source};{basis_note};写入字段 {'/'.join(values)};"
                f"命中 {result.rowcount};耗时 {elapsed}ms"
            ),
            source="worker",
        )
    except Exception:  # noqa: BLE001 —— 预识别绝不影响主流程
        logger.warning("姓名预识别异常(task %s)", task_id, exc_info=True)
        try:
            await audit(
                "name.pre_ocr",
                ok=False,
                detail=f"task {task_id};异常(详见后端日志)",
                source="worker",
            )
        except Exception:  # noqa: BLE001 —— 审计失败不再外抛
            pass


class NamePreOcrService:
    """姓名预识别轻量服务(asyncio.Queue + N worker;不占批改并发)"""

    def __init__(self, max_concurrent: int = 2):
        self._max_concurrent = max(1, max_concurrent)
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._queued: set[int] = set()
        #: 最近单任务耗时(供全局状态条估算 ETA)
        self._durations: deque[float] = deque(maxlen=20)

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def progress_snapshot(self) -> dict:
        """在途任务数与滚动均耗(供全局状态条;数据不足如实缺省)"""
        average = sum(self._durations) / len(self._durations) if self._durations else None
        return {"queued": len(self._queued), "recent_avg_seconds": average}

    def schedule(self, task_ids: list[int]) -> None:
        """任务创建后调用:入队(幂等;不阻塞)"""
        if not settings.name_pre_ocr_enabled:
            return
        for task_id in task_ids:
            if task_id in self._queued:
                continue
            self._queued.add(task_id)
            self._queue.put_nowait(task_id)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for index in range(self._max_concurrent):
            worker = asyncio.create_task(self._worker_loop(index), name=f"name-pre-ocr-{index}")
            self._workers.append(worker)
        logger.info("姓名预识别服务已启动(并发数:%s;本地引擎:%s)",
                    self._max_concurrent, "可用" if _RapidOCR is not None else "缺省(仅端点)")

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def _worker_loop(self, worker_index: int) -> None:
        while self._running:
            try:
                task_id = await self._queue.get()
            except asyncio.CancelledError:
                break
            started = time.monotonic()
            try:
                await run_pre_ocr(task_id)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.warning("预识别 worker %s 未捕获异常(task %s)", worker_index, task_id)
            finally:
                self._durations.append(time.monotonic() - started)
                self._queued.discard(task_id)
                self._queue.task_done()


#: 模块级单例(随 lifespan 启停;路由创建任务后直接 schedule)
name_pre_ocr_service = NamePreOcrService()
