@echo off
chcp 65001 >nul
setlocal

set "PROJECT_ROOT=%~dp0"
set "QUANTDINGER_ROOT=%PROJECT_ROOT%..\QuantDinger"

if not exist "%QUANTDINGER_ROOT%\docker-compose.yml" (
  echo QuantDinger directory not found.
  pause
  exit /b 1
)

cd /d "%QUANTDINGER_ROOT%"
docker compose stop
endlocal
