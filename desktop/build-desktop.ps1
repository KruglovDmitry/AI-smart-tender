# Orchestrator: services.json + versions.json -> enabled exes + start.exe
param(
  [switch]$SkipElectron,
  [switch]$SkipServices,
  [switch]$Zip,
  [string]$OpenWebUISource = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-paths.ps1")
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

# Docker-вариант должен собираться из тех же версий, что и desktop.
$compose = Get-Content (Join-Path $Repo "docker-compose.yml") -Raw -Encoding UTF8
$owuiDockerfile = Get-Content (Join-Path $Repo "owui-patches\Dockerfile") -Raw -Encoding UTF8
function Find-Version([string]$Text, [string]$Pattern) {
  if ($Text -match $Pattern) { return $Matches[1] } else { return "?" }
}
$versionChecks = @(
  @("open_webui", (Find-Version $owuiDockerfile "OPEN_WEBUI_VERSION=([\d.]+)"), "owui-patches/Dockerfile"),
  @("tools_server", (Find-Version $compose "ai-smart-tender-tools:([\d.]+)"), "docker-compose.yml TOOLS_IMAGE"),
  @("open_webui", (Find-Version $compose "ai-smart-tender-open-webui:([\d.]+)"), "docker-compose.yml OPEN_WEBUI_IMAGE"),
  @("tender_agent", (Find-Version $compose "ai-smart-tender-agent:([\d.]+)"), "docker-compose.yml TENDER_AGENT_IMAGE"),
  @("dashboard", (Find-Version $compose "ai-smart-tender-dashboard:([\d.]+)"), "docker-compose.yml DASHBOARD_IMAGE")
)
foreach ($c in $versionChecks) {
  $expected = [string]$versions.($c[0])
  if ($expected -ne $c[1]) { Write-Warning ("versions.json {0}={1}, but {2} has {3}" -f $c[0], $expected, $c[2], $c[1]) }
}

Write-Host "=== AI Smart Tender Desktop $product ==="
Write-Host "Build root: $BuildRoot"
Write-Host "Enabled services:"
foreach ($s in $enabled) {
  Write-Host ("  - {0} ({1})" -f $s.id, $s.kind)
}

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
        $c = ([string]$c).Replace("{buildRoot}", $BuildRoot) -replace '/', '\'
        $cand = if ([System.IO.Path]::IsPathRooted($c)) { $c } else { [System.IO.Path]::GetFullPath((Join-Path $Desktop $c)) }
        if ((Test-Path (Join-Path $cand $svc.exe)) -or (Test-Path (Join-Path $cand "OpenWebUI.exe"))) {
          $src = $cand
          break
        }
      }
    }
    if (-not $src -or -not (Test-Path (Join-Path $src $svc.exe))) {
      Write-Error "Source for $($svc.id) not found. Put OpenWebUI in $VendorRoot\OpenWebUI or pass -OpenWebUISource"
      exit 1
    }
    $dest = Join-Path $Dist $svc.resourceDir
    Write-Host "Copy $src -> $dest"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Copy-Item -Recurse -Force $src $dest
    if ($svc.id -eq "openwebui") {
      & (Join-Path $Repo "owui-patches\apply.ps1") -OpenWebUIDir $dest
    }
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

  # electron-builder config = electron/electron-builder.json + absolute paths to artifacts in the build root;
  # package.json is not touched, so the repo stays clean after a build.
  $resJson = @()
  foreach ($svc in $enabled) {
    if ($svc.kind -ne "process") { continue }
    $absFrom = Join-Path $Dist $svc.resourceDir
    if (-not (Test-Path $absFrom)) {
      Write-Error "Dist folder missing for $($svc.id): $absFrom"
      exit 1
    }
    $resJson += @{ from = $absFrom; to = [string]$svc.resourceDir; filter = @("**/*") }
  }
  $resJson += @{ from = $ServicesPath; to = "services.json" }
  # Модель векторов для «Знаний» (main.js ищет её в resources/models): из <build root>\vendor\models или из уже
  # настроенной установки в %LOCALAPPDATA%\AISmartTender\models. Без неё остаётся англоязычная модель.
  $modelsDist = Join-Path $Dist "models"
  foreach ($src in @((Join-Path $VendorRoot "models"), (Join-Path $env:LOCALAPPDATA "AISmartTender\models"))) {
    if (Test-Path (Join-Path $src "paraphrase-multilingual-MiniLM-L12-v2\modules.json")) {
      Write-Host "Embedding model: $src"
      if (Test-Path $modelsDist) { Remove-Item -Recurse -Force $modelsDist }
      Copy-Item -Recurse -Force $src $modelsDist
      break
    }
  }
  if (Test-Path $modelsDist) {
    $resJson += @{ from = $modelsDist; to = "models"; filter = @("**/*") }
  } else {
    Write-Warning "Embedding model not found: Knowledge will use the English all-MiniLM model"
  }
  # Тексты лицензий сторонних компонентов (Open WebUI требует их в дистрибутиве) — «Справка» открывает отсюда.
  $resJson += @{ from = (Join-Path $Desktop "licenses"); to = "licenses"; filter = @("**/*") }
  $resFile = Join-Path $WorkRoot "extra-resources.json"
  [System.IO.File]::WriteAllText($resFile, (($resJson | ConvertTo-Json -Depth 5) + "`n"), $utf8NoBom)

  $ebConfig = Join-Path $WorkRoot "electron-builder.json"
  $nodeScript = Join-Path $WorkRoot "make-eb-config.js"
  @"
const fs = require('fs');
function readJson(p) {
  let t = fs.readFileSync(p, 'utf8');
  if (t.charCodeAt(0) === 0xFEFF) t = t.slice(1);
  return JSON.parse(t);
}
// node script.js base extra version output target -> argv[2..6]
const [base, extraPath, version, output, target] = process.argv.slice(2);
const cfg = readJson(base);
const extra = readJson(extraPath);
cfg.extraResources = Array.isArray(extra) ? extra : [extra];
cfg.directories = Object.assign({}, cfg.directories, { output });
cfg.extraMetadata = Object.assign({}, cfg.extraMetadata, { version });
fs.writeFileSync(target, JSON.stringify(cfg, null, 2) + '\n');
"@ | Set-Content -Path $nodeScript -Encoding ASCII

  node $nodeScript (Join-Path $Electron "electron-builder.json") $resFile $product $ReleaseDir $ebConfig
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

  node $ebCli --win dir --config $ebConfig
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  $unpacked = Join-Path $ReleaseDir "win-unpacked"
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
    $zipPath = Join-Path $ReleasesDir "AI-Smart-Tender-Desktop-$product.zip"
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Compress-Archive -Path (Join-Path $unpacked "*") -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host "Zip: $zipPath"
  }
} else {
  Write-Host "Skip Electron pack"
}

Write-Host ""
Write-Host "Done."
