$ErrorActionPreference = "Stop"

# Execute this script from the launcher project root on Windows.
# It intentionally builds the existing Python server and existing Hub; it does
# not recreate either component or place a second protocol/backend in the exe.

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Release = Join-Path $Root "release"
$Build = Join-Path $Root "build"
$Python = (Get-Command python -ErrorAction Stop).Source

Write-Host "[1/6] Checking Python and Tkinter..."
& $Python -c "import tkinter; print('Tkinter OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Tkinter is unavailable. Install the official Python for Windows with Tcl/Tk support."
}

Write-Host "[2/6] Installing build dependencies..."
& $Python -m pip install -r (Join-Path $Root "server-requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Could not install Python build dependencies." }

Write-Host "[3/6] Building the existing Python server..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "dist-server"), (Join-Path $Root "build-server")
& $Python -m PyInstaller --noconfirm --clean --onefile --name msn-server --paths $Root --distpath (Join-Path $Root "dist-server") --workpath (Join-Path $Root "build-server") (Join-Path $Root "server\main.py")
if ($LASTEXITCODE -ne 0) { throw "Could not build the Python server executable." }

Write-Host "[4/6] Building the existing Hub..."
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm is required only during packaging. Install Node.js/pnpm on the build machine."
}
Push-Location (Join-Path $Root "hub_source")
try {
    & pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "Could not install Hub dependencies." }
    & pnpm build
    if ($LASTEXITCODE -ne 0) { throw "Could not build the Hub." }
} finally {
    Pop-Location
}

Write-Host "[5/6] Building the launcher executable..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "dist-launcher"), (Join-Path $Root "build-launcher")
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name "MSN Messenger" --distpath (Join-Path $Root "dist-launcher") --workpath (Join-Path $Root "build-launcher") (Join-Path $Root "launcher\launcher.py")
if ($LASTEXITCODE -ne 0) { throw "Could not build the launcher executable." }

Write-Host "[6/6] Assembling portable release..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Release
New-Item -ItemType Directory -Force (Join-Path $Release "client\public"), (Join-Path $Release "server_bundle"), (Join-Path $Release "data"), (Join-Path $Release "logs"), (Join-Path $Release "config") | Out-Null
Copy-Item -Recurse -Force (Join-Path $Root "dist-launcher\MSN Messenger\*") $Release
Copy-Item -Recurse -Force (Join-Path $Root "hub_source\dist\public\*") (Join-Path $Release "client\public")
Copy-Item -Force (Join-Path $Root "dist-server\msn-server.exe") (Join-Path $Release "server_bundle\msn-server.exe")
Copy-Item -Force (Join-Path $Root "README_LAUNCHER.md") $Release
Copy-Item -Force (Join-Path $Root "LAUNCHER_AUDIT.md") $Release

Write-Host "Release ready: $Release\MSN Messenger.exe"
Write-Host "The user only needs to double-click the executable."
