"""学生手写样本模型(采集 → 处理 → 特征签名库;本地模板模型)

透明定性:本服务构建的是**本地手写样本模板模型**(非神经网络训练):
每位学生的样本经找平/裁姓名区/识别后,提取姓名区图像特征签名入库;
"上传后姓名预识别"按设置档位(轻量/中等/最精确)用该特征库做近邻判别,
与花名册约束结合提升手写姓名识别命中率。全程本地计算、可量化、可复现。

处理链(逐样本):保存 → 找平 → 裁姓名区 → 识别(端点优先,本地 RapidOCR 兜底)
→ 花名册对账 → 特征提取 → 完成;进度含 done/total/阶段/ETA。

特征设计(全量向量,统一存储):
- 两阈值二值化(自适应基阈值 ×1.0 / ×0.8)各产生一段 163 维子向量:
  3×6 分区墨量(18) + 垂直投影(96) + 水平投影(48) + 宽高比(1);
- 中等档使用前 163 维;最精确档使用全量(326 维)——训练/匹配天然同构。
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sys
from collections import deque
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import func, select

from app.config import settings
from app.models.db_models import ClassRoster, HandwritingModel, HandwritingSample, utc_now
from app.services.audit_service import audit
from app.services.sheet_align import align_standard_sheet, crop_name_region

logger = logging.getLogger(__name__)

#: 抄写素材句库(离线本地,零模型依赖;正文句与题目句各一句随机组合)
SENTENCES: tuple[str, ...] = (
    "Meine Familie wohnt in einer kleinen Stadt.",
    "Am Wochenende gehe ich gern mit Freunden spazieren.",
    "Im Sommer schwimmen wir oft im See.",
    "Mein Bruder spielt jeden Tag Fußball.",
    "Ich lese abends ein Buch und trinke Tee.",
    "Unsere Schule hat einen großen Garten.",
    "Der Hund meiner Nachbarin heißt Max.",
    "Wir kochen heute Nudeln mit Gemüse.",
)
TOPICS: tuple[str, ...] = (
    "Mein bester Freund",
    "Ein schöner Sonntag",
    "Meine Schule",
    "Ein Tag am See",
    "Mein Lieblingstier",
    "Meine Familie",
    "Das Wochenende",
    "Unser Sportfest",
)

#: 特征参数(目标尺寸 96×48;单段 = 18 分区 + 96 垂直投影 + 48 水平投影 + 1 宽高比)
_TARGET_W, _TARGET_H = 96, 48
_FEATURE_SEGMENT = 163
_FEATURE_FULL = _FEATURE_SEGMENT * 2

#: 近邻匹配参数(特征已分段归一,欧氏距离;阈值与领先差经合成样本校准)
_MATCH_MAX_DIST = 0.55
_MATCH_GAP = 1.08
_PRECISE_K = 3

_MODEL_CACHE_TTL = 60.0


def generate_practice_material() -> dict:
    """生成抄写素材:一题目句 + 一正文句(本地句库随机)"""
    return {"topic": random.choice(TOPICS), "sentence": random.choice(SENTENCES)}


def _describe_segment(binary: np.ndarray) -> np.ndarray | None:
    """单阈值子向量(163 维):3×6 分区墨量 + 垂直/水平投影 + 宽高比(分段归一)"""
    ys, xs = np.where(binary)
    if len(xs) < 20:  # 墨量过少(空白/无效),不可用作签名
        return None
    crop = binary[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1].astype(np.float32)
    height, width = crop.shape
    image = Image.fromarray((crop * 255).astype(np.uint8)).resize(
        (_TARGET_W, _TARGET_H), Image.BILINEAR
    )
    matrix = np.asarray(image, dtype=np.float32) / 255.0
    gh, gw = _TARGET_H // 3, _TARGET_W // 6
    grid = matrix.reshape(3, gh, 6, gw).mean(axis=(1, 3)).ravel()  # 18
    vproj = matrix.mean(axis=0)  # 96
    hproj = matrix.mean(axis=1)  # 48
    aspect = np.array([min(4.0, width / max(1, height)) / 4.0], dtype=np.float32)

    def unit(part: np.ndarray) -> np.ndarray:
        scale = float(part.max())
        return part / scale if scale > 1e-6 else part

    return np.concatenate([unit(grid), unit(vproj), unit(hproj), aspect])


def describe_features(content: bytes) -> list[float] | None:
    """姓名区图像 → 特征签名(全量 326 维;两阈值子段拼接);失败返回 None"""
    try:
        with Image.open(BytesIO(content)) as source:
            gray = ImageOps.exif_transpose(source).convert("L")
    except Exception:  # noqa: BLE001
        return None
    array = np.asarray(gray, dtype=np.float32)
    base = max(60.0, float(array.mean()) * 0.85)
    segments = []
    for factor in (1.0, 0.8):
        segment = _describe_segment(array < base * factor)
        if segment is None:
            return None
        segments.append(segment)
    vector = np.concatenate(segments)
    return [float(value) for value in vector]


def feature_slice(vector: list[float], level: str) -> np.ndarray:
    """按档位截取特征:中等=前 163 维;最精确=全量"""
    array = np.asarray(vector, dtype=np.float32)
    if level == "precise" and array.shape[0] >= _FEATURE_FULL:
        return array[:_FEATURE_FULL]
    return array[:_FEATURE_SEGMENT]


# ---------------------------------------------------------
# 设备画像与档位推荐(零新依赖:三平台分支)
# ---------------------------------------------------------
def _total_memory_gb() -> float | None:
    try:
        if os.name == "nt":  # Windows: GlobalMemoryStatusEx
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return round(status.ullTotalPhys / (1024**3), 1)
        if sys.platform == "darwin":  # macOS: sysctl
            import subprocess

            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3
            )
            return round(int(result.stdout.strip()) / (1024**3), 1)
        meminfo = Path("/proc/meminfo")  # Linux
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal"):
                return round(int(re.findall(r"\d+", line)[0]) / (1024**2), 1)
    except Exception:  # noqa: BLE001
        return None
    return None


def system_profile() -> dict:
    """本机画像:核心数 / 内存(GB,可能 None)/ 推荐档位"""
    cores = os.cpu_count() or 2
    ram_gb = _total_memory_gb()
    if ram_gb is not None and ram_gb >= 16 and cores >= 8:
        recommended = "precise"
    elif ram_gb is not None and ram_gb >= 8 and cores >= 4:
        recommended = "medium"
    elif ram_gb is None and cores >= 8:
        recommended = "medium"  # 内存不可得时按核心数保守推荐
    else:
        recommended = "light"
    return {"cores": cores, "ram_gb": ram_gb, "recommended_level": recommended}


def effective_level() -> str:
    """当前生效档位:手动值优先;auto 时用本机推荐"""
    configured = (settings.handwriting_ocr_level or "auto").strip().lower()
    if configured in ("light", "medium", "precise"):
        return configured
    return system_profile()["recommended_level"]


# ---------------------------------------------------------
# 特征库读取与近邻匹配(供姓名预识别调用)
# ---------------------------------------------------------
_model_cache: dict[int, tuple[float, list[tuple[str, str | None, list[float]]]]] = {}


async def load_model_entries(class_id: int) -> list[tuple[str, str | None, list[float]]]:
    """载入班级模型条目:(规范姓名, 学号, 特征全量向量);60s 缓存"""
    import time as _time

    now = _time.monotonic()
    cached = _model_cache.get(class_id)
    if cached and now - cached[0] < _MODEL_CACHE_TTL:
        return cached[1]
    from app.db.database import SessionLocal

    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(
                        HandwritingSample.student_name,
                        HandwritingSample.student_id,
                        HandwritingSample.features,
                    ).where(
                        HandwritingSample.class_id == class_id,
                        HandwritingSample.status == "ok",
                        HandwritingSample.features.is_not(None),
                    )
                )
            ).all()
    except Exception as error:  # noqa: BLE001 —— 加密锁定等场景静默降级
        logger.info("手写模型载入跳过:%s", type(error).__name__)
        return []
    entries = [(row[0] or "", row[1], row[2] or []) for row in rows if row[0] and row[2]]
    _model_cache[class_id] = (now, entries)
    return entries


def invalidate_model_cache(class_id: int | None = None) -> None:
    if class_id is None:
        _model_cache.clear()
    else:
        _model_cache.pop(class_id, None)


def match_candidate(
    crop_bytes: bytes | None,
    entries: list[tuple[str, str | None, list[float]]],
    level: str,
) -> tuple[str, str | None] | None:
    """姓名区图特征与模型库近邻判别;命中(且与次名拉开差距)返回 (姓名, 学号)

    中等档:单特征 Top1;最精确档:k=3 投票(取样本均值距离最优者)。
    """
    if not crop_bytes or not entries:
        return None
    features = describe_features(crop_bytes)
    if features is None:
        return None
    probe = feature_slice(features, level)

    if level == "precise":
        # k 近邻:每位学生取其 3 个最近样本的均值距离再排序
        by_student: dict[tuple[str, str | None], list[float]] = {}
        for name, sid, vector in entries:
            distance = float(np.linalg.norm(probe - feature_slice(vector, level)))
            by_student.setdefault((name, sid), []).append(distance)
        scored = sorted(
            (float(np.mean(sorted(ds)[:_PRECISE_K])), key) for key, ds in by_student.items()
        )
    else:
        scored = sorted(
            (
                (float(np.linalg.norm(probe - feature_slice(vector, level))), (name, sid))
                for name, sid, vector in entries
            )
        )
    if not scored:
        return None
    best_distance, best_key = scored[0]
    second_distance = scored[1][0] if len(scored) > 1 else float("inf")
    if best_distance > _MATCH_MAX_DIST:
        return None
    if second_distance < float("inf") and best_distance * _MATCH_GAP > second_distance:
        return None  # 与次名太接近,不抢答(交回花名册/人工复核链)
    return best_key


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s.·-]+", "", (value or "")).lower()


# ---------------------------------------------------------
# 样本处理服务(异步队列,进度含 ETA)
# ---------------------------------------------------------
class HandwritingService:
    """手写样本处理与模型构建服务(随 lifespan 启停)"""

    def __init__(self, max_concurrent: int = 1):
        self._max_concurrent = max(1, max_concurrent)
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._queued: set[int] = set()
        self._durations: deque[float] = deque(maxlen=20)
        self._progress: dict = self._fresh()

    @staticmethod
    def _fresh() -> dict:
        return {
            "running": False,
            "done": 0,
            "total": 0,
            "stage": "",
            "current": "",
            "errors": 0,
            "class_id": None,
            "started_at": None,
            "finished_at": None,
        }

    def progress(self) -> dict:
        """进度快照(含 ETA 秒,数据不足时为 None,如实缺省)"""
        snapshot = dict(self._progress)
        done, total = snapshot["done"], snapshot["total"]
        eta = None
        if snapshot["running"] and total and done:
            average = sum(self._durations) / len(self._durations) if self._durations else None
            if average:
                eta = int(round((total - done) * average))
        snapshot["eta_seconds"] = eta
        snapshot["percent"] = round(done * 100 / total, 1) if total else 0.0
        snapshot["recent_avg_seconds"] = (
            round(sum(self._durations) / len(self._durations), 2) if self._durations else None
        )
        return snapshot

    def schedule(self, sample_ids: list[int]) -> None:
        for sample_id in sample_ids:
            if sample_id in self._queued:
                continue
            self._queued.add(sample_id)
            self._queue.put_nowait(sample_id)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for index in range(self._max_concurrent):
            worker = asyncio.create_task(self._worker_loop(index), name=f"handwriting-{index}")
            self._workers.append(worker)
        logger.info("手写样本服务已启动(并发数:%s)", self._max_concurrent)

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def _worker_loop(self, worker_index: int) -> None:
        while self._running:
            try:
                sample_id = await self._queue.get()
            except asyncio.CancelledError:
                break
            started = datetime.now(timezone.utc)
            try:
                await self._process(sample_id)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 —— 单样本失败不影响整批
                self._progress["errors"] += 1
                logger.warning("手写样本处理失败(sample %s)", sample_id, exc_info=True)
            finally:
                self._progress["done"] += 1
                if self._progress["done"] >= self._progress["total"]:
                    self._progress["running"] = False
                    self._progress["finished_at"] = datetime.now(timezone.utc).isoformat()
                self._durations.append((datetime.now(timezone.utc) - started).total_seconds())
                self._queued.discard(sample_id)
                self._queue.task_done()

    async def _process(self, sample_id: int) -> None:
        from app.db.database import SessionLocal
        from app.services.name_pre_ocr import (
            _extract_via_endpoint,
            _extract_via_local_rapid,
            parse_identity,
        )

        async with SessionLocal() as session:
            sample = await session.get(HandwritingSample, sample_id)
            if sample is None:
                return
            class_id = sample.class_id
            image_path = settings.upload_path / sample.image_path
        if not image_path.exists():
            return
        self._progress["current"] = image_path.name

        # 1) 找平(命中则用找平结果;未命中用原图——安全回退)
        self._progress["stage"] = "找平"
        aligned, changed, _page_type = await asyncio.to_thread(
            align_standard_sheet, image_path.read_bytes()
        )
        working = aligned if changed else image_path.read_bytes()

        # 2) 裁姓名区并落盘(作为 name_crop_path,供展示与特征重算)
        self._progress["stage"] = "裁剪姓名区"
        crop = await asyncio.to_thread(crop_name_region, working)
        if crop is None:
            return
        crop_dir = settings.upload_path / "handwriting" / f"class_{class_id}"
        crop_dir.mkdir(parents=True, exist_ok=True)
        crop_name = f"crop_{sample_id}_{os.urandom(3).hex()}.jpg"
        (crop_dir / crop_name).write_bytes(crop)
        crop_rel = f"handwriting/class_{class_id}/{crop_name}"

        # 3) 识别(端点优先,本地 RapidOCR 兜底;与姓名预识别同引擎链)
        self._progress["stage"] = "识别"
        name: str | None = None
        student_id: str | None = None
        if settings.mock_mode:
            from app.pipelines.mock import MOCK_STUDENT_ID, MOCK_STUDENT_NAME

            name, student_id = MOCK_STUDENT_NAME, MOCK_STUDENT_ID
        else:
            for base_url, api_key, model, tag in self._endpoint_candidates():
                try:
                    ocr = await _extract_via_endpoint(
                        base_url, api_key, model, crop_dir / crop_name, tag
                    )
                    name, student_id = (ocr.student_name or "").strip() or None, (
                        (ocr.student_id or "").strip() or None
                    )
                    if name:
                        break
                except Exception as error:  # noqa: BLE001
                    logger.info("样本识别端点(%s)失败:%s", tag, type(error).__name__)
            if not name:
                text = await asyncio.to_thread(
                    _extract_via_local_rapid, crop_dir / crop_name
                )
                if text:
                    name, student_id = parse_identity(text)
        if name in ("未知", None):
            name = None

        # 4) 花名册对账(精确/包含;未命中 → 待教师指定)
        self._progress["stage"] = "花名册对账"
        status_value = "pending_bind"
        if name:
            async with SessionLocal() as session:
                roster = (
                    await session.execute(
                        select(ClassRoster.name, ClassRoster.student_id).where(
                            ClassRoster.class_id == class_id
                        )
                    )
                ).all()
            target = _normalize_name(name)
            matched = None
            for roster_name, roster_id in roster:
                normalized = _normalize_name(roster_name)
                if normalized == target or (target and (target in normalized or normalized in target)):
                    matched = (roster_name, roster_id)
                    break
            if matched:
                name, student_id = matched[0], (student_id or matched[1])
                status_value = "ok"

        # 5) 特征提取
        self._progress["stage"] = "特征提取"
        features = await asyncio.to_thread(describe_features, crop)

        # 6) 落库并刷新模型聚合
        async with SessionLocal() as session:
            sample = await session.get(HandwritingSample, sample_id)
            if sample is None:
                return
            sample.name_crop_path = crop_rel
            sample.student_name = name
            sample.student_id = student_id
            sample.features = features
            sample.status = status_value
            await session.commit()
        await self._refresh_model(class_id)
        invalidate_model_cache(class_id)
        await audit(
            "handwriting.sample_processed",
            ok=True,
            detail=f"sample {sample_id};状态 {status_value};特征 {'已生成' if features else '未生成'}",
            source="worker",
        )

    @staticmethod
    def _endpoint_candidates() -> list[tuple[str, str, str, str]]:
        candidates: list[tuple[str, str, str, str]] = []
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
        return candidates

    @staticmethod
    async def _refresh_model(class_id: int) -> None:
        from app.db.database import SessionLocal
        from sqlalchemy import update as sa_update

        async with SessionLocal() as session:
            counts = (
                await session.execute(
                    select(
                        func.count(HandwritingSample.id),
                        func.count(func.distinct(HandwritingSample.student_name)),
                    ).where(
                        HandwritingSample.class_id == class_id,
                        HandwritingSample.status == "ok",
                    )
                )
            ).one()
            samples_count, students_count = int(counts[0]), int(counts[1])
            exists = await session.get(HandwritingModel, class_id)
            if exists is None:
                session.add(
                    HandwritingModel(
                        class_id=class_id,
                        samples_count=samples_count,
                        students_count=students_count,
                        level=effective_level(),
                        updated_at=utc_now(),
                    )
                )
            else:
                await session.execute(
                    sa_update(HandwritingModel)
                    .where(HandwritingModel.class_id == class_id)
                    .values(
                        samples_count=samples_count,
                        students_count=students_count,
                        level=effective_level(),
                        updated_at=utc_now(),
                    )
                )
            await session.commit()


#: 模块级单例(随 lifespan 启停)
handwriting_service = HandwritingService()


async def build_model_overview(class_id: int) -> dict:
    """班级手写模型概览(端点用)"""
    from app.db.database import SessionLocal

    async with SessionLocal() as session:
        model = await session.get(HandwritingModel, class_id)
        pending = (
            await session.execute(
                select(func.count(HandwritingSample.id)).where(
                    HandwritingSample.class_id == class_id,
                    HandwritingSample.status == "pending_bind",
                )
            )
        ).scalar_one()
    profile = system_profile()
    if model is None:
        return {
            "class_id": class_id,
            "ready": False,
            "samples_count": 0,
            "students_count": 0,
            "pending_bind": int(pending),
            "level": effective_level(),
            "updated_at": None,
            **profile,
        }
    return {
        "class_id": class_id,
        "ready": model.samples_count > 0,
        "samples_count": model.samples_count,
        "students_count": model.students_count,
        "pending_bind": int(pending),
        "level": effective_level(),
        "configured_level": settings.handwriting_ocr_level,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        **profile,
    }
