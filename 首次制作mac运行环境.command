#!/bin/bash
# ============================================================
# 首次制作 macOS 运行环境(需联网一次;完成后即可完全离线使用)
#
# 工作内容:
#   1. 自动识别 Apple 芯片(arm64)/ Intel(x86_64);
#   2. 下载 python-build-standalone 便携运行时并解压到 runtime/python;
#   3. 安装 backend/requirements.txt 全部依赖;
#   4. 校验并授权全部 .command 脚本,然后运行一键自检。
# ============================================================

cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo ""
  echo "   [提示] 系统未找到 python3。"
  echo "   请先在终端执行:xcode-select --install  (安装 Xcode 命令行工具,免费)"
  echo "   安装完成后,再次双击本脚本。"
  echo ""
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

"$PY" "$DIR/backend/scripts/setup_macos_runtime.py"
RC=$?
echo ""
read -n 1 -s -r -p "按任意键关闭…"
exit $RC
