@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHONPATH=%~dp0src"
set "PYTHONUTF8=1"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "UV_FALLBACK=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
set "LOG_DIR=%~dp0logs"
set "LAUNCH_LOG=%LOG_DIR%\paper_gui_launch.log"
set "GUI_LOG=%LOG_DIR%\paper_gui_error.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
>"%LAUNCH_LOG%" echo [%date% %time%] Starting paper-trading GUI.
>"%GUI_LOG%" echo.

set "UV_EXE="
for /f "delims=" %%I in ('where uv 2^>nul') do if not defined UV_EXE set "UV_EXE=%%I"
if not defined UV_EXE if exist "%UV_FALLBACK%" set "UV_EXE=%UV_FALLBACK%"

if defined UV_EXE (
  echo [%date% %time%] uv=%UV_EXE%>>"%LAUNCH_LOG%"
  "%UV_EXE%" sync --locked --dev >>"%LAUNCH_LOG%" 2>&1
  if errorlevel 1 goto :environment_error

  "%UV_EXE%" run --locked python -c "import pandas, tkinter" >>"%LAUNCH_LOG%" 2>&1
  if errorlevel 1 (
    echo [%date% %time%] First check failed; reinstalling packages.>>"%LAUNCH_LOG%"
    "%UV_EXE%" sync --locked --dev --reinstall >>"%LAUNCH_LOG%" 2>&1
    if errorlevel 1 goto :environment_error
    "%UV_EXE%" run --locked python -c "import pandas, tkinter" >>"%LAUNCH_LOG%" 2>&1
    if errorlevel 1 goto :environment_error
  )

  echo [%date% %time%] Environment is ready.>>"%LAUNCH_LOG%"
  "%UV_EXE%" run --locked quant-paper-gui >"%GUI_LOG%" 2>&1
) else (
  if not exist "%PYTHON_EXE%" goto :environment_error
  echo [%date% %time%] uv not found; using the existing virtual environment.>>"%LAUNCH_LOG%"
  "%PYTHON_EXE%" -c "import pandas, tkinter" >>"%LAUNCH_LOG%" 2>&1
  if errorlevel 1 goto :environment_error
  "%PYTHON_EXE%" -m a_share_quant.paper.gui >"%GUI_LOG%" 2>&1
)

if errorlevel 1 (
  echo.
  echo Paper trading GUI exited with an error.
  echo Details: "%GUI_LOG%"
  if exist "%GUI_LOG%" type "%GUI_LOG%"
  pause
  exit /b 1
)

echo [%date% %time%] GUI exited normally.>>"%LAUNCH_LOG%"
endlocal
exit /b 0

:environment_error
echo.
echo The Python environment could not be prepared.
echo Diagnostics: "%LAUNCH_LOG%"
if exist "%LAUNCH_LOG%" type "%LAUNCH_LOG%"
pause
endlocal
exit /b 1
