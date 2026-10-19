# Сборка onedir Dashboard.exe
$ErrorActionPreference = "Stop"
$Desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Desktop
$DashSrc = Join-Path $Repo "dashboard"
$Venv = Join-Path $Desktop ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Out = Join-Path $Desktop "dist"
$Work = Join-Path $Desktop "build\dashboard"

Set-Location $Desktop

if (-not (Test-Path $Py)) {
  Write-Host "Creating desktop/.venv ..."
  py -3.12 -m venv $Venv
  if ($LASTEXITCODE -ne 0) { python -m venv $Venv }
  & $Py -m pip install --upgrade pip
  & $Py -m pip install -r (Join-Path $DashSrc "requirements.txt")
  & $Py -m pip install pyinstaller
}

Write-Host "Building Dashboard.exe (onedir)..."
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Py -m PyInstaller `
  --noconfirm --clean --onedir --console `
  --name Dashboard `
  --distpath $Out `
  --workpath $Work `
  --paths $DashSrc `
  --add-data "$(Join-Path $DashSrc 'static');static" `
  --collect-all uvicorn `
  --collect-all fastapi `
  --collect-all pydantic `
  --hidden-import app `
  --hidden-import app.main `
  --hidden-import app.db `
  --hidden-import app.data_scan `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.lifespan.on `
  --exclude-module playwright `
  --exclude-module tkinter `
  (Join-Path $Desktop "run_dashboard.py")
$pyExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($pyExit -ne 0) { Write-Error "PyInstaller Dashboard failed: $pyExit"; exit $pyExit }

$destInternal = Join-Path $Out "Dashboard\_internal"
$destApp = Join-Path $destInternal "app"
Write-Host "Copying dashboard/app into _internal..."
if (Test-Path $destApp) { Remove-Item -Recurse -Force $destApp }
Copy-Item -Recurse -Force (Join-Path $DashSrc "app") $destApp
$staticSrc = Join-Path $DashSrc "static"
$staticDst = Join-Path $destInternal "static"
if (Test-Path $staticSrc) {
  if (Test-Path $staticDst) { Remove-Item -Recurse -Force $staticDst }
  Copy-Item -Recurse -Force $staticSrc $staticDst
}

$exe = Join-Path $Out "Dashboard\Dashboard.exe"
Write-Host "Built: $exe"
