@echo off
title 德语作文批改系统 - 停止
echo ==============================================
echo    正在停止德语作文批改系统...
echo ==============================================
echo.

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%p >nul 2>&1
    set FOUND=1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8766 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%p >nul 2>&1
    set FOUND=1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%p >nul 2>&1
    set FOUND=1
)

if not "%FOUND%"=="1" goto notfound
echo    系统已停止。
goto after
:notfound
echo    没有发现正在运行的系统(可能已经停止运行)。
:after
echo.
echo    提示:也可以在后端窗口点右上角的 X 关闭它。
echo.
pause
