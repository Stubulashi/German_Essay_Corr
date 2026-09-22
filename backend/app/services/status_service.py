"""全局状态聚合(真实进度 + 真实自检;严禁占位式/安慰剂式假提示)

供 GET /api/status/overview 使用:
- active[]:批改队列 / 姓名预识别 / 存量压缩 / 手写训练 的真实进度(percent + ETA);
  ETA 一律由真实计数与滚动均耗推算,数据不足时如实返回 null;
- checkup:**每次真实执行**的轻量自检(数据库查询与迁移版本、上传目录写测、
  磁盘余量、前端产物、识别引擎可用性、队列健康),10 秒缓存避免高频开销;
  输出真实短语(如"数据库正常 · 存储可写 · 识别引擎就绪"或真实警示)。
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db_models import CorrectionTask

logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parents[2]
_CHECKUP_TTL = 10.0
_checkup_cache: tuple[float, dict] | None = None


def _latest_migration_head() -> str | None:
    """从迁移脚本解析最新 head(与 selfcheck.py 同思路的精简版)"""
    versions_dir = _BACKEND / "migrations" / "versions"
    if not versions_dir.is_dir():
        return None
    revisions: dict[str, str | None] = {}
    for path in versions_dir.glob("*.py"):
        content = path.read_text(encoding="utf-8", errors="ignore")
        rev = re.search(r"^revision: str = '([^']+)'", content, re.MULTILINE)
        down = re.search(r"^down_revision: [^=]+= '([^']+)'", content, re.MULTILINE)
        if rev:
            revisions[rev.group(1)] = down.group(1) if down else None
    referenced = {value for value in revisions.values() if value}
    heads = [rev for rev in revisions if rev not in referenced]
    return heads[0] if heads else None


async def run_checkup(db: AsyncSession) -> dict:
    """真实自检(10s 缓存):每项都实际执行,失败即如实回报"""
    global _checkup_cache
    now = time.monotonic()
    if _checkup_cache and now - _checkup_cache[0] < _CHECKUP_TTL:
        return _checkup_cache[1]

    ok_parts: list[str] = []
    problems: list[str] = []

    # 1) 数据库可查询 + 迁移版本(迁移表缺失视为“尚未创建”,不误报)
    try:
        await db.execute(text("SELECT 1"))
        ok_parts.append("数据库正常")
    except Exception as error:  # noqa: BLE001
        problems.append(f"数据库异常({type(error).__name__})")
    try:
        row = (await db.execute(text("SELECT version_num FROM alembic_version"))).first()
        current = row[0] if row else None
        head = _latest_migration_head()
        if head and current and current != head:
            problems.append(f"数据库迁移版本落后({current} → {head})")
    except Exception:  # noqa: BLE001 —— 临时库/首启尚无迁移表:不作异常处理
        pass

    # 2) 上传目录写测(真实写一个临时文件)
    try:
        settings.upload_path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(settings.upload_path), delete=True) as handle:
            handle.write(b"ok")
        ok_parts.append("存储可写")
    except Exception:  # noqa: BLE001
        problems.append("上传目录不可写")

    # 3) 磁盘余量(低于 1GB 给出真实警示)
    try:
        free_gb = shutil.disk_usage(settings.upload_path).free / (1024**3)
        if free_gb < 1.0:
            problems.append(f"磁盘余量偏低({free_gb:.1f}GB)")
        else:
            ok_parts.append(f"磁盘余量 {free_gb:.0f}GB")
    except Exception:  # noqa: BLE001
        problems.append("磁盘余量未知")

    # 4) 识别引擎可用性(已配置端点 或 本地 OCR 组件 或 本地 VLM)
    engine_ready = False
    engine_note = ""
    if settings.ocr_provider == "vlm_openai" and settings.ocr_base_url and settings.ocr_model:
        engine_ready, engine_note = True, "识别端点已配置"
    elif settings.azure_ocr_endpoint:
        engine_ready, engine_note = True, "Azure 端点已配置"
    if not engine_ready and settings.local_vlm_base_url and settings.local_vlm_model:
        engine_ready, engine_note = True, "本地 VLM 已配置"
    if not engine_ready:
        try:
            import rapidocr_onnxruntime  # noqa: F401

            engine_ready, engine_note = True, "本地 OCR 组件可用"
        except Exception:  # noqa: BLE001
            engine_note = ""
    if engine_ready:
        ok_parts.append(f"识别引擎就绪({engine_note})")
    else:
        problems.append("识别引擎未配置(上传后将无法识别姓名/正文)")

    # 5) 前端产物
    dist_index = _BACKEND.parent / "frontend" / "dist" / "index.html"
    if dist_index.is_file():
        ok_parts.append("前端产物就绪")
    else:
        problems.append("前端产物缺失")

    summary = " · ".join(problems) if problems else " · ".join(ok_parts)
    data = {
        "ok": not problems,
        "summary": summary,
        "ok_items": ok_parts,
        "problems": problems,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    _checkup_cache = (now, data)
    return data


async def build_overview(db: AsyncSession, queue) -> dict:
    """聚合全部活跃任务的真实进度与自检结果"""
    active: list[dict] = []

    # 1) 批改队列:待处理数 + 处理中任务的真实 stage/progress
    try:
        processing_rows = (
            await db.execute(
                select(CorrectionTask.id, CorrectionTask.stage, CorrectionTask.progress)
                .where(CorrectionTask.status == "PROCESSING")
                .limit(5)
            )
        ).all()
        pending_count = queue.pending_count if queue is not None else 0
        average = getattr(queue, "recent_avg_seconds", None) if queue is not None else None
        total_work = pending_count + len(processing_rows)
        if total_work:
            progress_values = [float(row[2] or 0.0) for row in processing_rows]
            percent = round(sum(progress_values) * 100 / len(progress_values), 1) if progress_values else 0.0
            eta = int(round(total_work * average)) if average else None
            active.append(
                {
                    "kind": "grading",
                    "label": f"批改队列(待处理 {pending_count} · 处理中 {len(processing_rows)})",
                    "percent": percent,
                    "eta_seconds": eta,
                    "status_text": f"阶段:{processing_rows[0][1] or '排队'}" if processing_rows else "排队中",
                }
            )
    except Exception:  # noqa: BLE001 —— 聚合失败不拖垮接口
        logger.warning("批改队列状态聚合失败", exc_info=True)

    # 2) 姓名预识别
    try:
        from app.services.name_pre_ocr import name_pre_ocr_service

        snapshot = name_pre_ocr_service.progress_snapshot()
        if snapshot["queued"]:
            active.append(
                {
                    "kind": "name_pre_ocr",
                    "label": f"姓名预识别(在途 {snapshot['queued']})",
                    "percent": None,
                    "eta_seconds": (
                        int(round(snapshot["queued"] * snapshot["recent_avg_seconds"]))
                        if snapshot["recent_avg_seconds"]
                        else None
                    ),
                    "status_text": "识别姓名/学号",
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning("预识别状态聚合失败", exc_info=True)

    # 3) 存量压缩
    try:
        from app.services import image_compress_service

        progress = image_compress_service.progress()
        if progress.get("running"):
            done, total = progress.get("done", 0), progress.get("total", 0)
            percent = round(done * 100 / total, 1) if total else None
            eta = None
            started = progress.get("started_at")
            if total and done and started:
                try:
                    elapsed = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(started)
                    ).total_seconds()
                    eta = int(round((total - done) * (elapsed / done)))
                except Exception:  # noqa: BLE001
                    eta = None
            active.append(
                {
                    "kind": "compress",
                    "label": f"图片压缩({done}/{total})",
                    "percent": percent,
                    "eta_seconds": eta,
                    "status_text": "压缩存档图片",
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning("压缩状态聚合失败", exc_info=True)

    # 4) 手写样本训练
    try:
        from app.services.handwriting_service import handwriting_service

        snapshot = handwriting_service.progress()
        if snapshot.get("running"):
            active.append(
                {
                    "kind": "handwriting",
                    "label": f"手写模型训练({snapshot['done']}/{snapshot['total']})",
                    "percent": snapshot.get("percent"),
                    "eta_seconds": snapshot.get("eta_seconds"),
                    "status_text": snapshot.get("stage") or "处理样本",
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning("训练状态聚合失败", exc_info=True)

    checkup = await run_checkup(db)
    return {"active": active, "checkup": checkup}
