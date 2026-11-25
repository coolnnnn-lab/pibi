"""РќР°СЃС‚СЂРѕР№РєРё СЃР±РѕСЂРєРё РїСЂРёР»РѕР¶РµРЅРёСЏ. РџСЂР°РІРёС‚СЃСЏ РѕРґРёРЅ СЂР°Р· РїРµСЂРµРґ РІС‹РїСѓСЃРєРѕРј."""
import os
import sys
from pathlib import Path

VERSION = "1.0.0"                 # РїРѕРґРЅРёРјР°Р№С‚Рµ РїСЂРё РєР°Р¶РґРѕРј РІС‹РїСѓСЃРєРµ (Рё С‚РµРі vX.Y.Z РЅР° GitHub)
REPO = "coolnnnn-lab/pibi"               # в†ђ РІР°С€_Р»РѕРіРёРЅ/РёРјСЏ_СЂРµРїРѕР·РёС‚РѕСЂРёСЏ РЅР° GitHub
DEFAULT_HOST = "45.83.26.131"     # СЃРµСЂРІРµСЂ РїРѕРґСЃС‚Р°РІР»СЏРµС‚СЃСЏ РїРѕРєСѓРїР°С‚РµР»СЋ СЃР°Рј
DEFAULT_PORT = 8000
BOT_LINK = "https://t.me/Pibi_assistant_bot"                     # в†ђ https://t.me/РёРјСЏ_РІР°С€РµРіРѕ_Р±РѕС‚Р° (РєРЅРѕРїРєР° В«РљСѓРїРёС‚СЊ/РїСЂРѕРґР»РёС‚СЊВ»)

FROZEN = bool(getattr(sys, "frozen", False))   # True РІРЅСѓС‚СЂРё СЃРѕР±СЂР°РЅРЅРѕРіРѕ Pibi.exe

if FROZEN:      # exe СЂР°СЃРїР°РєРѕРІС‹РІР°РµС‚СЃСЏ РІРѕ РІСЂРµРјРµРЅРЅСѓСЋ РїР°РїРєСѓ вЂ” РґР°РЅРЅС‹Рµ С…СЂР°РЅРёРј РѕС‚РґРµР»СЊРЅРѕ
    DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Pibi"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    DATA_DIR = Path(__file__).resolve().parent

# СѓСЃС‚Р°РЅРѕРІР»РµРЅРЅР°СЏ РєРѕРїРёСЏ: %LOCALAPPDATA%\Pibi\Pibi.exe
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Pibi"
INSTALL_EXE = INSTALL_DIR / "Pibi.exe"


def asset_path(name: str) -> Path:
    """Р›РѕРіРѕС‚РёРї Рё РёРєРѕРЅРєР°: РІРЅСѓС‚СЂРё exe Р»РµР¶Р°С‚ РІ РїР°РїРєРµ assets СЂСЏРґРѕРј СЃ РїСЂРѕРіСЂР°РјРјРѕР№."""
    base = Path(sys._MEIPASS) if FROZEN and hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent
    return base / "assets" / name
