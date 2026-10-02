# Push tools-server image to Docker Hub.
# Usage:
#   .\scripts\push-tools-image.ps1
#   .\scripts\push-tools-image.ps1 -Tag 1.3.1

param(
    [string]$User = "dim4098",
    [string]$Tag = "1.3.0",
    [string]$Name = "ai-smart-tender-tools"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$image = "${User}/${Name}:${Tag}"
$env:TOOLS_IMAGE = $image

Write-Host "Building $image ..."
docker compose build tools-server

Write-Host "Pushing $image ..."
docker push $image
Write-Host "Done. TOOLS_IMAGE=$image"
