@echo off
chcp 65001 >nul
title Пиби: микрофон ПК — сервер на VPS
cd /d "%~dp0.."

set PY=%CD%\.venv\Scripts\python.exe
if not exist "%PY%" (
    echo Сначала запустите pc\install_client.bat — установка не выполнена.
    pause
    exit /b 1
)

set "URL="
set "TOKEN="
if exist pc\server.cfg (
    set /p URL=<pc\server.cfg
    for /f "usebackq skip=1 delims=" %%a in ("pc\server.cfg") do set "TOKEN=%%a"
)

if not "%URL%"=="" goto run

echo Настройка подключения к серверу Пиби (один раз).
echo Введите только IP сервера, например: 45.83.26.131 (порт 8000 добавится сам).
echo Другой порт — укажите через двоеточие: 45.83.26.131:9000
set /p VPS=IP сервера VPS:
if "%VPS%"=="" (
    echo IP не введён. Запустите снова.
    pause
    exit /b 1
)
echo %VPS%| find ":" >nul
if errorlevel 1 set "VPS=%VPS%:8000"
set /p TOKEN=Токен из config.yaml на сервере (server.token; Enter — если пусто):
set "URL=ws://%VPS%/ws"
> pc\server.cfg echo %URL%
>> pc\server.cfg echo %TOKEN%
echo Сохранил настройки в pc\server.cfg. Удалите файл, чтобы настроить заново.

:run
echo.
echo Подключаюсь к серверу: %URL%
echo Скажите имя ассистента и команду. Для проверки без микрофона:
echo   .venv\Scripts\python pc\assistant_pc.py --url "%URL%" --token "%TOKEN%" --text "который час"
echo.
%PY% pc\assistant_pc.py --url "%URL%" --token "%TOKEN%"
pause
