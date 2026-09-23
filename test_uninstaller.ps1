# Xvoice — uninstaller test.
#
# Run from the repo root:   powershell -ExecutionPolicy Bypass -File .\test_uninstaller.ps1 [-Times 100]
#
# Builds installer.iss renamed to "XvoiceQA" (its own AppId, mutex, install
# folder, startup entry and config folder), then N times: installs it silently,
# does what the app does on first run (writes its startup entry and a config
# folder with a sign-in token), uninstalls silently, and checks nothing is left.
#
# The rename is the point: it exercises exactly the [Registry] / [UninstallDelete]
# lines in installer.iss without ever touching an installed Xvoice, its startup
# entry, or its saved sign-in. The payload is a tiny placeholder, so each cycle
# takes a second or two instead of unpacking the 300 MB exe.

param([int]$Times = 100, [switch]$BuildOnly)
$ErrorActionPreference = "Stop"

$root      = $PSScriptRoot
$work      = Join-Path $env:TEMP "xvoice-uninstaller-test"
$iscc      = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$qaName    = "XvoiceQA"
$qaGuid    = "7E0C2B64-3F1A-4C8E-9B51-0A0000000001"
$runKey    = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$appDir    = Join-Path $env:LOCALAPPDATA "Programs\$qaName"
$cfgDir    = Join-Path $env:LOCALAPPDATA $qaName
$uninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{$qaGuid}_is1"

# ── build the renamed copy of the current installer.iss ────────────────────
$src = Get-Content (Join-Path $root "installer.iss") -Raw
$subs = @(
    @('#define MyAppName "Xvoice"',                    "#define MyAppName `"$qaName`""),
    @('AppId={{5D1C7383-6B2A-44E9-B27E-6BD215D4B9E3}',  "AppId={{$qaGuid}"),
    @('AppMutex=XvoiceSingleInstanceMutex',             'AppMutex=XvoiceQASingleInstanceMutex'),
    @('OutputBaseFilename=XVoiceSetup',                 'OutputBaseFilename=XVoiceQASetup'),
    @('Source: "{#SourcePath}\dist\{#MyAppExeName}"',   "Source: `"$work\payload\{#MyAppExeName}`"")
)
foreach ($s in $subs) {
    $count = ([regex]::Matches($src, [regex]::Escape($s[0]))).Count
    if ($count -ne 1) { throw "expected exactly one '$($s[0])' in installer.iss, found $count" }
    $src = $src.Replace($s[0], $s[1])
}
New-Item -ItemType Directory -Force "$work\payload", "$work\out" | Out-Null
Set-Content "$work\payload\xvoice.exe" "QA placeholder - never executed (Setup runs silently)"
Set-Content "$work\qa_installer.iss" $src -Encoding UTF8
& $iscc /Q /O"$work\out" "$work\qa_installer.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
$setup = Join-Path $work "out\XVoiceQASetup.exe"
if ($BuildOnly) { "built $setup"; return }

# The real Xvoice startup entry, to prove the test never touches it.
$realRunBefore = (Get-ItemProperty $runKey -Name "Xvoice" -ErrorAction SilentlyContinue).Xvoice

function Clear-QaLeftovers {
    Remove-ItemProperty $runKey -Name $qaName -ErrorAction SilentlyContinue
    Remove-Item $cfgDir -Recurse -Force -ErrorAction SilentlyContinue
}
Clear-QaLeftovers

$results = @()
for ($i = 1; $i -le $Times; $i++) {
    Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
    $installed = (Test-Path "$appDir\xvoice.exe") -and (Test-Path $uninstKey)

    # First run: setup_startup() writes the startup entry (unquoted, as main.py
    # does) and CONFIG_DIR fills with the sign-in, the CA bundle and the log.
    New-Item -ItemType Directory -Force $cfgDir | Out-Null
    Set-Content "$cfgDir\config.json" '{"token": "qa-token", "refresh_token": "qa-refresh"}'
    Set-Content "$cfgDir\cacert.pem" "qa"
    Set-Content "$cfgDir\xvoice.log" "qa"
    Set-ItemProperty $runKey -Name $qaName -Value "$appDir\xvoice.exe"

    Start-Process "$appDir\unins000.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
    # The uninstaller re-launches itself from %TEMP% to delete its own files.
    $deadline = (Get-Date).AddSeconds(60)
    while (((Test-Path $appDir) -or (Test-Path $uninstKey)) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }

    $r = [pscustomobject]@{
        installed    = $installed
        appRemoved   = -not (Test-Path $appDir)
        unregistered = -not (Test-Path $uninstKey)
        runRemoved   = $null -eq (Get-ItemProperty $runKey -Name $qaName -ErrorAction SilentlyContinue)
        cfgRemoved   = -not (Test-Path $cfgDir)
    }
    $r | Add-Member ok ($r.installed -and $r.appRemoved -and $r.unregistered -and $r.runRemoved -and $r.cfgRemoved)
    $results += $r
    Clear-QaLeftovers   # every iteration starts from a clean machine
}

$realRunAfter = (Get-ItemProperty $runKey -Name "Xvoice" -ErrorAction SilentlyContinue).Xvoice
$pass = @($results | Where-Object ok).Count
"clean uninstall:        $pass/$Times"
"installed:              $(@($results | Where-Object installed).Count)/$Times"
"app folder removed:     $(@($results | Where-Object appRemoved).Count)/$Times"
"uninstall entry gone:   $(@($results | Where-Object unregistered).Count)/$Times"
"startup entry removed:  $(@($results | Where-Object runRemoved).Count)/$Times"
"config + token removed: $(@($results | Where-Object cfgRemoved).Count)/$Times"
"real Xvoice startup entry untouched: $($realRunBefore -eq $realRunAfter)"
if ($pass -ne $Times) { exit 1 }
