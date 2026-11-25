"""Настройки сборки приложения. Правится один раз перед выпуском."""
import os
import sys
from pathlib import Path

VERSION = "1.0.0"                 # поднимайте при каждом выпуске (и тег vX.Y.Z на GitHub)
REPO = "coolnnnn-lab/pibi"               # ← ваш_логин/имя_репозитория на GitHub
DEFAULT_HOST = "45.83.26.131"     # сервер подставляется покупателю сам
DEFAULT_PORT = 8000
BOT_LINK = ""                     # ← "https://t.me/Pibi_assistant_bot" (кнопка «Купить/продлить»)

FROZEN = bool(getattr(sys, "frozen", False))   # True внутри собранного Pibi.exe

if FROZEN:      # exe распаковывается во временную папку — данные храним отдельно
    DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Pibi"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    DATA_DIR = Path(__file__).resolve().parent
