@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Python environment is missing. Run "uv sync --dev" first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "src\a_share_quant\paper\gui.py"
endlocal
