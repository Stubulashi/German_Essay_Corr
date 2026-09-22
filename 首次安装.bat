@echo off
title 德语作文批改系统 - 首次安装
cd /d "%~dp0"

echo ==============================================
echo    德语作文批改系统 - 首次安装
echo ==============================================
echo.

if exist "runtime\python\python.exe" goto offline

echo     未检测到内置运行环境,将尝试联网安装。
echo.
python --version >nul 2>&1
if errorlevel 1 goto no_python
if exist "C:\Program Files\nodejs\npm.cmd" set "PATH=C:\Program Files\nodejs;%PATH%"
call npm --version >nul 2>&1
if errorlevel 1 goto no_node
cd backend
if exist ".venv\Scripts\python.exe" goto pip
python -m venv .venv
:pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 goto pip_fail
cd ..\frontend
call npm install
if errorlevel 1 goto npm_fail
call npm run build
cd ..
echo.
echo     安装完成!以后每次使用:双击"一键启动.bat"
pause
exit /b 0

:offline
echo     [OK] 检测到内置离线运行环境 runtime\python,无需联网安装。
if not exist "frontend\dist\index.html" goto offline_nodist
echo     [OK] 前端界面文件已就绪,全部就绪。
echo.
echo     以后每次使用:双击"一键启动.bat"
echo     建议先运行一次"一键自检.bat"确认环境完整。
pause
exit /b 0

:offline_nodist
echo     [提示] 未发现前端界面文件 frontend\dist。
echo     请在有 Node.js 的电脑上执行 npm run build 后,把 frontend\dist 文件夹拷贝过来。
echo.
pause
exit /b 0

:no_python
echo     [缺失] 本机没有安装 Python,且未内置 runtime 目录。
echo     请安装 Python 3.11 以上版本,安装时勾选 Add python.exe to PATH:
echo     https://www.python.org/downloads/
pause
exit /b 1

:no_node
echo     [缺失] 本机没有安装 Node.js,且未内置 runtime 目录。
echo     请安装 Node.js LTS 版本:https://nodejs.org/zh-cn
pause
exit /b 1

:pip_fail
echo     后端依赖安装失败,请检查网络后重试。
pause
exit /b 1

:npm_fail
echo     前端依赖安装失败,请检查网络后重试。
pause
exit /b 1
