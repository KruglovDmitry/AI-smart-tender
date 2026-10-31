# Сборка onedir Dashboard.exe
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-paths.ps1")
$DashSrc = Join-Path $Repo "dashboard"
$Venv = Join-Path $VenvRoot "dashboard"
$Py = Join-Path $Venv "Scripts\python.exe"
$Out = $Dist
$Work = Join-Path $WorkRoot "dashboard"

Set-Location $Desktop

if (-not (Test-Path $Py)) {
  Write-Host "Creating $Venv ..."
  py -3.12 -m venv $Venv
  if ($LASTEXITCODE -ne 0) { python -m venv $Venv }
  & $Py -m pip install --upgrade pip
}
# Зависимости ставим при каждой сборке: requirements.txt мог измениться.
& $Py -m pip install -r (Join-Path $DashSrc "requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }

Write-Host "Building Dashboard.exe (onedir)..."
. (Join-Path $Desktop "protect.ps1")

Invoke-ProtectedPyInstaller $Py "Dashboard" $Out $Work @("app", "smart_license") @(
  "--onedir", "--console",
  "--paths", $DashSrc,
  "--paths", $Licensing,
  "--add-data", "$(Join-Path $DashSrc 'static');static",
  "--collect-all", "uvicorn",
  "--collect-all", "fastapi",
  "--collect-all", "pydantic",
  "--hidden-import", "app",
  "--hidden-import", "app.main",
  "--hidden-import", "app.db",
  "--hidden-import", "app.data_scan",
  "--hidden-import", "smart_license",
  "--hidden-import", "smart_license.web",
  "--hidden-import", "uvicorn.logging",
  "--hidden-import", "uvicorn.loops.auto",
  "--hidden-import", "uvicorn.protocols.http.auto",
  "--hidden-import", "uvicorn.lifespan.on",
  "--exclude-module", "playwright",
  "--exclude-module", "tkinter",
  (Join-Path $Desktop "run_dashboard.py")
)

# Наш код — только скомпилированным (.pyd); статика страницы — рядом, как раньше.
$destInternal = Join-Path $Out "Dashboard\_internal"
Compile-Packages $Py $destInternal $Work @((Join-Path $DashSrc "app"), (Join-Path $Licensing "smart_license"))
$staticSrc = Join-Path $DashSrc "static"
$staticDst = Join-Path $destInternal "static"
if (Test-Path $staticSrc) {
  if (Test-Path $staticDst) { Remove-Item -Recurse -Force $staticDst }
  Copy-Item -Recurse -Force $staticSrc $staticDst
}

$exe = Join-Path $Out "Dashboard\Dashboard.exe"
Write-Host "Built: $exe"
