@echo off
cd /d "%~dp0"
if not exist "node_modules\electron\cli.js" (
  call npm install
)
set OWUI_HOST=127.0.0.1
set OWUI_PORT=18082
set TOOLS_HOST=127.0.0.1
set TOOLS_PORT=18000
call npm start
