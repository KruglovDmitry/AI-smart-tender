# Сборка onedir TenderAgent.exe + Playwright Chromium
$ErrorActionPreference = "Stop"
$Desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Desktop
$AgentSrc = Join-Path $Repo "tender-agent"
$Venv = Join-Path $Desktop ".venv-agent"
$Py = Join-Path $Venv "Scripts\python.exe"
$Out = Join-Path $Desktop "dist"
$Work = Join-Path $Desktop "build\agent"
$BrowsersDest = Join-Path $Out "TenderAgent\ms-playwright"

Set-Location $Desktop

# Свой venv у каждого сервиса: версии не конфликтуют; зависимости ставятся при каждой сборке.
if (-not (Test-Path $Py)) {
  Write-Host "Creating desktop/.venv-agent ..."
  py -3.12 -m venv $Venv
  if ($LASTEXITCODE -ne 0) { python -m venv $Venv }
  & $Py -m pip install --upgrade pip
}

Write-Host "Installing tender-agent into desktop/.venv-agent ..."
# --no-build-isolation: сборка пакета без скачивания setuptools — сборка не падает, если PyPI недоступен,
# а зависимости уже стоят.
& $Py -m pip install --no-build-isolation -e $AgentSrc "uvicorn[standard]" pyinstaller
if ($LASTEXITCODE -ne 0) { Write-Error "pip install tender-agent failed"; exit 1 }

# Библиотека ai_vision (репозиторий AI-vision) - запасной шаг по скриншотам. Без неё зрение в exe молча не работает.
$AiVision = $env:AI_VISION_PATH
if (-not $AiVision) { $AiVision = Join-Path (Split-Path -Parent $Repo) "AI-vision" }
if (-not (Test-Path (Join-Path $AiVision "pyproject.toml"))) {
  Write-Error "AI-vision repo not found: $AiVision (set AI_VISION_PATH)"
  exit 1
}
Write-Host "Installing ai_vision from $AiVision ..."
& $Py -m pip install --no-build-isolation $AiVision
if ($LASTEXITCODE -ne 0) { Write-Error "pip install ai_vision failed"; exit 1 }

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
  --collect-all ai_vision `
  --hidden-import tender_agent `
  --hidden-import tender_agent.server `
  --hidden-import tender_agent.config `
  --hidden-import tender_agent.api.service `
  --hidden-import ai_vision `
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

# Chromium для Playwright ставится прямо рядом с exe: только нужная версия, без копирования всего кэша.
# Сертификат Минцифры не копируем: на Windows Chromium берёт корневые сертификаты из системы.
Write-Host "Installing Playwright Chromium into $BrowsersDest ..."
if (Test-Path $BrowsersDest) { Remove-Item -Recurse -Force $BrowsersDest }
$prevBrowsers = $env:PLAYWRIGHT_BROWSERS_PATH
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowsersDest
& $Py -m playwright install chromium
$pwExit = $LASTEXITCODE
$env:PLAYWRIGHT_BROWSERS_PATH = $prevBrowsers
if ($pwExit -ne 0) { Write-Error "playwright install chromium failed: $pwExit"; exit $pwExit }

$exe = Join-Path $Out "TenderAgent\TenderAgent.exe"
Write-Host "Built: $exe"
if (Test-Path $exe) {
  $size = [math]::Round(((Get-ChildItem (Join-Path $Out "TenderAgent") -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
  Write-Host "Folder size: ${size} MB"
}
