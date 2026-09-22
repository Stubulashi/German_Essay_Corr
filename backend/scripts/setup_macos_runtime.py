"""macOS 便携运行环境制作器(仅在 Mac 上运行;仅使用标准库)

工作内容:
1. 自动识别架构(Apple 芯片 arm64 / Intel x86_64);
2. 从 python-build-standalone(astral-sh)下载便携运行时(install_only 版本);
3. 解压为 runtime/python,并安装 backend/requirements.txt 全部依赖;
4. 校验全部 .command 脚本语法(bash -n)并授权可执行(chmod +x);
5. 尝试清理 quarantine 属性,收尾运行一键自检。

可用环境变量覆盖(可选):
- EMBED_PYTHON_URL               : 直接指定完整下载地址(跳过 GitHub 查询)
- EMBED_PYTHON_VERSION          : 首选 Python 系列,默认 "3.12"
- EMBED_PYTHON_RELEASE          : 指定 python-build-standalone 发布 tag,默认取 latest

注意:本脚本只应在 macOS 上运行;在 Windows/Linux 上会直接退出。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "runtime"
PYTHON_DIR = RUNTIME_DIR / "python"
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
GITHUB_LATEST_API = (
    "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
)


def fail(message: str, code: int = 1) -> None:
    print(f"\n   [失败] {message}\n")
    sys.exit(code)


def step(message: str) -> None:
    print(f"\n==> {message}")


def detect_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "aarch64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    fail(f"无法识别的 CPU 架构:{machine}")


def resolve_download_url(arch: str) -> str:
    override = os.environ.get("EMBED_PYTHON_URL", "").strip()
    if override:
        print(f"   使用指定的运行时地址:{override}")
        return override

    prefer = os.environ.get("EMBED_PYTHON_VERSION", "3.12").strip()
    release = os.environ.get("EMBED_PYTHON_RELEASE", "").strip()
    if release:
        api = (
            "https://api.github.com/repos/astral-sh/python-build-standalone/"
            f"releases/tags/{release}"
        )
    else:
        api = GITHUB_LATEST_API

    print(f"   查询 python-build-standalone 发布信息(架构 {arch})…")
    request = urllib.request.Request(api, headers={"User-Agent": "corrector-setup"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
    except Exception as error:  # noqa: BLE001
        fail(f"获取发布信息失败(请检查网络):{error}")

    suffix = f"-{arch}-apple-darwin-install_only.tar.gz"
    candidates: list[tuple[str, str]] = []
    for asset in data.get("assets") or []:
        name = asset.get("name") or ""
        if name.startswith("cpython-") and name.endswith(suffix):
            candidates.append((name, asset.get("browser_download_url") or ""))

    def pick(prefix: str) -> tuple[str, str] | None:
        for name, url in candidates:
            if name.startswith(prefix):
                return name, url
        return None

    chosen = pick(f"cpython-{prefer}.") or pick("cpython-3.12.") or (candidates[0] if candidates else None)
    if chosen is None:
        fail(f"未找到适配 {arch} 的运行时包(可在环境变量 EMBED_PYTHON_URL 指定直链)")
    print(f"   选定运行时:{chosen[0]}")
    return chosen[1]


def download(url: str, target: Path) -> None:
    print(f"   下载中:{url}")
    request = urllib.request.Request(url, headers={"User-Agent": "corrector-setup"})
    tmp = target.with_name("_download.tar.gz.part")
    with urllib.request.urlopen(request, timeout=600) as response, open(tmp, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        last_mark = 0
        while True:
            chunk = response.read(1024 * 512)
            if not chunk:
                break
            out.write(chunk)
            received += len(chunk)
            if total:
                percent = received * 100 // total
                if percent >= last_mark + 10:
                    last_mark = percent - percent % 10
                    print(f"   进度约 {last_mark}%…")
    tmp.replace(target)


def extract(tarball: Path) -> None:
    if PYTHON_DIR.exists():
        backup = RUNTIME_DIR / f"python.bak-{time.strftime('%Y%m%d%H%M%S')}"
        print(f"   已存在旧运行时,移动到备份:{backup.name}(确认无误后可手动删除)")
        PYTHON_DIR.rename(backup)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    print("   解压到 runtime/python…")
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(RUNTIME_DIR)  # install_only 包根目录即为 python/
    tarball.unlink(missing_ok=True)
    if not (PYTHON_DIR / "bin" / "python3").exists():
        fail("解压结果异常:未找到 runtime/python/bin/python3")


def install_dependencies(python_exe: Path) -> None:
    print("   安装依赖(backend/requirements.txt)…")
    result = subprocess.run(
        [str(python_exe), "-m", "pip", "install", "--no-warn-script-location", "-r", str(REQUIREMENTS)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        fail("依赖安装失败;请检查网络后重新双击「首次制作mac运行环境.command」")
    print("   依赖安装完成。")


def prepare_scripts() -> None:
    print("   校验并授权 .command 脚本…")
    for path in sorted(ROOT.glob("*.command")):
        syntax = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        if syntax.returncode != 0:
            print(f"   [警告] 语法检查未通过:{path.name}({syntax.stderr.strip()})")
        os.chmod(path, 0o755)
    # 尝试清理下载隔离属性(失败属正常,不影响使用)
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(ROOT)], capture_output=True)


def main() -> None:
    if sys.platform != "darwin":
        fail("本脚本只应在 macOS 上运行(当前系统不是 macOS)。")

    print("=" * 54)
    print(" 德语作文批改系统 - 首次制作 macOS 运行环境")
    print("=" * 54)

    arch = detect_arch()
    print(f"   检测到架构:{arch}")

    step("1/5 获取并下载便携运行时")
    url = resolve_download_url(arch)
    tarball = RUNTIME_DIR / "_download.tar.gz"
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    download(url, tarball)

    step("2/5 解压运行时")
    extract(tarball)
    python_exe = PYTHON_DIR / "bin" / "python3"
    subprocess.run([str(python_exe), "-m", "ensurepip", "--upgrade"], capture_output=True)

    step("3/5 安装依赖")
    install_dependencies(python_exe)

    step("4/5 脚本校验与授权")
    prepare_scripts()

    step("5/5 运行一键自检")
    subprocess.run([str(python_exe), str(ROOT / "backend" / "scripts" / "selfcheck.py")])

    print("")
    print("   全部完成!此后双击「一键启动.command」即可离线使用。")
    print("   提示:若曾从 Windows 目录拷来旧 runtime,可在确认启动正常后删除 runtime/python.bak-* 旧目录。")
    print("")


if __name__ == "__main__":
    main()
