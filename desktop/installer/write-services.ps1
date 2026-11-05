# После установки выставляет enabled у сервисов по выбранным компонентам Inno Setup.
param(
  [Parameter(Mandatory = $true)][string]$Path,
  [switch]$Tools,
  [switch]$Agent,
  [switch]$Dashboard
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Path)) {
  throw "Не найден $Path"
}

$text = [System.IO.File]::ReadAllText($Path)

function Set-EnabledFlag([string]$Source, [string]$Id, [bool]$Enabled) {
  $value = if ($Enabled) { "true" } else { "false" }
  $pattern = '("id"\s*:\s*"' + [regex]::Escape($Id) + '"\s*,\s*"enabled"\s*:\s*)(true|false)'
  $regex = New-Object System.Text.RegularExpressions.Regex($pattern)
  if (-not $regex.IsMatch($Source)) {
    throw "В $Path нет блока id=$Id с полем enabled"
  }
  return $regex.Replace($Source, { param($m) $m.Groups[1].Value + $value }, 1)
}

$text = Set-EnabledFlag $text "tools" ([bool]$Tools)
$text = Set-EnabledFlag $text "tender-agent" ([bool]$Agent)
$text = Set-EnabledFlag $text "dashboard" ([bool]$Dashboard)

$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($Path, $text, $utf8)
