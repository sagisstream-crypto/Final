@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   ShelfScan (Python) - starting
echo ============================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found.
    echo Install it from https://www.python.org/downloads/
    echo On the first setup screen, check the box "Add python.exe to PATH".
    echo Then run this file again.
    pause
    exit /b 1
)

echo Installing/checking dependencies (aiohttp, truststore)...
python -m pip install --disable-pip-version-check -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo pip install FAILED. See the error above and fix it before continuing.
    pause
    exit /b 1
)

echo Verifying the dependencies actually imported...
python -c "import aiohttp, truststore" 2>nul
if %errorlevel% neq 0 (
    echo.
    echo WARNING: aiohttp or truststore did not import correctly even though
    echo pip install reported success. Telegram/Binance requests may fail
    echo with a certificate error. Try running this command manually and
    echo read the error text:
    echo     python -m pip install --force-reinstall -r requirements.txt
    echo.
    pause
)

echo.
echo Starting the scanner. The dashboard will open at http://localhost:8765
echo Keep this window open - the scanner runs as long as it stays open.
echo Press Ctrl+C to stop, or just close this window.
echo.
start "" http://localhost:8765
python app.py
pause
