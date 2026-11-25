@echo off
chcp 65001 >nul
title Установка клиента Пиби на Windows
echo ==========================================
echo  Пиби: установка клиента на этот ПК
echo  Сервер у вас на VPS — ПК только слушает
echo  микрофон и проигрывает ответ в наушники.
echo ==========================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден. Установите Python 3.10-3.12 x64
    echo с python.org и отметьте "Add Python to PATH", потом запустите снова.
    pause
    exit /b 1
)

cd /d "%~dp0.."

if not exist .venv (
    echo [1/2] Создаю виртуальное окружение...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip

echo [2/2] Ставлю зависимости клиента: микрофон, звук, умение "компьютер"...
pip install -r pc\requirements-windows.txt

echo.
echo ==========================================
echo  Готово! Запуск: pc\start_client.bat
echo  Понадобятся IP сервера и токен из config.yaml
echo  на VPS (строка server.token).
echo ==========================================
pause
