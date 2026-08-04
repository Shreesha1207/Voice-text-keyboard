@echo off
setlocal
echo ==========================================
echo       Xvoice Desktop App Compiler
echo ==========================================
echo.

cd /d "%~dp0"

echo [1/5] Stopping any running Xvoice...
REM Windows will not overwrite a running .exe, and Xvoice lives in the tray with
REM no window, so it is easy to miss that a copy is still up. Building over it
REM leaves the old binary in place with no error.
taskkill /IM xvoice.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul
echo.

echo [2/5] Installing dependencies...
REM Install from requirements.txt so the build never drifts from the app's deps
REM (this is what previously dropped PySide6 and broke the Writing engine + glow).
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency install failed. Stopping.
    pause
    exit /b 1
)
echo.

REM The webrtcvad metadata stub that used to live here is gone: VAD was removed
REM from the app, so the step was fabricating dist-info for a package nothing
REM imports any more.

echo [3/5] Clearing previous build output...
REM Leftovers here are exactly how a stale build survives a rebuild:
REM   build\   PyInstaller's analysis cache
REM   dist\    may hold an exe from an older one-dir build
REM   Output\  held XVoiceWritingSetup.exe, committed to git back in July. The
REM            current installer.iss writes XVoiceSetup.exe, so it never
REM            overwrote that file - and installing the older, more
REM            official-looking name made weeks of rebuilds appear to do nothing.
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Output rmdir /s /q Output
echo.

echo [4/5] Compiling xvoice.exe (single-file)...
pyinstaller --noconfirm --clean xvoice.spec
echo.

echo [5/5] Verifying...
if not exist "dist\xvoice.exe" (
    echo ERROR: Build failed - dist\xvoice.exe was not produced.
    pause
    exit /b 1
)

REM Confirm the exe was written today, not recovered from somewhere stale.
forfiles /p dist /m xvoice.exe /d 0 >nul 2>&1
if errorlevel 1 (
    echo ERROR: dist\xvoice.exe is not from today - it was not rebuilt.
    pause
    exit /b 1
)

echo SUCCESS: dist\xvoice.exe is ready.
echo.
echo Next: compile installer.iss with Inno Setup. It produces
echo    Output\XVoiceSetup.exe
echo That is the ONLY installer that should be in Output\. Any other name there
echo is stale and must not be run.
echo.
pause
