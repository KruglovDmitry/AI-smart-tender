@echo off
setlocal
cd /d "%~dp0"

if not exist ".env" (
  echo Creating .env from .env.example ...
  copy /Y ".env.example" ".env" >nul
  echo.
  echo Edit .env: set AGENT_LLM_API_KEY and TENDERS_HOST_PATH, then run start.bat again.
  notepad ".env"
  exit /b 1
)

echo Starting AI Smart Tender...
docker compose up -d
if errorlevel 1 (
  echo docker compose failed. Is Docker Desktop running?
  pause
  exit /b 1
)

echo.
echo UI:    http://localhost:3000
echo Tools: http://localhost:8000/docs
start "" "http://localhost:3000"
endlocal
