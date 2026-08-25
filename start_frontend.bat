@echo off
rem ============================================
rem  Qlib Backtest Frontend (Vite) - port 5173
rem ============================================
echo.
echo Starting Qlib frontend (Vite)...
echo Open http://localhost:5173 in browser when ready.
echo.
cd /d "%~dp0frontend"
npm run dev
pause
