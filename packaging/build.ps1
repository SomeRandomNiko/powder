# Builds the Powder installer, exactly as the release workflow does:
#   native helpers -> tests -> PyInstaller (dist\Powder\) -> third-party licences -> Inno Setup
#   packaging\build.ps1 [-Version 0.1.0] [-SkipTests]
# Needs uv, zig and Inno Setup 6 (ISCC.exe). Output: dist\Powder-Setup-v<version>.exe
param(
    [string]$Version = "0.0.0+dev",
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    if ($Version -notmatch '^\d+\.\d+\.\d+(\+[\w.]+)?$') { throw "version '$Version' is not X.Y.Z" }
    $numeric = ($Version -split '\+')[0]

    Write-Host "== native helpers"
    & "$root\native\build.ps1"

    Write-Host "== dependencies"
    uv sync --group build
    if ($LASTEXITCODE) { throw "uv sync failed" }

    if (-not $SkipTests) {
        Write-Host "== tests"
        uv run pytest -q
        if ($LASTEXITCODE) { throw "tests failed" }
    }

    Write-Host "== version $Version"
    $versionPy = "$root\powder\version.py"
    $original = Get-Content $versionPy -Raw
    $build = "$root\build\packaging"
    New-Item -ItemType Directory -Force $build | Out-Null
    $parts = ($numeric -split '\.') + @('0')
    $tuple = ($parts[0..3] -join ', ')
    @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Powder'),
      StringStruct('FileDescription', 'Powder - keeps Steep playable without Ubisoft''s servers'),
      StringStruct('FileVersion', '$Version'),
      StringStruct('ProductName', 'Powder'),
      StringStruct('ProductVersion', '$Version'),
      StringStruct('LegalCopyright', 'AGPL-3.0-or-later')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content "$build\version_info.txt" -Encoding utf8
    $env:POWDER_VERSION_RESOURCE = "$build\version_info.txt"

    try {
        ($original -replace '__version__ = ".*"', "__version__ = `"$Version`"") | Set-Content $versionPy -NoNewline -Encoding utf8
        Write-Host "== PyInstaller"
        uv run pyinstaller --noconfirm --clean --distpath "$root\dist" --workpath "$build\work" "$root\packaging\powder.spec"
        if ($LASTEXITCODE) { throw "PyInstaller failed" }
    } finally {
        Set-Content $versionPy $original -NoNewline -Encoding utf8          # never leave the build version in git
    }

    Write-Host "== third-party licences"
    $licences = "$root\dist\Powder\THIRD_PARTY_LICENSES.txt"
    uv run pip-licenses --from=mixed --with-license-file --no-license-path --format=plain-vertical --output-file $licences
    if ($LASTEXITCODE) { throw "pip-licenses failed" }
    "`n`nPython`n$(uv run python -c 'import sys; print(sys.version)')`nPython Software Foundation License; see https://docs.python.org/3/license.html" |
        Add-Content $licences
    Copy-Item "$root\LICENSE" "$root\dist\Powder\LICENSE.txt"

    Write-Host "== installer"
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found" }
    & $iscc /Qp "/DAppVersion=$Version" "/DNumericVersion=$numeric" "/DSourceDir=$root\dist\Powder" "/DOutputDir=$root\dist" "$root\packaging\powder.iss"
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
    Get-ChildItem "$root\dist\Powder-Setup-*.exe" | ForEach-Object { "{0,12:N0}  {1}" -f $_.Length, $_.FullName }
} finally {
    Pop-Location
}
