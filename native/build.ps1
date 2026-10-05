# Build the native helpers into native\bin\ (gitignored). Needs zig (https://ziglang.org) on PATH.
#   native\build.ps1
$ErrorActionPreference = 'Stop'
$bin = Join-Path $PSScriptRoot 'bin'
New-Item -ItemType Directory -Force $bin | Out-Null
Push-Location $PSScriptRoot
try {
    zig cc -target x86_64-windows-gnu -shared -O2 -o bin/hook_guard.dll hook_guard.c
    if ($LASTEXITCODE) { throw "hook_guard.dll failed" }
    zig cc -target x86_64-windows-gnu -O2 -o bin/armer.exe armer.c
    if ($LASTEXITCODE) { throw "armer.exe failed" }
} finally { Pop-Location }
Get-ChildItem $bin -Include *.dll, *.exe -Recurse | ForEach-Object { "{0,10:N0}  {1}" -f $_.Length, $_.Name }
