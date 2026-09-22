#!/bin/bash
# ============================================================
# 德语作文批改系统 - 启动(macOS 版,与「一键启动.bat」行为对等)
# 端口:默认 8765;被占用自动改用 8766(两者都占用则提示后退出,退出码 1)
# 本窗口即为服务窗口:使用期间请勿关闭;关闭窗口即停止服务。
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

if [ ! -f "$DIR/frontend/dist/index.html" ]; then
  echo ""
  echo "   [提示] 前端界面文件缺失(frontend/dist)。"
  echo "   请在装有 Node.js 的电脑上执行 npm run build 后,把 frontend/dist 文件夹拷贝过来。"
  echo ""
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

# 端口检测(lsof 优先,nc 兜底)
port_busy() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  nc -z 127.0.0.1 "$1" >/dev/null 2>&1
  return $?
}

PORT=8765
port_busy 8765 && PORT=8766
if port_busy "$PORT"; then
  echo ""
  echo "   [提示] 端口 8765 与 8766 都被占用,系统暂时无法启动。"
  echo "   请先关闭占用端口的其他程序,或双击「一键停止.command」后再试。"
  echo ""
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

echo "=============================================="
echo "    正在启动德语作文批改系统..."
echo "=============================================="
echo ""
echo "[1/2] 正在启动后端服务(端口 $PORT);本窗口请保持开启..."
echo ""

# 后台等待后端就绪(HTTP 健康检查轮询,最长约 120 秒),就绪后再自动打开浏览器;
# 超时则不打开浏览器,避免用户面对无法访问的空白页。
(
  for _ in $(seq 1 120); do
    if curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/health"; then
      open "http://127.0.0.1:$PORT"
      exit 0
    fi
    sleep 1
  done
  echo ""
  echo "   [提示] 后端启动超时(已等待约 120 秒),暂未打开浏览器。"
  echo "   请查看本窗口中的错误信息,排除后重新双击本脚本;"
  echo "   或稍后手动访问: http://127.0.0.1:$PORT"
) &

echo "[2/2] 等待后端就绪后自动打开浏览器(通常 10 秒内,请稍候)..."
echo ""
echo "=============================================="
echo "    启动完成(浏览器将自动弹出)!"
echo ""
echo "    1. 如浏览器未弹出,请手动访问:http://127.0.0.1:$PORT"
echo "    2. 本窗口就是服务窗口,使用期间请不要关闭。"
echo "    3. 使用结束后,双击「一键停止.command」关闭系统。"
echo "=============================================="
echo ""

cd "$DIR/backend" || exit 1
exec "$PYEXE" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
