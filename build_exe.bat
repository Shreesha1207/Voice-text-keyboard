@echo off
echo ==========================================
echo       Xvoice Desktop App Compiler
echo ==========================================
echo.
echo Installing requirements...
pip install -r requirements.txt
pip install pyinstaller requests

echo.
echo Compiling xvoice into a single silent .exe...
pyinstaller --onefile --noconsole --name xvoice main.py

echo.
echo Compilation Complete!
echo You can find your background executable inside the 'dist' folder:
echo dist\xvoice.exe
echo.
pause
