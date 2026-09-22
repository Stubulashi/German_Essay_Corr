#!/bin/bash
# ============================================================
# 德语作文批改系统 - 自检(macOS 版,与「一键自检.bat」行为对等)
# 逐项检查运行时/依赖/前端产物/数据库迁移/上传目录等。
# ============================================================

cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"

PYEXE="$DIR/runtime/python/bin/python3"
if [ ! -x "$PYEXE" ]; then
  PYEXE="$DIR/backend/.venv/bin/python"
fi
if [ ! -x "$PYEXE" ]; then
  echo ""
  echo "   [提示] 没有找到可用的运行环境。"
  echo "   请先双击「首次制作mac运行环境.command」完成安装(需联网一次)。"
  echo ""
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

"$PYEXE" "$DIR/backend/scripts/selfcheck.py"
RC=$?
echo ""
read -n 1 -s -r -p "按任意键关闭…"
exit $RC
