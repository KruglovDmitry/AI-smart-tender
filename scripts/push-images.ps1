# Build Docker images of the server variant with tags from desktop/versions.json and push them to Docker Hub.
# Usage:
#   .\scripts\push-images.ps1                         # build + push all
#   .\scripts\push-images.ps1 -NoPush                 # build only
#   .\scripts\push-images.ps1 -Services tools-server  # one service
# Requires: docker login; ../AI-vision next to the repo (or AI_VISION_PATH) for tender-agent.

param(
    [string]$User = "dim4098",
    [string[]]$Services = @("open-webui", "tools-server", "tender-agent", "dashboard"),
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$v = Get-Content (Join-Path $root "desktop\versions.json") -Raw | ConvertFrom-Json
# Shell env wins over .env, so the tags below are used even if .env pins older ones.
$images = [ordered]@{
    "open-webui"   = @("OPEN_WEBUI_IMAGE", "$User/ai-smart-tender-open-webui:$($v.open_webui)")
    "tools-server" = @("TOOLS_IMAGE", "$User/ai-smart-tender-tools:$($v.tools_server)")
    "tender-agent" = @("TENDER_AGENT_IMAGE", "$User/ai-smart-tender-agent:$($v.tender_agent)")
    "dashboard"    = @("DASHBOARD_IMAGE", "$User/ai-smart-tender-dashboard:$($v.dashboard)")
}
$env:OPEN_WEBUI_VERSION = [string]$v.open_webui
foreach ($svc in $images.Keys) { Set-Item "Env:$($images[$svc][0])" $images[$svc][1] }

# docker prints progress to stderr; with Stop, PowerShell 5.1 turns that into an error when output is redirected.
$ErrorActionPreference = "Continue"
Write-Host "Building: $($Services -join ', ')"
docker compose build @Services
if ($LASTEXITCODE -ne 0) { Write-Error "docker compose build failed"; exit 1 }

foreach ($svc in $Services) {
    $image = $images[$svc][1]
    if ($NoPush) { Write-Host "Built $image"; continue }
    Write-Host "Pushing $image ..."
    docker push $image
    if ($LASTEXITCODE -ne 0) { Write-Error "docker push $image failed"; exit 1 }
}
Write-Host "Done."
