@echo off
rem ============================================
rem  Stop Qlib Backend (port 8001)
rem ============================================
echo.
echo Stopping backend on port 8001 ...

set "PORT=8001"
set "FOUND=0"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    if not "%%a"=="0" (
        echo Found PID %%a on port %PORT%, terminating...
        taskkill /F /PID %%a >nul 2>&1
        set "FOUND=1"
    )
)

if "%FOUND%"=="0" (
    echo No process listening on port %PORT%. Backend may already be stopped.
) else (
    echo Backend stopped.
)
echo.
pause
