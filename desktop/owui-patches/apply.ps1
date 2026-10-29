# Правки поверх собранного Open WebUI. Повторный запуск ничего не меняет.
# Использование: & .\owui-patches\apply.ps1 -OpenWebUIDir <папка с OpenWebUI.exe>
param([Parameter(Mandatory = $true)][string]$OpenWebUIDir)
$ErrorActionPreference = "Stop"

# pdf.js 5.5 (предпросмотр PDF в «Знаниях») использует Map.getOrInsertComputed, Math.sumPrecise, Uint8Array.toHex:
# в Chromium 138 (Electron 37) их нет — «Failed to load PDF». Полифилы дописываем в начало pdf.js и его воркера.
$polyfill = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot "pdfjs-polyfills.js")
$marker = "AI Smart Tender: pdf.js polyfills"
$immutable = Join-Path $OpenWebUIDir "_internal\open_webui\frontend\_app\immutable"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$patched = 0
Get-ChildItem $immutable -Recurse -Include *.js, *.mjs | ForEach-Object {
  $text = [System.IO.File]::ReadAllText($_.FullName, $utf8)
  if ($text.Contains("getOrInsertComputed(") -and -not $text.Contains($marker)) {
    [System.IO.File]::WriteAllText($_.FullName, $polyfill + "`n" + $text, $utf8)
    Write-Host "  pdf.js polyfills -> $($_.Name)"
    $patched++
  }
}
Write-Host "OpenWebUI patches: $patched file(s) patched"
