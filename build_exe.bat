@echo off
echo ==========================================
echo       Xvoice Desktop App Compiler
echo ==========================================
echo.

echo [1/4] Installing dependencies...
REM Install from requirements.txt so the build never drifts from the app's deps
REM (this is what previously dropped PySide6 and broke the Writing engine + glow).
pip install -r requirements.txt
echo.

echo [2/4] Fixing webrtcvad metadata (PyInstaller compatibility patch)...
FOR /F "tokens=*" %%i IN ('python -c "import site; dirs = site.getsitepackages(); sp = next((d for d in dirs if 'site-packages' in d), dirs[-1]); print(sp)"') DO SET SITEPACKAGES=%%i
SET DISTINFO=%SITEPACKAGES%\webrtcvad-2.0.14.dist-info
IF NOT EXIST "%DISTINFO%" (
    mkdir "%DISTINFO%"
    echo Metadata-Version: 2.1 > "%DISTINFO%\METADATA"
    echo Name: webrtcvad >> "%DISTINFO%\METADATA"
    echo Version: 2.0.14 >> "%DISTINFO%\METADATA"
    echo   Created webrtcvad stub metadata.
) ELSE (
    echo   Stub already exists, skipping.
)
echo.

echo [3/4] Compiling xvoice.exe (single-file)...
pyinstaller --noconfirm xvoice.spec
echo.

echo [4/4] Done!
IF EXIST "dist\xvoice.exe" (
    echo SUCCESS: dist\xvoice.exe is ready to distribute!
) ELSE (
    echo ERROR: Build failed. Check the output above for details.
)
echo.
pause
