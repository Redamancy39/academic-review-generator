@echo off
chcp 65001 >nul
echo ========================================
echo   学术综述自动生成系统 - 停止脚本
echo ========================================
echo.

:: 关闭后端 (8000端口)
echo 正在停止后端服务...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
    echo 已停止进程 PID: %%a
)

:: 关闭前端 (5173端口)
echo 正在停止前端服务...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5173 ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
    echo 已停止进程 PID: %%a
)

echo.
echo 所有服务已停止。
pause
