# Where build artifacts live: outside the repo, so the sources stay small.
# Override with $env:AST_BUILD_ROOT; default is a sibling folder ..\AI-smart-tender-build.
# Dot-source from build scripts:  . (Join-Path $PSScriptRoot "build-paths.ps1")

$Desktop = $PSScriptRoot
$Repo = Split-Path -Parent $Desktop
$BuildRoot = if ($env:AST_BUILD_ROOT) { $env:AST_BUILD_ROOT } else { Join-Path (Split-Path -Parent $Repo) "AI-smart-tender-build" }
$BuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)

$Dist = Join-Path $BuildRoot "dist"            # service exes, models, build-info.json
$WorkRoot = Join-Path $BuildRoot "work"        # PyInstaller / Nuitka temp
$VenvRoot = Join-Path $BuildRoot "venvs"       # one venv per service
$VendorRoot = Join-Path $BuildRoot "vendor"    # frozen OpenWebUI, embedding models
$ReleaseDir = Join-Path $BuildRoot "release"   # electron-builder output (win-unpacked)
$ReleasesDir = Join-Path $BuildRoot "releases" # zips handed to customers

foreach ($d in @($BuildRoot, $Dist, $WorkRoot, $VenvRoot, $VendorRoot, $ReleasesDir)) {
  New-Item -ItemType Directory -Force -Path $d | Out-Null
}
