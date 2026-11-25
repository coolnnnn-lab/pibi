@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
    echo Сначала запустите pc\install_client.bat — установка не выполнена.
    pause
    exit /b 1
)
rem pythonw — без окна консоли: приложение открывается как обычная программа
start "" ".venv\Scripts\pythonw.exe" "pc\pibi_app.py"
