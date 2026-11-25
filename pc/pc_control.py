"""Управление Windows-ПК по командам ассистента.

Сервер присылает только ИМЯ действия и его параметры, например
{"type": "pc", "action": "open", "app": "браузер"}. Исполнитель знает конечный
список действий и приложений и не запускает произвольные строки, пришедшие
по сети, — даже если токен сервера окажется скомпрометирован.
"""
from __future__ import annotations

import datetime as dt
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("assistant.pc.control")

IS_WINDOWS = sys.platform == "win32"

# каноничное имя приложения → команда запуска (имена совпадают с серверными)
APPS: dict[str, str] = {
    "браузер": "start chrome",
    "блокнот": "start notepad",
    "калькулятор": "start calc",
    "проводник": "start explorer",
    "терминал": "start cmd",
    "paint": "start mspaint",
    "диспетчер задач": "start taskmgr",
    "настройки": "start ms-settings:",
    "почта": "start outlook",
    "word": "start winword",
    "excel": "start excel",
    "powerpoint": "start powerpnt",
    "spotify": "start spotify:",
    "telegram": "start telegram",
    "discord": "start discord",
    "steam": "start steam",
    "музыку Ютуб": "start https://music.youtube.com",
}

# имя → процесс для taskkill
CLOSABLE: dict[str, str] = {
    "браузер": "chrome.exe",
    "блокнот": "notepad.exe",
    "paint": "mspaint.exe",
    "терминал": "cmd.exe",
    "telegram": "telegram.exe",
    "discord": "discord.exe",
    "spotify": "spotify.exe",
    "steam": "steam.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
}


def _run(command: str) -> None:
    subprocess.Popen(command, shell=True)


def _open(cmd: dict) -> str:
    app = cmd.get("app")
    command = APPS.get(app)
    if not command:
        return f"Не знаю приложение «{app}»."
    _run(command)
    return ""


def _close(cmd: dict) -> str:
    app = cmd.get("app")
    exe = CLOSABLE.get(app)
    if not exe:
        return f"Не умею закрывать «{app}»."
    r = subprocess.run(["taskkill", "/IM", exe, "/F", "/T"],
                       capture_output=True, timeout=10)
    return "" if r.returncode == 0 else f"Не нашла запущенное «{app}»."


def _set_volume(cmd: dict) -> str:
    pct = max(0, min(100, int(cmd.get("level", 50))))
    try:
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetSpeakers()
        volume = getattr(dev, "EndpointVolume", None)
        if volume is None:                      # старые версии pycaw
            iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(iface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(pct / 100.0, None)
        return ""
    except Exception as e:
        log.info("pycaw недоступен (%s), задаю громкость клавишами", e)
    import pyautogui
    pyautogui.press("volumedown", presses=50, interval=0.005)   # в ноль
    pyautogui.press("volumeup", presses=round(pct / 2), interval=0.005)
    return ""


def _mute(_cmd: dict) -> str:
    import pyautogui
    pyautogui.press("volumemute")
    return ""


def _screenshot(_cmd: dict) -> str:
    from PIL import ImageGrab
    folder = Path.home() / "Desktop" / "Скриншоты Пиби"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"скриншот_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    subprocess.Popen(["explorer", "/select,", str(path)])
    return ""


def _type(cmd: dict) -> str:
    text = str(cmd.get("text") or "")[:500]
    if not text.strip():
        return "Нечего печатать."
    import pyautogui
    import pyperclip
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    return ""


def _minimize(_cmd: dict) -> str:
    import pyautogui
    pyautogui.hotkey("win", "d")
    return ""


def _lock(_cmd: dict) -> str:
    _run("rundll32.exe user32.dll,LockWorkStation")
    return ""


def _shutdown(_cmd: dict) -> str:
    _run("shutdown /s /t 60")       # минута на отмену: «отмени выключение»
    return ""


def _reboot(_cmd: dict) -> str:
    _run("shutdown /r /t 60")
    return ""


def _shutdown_cancel(_cmd: dict) -> str:
    r = subprocess.run(["shutdown", "/a"], capture_output=True, timeout=10)
    return "" if r.returncode == 0 else "Нечего отменять."


ACTIONS = {
    "open": _open, "close": _close, "volume_set": _set_volume, "mute": _mute,
    "unmute": _mute, "screenshot": _screenshot, "type": _type,
    "minimize": _minimize, "lock": _lock, "shutdown": _shutdown,
    "reboot": _reboot, "shutdown_cancel": _shutdown_cancel,
}


def execute(cmd: dict) -> str:
    """Выполняет действие. Возвращает "" при успехе, иначе короткое пояснение."""
    action = cmd.get("action")
    handler = ACTIONS.get(action)
    if handler is None:
        return f"Неизвестное действие «{action}»."
    if not IS_WINDOWS:
        return "Управление ПК работает только в Windows."
    try:
        return handler(cmd)
    except Exception as e:
        log.error("PC-действие %s: %s", action, e)
        return f"Не получилось выполнить «{action}»: {e}"
