@echo off
rem ============================================
rem  Qlib Backtest Backend (FastAPI) - port 8001
rem  Data path priority: QLIB_PROVIDER_URI > data\cn_data > ~\.qlib
rem  To use a custom data path, set env var first:
rem     set QLIB_PROVIDER_URI=D:\your\cn_data_path
rem ============================================
echo.
echo Make sure qlib env is activated (conda activate qlib)
echo or edit the python command below to your qlib python path.
echo.
cd /d "%~dp0backend"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
pause
