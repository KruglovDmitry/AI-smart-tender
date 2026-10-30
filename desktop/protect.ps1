# Защита наших пакетов в сборке: код компилируется Nuitka в .pyd, а в PYZ PyInstaller'а его нет
# (байткод PYZ легко декомпилировать, а проверку лицензии — вырезать).
# Подключается из build_*_exe.ps1:  . (Join-Path $Desktop "protect.ps1")

$Licensing = Join-Path (Split-Path -Parent $PSScriptRoot) "licensing"

function Install-Nuitka([string]$Py) {
  # stderr нативной команды при ErrorActionPreference=Stop роняет скрипт — проверяем через find_spec.
  $found = & $Py -c "import importlib.util as u; print(bool(u.find_spec('nuitka')))"
  if ($found -eq "True") { return }
  Write-Host "Installing Nuitka into $(Split-Path -Parent (Split-Path -Parent $Py)) ..."
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  & $Py -m pip install --disable-pip-version-check -q nuitka zstandard ordered-set
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prevEap
  if ($code -ne 0) { Write-Error "pip install nuitka failed"; exit 1 }
}

# PyInstaller по спецификации, из которой выкинуты наши пакеты: зависимости он находит по исходникам,
# но сами пакеты в сборку не кладёт — их заменят .pyd из Compile-Packages.
function Invoke-ProtectedPyInstaller([string]$Py, [string]$Name, [string]$Out, [string]$Work, [string[]]$Packages, [string[]]$MakespecArgs) {
  New-Item -ItemType Directory -Force -Path $Work | Out-Null
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  & $Py -m PyInstaller.utils.cliutils.makespec --name $Name --specpath $Work @MakespecArgs
  $ErrorActionPreference = $prevEap
  if ($LASTEXITCODE -ne 0) { Write-Error "makespec $Name failed"; exit 1 }
  $spec = Join-Path $Work "$Name.spec"
  $prefixes = ($Packages | ForEach-Object { "'$_'" }) -join ", "
  $filter = "_protected = ($prefixes,)`n" +
    "a.pure = [e for e in a.pure if not any(e[0] == p or e[0].startswith(p + '.') for p in _protected)]`n" +
    "pyz = PYZ(a.pure)"
  $text = (Get-Content $spec -Raw -Encoding UTF8) -replace "pyz = PYZ\(a\.pure\)", $filter
  Set-Content -Path $spec -Value $text -Encoding UTF8
  $ErrorActionPreference = "Continue"
  & $Py -m PyInstaller --noconfirm --clean --distpath $Out --workpath $Work $spec
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prevEap
  if ($code -ne 0) { Write-Error "PyInstaller $Name failed: $code"; exit $code }
}

# Короткий кэш Nuitka: длинный путь Codex AppData ломает MinGW (нет psdk_inc/intrin-impl.h).
function Ensure-NuitkaToolchain {
  $cache = "C:\nkit"
  New-Item -ItemType Directory -Force -Path $cache | Out-Null
  $env:NUITKA_CACHE_DIR = $cache
  # Сбрасываем CC, если указывал на битый gcc из кэша Codex.
  if ($env:CC -and ($env:CC -match "OPENAI|Codex|LOCALC~1")) {
    Remove-Item Env:CC -ErrorAction SilentlyContinue
  }
  Write-Host "Nuitka toolchain: cache $cache (short path)"
}

# Каждый пакет — в один .pyd рядом с exe (в _internal); файлы данных пакета (не .py) — следом, по тем же путям.
function Compile-Packages([string]$Py, [string]$DestInternal, [string]$Work, [string[]]$PackageDirs) {
  Install-Nuitka $Py
  Ensure-NuitkaToolchain
  foreach ($dir in $PackageDirs) {
    $name = Split-Path -Leaf $dir
    $build = Join-Path $Work "nuitka-$name"
    if (Test-Path $build) { Remove-Item -Recurse -Force $build }
    Write-Host "Nuitka: compiling $name ..."
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Py -m nuitka --module $dir "--include-package=$name" "--output-dir=$build" `
      --mingw64 --assume-yes-for-downloads --remove-output --no-pyi-file --quiet
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($code -ne 0) { Write-Error "Nuitka $name failed: $code"; exit $code }
    $pyd = Get-ChildItem $build -Filter "$name.*.pyd" | Select-Object -First 1
    if (-not $pyd) { Write-Error "Nuitka did not produce $name.pyd"; exit 1 }
    $pkgDst = Join-Path $DestInternal $name
    if (Test-Path $pkgDst) { Remove-Item -Recurse -Force $pkgDst }
    Copy-Item $pyd.FullName $DestInternal -Force
    Get-ChildItem $dir -Recurse -File |
      Where-Object { $_.Extension -notin ".py", ".pyc" -and $_.FullName -notmatch "\\__pycache__\\" } |
      ForEach-Object {
        $target = Join-Path $pkgDst $_.FullName.Substring($dir.Length).TrimStart("\")
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item $_.FullName $target -Force
      }
  }
  $left = Get-ChildItem $DestInternal -Recurse -Filter "*.py" | Where-Object {
    $rel = $_.FullName.Substring($DestInternal.Length + 1)
    $PackageDirs | Where-Object { $rel -like "$(Split-Path -Leaf $_)\*" }
  }
  if ($left) { Write-Error "Source files left in the build: $($left.FullName -join ', ')"; exit 1 }
}
