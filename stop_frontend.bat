@echo off
rem ============================================
rem  Stop Qlib Frontend (port 5173)
rem ============================================
echo.
echo Stopping frontend on port 5173 ...

set "PORT=5173"
set "FOUND=0"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    if not "%%a"=="0" (
        echo Found PID %%a on port %PORT%, terminating...
        taskkill /F /PID %%a >nul 2>&1
        set "FOUND=1"
    )
)

if "%FOUND%"=="0" (
    echo No process listening on port %PORT%. Frontend may already be stopped.
) else (
    echo Frontend stopped.
)
echo.
pause
