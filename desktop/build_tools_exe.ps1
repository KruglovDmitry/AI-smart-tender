# Сборка onedir ToolsServer.exe через PyInstaller.
$ErrorActionPreference = "Stop"
$Desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Desktop
$ToolsSrc = Join-Path $Repo "tools-server"
$Venv = Join-Path $Desktop ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Out = Join-Path $Desktop "dist"
$Work = Join-Path $Desktop "build\tools"

Set-Location $Desktop

if (-not (Test-Path $Py)) {
  Write-Host "Creating desktop/.venv ..."
  py -3.12 -m venv $Venv
  if ($LASTEXITCODE -ne 0) {
    python -m venv $Venv
  }
  & $Py -m pip install --upgrade pip
  & $Py -m pip install -r (Join-Path $ToolsSrc "requirements.txt")
  # PDF/DOCX loaders (lazy-import in extract.py)
  & $Py -m pip install "langchain-community>=0.3" "langchain-core>=0.3" pyinstaller
}

$env:DATA_ROOT = Join-Path $Desktop "data-build"
New-Item -ItemType Directory -Force -Path $env:DATA_ROOT | Out-Null
foreach ($sub in @("tenders", "catalogs", "uploads", "exports")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $env:DATA_ROOT $sub) | Out-Null
}
$env:TOOLS_HOST = "127.0.0.1"
$env:TOOLS_PORT = "18000"

Write-Host "Building ToolsServer.exe (onedir)..."

# PyInstaller пишет DEPRECATION в stderr — не роняем скрипт из-за этого
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Py -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --console `
  --name ToolsServer `
  --distpath $Out `
  --workpath $Work `
  --paths $ToolsSrc `
  --collect-all uvicorn `
  --collect-all starlette `
  --collect-all fastapi `
  --collect-all pydantic `
  --collect-all pydantic_core `
  --collect-all httpx `
  --collect-all trafilatura `
  --collect-all pandas `
  --collect-all openpyxl `
  --hidden-import app `
  --hidden-import app.main `
  --hidden-import app.config `
  --hidden-import app.document_tool `
  --hidden-import app.document_tool.extract `
  --hidden-import app.excel_tool `
  --hidden-import app.web_tool `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols `
  --hidden-import uvicorn.protocols.http `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.protocols.websockets `
  --hidden-import uvicorn.protocols.websockets.auto `
  --hidden-import uvicorn.lifespan `
  --hidden-import uvicorn.lifespan.on `
  --hidden-import langchain_community `
  --hidden-import langchain_community.document_loaders `
  --hidden-import langchain_community.document_loaders.pdf `
  --hidden-import langchain_community.document_loaders.word_document `
  --hidden-import langchain_community.document_loaders.html_bs `
  --exclude-module tkinter `
  --exclude-module matplotlib `
  --exclude-module playwright `
  (Join-Path $Desktop "run_tools_server.py")
$pyExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap

if ($pyExit -ne 0) {
  Write-Error "PyInstaller ToolsServer failed with code $pyExit"
  exit $pyExit
}

# Всегда кладём исходники app/ в _internal (надёжный import на диске)
$destInternal = Join-Path $Out "ToolsServer\_internal"
$destApp = Join-Path $destInternal "app"
Write-Host "Copying tools-server/app into _internal..."
if (Test-Path $destApp) { Remove-Item -Recurse -Force $destApp }
Copy-Item -Recurse -Force (Join-Path $ToolsSrc "app") $destApp
if (-not (Test-Path (Join-Path $destApp "main.py"))) {
  Write-Error "Failed to copy app into $destApp"
  exit 1
}

$exe = Join-Path $Out "ToolsServer\ToolsServer.exe"
Write-Host ""
Write-Host "Built: $exe"
if (Test-Path $exe) {
  $size = [math]::Round(((Get-ChildItem (Join-Path $Out "ToolsServer") -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
  Write-Host "Folder size: ${size} MB"
}
