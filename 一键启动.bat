@echo off
title 德语作文批改系统 - 启动
cd /d "%~dp0"

set "PYEXE=%~dp0runtime\python\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PYEXE%" goto noenv
if not exist "%~dp0frontend\dist\index.html" goto nodist

rem 端口选择:默认 8765;被占用自动改用 8766(两者都占用则提示后退出)
set "PORT=8765"
netstat -ano -p tcp | findstr ":8765 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 set "PORT=8766"
netstat -ano -p tcp | findstr ":8766 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto portbusy

echo ==============================================
echo    正在启动德语作文批改系统...
echo ==============================================
echo.
echo [1/2] 正在启动后端服务(端口 %PORT%),出现的新窗口请不要关闭...
start "批改系统-后端(使用期间不要关)" /D "%~dp0backend" cmd /k ""%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%"

echo [2/2] 等待后端就绪后自动打开浏览器(通常 10 秒内,请稍候)...
set /a WAIT=0

:waitready
netstat -ano -p tcp | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto serverok
set /a WAIT+=1
if %WAIT% GEQ 120 goto waittimeout
set /a HALF=WAIT-10*(WAIT/10)
if %HALF% EQU 0 echo    ...已等待 %WAIT% 秒,后端仍在启动中(最长等待 120 秒)
ping -n 2 127.0.0.1 >nul
goto waitready

:serverok
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

:waittimeout
echo.
echo    [提示] 后端启动超时(已等待 120 秒),暂未打开浏览器。
echo    请查看"后端"窗口中的错误信息,排除后重新双击本脚本;
echo    或稍候手动访问: http://127.0.0.1:%PORT%
echo.
pause
exit /b 1

:portbusy
echo.
echo    [提示] 端口 8765 与 8766 都被占用,系统暂时无法启动。
echo    请先关闭占用端口的其他程序,或双击"一键停止.bat"后再试。
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
