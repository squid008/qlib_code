@echo off
chcp 65001 >nul
echo 使用 qlib 环境运行 test2.py ...
python "%~dp0test2.py"
echo.
echo 运行结束。
pause
