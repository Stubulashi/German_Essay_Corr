"""存量图片批量压缩(后台任务;幂等可重跑)

- 遍历 upload 根目录下全部支持的图片文件,逐个调用 compress_file(原子替换同路径);
- "未变小"的文件自动跳过 → 二次运行 = 0 变更(幂等);
- 内存进度(供设置中心轮询)+ 审计日志(仅文件数与前后字节总量,不含学生数据);
- 被锁/不可读文件跳过,可随时重跑补齐。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.services.audit_service import audit
from app.services.image_compress import SUPPORTED_SUFFIXES, compress_file

logger = logging.getLogger(__name__)

_progress: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "changed": 0,
    "skipped": 0,
    "before_bytes": 0,
    "after_bytes": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
}


def progress() -> dict:
    """当前/最近一次批量压缩的进度快照"""
    return dict(_progress)


def _iter_images(root: Path) -> list[Path]:
    """列出根目录下全部支持的图片文件(稳定排序,便于进度可预期)"""
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


async def run_compress(root: Path, *, max_side: int, quality: int) -> None:
    """执行批量压缩(异常内部消化并记录进度与审计)"""
    _progress.update(
        running=True, done=0, total=0, changed=0, skipped=0,
        before_bytes=0, after_bytes=0, error=None,
        started_at=datetime.now(timezone.utc).isoformat(), finished_at=None,
    )
    try:
        files = await asyncio.to_thread(_iter_images, root)
        _progress["total"] = len(files)
        for path in files:
            try:
                before, after, changed = await asyncio.to_thread(
                    compress_file, path, max_side=max_side, quality=quality
                )
            except Exception as error:  # noqa: BLE001 —— 单文件失败跳过,可重跑补齐
                logger.warning("压缩失败(已跳过,可重跑):%s %s", path, error)
                before = after = 0
                changed = False
            _progress["done"] += 1
            if changed:
                _progress["changed"] += 1
            else:
                _progress["skipped"] += 1
            _progress["before_bytes"] += before
            _progress["after_bytes"] += after
        logger.info(
            "存量图片压缩完成:处理 %s 个,压缩 %s 个,跳过 %s 个",
            _progress["total"], _progress["changed"], _progress["skipped"],
        )
        await audit(
            "maintenance.compress_images",
            detail=(
                f"处理 {_progress['total']} 个文件,压缩 {_progress['changed']} 个;"
                f"{_progress['before_bytes']} -> {_progress['after_bytes']} 字节"
            ),
        )
    except Exception as error:  # noqa: BLE001
        _progress["error"] = str(error)[:300]
        logger.exception("存量图片压缩失败")
        await audit("maintenance.compress_images", ok=False, detail=str(error)[:200])
    finally:
        _progress["running"] = False
        _progress["finished_at"] = datetime.now(timezone.utc).isoformat()


async def start_compress(root: Path, *, max_side: int, quality: int) -> None:
    """启动后台压缩任务(同一时间仅允许一个)

    先将 running 置位再调度任务:消除旧实现中 check→create_task 之间
    任务尚未执行导致的竞态双启动(进度互踩/并发遍历同目录)。
    """
    if _progress["running"]:
        raise ValueError("已有压缩任务进行中,请等待完成或稍后重试")
    _progress["running"] = True
    try:
        asyncio.create_task(run_compress(root, max_side=max_side, quality=quality))
    except Exception:
        _progress["running"] = False
        raise
