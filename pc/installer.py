"""Установка exe: копия в %LOCALAPPDATA%\\Pibi, ярлыки на рабочем столе и в меню «Пуск»."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import app_config as cfg

PS_TEMPLATE = r"""
$w = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($d in $dirs) {
    $s = $w.CreateShortcut((Join-Path $d 'Пиби.lnk'))
    $s.TargetPath = '__EXE__'
    $s.WorkingDirectory = '__DIR__'
    $s.IconLocation = '__ICO__,0'
    $s.Description = 'Пиби — голосовой ассистент'
    $s.Save()
}
"""


def is_installed() -> bool:
    try:
        return Path(sys.executable).resolve() == cfg.INSTALL_EXE.resolve()
    except Exception:
        return False


def install() -> Path:
    cfg.INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sys.executable, cfg.INSTALL_EXE)
    ico = cfg.INSTALL_DIR / "pibi.ico"
    shutil.copy2(cfg.asset_path("pibi.ico"), ico)
    script = PS_TEMPLATE.replace("__EXE__", str(cfg.INSTALL_EXE)) \
        .replace("__DIR__", str(cfg.INSTALL_DIR)).replace("__ICO__", str(ico))
    ps1 = Path(tempfile.gettempdir()) / "pibi_shortcuts.ps1"
    ps1.write_text(script, encoding="utf-8-sig")     # BOM — иначе кириллица в ярлыке сломается
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                   creationflags=0x08000000, check=False)
    ps1.unlink(missing_ok=True)
    return cfg.INSTALL_EXE


def launch_installed() -> None:
    subprocess.Popen([str(cfg.INSTALL_EXE)], cwd=str(cfg.INSTALL_DIR), close_fds=True)
