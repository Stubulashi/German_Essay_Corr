"""离线自包含运行环境自检(开箱即用验证)

使用方式(项目根目录任选其一):
    - 双击「一键自检.bat」
    - runtime\\python\\python.exe backend\\scripts\\selfcheck.py
    - backend\\.venv\\Scripts\\python.exe backend\\scripts\\selfcheck.py

检查项:
1. Python 解释器与版本;
2. 后端全部依赖模块可导入(fastapi/sqlalchemy/pydantic/Pillow/numpy/cryptography/…);
3. VC++ 运行库(vcruntime)是否随解释器目录携带(便携分发的关键);
4. 端口占用信息项(8765 / 8766;占用时启动脚本会自动切换);
5. 前端构建产物 frontend/dist(是否存在、引用是否完整、是否与源码同步);
6. 数据库文件与 Alembic 迁移版本(当前版本 vs 迁移脚本最新 head);
7. 上传目录可写性;
8. 关键目录结构完整性。

退出码:0 = 全部通过;1 = 存在失败项。
"""

from __future__ import annotations

import importlib
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"

#: 后端依赖模块(requirements.txt 覆盖的运行期依赖;含密码学与上传组件)
REQUIRED_MODULES = [
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "aiosqlite",
    "alembic",
    "pydantic",
    "pydantic_settings",
    "httpx",
    "aiofiles",
    "PIL",
    "numpy",
    "cryptography",
    "multipart",
]

_results: list[tuple[bool, str]] = []


def check(ok: bool, message: str) -> None:
    _results.append((ok, message))


def check_python() -> None:
    check(True, f"Python 解释器:{sys.executable}")
    check(sys.version_info >= (3, 11), f"Python 版本:{sys.version.split()[0]}(要求 ≥ 3.11)")


def check_modules() -> None:
    missing: list[str] = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 —— 任一依赖不可导入即视为缺失
            missing.append(name)
    check(not missing, f"后端依赖 {len(REQUIRED_MODULES)} 项" + ("全部可用" if not missing else f"缺失:{', '.join(missing)}"))
    # 可选组件:HEIC 支持(pillow-heif 缺失时 HEIC 上传会被明确拒绝,不视为失败)
    try:
        importlib.import_module("pillow_heif")
        check(True, "可选组件:pillow-heif(HEIC 支持)可用")
    except Exception:  # noqa: BLE001
        check(True, "可选组件:pillow-heif 未安装(HEIC 照片将被拒绝并提示转存 JPG)")

    try:
        import pypdfium2  # noqa: F401

        check(True, "可选组件:PDF 解析(pypdfium2)可用")
    except Exception:  # noqa: BLE001
        check(True, "可选组件:PDF 解析未安装(扫描 PDF 上传将被拒绝并提示)")

    try:
        import rapidocr_onnxruntime  # noqa: F401

        check(True, "可选组件:本地 OCR(RapidOCR)可用(纯本地 OCR 模式已就绪)")
    except Exception:  # noqa: BLE001
        check(True, "可选组件:本地 OCR(RapidOCR)未安装(纯本地 OCR 模式将不可用,请重跑「首次安装」)")

    try:
        import qrcode  # noqa: F401

        check(True, "可选组件:二维码生成(qrcode)可用(标准答题卷二维码已就绪)")
    except Exception:  # noqa: BLE001
        check(True, "可选组件:二维码生成(qrcode)未安装(含二维码的答题卷将不可用,请重跑「首次安装」)")


def check_runtime_dlls() -> None:
    """VC++ 运行库检查(便携分发关键项)

    便携包要求 vcruntime140.dll / vcruntime140_1.dll 随解释器目录携带,
    这样拷到任何没装过 VC++ 运行库的新电脑也能直接运行;
    仅依赖系统已安装的运行库时给出提示(不影响本机运行,但影响拷贝分发)。
    """
    if sys.platform != "win32":
        check(True, "VC++ 运行库:非 Windows 平台,无需 vcruntime")
        return
    exe_dir = Path(sys.executable).resolve().parent
    dll_names = ["vcruntime140.dll", "vcruntime140_1.dll"]
    missing_in_dir = [name for name in dll_names if not (exe_dir / name).is_file()]
    if not missing_in_dir:
        check(True, "VC++ 运行库:已随包内嵌(便携分发安全)")
        return
    import os

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    system_has = all((system_root / "System32" / name).is_file() for name in dll_names)
    if system_has:
        check(
            True,
            "VC++ 运行库:依赖系统已安装("
            + ", ".join(missing_in_dir)
            + " 不在解释器目录);便携分发到其他电脑前,请改用携带运行库的 runtime 目录",
        )
    else:
        check(False, "VC++ 运行库缺失:vcruntime140.dll / vcruntime140_1.dll 未找到(无法保证便携运行)")


def check_port() -> None:
    """端口占用信息项(不计入失败):启动脚本默认 8765,被占用会自动改用 8766"""
    import socket

    for port in (8765, 8766):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            occupied = sock.connect_ex(("127.0.0.1", port)) == 0
        note = "已被占用(启动脚本会自动切换)" if occupied else "空闲"
        check(True, f"端口 {port}:{note}")


