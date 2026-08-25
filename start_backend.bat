@echo off
rem ============================================
rem  Qlib Backtest Backend (FastAPI) - port 8001
rem  Data path priority: QLIB_PROVIDER_URI > data\cn_data > ~\.qlib
rem ============================================
echo.
echo Starting Qlib backend (FastAPI) on port 8001...
echo.
rem Use the qlib env python directly (no need to activate conda manually).
set "PYTHON=D:\miniconda3\envs\qlib\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

cd /d "%~dp0backend"
"%PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 8001
pause
