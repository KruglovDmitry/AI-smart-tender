# Orchestrator: services.json + versions.json -> enabled exes + start.exe
param(
  [switch]$SkipElectron,
  [switch]$SkipServices,
  [switch]$Zip,
  [string]$OpenWebUISource = ""
)

$ErrorActionPreference = "Stop"
$Desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Desktop
$Dist = Join-Path $Desktop "dist"
$VersionsPath = Join-Path $Desktop "versions.json"
$ServicesPath = Join-Path $Desktop "services.json"
$Electron = Join-Path $Desktop "electron"

Set-Location $Desktop

if (-not (Test-Path $VersionsPath)) { Write-Error "Missing $VersionsPath"; exit 1 }
if (-not (Test-Path $ServicesPath)) { Write-Error "Missing $ServicesPath"; exit 1 }

$versions = Get-Content $VersionsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$servicesDoc = Get-Content $ServicesPath -Raw -Encoding UTF8 | ConvertFrom-Json
$product = [string]$versions.product
$enabled = @($servicesDoc.services | Where-Object { $_.enabled -ne $false })

Write-Host "=== AI Smart Tender Desktop $product ==="
Write-Host "Enabled services:"
foreach ($s in $enabled) {
  Write-Host ("  - {0} ({1})" -f $s.id, $s.kind)
}

New-Item -ItemType Directory -Force -Path $Dist | Out-Null

$gitSha = "unknown"
try {
  Push-Location $Repo
  $gitSha = (git rev-parse --short HEAD 2>$null)
  if (-not $gitSha) { $gitSha = "unknown" }
  Pop-Location
} catch { $gitSha = "unknown" }

