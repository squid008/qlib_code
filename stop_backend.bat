@echo off
chcp 65001 >nul
echo ============================================
echo  停止 Qlib 回测后端 (端口 8001)
echo ============================================
echo.

set "PORT=8001"
set "FOUND=0"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    if not "%%a"=="0" (
        echo 找到端口 %PORT% 的进程 PID: %%a，正在结束...
        taskkill /F /PID %%a >nul 2>&1
        set "FOUND=1"
    )
)

if "%FOUND%"=="0" (
    echo 端口 %PORT% 没有找到正在监听的进程，后端可能已经停止。
) else (
    echo 后端已停止。
)

echo.
pause
