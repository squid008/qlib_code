@echo off
chcp 65001 >nul
echo 启动 Qlib 回测前端 (Vite) ...
cd /d "%~dp0frontend"
npm run dev
pause
