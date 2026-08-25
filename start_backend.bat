@echo off
chcp 65001 >nul
echo ============================================
echo  Qlib 回测后端 (FastAPI)
echo  端口: 8001
echo  数据路径优先级: 环境变量 QLIB_PROVIDER_URI ^> 项目 data\cn_data ^> ~\.qlib
echo  如需指定数据路径，请先设置环境变量，例如：
echo    set QLIB_PROVIDER_URI=D:\你的数据路径\cn_data
echo ============================================
echo.
echo 请确认已激活 qlib 环境（conda activate qlib），再运行本脚本
echo 或修改下面的 python 为你的 qlib 环境实际路径
echo.
cd /d "%~dp0backend"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
pause
