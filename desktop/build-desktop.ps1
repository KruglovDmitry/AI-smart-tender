# Orchestrator: versions.json -> ToolsServer + OpenWebUI + start.exe (+ zip).
param(
  [switch]$SkipOpenWebUI,
  [switch]$SkipTools,
  [switch]$SkipElectron,
  [switch]$Zip,
  [string]$OpenWebUISource = ""
)

$ErrorActionPreference = "Stop"
$Desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Desktop
$Dist = Join-Path $Desktop "dist"
$VersionsPath = Join-Path $Desktop "versions.json"
$Electron = Join-Path $Desktop "electron"

Set-Location $Desktop

if (-not (Test-Path $VersionsPath)) {
  Write-Error "Missing $VersionsPath"
  exit 1
}
$versions = Get-Content $VersionsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$product = [string]$versions.product

Write-Host "=== AI Smart Tender Desktop $product ==="
Write-Host "  open_webui:     $($versions.open_webui)"
Write-Host "  tools_server:   $($versions.tools_server)"
Write-Host "  electron_shell: $($versions.electron_shell)"

New-Item -ItemType Directory -Force -Path $Dist | Out-Null

$gitSha = "unknown"
try {
  Push-Location $Repo
  $gitSha = (git rev-parse --short HEAD 2>$null)
  if (-not $gitSha) { $gitSha = "unknown" }
  Pop-Location
} catch {
  $gitSha = "unknown"
}

if (-not $SkipTools) {
  Write-Host ""
  Write-Host "--- ToolsServer ---"
  & (Join-Path $Desktop "build_tools_exe.ps1")
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
  Write-Host "Skip ToolsServer build"
}

$toolsExe = Join-Path $Dist "ToolsServer\ToolsServer.exe"
if (-not (Test-Path $toolsExe)) {
  Write-Error "Missing $toolsExe - build tools or omit -SkipTools"
  exit 1
}

$owuiExe = Join-Path $Dist "OpenWebUI\OpenWebUI.exe"
if (-not $SkipOpenWebUI) {
  Write-Host ""
  Write-Host "--- OpenWebUI ---"
  if (-not $OpenWebUISource) {
    $candidates = @(
      (Join-Path $Desktop "vendor\OpenWebUI"),
      (Join-Path (Split-Path $Repo -Parent) "open-webui-freeze\dist\OpenWebUI")
    )
    foreach ($c in $candidates) {
      if (Test-Path (Join-Path $c "OpenWebUI.exe")) {
        $OpenWebUISource = $c
        break
      }
    }
  }
  if (-not $OpenWebUISource -or -not (Test-Path (Join-Path $OpenWebUISource "OpenWebUI.exe"))) {
    Write-Error "OpenWebUI not found. Build open-webui-freeze or pass -OpenWebUISource path (folder with OpenWebUI.exe)."
    exit 1
  }
  $owuiDest = Join-Path $Dist "OpenWebUI"
  Write-Host "Copy OpenWebUI from $OpenWebUISource -> $owuiDest"
  if (Test-Path $owuiDest) {
    Remove-Item -Recurse -Force $owuiDest
  }
  Copy-Item -Recurse -Force $OpenWebUISource $owuiDest
} else {
  Write-Host "Skip OpenWebUI copy"
}

if (-not (Test-Path $owuiExe)) {
  Write-Error "Missing $owuiExe"
  exit 1
}

$buildInfo = [ordered]@{
  product        = $product
  open_webui     = [string]$versions.open_webui
  tools_server   = [string]$versions.tools_server
  electron_shell = [string]$versions.electron_shell
  git_sha        = $gitSha
  built_at       = (Get-Date).ToUniversalTime().ToString("o")
}
$buildInfoPath = Join-Path $Dist "build-info.json"
($buildInfo | ConvertTo-Json) | Set-Content -Path $buildInfoPath -Encoding UTF8
Write-Host "Wrote $buildInfoPath"

if (-not $SkipElectron) {
  Write-Host ""
  Write-Host "--- Electron start.exe ---"
  Set-Location $Electron

  $pkgPath = Join-Path $Electron "package.json"
  $pkgText = [System.IO.File]::ReadAllText($pkgPath)
  if ($pkgText.Length -gt 0 -and [int][char]$pkgText[0] -eq 0xFEFF) {
    $pkgText = $pkgText.Substring(1)
  }
  $pkgText = [regex]::Replace($pkgText, '"version"\s*:\s*"[^"]*"', "`"version`": `"$product`"")
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($pkgPath, $pkgText, $utf8NoBom)

  $ebCli = Join-Path $Electron "node_modules\electron-builder\cli.js"
  if (-not (Test-Path $ebCli)) {
    Write-Host "npm install (electron-builder missing)..."
    npm install
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
  if (-not (Test-Path $ebCli)) {
    Write-Error "electron-builder not installed: $ebCli"
    exit 1
  }

  npm run dist
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  $unpacked = Join-Path $Electron "release\win-unpacked"
  $exe = Join-Path $unpacked "start.exe"
  if (-not (Test-Path $exe)) {
    Write-Error "Missing $exe"
    exit 1
  }

  $resInfo = Join-Path $unpacked "resources\build-info.json"
  Copy-Item -Force $buildInfoPath $resInfo

  $size = [math]::Round(((Get-ChildItem $unpacked -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
  Write-Host ""
  Write-Host "Ready: $exe"
  Write-Host "Folder: $unpacked (${size} MB)"
  Write-Host "Data: %LOCALAPPDATA%\AISmartTender\data"
  Write-Host "Copy the whole win-unpacked folder."

  if ($Zip) {
    $zipPath = Join-Path $Dist "AI-Smart-Tender-Desktop-$product.zip"
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Write-Host "Zipping -> $zipPath ..."
    Compress-Archive -Path (Join-Path $unpacked "*") -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host "Zip: $zipPath"
  }
} else {
  Write-Host "Skip Electron pack"
}

Write-Host ""
Write-Host "Done."
