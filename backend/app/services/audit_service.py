"""安全审计日志(JSONL 追加)

使用范围:设置更新、加密敏感操作(启用/解锁/改密/重置/禁用/迁移/归档重建)、
班级合并等。**严禁记录口令、密钥、密文与明文学生数据**——本项目只记录
事件名、结果与简短说明。

落盘位置:`backend/data/audit.log`(每条一行 JSON,便于 grep/归档)。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import aiofiles

from app.config import BACKEND_DIR

logger = logging.getLogger(__name__)

AUDIT_FILE = BACKEND_DIR / "data" / "audit.log"


async def audit(event: str, *, ok: bool = True, detail: str = "", source: str = "api") -> None:
    """追加一条审计记录(写入失败仅告警,不影响业务)"""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "ok": ok,
        "detail": (detail or "")[:300],
        "source": source,
    }
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(AUDIT_FILE, "a", encoding="utf-8") as f:
            await f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 —— 审计失败不允许影响业务
        logger.warning("审计日志写入失败(忽略):%s", event)
