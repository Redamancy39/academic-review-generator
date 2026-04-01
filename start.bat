@echo off
chcp 65001 >nul
echo ========================================
echo   学术综述自动生成系统 - 启动脚本
echo ========================================
echo.

:: 启动后端
echo [1/2] 启动后端服务...
start "后端服务" cmd /k "cd /d E:\crew_ai\backend && conda activate crew_ai && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

:: 等待后端启动
timeout /t 3 /nobreak >nul

:: 启动前端
echo [2/2] 启动前端服务...
start "前端服务" cmd /k "cd /d E:\crew_ai\frontend && npm run dev"

echo.
echo ========================================
echo   服务启动完成！
echo ========================================
echo.
echo   后端地址: http://localhost:8000
echo   API文档:  http://localhost:8000/docs
echo   前端地址: http://localhost:5173
echo.
echo   按任意键退出此窗口...
pause >nul
