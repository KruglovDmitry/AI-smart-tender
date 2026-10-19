# Сборка onedir TenderAgent.exe + Playwright Chromium
$ErrorActionPreference = "Stop"
$Desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Desktop
$AgentSrc = Join-Path $Repo "tender-agent"
$Venv = Join-Path $Desktop ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Out = Join-Path $Desktop "dist"
$Work = Join-Path $Desktop "build\agent"
$BrowsersDest = Join-Path $Out "TenderAgent\ms-playwright"

Set-Location $Desktop

if (-not (Test-Path $Py)) {
  Write-Host "Creating desktop/.venv ..."
  py -3.12 -m venv $Venv
  if ($LASTEXITCODE -ne 0) { python -m venv $Venv }
  & $Py -m pip install --upgrade pip
}

Write-Host "Installing tender-agent into desktop/.venv ..."
& $Py -m pip install -e $AgentSrc
& $Py -m pip install "uvicorn[standard]" pyinstaller
& $Py -m playwright install chromium

Write-Host "Building TenderAgent.exe (onedir)..."
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Py -m PyInstaller `
  --noconfirm --clean --onedir --console `
  --name TenderAgent `
  --distpath $Out `
  --workpath $Work `
  --paths (Join-Path $AgentSrc "src") `
  --collect-all uvicorn `
  --collect-all fastapi `
  --collect-all pydantic `
  --collect-all playwright `
  --hidden-import tender_agent `
  --hidden-import tender_agent.server `
  --hidden-import tender_agent.config `
  --hidden-import tender_agent.api.service `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.lifespan.on `
  --exclude-module tkinter `
  --exclude-module matplotlib `
  (Join-Path $Desktop "run_agent_server.py")
$pyExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($pyExit -ne 0) { Write-Error "PyInstaller TenderAgent failed: $pyExit"; exit $pyExit }

$destInternal = Join-Path $Out "TenderAgent\_internal"
$pkgSrc = Join-Path $AgentSrc "src\tender_agent"
$pkgDst = Join-Path $destInternal "tender_agent"
Write-Host "Copying tender_agent package into _internal..."
if (Test-Path $pkgDst) { Remove-Item -Recurse -Force $pkgDst }
Copy-Item -Recurse -Force $pkgSrc $pkgDst

# Chromium for Playwright (headed desktop)
$pwCache = Join-Path $env:LOCALAPPDATA "ms-playwright"
if (-not (Test-Path $pwCache)) {
  $pwCache = Join-Path $env:USERPROFILE "AppData\Local\ms-playwright"
}
Write-Host "Copying Playwright browsers from $pwCache -> $BrowsersDest"
if (Test-Path $BrowsersDest) { Remove-Item -Recurse -Force $BrowsersDest }
New-Item -ItemType Directory -Force -Path $BrowsersDest | Out-Null
if (Test-Path $pwCache) {
  Copy-Item -Recurse -Force (Join-Path $pwCache "*") $BrowsersDest
} else {
  Write-Warning "ms-playwright cache not found; run: playwright install chromium"
}

# Russian Trusted Root CA if present in repo
$cert = Join-Path $AgentSrc "certs\russian_trusted_root_ca.crt"
if (Test-Path $cert) {
  Copy-Item -Force $cert (Join-Path $Out "TenderAgent\russian_trusted_root_ca.crt")
}

$exe = Join-Path $Out "TenderAgent\TenderAgent.exe"
Write-Host "Built: $exe"
if (Test-Path $exe) {
  $size = [math]::Round(((Get-ChildItem (Join-Path $Out "TenderAgent") -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
  Write-Host "Folder size: ${size} MB"
}
