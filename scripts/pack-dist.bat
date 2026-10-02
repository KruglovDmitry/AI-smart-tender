@echo off
REM Build customer dist. Usage: scripts\pack-dist.bat   or   scripts\pack-dist.bat --zip
cd /d "%~dp0\.."
py -3 scripts\pack_dist.py %*
if errorlevel 1 (
  echo Failed. Is Python installed? Try: py -3 scripts\pack_dist.py --zip
  exit /b 1
)
