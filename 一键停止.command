#!/bin/bash
# ============================================================
# 德语作文批改系统 - 停止(macOS 版,与「一键停止.bat」行为对等)
# 依次释放 8765 / 8766 / 5173 端口的监听进程。
# ============================================================

cd "$(dirname "$0")" || exit 1

echo "=============================================="
echo "    正在停止德语作文批改系统..."
echo "=============================================="
echo ""

FOUND=0
if command -v lsof >/dev/null 2>&1; then
  for PORT in 8765 8766 5173; do
    PIDS="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null)"
    if [ -n "$PIDS" ]; then
      echo "$PIDS" | xargs kill >/dev/null 2>&1 && FOUND=1
    fi
  done
else
  echo "   [提示] 当前系统缺少 lsof,请直接在服务窗口按 Control+C 停止。"
fi

if [ "$FOUND" -eq 1 ]; then
  echo "    系统已停止。"
else
  echo "    没有发现正在运行的系统(可能已经停止运行)。"
fi
echo ""
echo "    提示:也可以直接关闭后端服务窗口(Terminal 窗口的红色关闭按钮)。"
echo ""
read -n 1 -s -r -p "按任意键关闭…"
