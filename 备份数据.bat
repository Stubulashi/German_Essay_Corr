@echo off
title 德语作文批改系统 - 数据备份
cd /d "%~dp0"

if exist "backend\.venv\Scripts\python.exe" goto dobackup
echo.
echo    [提示] 这台电脑还没有完成首次安装。
echo    请先双击运行"首次安装.bat",完成后再使用本功能。
echo.
pause
exit /b 1

:dobackup
echo ==============================================
echo    正在备份数据(数据库 + 上传的作文图片)...
echo ==============================================
backend\.venv\Scripts\python backend\scripts\backup.py
echo.
echo    提示:备份文件在"批改器"文件夹的 backend\backups 里,
echo    建议把它复制到 U 盘或网盘长期保存。
echo.
pause
