# Сборка onedir ToolsServer.exe через PyInstaller.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-paths.ps1")
$ToolsSrc = Join-Path $Repo "tools-server"
$Venv = Join-Path $VenvRoot "tools"
$Py = Join-Path $Venv "Scripts\python.exe"
$Out = $Dist
$Work = Join-Path $WorkRoot "tools"

Set-Location $Desktop

if (-not (Test-Path $Py)) {
  Write-Host "Creating $Venv ..."
  py -3.12 -m venv $Venv
  if ($LASTEXITCODE -ne 0) { python -m venv $Venv }
  & $Py -m pip install --upgrade pip
}
# Зависимости ставим при каждой сборке: requirements.txt мог измениться.
# Те же зависимости, что у Docker-образа (tools-server/requirements.txt), плюс сборщик.
& $Py -m pip install -r (Join-Path $ToolsSrc "requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }

$env:DATA_ROOT = Join-Path $WorkRoot "data-build"
New-Item -ItemType Directory -Force -Path $env:DATA_ROOT | Out-Null
foreach ($sub in @("tenders", "catalogs", "uploads", "exports")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $env:DATA_ROOT $sub) | Out-Null
}

# Модели OCR скачиваются в пакет rapidocr при первом создании движка; --collect-all rapidocr кладёт их в exe.
& $Py -c "import sys; sys.path.insert(0, r'$ToolsSrc'); from app.document_tool.pdf import ocr; assert ocr.available(), ocr._unavailable"
if ($LASTEXITCODE -ne 0) { Write-Error "OCR models are not available (rapidocr download failed?)"; exit 1 }
$env:TOOLS_HOST = "127.0.0.1"
$env:TOOLS_PORT = "18000"

Write-Host "Building ToolsServer.exe (onedir)..."

. (Join-Path $Desktop "protect.ps1")

Invoke-ProtectedPyInstaller $Py "ToolsServer" $Out $Work @("app", "smart_license") @(
  "--onedir",
  "--console",
  "--paths", $ToolsSrc,
  "--add-data", ((Join-Path $ToolsSrc "app\reference_data") + ";app\reference_data"),
  "--add-data", ((Join-Path $ToolsSrc "app\document_tool\pdf\SKILL.md") + ";app\document_tool\pdf"),
  "--paths", $Licensing,
  "--collect-all", "uvicorn",
  "--collect-all", "starlette",
  "--collect-all", "fastapi",
  "--collect-all", "pydantic",
  "--collect-all", "pydantic_core",
  "--collect-all", "httpx",
  "--collect-all", "trafilatura",
  "--collect-all", "pandas",
  "--collect-all", "openpyxl",
  "--collect-all", "pymupdf",
  "--collect-all", "rapidocr",
  "--collect-all", "onnxruntime",
  "--collect-all", "markitdown",
  "--collect-all", "magika",
  "--hidden-import", "app",
  "--hidden-import", "app.main",
  "--hidden-import", "app.config",
  "--hidden-import", "app.document_tool",
  "--hidden-import", "app.document_tool.extract",
  "--hidden-import", "app.document_tool.skills",
  "--hidden-import", "app.document_tool.pdf",
  "--hidden-import", "app.document_tool.pdf.read",
  "--hidden-import", "app.document_tool.pdf.ocr",
  "--hidden-import", "app.document_tool.pdf.vision",
  "--hidden-import", "app.document_tool.pdf.workflow",
  "--hidden-import", "app.document_tool.pdf.reader",
  "--hidden-import", "app.excel_tool",
  "--hidden-import", "app.web_tool",
  "--hidden-import", "app.document_index",
  "--hidden-import", "app.document_index.reading",
  "--hidden-import", "app.integrations",
  "--hidden-import", "app.integrations.open_webui",
  "--hidden-import", "app.integrations.open_webui.loader",
  "--hidden-import", "app.table_tool",
  "--hidden-import", "app.table_tool.jobs",
  "--hidden-import", "app.table_tool.evidence",
  "--hidden-import", "app.table_tool.classification",
  "--hidden-import", "smart_license",
  "--hidden-import", "smart_license.web",
  "--hidden-import", "uvicorn.logging",
  "--hidden-import", "uvicorn.loops",
  "--hidden-import", "uvicorn.loops.auto",
  "--hidden-import", "uvicorn.protocols",
  "--hidden-import", "uvicorn.protocols.http",
  "--hidden-import", "uvicorn.protocols.http.auto",
  "--hidden-import", "uvicorn.protocols.websockets",
  "--hidden-import", "uvicorn.protocols.websockets.auto",
  "--hidden-import", "uvicorn.lifespan",
  "--hidden-import", "uvicorn.lifespan.on",
  "--exclude-module", "tkinter",
  "--exclude-module", "matplotlib",
  "--exclude-module", "playwright",
  (Join-Path $Desktop "run_tools_server.py")
)

# Наш код — только скомпилированным (.pyd).
$destInternal = Join-Path $Out "ToolsServer\_internal"
Compile-Packages $Py $destInternal $Work @((Join-Path $ToolsSrc "app"), (Join-Path $Licensing "smart_license"))

$exe = Join-Path $Out "ToolsServer\ToolsServer.exe"
Write-Host ""
Write-Host "Built: $exe"
if (Test-Path $exe) {
  $size = [math]::Round(((Get-ChildItem (Join-Path $Out "ToolsServer") -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
  Write-Host "Folder size: ${size} MB"
}
