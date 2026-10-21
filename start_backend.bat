@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem =====================================================================
rem  One-click start of the Agent backend  ->  http://127.0.0.1:8765
rem  - Only the necessary env vars are set here.
rem  - The LLM endpoint / key / model are best set in the web UI "Settings"
rem    (the UI saves them locally and sends them per request; the backend
rem    does not write them to disk).
rem  - If you want a backend-side DEFAULT model (used when the UI has none),
rem    uncomment the block below and fill it in.
rem =====================================================================

set PYTHONIOENCODING=utf-8
set AGENT_LLM_SUPPORTED=1

rem --- raw data dir (needed by the LOCAL engine; not needed for cluster mode) ---
rem     Data lives only in the distributed system; the local engine reads it from
rem     ML_RAW_DIR. Set it in the environment before starting (see hadoop/scripts/env.sh);
rem     nothing is auto-detected here on purpose.

rem --- optional: backend-side default model ---
rem set AGENT_LLM_ENABLED=1
rem set AGENT_LLM_API_BASE=https://opencode.ai/zen/go/v1
rem set AGENT_LLM_API_KEY=
rem set AGENT_LLM_MODEL=deepseek-v4.1-flash
rem set AGENT_LLM_PROVIDER=opencode

rem --- remote Hadoop capability service (AG-13) ---
rem The Agent calls the remote service over the standard /mcp entry with a
rem Bearer token. Configure it with these two env vars (do NOT hardcode the
rem token here -- pass it via the environment only):
rem   set AGENT_MCP_URL=http://127.0.0.1:8900/mcp   (host 8900 -> VM 8765)
rem   set AGENT_MCP_TOKEN=<your token>
rem Without them the backend still starts; remote tools report as unavailable.

rem --- pick interpreter: project .venv, then stage-0 probe venv, then system python ---
set "PY=.venv\Scripts\python.exe"
if exist "%PY%" goto run
set "PY=.scratch\langgraph-probe\.venv\Scripts\python.exe"
if exist "%PY%" goto run

echo [start_backend] First run: creating venv and installing dependencies (needs internet)...
python -m venv .venv
if errorlevel 1 goto err
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto err
set "PY=.venv\Scripts\python.exe"

:run
echo [start_backend] interpreter: %PY%
echo [start_backend] backend at http://127.0.0.1:8765   (Ctrl+C to stop)
"%PY%" -m agent.http_api --port 8765
goto :eof

:err
echo [start_backend] FAILED: need Python 3.10+ and internet access.
pause
