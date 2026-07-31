# Xvoice — Windows build.
#
# Run from the repo root:   powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
#
# Does the whole thing in the right order and refuses to continue when a step
# would silently produce a stale installer — which is exactly what happened
# before: installer.iss pointed at a hardcoded OneDrive path, so rebuilding in
# any other folder shipped an old binary no matter how many times you built.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

Write-Host "Building Xvoice in $root" -ForegroundColor Cyan

# ── 1. Stop any running copy ───────────────────────────────────────────────
# Windows will not overwrite a running .exe, and Xvoice has no window, so it is
# easy to miss in the tray. The installer can now close it via AppMutex, but the
# PyInstaller output must not be locked either.
$running = Get-Process xvoice -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "Stopping $($running.Count) running Xvoice process(es)..." -ForegroundColor Yellow
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# ── 2. Confirm the three version numbers agree ─────────────────────────────
$mainV = (Select-String -Path "main.py"      -Pattern '^__version__ = "([^"]+)"').Matches[0].Groups[1].Value
$specV = (Select-String -Path "xvoice.spec"  -Pattern "^APP_VERSION = '([\d.]+)'").Matches[0].Groups[1].Value
$issV  = (Select-String -Path "installer.iss" -Pattern '#define MyAppVersion "([^"]+)"').Matches[0].Groups[1].Value
if ($mainV -ne $specV -or $mainV -ne $issV) {
    throw "Version mismatch: main.py=$mainV xvoice.spec=$specV installer.iss=$issV"
}
Write-Host "Version $mainV (main.py, xvoice.spec, installer.iss all agree)" -ForegroundColor Green

# ── 3. Clean previous output ───────────────────────────────────────────────
# Leftovers from an older one-dir build in dist\ can otherwise end up shipped.
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# ── 4. Dependencies ────────────────────────────────────────────────────────
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

# ── 5. Build the exe ───────────────────────────────────────────────────────
python -m PyInstaller xvoice.spec --noconfirm

$exe = Join-Path $root "dist\xvoice.exe"
if (-not (Test-Path $exe)) { throw "Build failed: dist\xvoice.exe was not produced" }

$built = (Get-Item $exe).LastWriteTime
if (((Get-Date) - $built).TotalMinutes -gt 10) {
    throw "dist\xvoice.exe is $([int]((Get-Date) - $built).TotalMinutes) minutes old — it was not rebuilt just now"
}
Write-Host "Built dist\xvoice.exe  ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB, $built)" -ForegroundColor Green

# ── 6. Build the installer ─────────────────────────────────────────────────
# installer.iss reads {#SourcePath}\dist\xvoice.exe — relative to the .iss — so
# it always packages the exe produced above, in this folder.
$iscc = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Inno Setup not found — skipping installer." -ForegroundColor Yellow
    Write-Host "Install it from https://jrsoftware.org/isdl.php, then re-run." -ForegroundColor Yellow
    exit 0
}

& $iscc "installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$setup = Get-ChildItem -Recurse -Filter "XVoiceSetup.exe" | Select-Object -First 1
Write-Host ""
Write-Host "Done. Installer: $($setup.FullName)" -ForegroundColor Green
Write-Host ""
Write-Host "Run it, then check the log's first lines for:" -ForegroundColor Cyan
Write-Host "  Xvoice v$mainV starting"
Write-Host "  Executable: ...\xvoice.exe"
Write-Host "  Frozen: True  PID: ..."
Write-Host "If the version or path is not what you expect, the old copy is still the one running."
