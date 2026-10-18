@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem =====================================================================
rem  restart_dev.bat  ——  一键重启「Agent 后端 + 前端 dev server」
rem  为什么需要：后端与前端都是常驻进程，改完代码不重启就一直在跑旧状态。
rem  后端读取 config/mcp.settings.json 里的远端能力服务地址与口令（该文件不入库）。
rem =====================================================================

echo [restart] 停止占用 8765（后端）与 5173（前端）的进程...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr LISTENING') do taskkill /f /pid %%p >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr LISTENING') do taskkill /f /pid %%p >nul 2>&1

echo [restart] 启动后端（新窗口）...
start "agent-backend" cmd /k call "%~dp0start_backend.bat"

echo [restart] 启动前端（新窗口）...
start "movielens-frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo.
echo [restart] 已启动：
echo   后端  http://127.0.0.1:8765
echo   前端  http://localhost:5173   （注意用 localhost，dev server 只绑 IPv6 回环）
echo.
endlocal