def check_frontend_dist() -> None:
    index = ROOT / "frontend" / "dist" / "index.html"
    assets = ROOT / "frontend" / "dist" / "assets"
    if not index.is_file():
        check(False, "前端构建产物缺失:frontend/dist/index.html(离线模式需先执行 npm run build)")
        return
    check(True, "前端构建产物:已就绪")
    check(assets.is_dir(), f"前端静态资源:{'已就绪' if assets.is_dir() else '缺失 frontend/dist/assets'}")
    # 引用完整性:index.html 中引用的 /assets/* 资源必须真实存在
    # (半成品构建/拷贝不全会导致资源 404,页面白屏)
    try:
        html = index.read_text(encoding="utf-8", errors="ignore")
        refs = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
        missing_refs = [
            ref for ref in refs if not (ROOT / "frontend" / "dist" / ref.lstrip("/")).is_file()
        ]
        if refs and not missing_refs:
            check(True, f"前端资源引用完整(index.html → assets,共 {len(refs)} 项)")
        else:
            detail = "、".join(missing_refs) if missing_refs else "未找到 /assets 引用"
            check(False, f"前端构建产物不完整:index.html 引用的资源缺失({detail}),请重新执行 npm run build")
    except Exception as error:  # noqa: BLE001
        check(False, f"前端资源引用检查失败:{error}")
    # 陈旧检测:源码/依赖更新晚于构建产物 → 提示重新构建(避免“白屏/旧页面”问题)
    try:
        newest = index.stat().st_mtime
        candidates = [ROOT / "frontend" / "package.json", ROOT / "frontend" / "index.html"]
        src_dir = ROOT / "frontend" / "src"
        if src_dir.is_dir():
            candidates.extend(src_dir.rglob("*.ts"))
            candidates.extend(src_dir.rglob("*.tsx"))
            candidates.extend(src_dir.rglob("*.css"))
        latest = max((path.stat().st_mtime for path in candidates if path.is_file()), default=0.0)
        if latest > newest + 1:
            check(False, "前端构建产物可能陈旧(源码/依赖有更新):请重新执行 npm run build")
        else:
            check(True, "前端构建产物为最新(与源码同步)")
    except Exception as error:  # noqa: BLE001
        check(True, f"前端产物陈旧检查跳过({type(error).__name__})")


def _latest_migration_head() -> str | None:
    """从迁移脚本中解析最新 head(未被任何 down_revision 引用的 revision)"""
    versions_dir = BACKEND / "migrations" / "versions"
    if not versions_dir.is_dir():
        return None
    revisions: dict[str, str | None] = {}
    for path in versions_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rev = re.search(r"^revision: str = '([^']+)'", text, re.MULTILINE)
        down = re.search(r"^down_revision: [^=]+= '([^']+)'", text, re.MULTILINE)
        if rev:
            revisions[rev.group(1)] = down.group(1) if down else None
    referenced = {value for value in revisions.values() if value}
    heads = [rev for rev in revisions if rev not in referenced]
    return heads[0] if len(heads) == 1 else (heads[0] if heads else None)


def check_database() -> None:
    db_file = BACKEND / "data" / "corrector.db"
    if not db_file.is_file():
        check(True, "数据库:尚未创建(首次启动时自动创建并执行迁移)")
        return
    check(True, f"数据库文件:{db_file}")
    try:
        import sqlite3

        con = sqlite3.connect(str(db_file))
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        con.close()
        current = row[0] if row else None
        head = _latest_migration_head()
        ok = current == head if head else True
        check(ok, f"Alembic 迁移版本:{current or '未标记'} / 最新 {head or '未知'}")
    except Exception as e:  # noqa: BLE001
        check(False, f"数据库检查失败:{e}")


def check_upload_dir() -> None:
    upload_dir = BACKEND / "data" / "uploads"
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(upload_dir), delete=True) as tmp:
            tmp.write(b"ok")
        check(True, f"上传目录可写:{upload_dir}")
    except Exception as e:  # noqa: BLE001
        check(False, f"上传目录不可写:{upload_dir}({e})")


def check_structure() -> None:
    required = [
        BACKEND / "app" / "main.py",
        BACKEND / "migrations" / "env.py",
        ROOT / "一键启动.bat",
    ]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.exists()]
    check(not missing, "关键目录结构" + ("完整" if not missing else f"缺失:{', '.join(missing)}"))


def main() -> int:
    print("=" * 62)
    print("  德语作文批改系统 - 离线运行环境自检")
    print("=" * 62)
    check_python()
    check_modules()
    check_runtime_dlls()
    check_port()
    check_frontend_dist()
    check_database()
    check_upload_dir()
    check_structure()

    failed = 0
    for ok, message in _results:
        print(f"  [{'OK' if ok else 'FAIL'}] {message}")
        if not ok:
            failed += 1
    print("-" * 62)
    if failed == 0:
        print("  全部检查通过,可双击「一键启动.bat」正常使用。")
    else:
        print(f"  存在 {failed} 项未通过,请按上方提示处理后重试。")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
