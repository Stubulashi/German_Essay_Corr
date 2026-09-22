#!/bin/bash
# ============================================================
# 德语作文批改系统 - 应急启动(macOS 版;内置云端配置)
# 读取根目录「应急配置.env」,以进程环境变量方式注入;
# 不会写入 backend/.env,不影响设置中心与常规配置。
# 端口:默认 8765;被占用自动改用 8766(两者都占用则提示后退出)。
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
  echo "   [提示] 前端界面文件缺失(frontend/dist)。请先构建或从开发机拷贝。"
  echo ""
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

ENVFILE="$DIR/应急配置.env"
if [ ! -f "$ENVFILE" ]; then
  echo ""
  echo "   [提示] 应急配置缺失(应急配置.env)。"
  echo "   请参照「应急配置.env.example」填写密钥后重试。"
  echo ""
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

# 载入云端预设(仅对本进程与子进程生效)
set -a
# shellcheck disable=SC1090
. "$ENVFILE"
set +a
if [ -z "$OCR_API_KEY" ] || [ -z "$DEEPSEEK_API_KEY" ]; then
  echo ""
  echo "   [提示] 应急配置缺少关键密钥(OCR_API_KEY / DEEPSEEK_API_KEY)。"
  echo ""
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

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
echo "    正在启动德语作文批改系统(应急云端配置)..."
echo "=============================================="
echo ""
echo "    [应急版] 本次启动使用内置云端模型配置(识别:百炼 Qwen / 评分:DeepSeek)。"
echo "    该配置仅对本窗口生效:不会写入 backend/.env,不影响设置中心与常规配置。"
echo ""

(
  sleep 10
  open "http://127.0.0.1:$PORT"
) &

echo "[已就绪] 浏览器将自动打开:http://127.0.0.1:$PORT"
echo "本窗口就是服务窗口,使用期间请不要关闭;结束后双击「一键停止.command」。"
echo ""

cd "$DIR/backend" || exit 1
exec "$PYEXE" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
