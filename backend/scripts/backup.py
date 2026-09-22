# -*- coding: utf-8 -*-
"""数据备份脚本(标准库实现,无需停止服务)

功能:
- 使用 SQLite Online Backup API 生成一致性快照(WAL 模式下运行中也安全);
- 将 数据库快照 + uploads 上传图片 + 备份说明 打包为一个 ZIP;
- 输出文件:backend/backups/corrector_backup_YYYYMMDD_HHMMSS.zip

用法:
    python backend/scripts/backup.py            # 生成一份备份
    python backend/scripts/backup.py --keep 10  # 生成后仅保留最近 10 份
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# 路径约定:本脚本位于 backend/scripts/,数据位于 backend/data/
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
DB_FILE = DATA_DIR / "corrector.db"
UPLOADS_DIR = DATA_DIR / "uploads"
BACKUPS_DIR = BACKEND_DIR / "backups"


def make_backup(keep: int | None = None) -> Path:
    """生成一份完整备份,返回备份文件路径

    Args:
        keep: 生成后仅保留最近 N 份备份(None 表示不清理旧备份)
    """
    if not DB_FILE.exists():
        raise FileNotFoundError(f"找不到数据库文件:{DB_FILE}\n请先确认系统已至少启动过一次。")

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = BACKUPS_DIR / f"corrector_backup_{stamp}.zip"
    snapshot_path = BACKUPS_DIR / f".tmp_snapshot_{stamp}.db"

    # ---------- 1. SQLite 一致性快照(运行中也安全) ----------
    src = sqlite3.connect(str(DB_FILE))
    try:
        dst = sqlite3.connect(str(snapshot_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # ---------- 2. 统计信息(写入备份说明) ----------
    conn = sqlite3.connect(str(snapshot_path))
    try:
        task_count = conn.execute("SELECT COUNT(*) FROM correction_tasks").fetchone()[0]
        try:
            revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            revision_text = revision[0] if revision else "未知"
        except sqlite3.Error:
            revision_text = "未知"
    finally:
        conn.close()

    image_count = sum(1 for p in UPLOADS_DIR.rglob("*") if p.is_file()) if UPLOADS_DIR.exists() else 0

    manifest = (
        "德语作文批改系统 数据备份说明\n"
        "==============================\n"
        f"备份时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"批改任务数:{task_count}\n"
        f"图片文件数:{image_count}\n"
        f"数据库版本:{revision_text}\n"
        "\n"
        "恢复方法:双击“恢复数据.bat”,选择本备份文件即可。\n"
        "也可手动恢复:关闭系统后,把本压缩包内的 data 文件夹解压覆盖到 backend 目录下。\n"
    )

    # ---------- 3. 打包 ----------
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(snapshot_path, "data/corrector.db")
        if UPLOADS_DIR.exists():
            for file in UPLOADS_DIR.rglob("*"):
                if file.is_file():
                    arcname = Path("data/uploads") / file.relative_to(UPLOADS_DIR)
                    zf.write(file, arcname.as_posix())
        zf.writestr("manifest.txt", manifest)

    snapshot_path.unlink(missing_ok=True)

    # ---------- 4. 保留策略 ----------
    if keep is not None and keep > 0:
        backups = sorted(BACKUPS_DIR.glob("corrector_backup_*.zip"))
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)

    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="备份德语作文批改系统的数据库与上传图片")
    parser.add_argument("--keep", type=int, default=None, help="仅保留最近 N 份备份(可选)")
    args = parser.parse_args()

    print()
    print("正在生成备份(数据库 + 上传图片),请稍候...")
    try:
        out = make_backup(keep=args.keep)
    except Exception as e:  # noqa: BLE001 —— 脚本入口统一提示
        print()
        print(f"[失败] 备份没有完成:{e}")
        return 1

    size_kb = out.stat().st_size / 1024
    size_text = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
    print()
    print("[完成] 备份已生成:")
    print(f"    {out}")
    print(f"    大小约 {size_text}")
    print()
    print("提示:建议把备份文件复制到 U 盘或网盘长期保存。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
