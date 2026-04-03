@echo off
echo ==========================================
echo       Xvoice Desktop App Compiler
echo ==========================================
echo.

echo [1/4] Installing dependencies...
pip install pyaudio pynput webrtcvad-wheels python-dotenv requests pyinstaller
echo.

echo [2/4] Fixing webrtcvad metadata (PyInstaller compatibility patch)...
FOR /F "tokens=*" %%i IN ('python -c "import site; print(site.getsitepackages()[0])"') DO SET SITEPACKAGES=%%i
SET DISTINFO=%SITEPACKAGES%\Lib\site-packages\webrtcvad-2.0.10.dist-info
IF NOT EXIST "%DISTINFO%" (
    mkdir "%DISTINFO%"
    echo Metadata-Version: 2.1 > "%DISTINFO%\METADATA"
    echo Name: webrtcvad >> "%DISTINFO%\METADATA"
    echo Version: 2.0.10 >> "%DISTINFO%\METADATA"
    echo   Created webrtcvad stub metadata.
) ELSE (
    echo   Stub already exists, skipping.
)
echo.

echo [3/4] Compiling xvoice.exe silently...
pyinstaller xvoice.spec
echo.

echo [4/4] Done!
IF EXIST "dist\xvoice.exe" (
    echo SUCCESS: dist\xvoice.exe is ready to distribute!
) ELSE (
    echo ERROR: Build failed. Check the output above for details.
)
echo.
pause
