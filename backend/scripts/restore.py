# -*- coding: utf-8 -*-
"""数据恢复脚本(配合“恢复数据.bat”使用)

安全设计:
- 恢复前自动把现有 data 文件夹改名为 data_beforerestore_时间戳(防误操作);
- 交互式列出全部可用备份供选择,确认后才执行;
- 恢复后提示重新启动系统。

用法:
    python backend/scripts/restore.py
"""

from __future__ import annotations

import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
BACKUPS_DIR = BACKEND_DIR / "backups"


def list_backups() -> list[Path]:
    """按时间倒序列出全部备份文件"""
    return sorted(BACKUPS_DIR.glob("corrector_backup_*.zip"), reverse=True)


def _read_manifest(zip_path: Path) -> str:
    """读取备份包内的说明文件(不存在时返回空串)"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return zf.read("manifest.txt").decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _peek_task_count(zip_path: Path) -> str:
    """从备份包的 manifest 中提取任务数(用于列表展示)"""
    for line in _read_manifest(zip_path).splitlines():
        if line.startswith("批改任务数:"):
            return line.split(":", 1)[1].strip()
    return "?"


def restore(zip_path: Path) -> None:
    """执行恢复:备份现有数据 -> 解压覆盖

    Raises:
        ValueError: 备份包结构不合法(缺少 data/ 目录)
    """
    # 1. 校验备份包结构
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not any(n.startswith("data/") for n in names):
            raise ValueError("备份文件结构不正确(缺少 data 目录),无法恢复。")

    # 2. 保护现有数据:改名保留
    if DATA_DIR.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rescue_dir = BACKEND_DIR / f"data_beforerestore_{stamp}"
        DATA_DIR.rename(rescue_dir)
        print(f"    现有数据已保留到:{rescue_dir.name}")

    # 3. 解压恢复(包内路径为 data/...,解压到 backend/ 下)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(BACKEND_DIR)

    # 4. 校验恢复结果
    db_file = DATA_DIR / "corrector.db"
    if not db_file.exists():
        raise ValueError("恢复后未找到数据库文件,请尝试用其他备份重试。")
    conn = sqlite3.connect(str(db_file))
    try:
        count = conn.execute("SELECT COUNT(*) FROM correction_tasks").fetchone()[0]
    finally:
        conn.close()
    print(f"    恢复完成:库内共有 {count} 个批改任务。")


def main() -> int:
    print()
    print("==============================================")
    print("   德语作文批改系统 - 数据恢复")
    print("==============================================")
    print()
    print("    重要提示:请先双击“一键停止.bat”关闭系统,再进行恢复!")
    print()

    backups = list_backups()
    if not backups:
        print("    [提示] backend\\backups 文件夹里还没有任何备份文件。")
        print("    请先双击“备份数据.bat”生成备份。")
        return 1

    print("    可用备份(越靠上越新):")
    for i, path in enumerate(backups, start=1):
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"      {i}. {path.name}  (任务数 {_peek_task_count(path)},约 {size_mb:.1f} MB)")
    print()

    raw = input("    请输入要恢复的备份序号(直接回车取消):").strip()
    if not raw:
        print("    已取消,未做任何改动。")
        return 0
    if not raw.isdigit() or not (1 <= int(raw) <= len(backups)):
        print("    [错误] 序号无效,已取消。")
        return 1

    choice = backups[int(raw) - 1]
    confirm = input(f"    确认用「{choice.name}」覆盖当前数据吗?输入 y 确认:").strip().lower()
    if confirm != "y":
        print("    已取消,未做任何改动。")
        return 0

    print()
    print("    正在恢复...")
    try:
        restore(choice)
    except Exception as e:  # noqa: BLE001 —— 脚本入口统一提示
        print()
        print(f"    [失败] 恢复没有完成:{e}")
        print("    提示:原数据已保留在 data_beforerestore_* 文件夹,可手动改回 data。")
        return 1

    print()
    print("    恢复成功!现在可以双击“一键启动.bat”启动系统了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
