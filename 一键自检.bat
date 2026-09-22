@echo off
title 德语作文批改系统 - 环境自检
cd /d "%~dp0"

set "PYEXE=%~dp0runtime\python\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PYEXE%" goto noenv

"%PYEXE%" "%~dp0backend\scripts\selfcheck.py"
echo.
pause
exit /b 0

:noenv
echo.
echo    [提示] 没有找到可用的 Python 运行环境,请先运行"首次安装.bat"。
echo.
pause
exit /b 1
