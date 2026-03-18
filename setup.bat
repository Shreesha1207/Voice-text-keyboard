@echo off
echo ==========================================
echo   Voice-to-Text Keyboard Setup
echo ==========================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to install dependencies. Make sure Python is installed and added to PATH.
    pause
    exit /b %errorlevel%
)

echo.
echo [2/3] Configuring to run hidden on startup...
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS_FILE=%STARTUP_DIR%\VoiceTextKeyboard.vbs"
set "MAIN_PATH=%~dp0main.py"

:: Write a clean VBScript that launches pythonw.exe with the script path (handles spaces correctly)
(
    echo Set WshShell = CreateObject^("WScript.Shell"^)
    echo WshShell.CurrentDirectory = "%~dp0"
    echo WshShell.Run "pythonw.exe """ ^& "%MAIN_PATH%" ^& """", 0, False
) > "%VBS_FILE%"

echo.
echo [3/3] Launching the script now in the background...
:: Kill any existing instance first to avoid duplicates
taskkill /F /IM pythonw.exe >nul 2>&1
wscript "%VBS_FILE%"

echo.
echo ==========================================
echo   SUCCESS!
echo ==========================================
echo The script is now running invisibly in the background.
echo You can use the F8 hotkey anywhere.
echo.
echo It has also been added to your Windows Startup folder.
echo It will automatically launch invisibly every time you turn on this PC!
echo.
pause
