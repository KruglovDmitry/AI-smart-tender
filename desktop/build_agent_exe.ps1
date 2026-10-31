# Сборка onedir TenderAgent.exe + Playwright Chromium
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-paths.ps1")
$AgentSrc = Join-Path $Repo "tender-agent"
$Venv = Join-Path $VenvRoot "agent"
$Py = Join-Path $Venv "Scripts\python.exe"
$Out = $Dist
$Work = Join-Path $WorkRoot "agent"
$BrowsersDest = Join-Path $Out "TenderAgent\ms-playwright"

Set-Location $Desktop

# Свой venv у каждого сервиса: версии не конфликтуют; зависимости ставятся при каждой сборке.
if (-not (Test-Path $Py)) {
  Write-Host "Creating $Venv ..."
  py -3.12 -m venv $Venv
  if ($LASTEXITCODE -ne 0) { python -m venv $Venv }
  & $Py -m pip install --upgrade pip
}

Write-Host "Installing tender-agent into $Venv ..."
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

. (Join-Path $Desktop "protect.ps1")

Write-Host "Building TenderAgent.exe (onedir)..."
Invoke-ProtectedPyInstaller $Py "TenderAgent" $Out $Work @("tender_agent", "smart_license", "ai_vision") @(
  "--onedir", "--console",
  "--paths", (Join-Path $AgentSrc "src"),
  "--paths", $Licensing,
  "--collect-all", "uvicorn",
  "--collect-all", "fastapi",
  "--collect-all", "pydantic",
  "--collect-all", "playwright",
  "--collect-all", "ai_vision",
  "--hidden-import", "tender_agent",
  "--hidden-import", "tender_agent.server",
  "--hidden-import", "tender_agent.config",
  "--hidden-import", "tender_agent.api.service",
  "--hidden-import", "smart_license",
  "--hidden-import", "smart_license.web",
  "--hidden-import", "ai_vision",
  "--hidden-import", "uvicorn.logging",
  "--hidden-import", "uvicorn.loops.auto",
  "--hidden-import", "uvicorn.protocols.http.auto",
  "--hidden-import", "uvicorn.lifespan.on",
  "--exclude-module", "tkinter",
  "--exclude-module", "matplotlib",
  (Join-Path $Desktop "run_agent_server.py")
)

# Наш код — только скомпилированным (.pyd), данные пакета (заметки площадок и т.п.) — рядом.
$destInternal = Join-Path $Out "TenderAgent\_internal"
Compile-Packages $Py $destInternal $Work @(
  (Join-Path $AgentSrc "src\tender_agent"), (Join-Path $Licensing "smart_license"), (Join-Path $AiVision "src\ai_vision"))

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
