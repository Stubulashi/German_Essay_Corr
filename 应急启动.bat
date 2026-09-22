@echo off
title 德语作文批改系统 - 应急启动(内置云端配置)
cd /d "%~dp0"

set "PYEXE=%~dp0runtime\python\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PYEXE%" goto noenv
if not exist "%~dp0frontend\dist\index.html" goto nodist
if not exist "%~dp0应急配置.env" goto noenvfile

set COUNT=0
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%~dp0应急配置.env") do (
    set "%%a=%%b"
    set /a COUNT+=1
)
if "%COUNT%"=="0" goto noenvfile
if "%DEEPSEEK_API_KEY%"=="" goto nokey
if "%OCR_API_KEY%"=="" goto nokey

rem 端口选择:默认 8765;被占用自动改用 8766(两者都占用则提示后退出)
set "PORT=8765"
netstat -ano -p tcp | findstr ":8765 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 set "PORT=8766"
netstat -ano -p tcp | findstr ":8766 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto portbusy

echo ==============================================
echo    正在启动德语作文批改系统(应急云端配置)...
echo ==============================================
echo.
echo    [应急版] 本次启动使用内置云端模型配置(识别:百炼 Qwen / 评分:DeepSeek)。
echo    该配置仅对本窗口生效:不会写入 backend\.env,不影响设置中心与常规配置。
echo.
echo [1/2] 正在启动后端服务(端口 %PORT%),出现的新窗口请不要关闭...
start "批改系统-后端(应急版,使用期间不要关)" /D "%~dp0backend" cmd /k ""%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%"

echo [2/2] 等待约 10 秒后自动打开浏览器...
ping -n 11 127.0.0.1 >nul
start "" http://127.0.0.1:%PORT%

echo.
echo ==============================================
echo    启动完成!
echo.
echo    1. 浏览器已自动打开;如未弹出请手动访问:
echo       http://127.0.0.1:%PORT%
echo    2. 使用期间请不要关闭服务窗口。
echo    3. 使用结束后,双击"一键停止.bat"关闭系统。
echo ==============================================
pause
exit /b 0

:portbusy
echo.
echo    [提示] 端口 8765 与 8766 都被占用,系统暂时无法启动。
echo    请先关闭占用端口的其他程序,或双击"一键停止.bat"后再试。
echo.
pause
exit /b 1

:noenvfile
echo.
echo    [提示] 应急配置缺失或为空(应急配置.env)。
echo    请参照「应急配置.env.example」填写密钥后重试。
echo.
pause
exit /b 1

:nokey
echo.
echo    [提示] 应急配置缺少关键密钥(OCR_API_KEY / DEEPSEEK_API_KEY)。
echo    请检查「应急配置.env」后重试。
echo.
pause
exit /b 1

:noenv
echo.
echo    [提示] 没有找到可用的运行环境。
echo    请双击"首次安装.bat"完成安装;或检查 runtime 目录,确认文件是否完整。
echo.
pause
exit /b 1

:nodist
echo.
echo    [提示] 前端界面文件缺失(frontend\dist)。
echo    请在装有 Node.js 的电脑上执行 npm run build 后,把 frontend\dist 文件夹拷贝过来。
echo.
pause
exit /b 1
