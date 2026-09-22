@echo off
title 德语作文批改系统 - 数据恢复
cd /d "%~dp0"

if exist "backend\.venv\Scripts\python.exe" goto dorestore
echo.
echo    [提示] 这台电脑还没有完成首次安装。
echo    请先双击运行"首次安装.bat",完成后再使用本功能。
echo.
pause
exit /b 1

:dorestore
backend\.venv\Scripts\python backend\scripts\restore.py
pause