function Build-ProcessService($svc) {
  $build = $svc.build
  if (-not $build) {
    Write-Host "No build block for $($svc.id), skip"
    return
  }
  if ($build.type -eq "script") {
    $script = Join-Path $Desktop ([string]$build.script)
    if (-not (Test-Path $script)) {
      Write-Error "Build script missing for $($svc.id): $script"
      exit 1
    }
    Write-Host ""
    Write-Host "--- $($svc.id) ($($build.script)) ---"
    & $script
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $exe = Join-Path $Dist "$($svc.resourceDir)\$($svc.exe)"
    if (-not (Test-Path $exe)) {
      Write-Error "Missing output $exe"
      exit 1
    }
    return
  }
  if ($build.type -eq "copy") {
    Write-Host ""
    Write-Host "--- $($svc.id) (copy) ---"
    $src = $OpenWebUISource
    if (-not $src) {
      foreach ($c in @($build.candidates)) {
        $cand = if ([System.IO.Path]::IsPathRooted($c)) { $c } else { Join-Path $Desktop $c }
        if (-not (Test-Path $cand)) {
          $cand = Join-Path $Desktop ($c -replace '/', '\')
        }
        if (-not (Test-Path $cand)) {
          $cand = [System.IO.Path]::GetFullPath((Join-Path $Desktop ($c -replace '/', '\')))
        }
        if ((Test-Path (Join-Path $cand $svc.exe)) -or (Test-Path (Join-Path $cand "OpenWebUI.exe"))) {
          $src = $cand
          break
        }
      }
    }
    if (-not $src -or -not (Test-Path (Join-Path $src $svc.exe))) {
      Write-Error "Source for $($svc.id) not found. Put OpenWebUI in desktop/vendor/OpenWebUI or pass -OpenWebUISource"
      exit 1
    }
    $dest = Join-Path $Dist $svc.resourceDir
    Write-Host "Copy $src -> $dest"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Copy-Item -Recurse -Force $src $dest
    return
  }
  Write-Error "Unknown build.type for $($svc.id): $($build.type)"
  exit 1
}

foreach ($svc in $enabled) {
  if ($svc.kind -eq "process") {
    if ($SkipServices) {
      $exe = Join-Path $Dist "$($svc.resourceDir)\$($svc.exe)"
      if (-not (Test-Path $exe)) {
        Write-Error "SkipServices set but missing $exe"
        exit 1
      }
      Write-Host ("Skip build (reuse dist): {0}" -f $svc.id)
    } else {
      Build-ProcessService $svc
    }
  } elseif ($svc.kind -eq "sqlite") {
    Write-Host ("DB (sqlite): pathRelative={0}" -f $svc.pathRelative)
  } elseif ($svc.kind -eq "external") {
    Write-Host ("External skipped: {0}" -f $svc.id)
  }
}

$buildInfo = [ordered]@{
  product        = $product
  open_webui     = [string]$versions.open_webui
  tools_server   = [string]$versions.tools_server
  tender_agent   = [string]$versions.tender_agent
  dashboard      = [string]$versions.dashboard
  electron_shell = [string]$versions.electron_shell
  services       = @($enabled | ForEach-Object { $_.id })
  git_sha        = $gitSha
  built_at       = (Get-Date).ToUniversalTime().ToString("o")
}
$buildInfoPath = Join-Path $Dist "build-info.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($buildInfoPath, (($buildInfo | ConvertTo-Json -Depth 5) + "`n"), $utf8NoBom)
Write-Host "Wrote $buildInfoPath"

if (-not $SkipElectron) {
  Write-Host ""
  Write-Host "--- Electron start.exe ---"
  Set-Location $Electron

  $pkgPath = Join-Path $Electron "package.json"
  # Rebuild extraResources via node (avoid PowerShell JSON quirks)
  $resJson = @()
  foreach ($svc in $enabled) {
    if ($svc.kind -ne "process") { continue }
    $absFrom = Join-Path $Dist $svc.resourceDir
    if (-not (Test-Path $absFrom)) {
      Write-Error "Dist folder missing for $($svc.id): $absFrom"
      exit 1
    }
    $resJson += @{ from = "../dist/$($svc.resourceDir)"; to = [string]$svc.resourceDir; filter = @("**/*") }
  }
  $resJson += @{ from = "../services.json"; to = "services.json" }
  $resFile = Join-Path $Dist "extra-resources.json"
  [System.IO.File]::WriteAllText($resFile, (($resJson | ConvertTo-Json -Depth 5) + "`n"), $utf8NoBom)

  $nodeScript = Join-Path $Dist "patch-package-json.js"
  @"
const fs = require('fs');
function readJson(p) {
  let t = fs.readFileSync(p, 'utf8');
  if (t.charCodeAt(0) === 0xFEFF) t = t.slice(1);
  return JSON.parse(t);
}
// node script.js pkg extra version -> argv[0]=node argv[1]=script argv[2..]
const pkgPath = process.argv[2];
const extraPath = process.argv[3];
const version = process.argv[4];
const pkg = readJson(pkgPath);
const extra = readJson(extraPath);
pkg.version = version;
pkg.build = pkg.build || {};
pkg.build.extraResources = Array.isArray(extra) ? extra : [extra];
fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
"@ | Set-Content -Path $nodeScript -Encoding ASCII

  node $nodeScript $pkgPath $resFile $product
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  $ebCli = Join-Path $Electron "node_modules\electron-builder\cli.js"
  if (-not (Test-Path $ebCli)) {
    Write-Host "npm install..."
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
  if (-not (Test-Path $exe)) { Write-Error "Missing $exe"; exit 1 }

  Copy-Item -Force $buildInfoPath (Join-Path $unpacked "resources\build-info.json")
  Copy-Item -Force $ServicesPath (Join-Path $unpacked "resources\services.json")

  $size = [math]::Round(((Get-ChildItem $unpacked -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
  Write-Host ""
  Write-Host "Ready: $exe"
  Write-Host "Folder: $unpacked (${size} MB)"
  Write-Host "Data: %LOCALAPPDATA%\AISmartTender\data"
  Write-Host "Toggle services: desktop/services.json (enabled true/false)"

  if ($Zip) {
    $zipPath = Join-Path $Dist "AI-Smart-Tender-Desktop-$product.zip"
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Compress-Archive -Path (Join-Path $unpacked "*") -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host "Zip: $zipPath"
  }
} else {
  Write-Host "Skip Electron pack"
}

Write-Host ""
Write-Host "Done."
