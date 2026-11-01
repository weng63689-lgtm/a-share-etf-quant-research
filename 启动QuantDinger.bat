@echo off
chcp 65001 >nul
setlocal

set "PROJECT_ROOT=%~dp0"
set "QUANTDINGER_ROOT=%PROJECT_ROOT%..\QuantDinger"
set "DOCKER_DESKTOP=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"

if not exist "%QUANTDINGER_ROOT%\docker-compose.yml" (
  echo QuantDinger directory not found.
  pause
  exit /b 1
)

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not available.
  pause
  exit /b 1
)

docker info >nul 2>nul
if not errorlevel 1 goto :docker_ready

(
  if not exist "%DOCKER_DESKTOP%" (
    echo Docker Desktop is installed but its launcher was not found.
    pause
    exit /b 1
  )
  echo Starting Docker Desktop and waiting for the Docker engine...
  start "" "%DOCKER_DESKTOP%"
)

set /a DOCKER_WAIT_COUNT=0
:wait_for_docker
docker info >nul 2>nul
if not errorlevel 1 goto :docker_ready
set /a DOCKER_WAIT_COUNT+=1
if %DOCKER_WAIT_COUNT% GEQ 90 goto :docker_timeout
timeout /t 2 /nobreak >nul
goto :wait_for_docker

:docker_ready
echo Docker engine is ready.
cd /d "%QUANTDINGER_ROOT%"
docker compose up -d
if errorlevel 1 (
  echo Failed to start QuantDinger.
  pause
  exit /b 1
)

set /a QD_WAIT_COUNT=0
:wait_for_quantdinger
curl.exe -fsS "http://127.0.0.1:5000/api/health" >nul 2>nul
if not errorlevel 1 goto :quantdinger_ready
set /a QD_WAIT_COUNT+=1
if %QD_WAIT_COUNT% GEQ 60 goto :quantdinger_timeout
timeout /t 2 /nobreak >nul
goto :wait_for_quantdinger

:quantdinger_ready
start "" "http://127.0.0.1:8888"
endlocal
exit /b 0

:docker_timeout
echo Docker Desktop did not become ready within 3 minutes.
echo Open Docker Desktop, wait until it says Engine running, then try again.
pause
exit /b 1

:quantdinger_timeout
echo QuantDinger containers started, but the backend did not become ready.
echo Run this command for details:
echo   docker compose logs --tail 200 backend
pause
exit /b 1
