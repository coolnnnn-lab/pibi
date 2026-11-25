"""Автообновление через GitHub Releases: проверка, загрузка с проверкой SHA-256,
замена exe и перезапуск. Из исходников (не exe) только открывает страницу релиза."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import app_config as cfg

EXE_NAME = "Pibi.exe"


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def _get(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Pibi-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check() -> dict | None:
    """Новая версия → {version, page, exe, sha}; иначе None. Ошибки сети глушатся."""
    if not cfg.REPO or cfg.REPO.startswith("OWNER/"):
        return None
    try:
        rel = json.loads(_get(f"https://api.github.com/repos/{cfg.REPO}/releases/latest"))
        tag = rel.get("tag_name", "")
        if _vtuple(tag) <= _vtuple(cfg.VERSION):
            return None
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        return {"version": tag.lstrip("v"), "page": rel.get("html_url", ""),
                "exe": assets.get(EXE_NAME), "sha": assets.get(EXE_NAME + ".sha256")}
    except Exception:
        return None


def download(info: dict) -> Path:
    """Скачивает новый exe и сверяет SHA-256. Бросает исключение при несовпадении."""
    if not info.get("exe") or not info.get("sha"):
        raise RuntimeError("В релизе нет Pibi.exe или файла .sha256")
    want = _get(info["sha"]).decode().split()[0].lower()
    folder = cfg.DATA_DIR / "update"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "Pibi-new.exe"
    h = hashlib.sha256()
    req = urllib.request.Request(info["exe"], headers={"User-Agent": "Pibi-updater"})
    with urllib.request.urlopen(req, timeout=60) as r, open(target, "wb") as f:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != want:
        target.unlink(missing_ok=True)
        raise RuntimeError("Контрольная сумма не совпала — обновление отменено")
    return target


def apply_and_restart(new_exe: Path) -> None:
    """Подменяет запущенный exe (после выхода приложения) и запускает заново."""
    current = Path(sys.executable)
    bat = cfg.DATA_DIR / "update" / "apply.bat"
    bat.write_text(
        "@echo off\r\nset n=0\r\n:l\r\ntimeout /t 1 /nobreak >nul\r\n"
        f'move /y "{new_exe}" "{current}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto ok\r\nset /a n+=1\r\nif %n% lss 60 goto l\r\nexit /b 1\r\n"
        f':ok\r\nstart "" "{current}"\r\ndel "%~f0"\r\n', encoding="utf-8")
    flags = 0x08000000 | 0x00000008     # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags, close_fds=True)
