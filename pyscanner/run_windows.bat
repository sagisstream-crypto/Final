@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   ShelfScan (Python) — запуск
echo ============================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python не найден.
    echo Установите Python с https://www.python.org/downloads/
    echo При установке ОБЯЗАТЕЛЬНО отметьте галочку "Add python.exe to PATH".
    echo После установки запустите этот файл снова.
    pause
    exit /b 1
)

echo Проверяю зависимости...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo Не получилось установить зависимости. Смотрите ошибку выше.
    pause
    exit /b 1
)

echo.
echo Запускаю сканер. Дашборд откроется на http://localhost:8765
echo Оставьте это окно открытым — сканер работает, пока оно не закрыто.
echo Для остановки закройте окно или нажмите Ctrl+C.
echo.
start "" http://localhost:8765
python app.py
pause
