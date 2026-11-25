#!/usr/bin/env python3
"""Пиби — окно управления голосовым ассистентом для Windows (без консоли).

Запуск: двойной клик по pc\\start_app.bat (через pythonw, окно консоли не появляется).
Ядро (микрофон, WebSocket, воспроизведение) — assistant_pc.py, команды ПК — pc_control.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import re
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

PC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PC_DIR))
import app_config as appcfg  # noqa: E402
import updater  # noqa: E402
import installer  # noqa: E402

SETTINGS_PATH = appcfg.DATA_DIR / "app_settings.json"
LEGACY_CFG = appcfg.DATA_DIR / "server.cfg"
ERROR_LOG = appcfg.DATA_DIR / "app_error.log"
DEFAULT_PORT = appcfg.DEFAULT_PORT


# ----------------------------------------------------------- перенаправление вывода
# В pythonw нет консоли: print() и логи уходят в очередь и в окно журнала.

class BusWriter:
    def __init__(self, bus: queue.Queue) -> None:
        self.bus = bus

    def write(self, s: str) -> int:
        if s and s.strip():
            self.bus.put(("log", s.rstrip("\n")))
        return len(s)

    def flush(self) -> None:
        pass

    def reconfigure(self, **_kwargs) -> None:
        pass


class BusHandler(logging.Handler):
    def __init__(self, bus: queue.Queue) -> None:
        super().__init__()
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        self.bus.put(("log", self.format(record)))


BUS: queue.Queue = queue.Queue()
sys.stdout = BusWriter(BUS)
sys.stderr = BusWriter(BUS)
logging.basicConfig(level=logging.INFO, handlers=[BusHandler(BUS)],
                    format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")

import tkinter as tk  # noqa: E402
import customtkinter as ctk  # noqa: E402
import sounddevice as sd  # noqa: E402
from tkinter import messagebox  # noqa: E402

sys.path.insert(0, str(PC_DIR))
import assistant_pc as core  # noqa: E402

# ----------------------------------------------------------- оформление

BG = "#0A0C12"
CARD = "#141821"
CARD_HI = "#1C2230"
TEXT = "#E8EAF2"
MUTED = "#7C859A"
ACCENT = "#7C5CFF"
ACCENT_HOVER = "#6A4BE8"
USER_BUBBLE = "#2A2D52"
DANGER = "#E5484D"

# состояние → (подпись, цвет точки, цвет шара)
STATUS = {
    "offline":    ("Нет связи, переподключаюсь…", "#EF4444", "#3B4252"),
    "stopped":    ("Отключено",                   "#6B7280", "#3B4252"),
    "connecting": ("Подключаюсь…",                "#F59E0B", "#F59E0B"),
    "idle":       ("На связи · скажите «Пиби»",   "#22C55E", "#7C5CFF"),
    "listening":  ("Слушаю вас…",                 "#38BDF8", "#38BDF8"),
    "thinking":   ("Думаю…",                      "#F59E0B", "#F59E0B"),
    "speaking":   ("Отвечаю",                     "#E879F9", "#E879F9"),
}
STATE_MAP = {"idle": "idle", "listening": "listening",
             "thinking": "thinking", "speaking": "speaking"}


def _mix(c1: str, c2: str, t: float) -> str:
    """Смешивает два #rrggbb: t=0 → c1, t=1 → c2 (имитация прозрачности)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ----------------------------------------------------------- настройки

def load_settings() -> dict:
    data: dict = {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        # миграция со старого server.cfg (строка 1 — ws://…, строка 2 — токен)
        try:
            lines = LEGACY_CFG.read_text(encoding="utf-8").splitlines()
            m = re.match(r"ws://([^:/]+)(?::(\d+))?/ws", lines[0].strip())
            if m:
                data = {"host": m.group(1), "port": int(m.group(2) or DEFAULT_PORT),
                        "token": lines[1].strip() if len(lines) > 1 else ""}
        except Exception:
            pass
    data.setdefault("host", appcfg.DEFAULT_HOST)
    data.setdefault("port", DEFAULT_PORT)
    data.setdefault("token", "")
    data.setdefault("device", None)
    data.setdefault("mic_gain", 1.0)        # чувствительность микрофона (множитель)
    data.setdefault("pc_control", True)     # можно ли голосом управлять этим ПК
    return data


def save_settings(data: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except Exception as e:
        logging.getLogger("assistant.app").warning("Не удалось сохранить настройки: %s", e)


def input_devices() -> list[tuple[int, str]]:
    out = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                out.append((i, d.get("name", f"Устройство {i}")))
    except Exception:
        pass
    return out


# ----------------------------------------------------------- фоновый движок

class GuiClient(core.Client):
    """Клиент ядра, который сообщает о событиях и статусе в окно."""

    def __init__(self, args, bus: queue.Queue, gain: float, pc_enabled: bool) -> None:
        super().__init__(args)
        self.bus = bus
        self.gain = gain
        self.pc_enabled = pc_enabled

    def note(self, text: str) -> None:
        self.bus.put(("note", text))

    async def run_once(self) -> None:
        self.bus.put(("status", "connecting"))
        try:
            await super().run_once()
        except Exception:
            self.bus.put(("status", "offline"))
            raise

    async def handle_event(self, msg: dict) -> None:
        self.bus.put(("event", msg))
        await super().handle_event(msg)

    def on_fatal(self, code: int) -> None:
        if code == 4401:     # 4403 объясняет само событие license от сервера
            self.bus.put(("event", {"type": "license", "status": "unknown"}))


class Engine:
    def __init__(self, bus: queue.Queue) -> None:
        self.bus = bus
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task = None
        self.client: GuiClient | None = None
        self.thread: threading.Thread | None = None
        self.gain = 1.0
        self.pc_enabled = True

    def is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, url: str, token: str, device: int | None) -> None:
        if self.is_running():
            return
        args = SimpleNamespace(url=url, token=token, device=device,
                               no_mic=False, text=None)
        self.thread = threading.Thread(target=self._run, args=(args,), daemon=True)
        self.thread.start()

    def _run(self, args) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.client = GuiClient(args, self.bus, self.gain, self.pc_enabled)
        self.task = self.loop.create_task(self.client.run())
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.bus.put(("log", f"Ошибка: {e}"))
        finally:
            self.bus.put(("status", "stopped"))
            self.client = None
            self.loop.close()
            self.loop = None

    def stop(self) -> None:
        if self.loop and self.task:
            self.loop.call_soon_threadsafe(self.task.cancel)

    def set_gain(self, gain: float) -> None:
        self.gain = gain
        if self.client is not None:
            self.client.gain = gain

    def set_pc(self, enabled: bool) -> None:
        self.pc_enabled = enabled
        if self.client is not None:
            self.client.pc_enabled = enabled

    def level(self) -> float:
        c = self.client
        return float(getattr(c, "level", 0.0)) if c is not None else 0.0

    def send_json(self, obj: dict) -> bool:
        if self.loop and self.client and self.client.ws:
            asyncio.run_coroutine_threadsafe(self.client.send_json(obj), self.loop)
            return True
        return False

    def send_text(self, text: str) -> None:
        self.send_json({"type": "text", "text": text})


# ----------------------------------------------------------- «шар» ассистента

class Orb(tk.Canvas):
    """Анимированный шар: цвет — состояние, пульсация и кольца — голос в микрофоне."""

    def __init__(self, master, on_click, bg: str, scale: float = 1.0) -> None:
        self.k = scale
        self.size = int(270 * scale)
        super().__init__(master, width=self.size, height=self.size, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bg = bg
        self.color = "#3B4252"
        self.label = "Старт"
        self.level = 0.0
        self.phase = 0.0
        self.alive = False
        c = self.size / 2
        self.rings = [self.create_oval(0, 0, 0, 0, outline=bg, width=2) for _ in range(3)]
        self.core = self.create_oval(0, 0, 0, 0, fill=self.color, outline="")
        self.shine = self.create_oval(0, 0, 0, 0, fill=self.color, outline="")
        self.caption = self.create_text(
            c, c, text=self.label, fill="#FFFFFF",
            font=("Segoe UI", int(15 * scale), "bold"))
        self.bind("<Button-1>", lambda _e: on_click())
        self._draw()

    def set_state(self, color: str, label: str, alive: bool) -> None:
        self.color, self.label, self.alive = color, label, alive

    def tick(self, level: float) -> None:
        self.phase += 0.07
        self.level += (level - self.level) * 0.35        # сглаживание
        self._draw()

    def _draw(self) -> None:
        k, c = self.k, self.size / 2
        pulse = (math.sin(self.phase) + 1) / 2
        amp = self.level if self.alive else 0.0
        r = (52 + 3 * pulse + 18 * amp) * k
        self.coords(self.core, c - r, c - r, c + r, c + r)
        self.itemconfigure(self.core, fill=self.color)
        # блик: светлая область сверху-слева придаёт объём
        sr = r * 0.62
        self.coords(self.shine, c - r * 0.55 - sr / 2, c - r * 0.55 - sr / 2,
                    c - r * 0.55 + sr / 2, c - r * 0.55 + sr / 2)
        self.itemconfigure(self.shine, fill=_mix(self.color, "#FFFFFF", 0.18))
        for i, ring in enumerate(self.rings):
            rr = r + (12 + i * 16) * k + (6 * pulse * k if self.alive else 0) \
                + 14 * amp * (i + 1) / 3 * k
            fade = (0.50 + 0.17 * i) if self.alive else 0.82
            self.coords(ring, c - rr, c - rr, c + rr, c + rr)
            self.itemconfigure(ring, outline=_mix(self.color, self.bg, fade))
        self.tag_raise(self.caption)
        self.itemconfigure(self.caption, text=self.label)


# ----------------------------------------------------------- окно обучения

class TeachWindow(ctk.CTkToplevel):
    """Правка данных ассистента: профиль, факты, выученные фразы, ссылки на музыку."""

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.app = app
        self.title("Обучить Пиби")
        self.geometry("440x640")
        self.minsize(400, 520)
        self.configure(fg_color=BG)
        self.transient(app)
        self.lists: dict[str, ctk.CTkScrollableFrame] = {}

        self.tabs = ctk.CTkTabview(
            self, fg_color=CARD, segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=CARD_HI, text_color=TEXT)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=16)
        for name in ("Профиль", "Факты", "Фразы", "Музыка"):
            self.tabs.add(name)

        self._tab("Профиль", "Личные данные: имя, город и т. п. Ключ — например user_name.",
                  ["Ключ (например user_name)", "Значение"], "Сохранить",
                  lambda v: self.app.admin("profile_set", {"key": v[0], "value": v[1]}),
                  "profile")
        self._tab("Факты", "Всё, что Пиби должен помнить. Можно и голосом: «запомни что …».",
                  ["Новый факт"], "Запомнить",
                  lambda v: self.app.admin("fact_add", {"text": v[0]}),
                  "facts")
        self._tab("Фразы", "Когда вы говорите фразу слева, Пиби отвечает фразой справа.",
                  ["Что вы говорите", "Что ответить"], "Научить",
                  lambda v: self.app.admin("phrase_set", {"phrase": v[0], "reply": v[1]}),
                  "phrases")
        self._tab("Музыка", "Ссылки на плейлисты и радио (Spotify, VK, YouTube). "
                            "Скажите «включи» + название — откроется на ПК.",
                  ["Название", "https://…"], "Добавить",
                  lambda v: self.app.admin("link_add", {"name": v[0], "url": v[1]}),
                  "links")

        self.refresh()

    def _tab(self, name, hint, placeholders, button, on_add, list_key) -> None:
        tab = self.tabs.tab(name)
        ctk.CTkLabel(tab, text=hint, text_color=MUTED, wraplength=380, justify="left",
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=8, pady=(8, 6))
        entries = []
        for ph in placeholders:
            e = ctk.CTkEntry(tab, placeholder_text=ph, height=38, corner_radius=12,
                             border_width=0, fg_color=CARD_HI, text_color=TEXT)
            e.pack(fill="x", padx=8, pady=4)
            entries.append(e)

        def add() -> None:
            values = [e.get().strip() for e in entries]
            if not all(values):
                messagebox.showwarning("Пиби", "Заполните все поля.", parent=self)
                return
            if on_add(values):
                for e in entries:
                    e.delete(0, "end")

        ctk.CTkButton(tab, text=button, height=38, corner_radius=19, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, font=ctk.CTkFont(size=14, weight="bold"),
                      command=add).pack(fill="x", padx=8, pady=(4, 8))
        frame = ctk.CTkScrollableFrame(tab, fg_color=CARD_HI, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.lists[list_key] = frame

    def refresh(self) -> None:
        data = self.app.admin_data or {}
        self._fill("profile", data.get("profile", []),
                   lambda r: f"{r['key']}: {r['value']}",
                   lambda r: ("profile_delete", {"key": r["key"]}))
        self._fill("facts", data.get("facts", []),
                   lambda r: r["text"],
                   lambda r: ("fact_delete", {"id": r["id"]}))
        self._fill("phrases", data.get("phrases", []),
                   lambda r: f"«{r['phrase']}» → {r['reply']}",
                   lambda r: ("phrase_delete", {"id": r["id"]}))
        self._fill("links", data.get("links", []),
                   lambda r: f"{r['name']}\n{r['url']}",
                   lambda r: ("link_delete", {"id": r["id"]}))

    def _fill(self, key, rows, label, delete) -> None:
        frame = self.lists[key]
        for w in frame.winfo_children():
            w.destroy()
        if not rows:
            ctk.CTkLabel(frame, text="Пока пусто", text_color=MUTED).pack(pady=12)
            return
        for r in rows:
            line = ctk.CTkFrame(frame, fg_color="transparent")
            line.pack(fill="x", pady=3)
            ctk.CTkLabel(line, text=label(r), text_color=TEXT, wraplength=300,
                         justify="left", font=ctk.CTkFont(size=13)
                         ).pack(side="left", fill="x", expand=True, padx=(6, 4))
            action, data = delete(r)
            ctk.CTkButton(line, text="✕", width=30, height=30, corner_radius=15,
                          fg_color=CARD, hover_color=DANGER, text_color=TEXT,
                          command=lambda a=action, d=data: self.app.admin(a, d)
                          ).pack(side="right", padx=4)


# ----------------------------------------------------------- окно

class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Пиби")
        try:
            self.iconbitmap(str(appcfg.asset_path("pibi.ico")))
        except Exception:
            pass
        self.geometry("440x900")
        self.minsize(400, 780)
        self.configure(fg_color=BG)

        self.first_run = not SETTINGS_PATH.exists()
        self.settings = load_settings()
        self.license_shown = False
        self.update_info: dict | None = None
        self.engine = Engine(BUS)
        self.engine.gain = float(self.settings["mic_gain"])
        self.engine.pc_enabled = bool(self.settings["pc_control"])
        self.assistant_name = "Пиби"
        self.log_visible = False
        self.admin_data: dict | None = None
        self.teach_win: TeachWindow | None = None
        self._save_job = None
        self._scale = max(1.0, self.winfo_fpixels("1i") / 96.0)

        self._build_header()
        self._build_stage()
        self._build_controls()
        self._build_chat()
        self._build_input()
        self._build_log()

        self._set_status("stopped")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Ctrl+C/V/X работают и при русской раскладке (см. _ctrl_keys)
        self.bind_all("<Control-KeyPress>", self._ctrl_keys)
        self.after(60, self._poll)
        self.after(40, self._frame)
        if appcfg.FROZEN and not installer.is_installed() and not self.settings.get("skip_install"):
            self.after(300, self._maybe_install)
        elif self.first_run or not self.settings.get("host"):
            self.after(300, self._open_settings)
        threading.Thread(target=self._check_update, daemon=True).start()

    # -- разметка

    def _build_header(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=22, pady=(18, 2))
        self.header_bar = bar
        try:
            from PIL import Image
            self._logo = ctk.CTkImage(Image.open(appcfg.asset_path("pibi_header.png")),
                                      size=(40, 40))
            ctk.CTkLabel(bar, text="", image=self._logo).pack(side="left", padx=(0, 10))
        except Exception:
            pass
        ctk.CTkLabel(bar, text="Пиби", text_color=TEXT,
                     font=ctk.CTkFont(size=28, weight="bold")).pack(side="left")
        ctk.CTkLabel(bar, text="голосовой ассистент", text_color=MUTED,
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=(10, 0), pady=(10, 0))
        ctk.CTkButton(bar, text="⚙", width=40, height=40, corner_radius=20,
                      fg_color=CARD, hover_color=CARD_HI, text_color=TEXT,
                      font=ctk.CTkFont(size=18), command=self._open_settings
                      ).pack(side="right")
        ctk.CTkButton(bar, text="Обучить", width=92, height=40, corner_radius=20,
                      fg_color=CARD, hover_color=CARD_HI, text_color=TEXT,
                      font=ctk.CTkFont(size=13, weight="bold"), command=self._open_teach
                      ).pack(side="right", padx=(0, 8))

    def _build_stage(self) -> None:
        stage = ctk.CTkFrame(self, fg_color=CARD, corner_radius=28)
        stage.pack(fill="x", padx=22, pady=12)

        self.orb = Orb(stage, self._toggle, CARD, self._scale)
        self.orb.pack(pady=(12, 0))

        row = ctk.CTkFrame(stage, fg_color="transparent")
        row.pack(pady=(0, 0))
        self.dot = ctk.CTkLabel(row, text="●", text_color=MUTED, font=ctk.CTkFont(size=14))
        self.dot.pack(side="left", padx=(0, 8))
        self.status_label = ctk.CTkLabel(row, text="", text_color=TEXT,
                                         font=ctk.CTkFont(size=16, weight="bold"))
        self.status_label.pack(side="left")
        self.hint = ctk.CTkLabel(stage, text="", text_color=MUTED,
                                 font=ctk.CTkFont(size=13), wraplength=340, height=20)
        self.hint.pack(pady=(2, 0), padx=16)
        self.server_label = ctk.CTkLabel(stage, text="", text_color=MUTED,
                                         font=ctk.CTkFont(size=11))
        self.server_label.pack(pady=(2, 14))
        self._refresh_server_label()

    def _build_controls(self) -> None:
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=22)
        card.pack(fill="x", padx=22, pady=(0, 12))

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(top, text="Чувствительность микрофона", text_color=TEXT,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.gain_label = ctk.CTkLabel(top, text="", text_color=ACCENT,
                                       font=ctk.CTkFont(size=13, weight="bold"))
        self.gain_label.pack(side="right")

        self.gain_slider = ctk.CTkSlider(
            card, from_=50, to=400, number_of_steps=70, command=self._on_gain,
            height=16, progress_color=ACCENT, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER, fg_color=CARD_HI)
        self.gain_slider.pack(fill="x", padx=18, pady=(8, 6))
        self.gain_slider.set(float(self.settings["mic_gain"]) * 100)
        self.gain_label.configure(text=f"{round(float(self.settings['mic_gain']) * 100)}%")

        self.meter = ctk.CTkProgressBar(card, height=8, corner_radius=4,
                                        fg_color=CARD_HI, progress_color="#22C55E")
        self.meter.pack(fill="x", padx=18, pady=(0, 4))
        self.meter.set(0)
        ctk.CTkLabel(card, text="Говорите: полоса должна заметно прыгать. Не слышит — "
                                "добавьте чувствительность, ловит шум — убавьте.",
                     text_color=MUTED, font=ctk.CTkFont(size=11), wraplength=370,
                     justify="left").pack(anchor="w", padx=18, pady=(0, 8))

        self.pc_var = tk.BooleanVar(value=bool(self.settings["pc_control"]))
        ctk.CTkSwitch(card, text="Управление компьютером голосом", variable=self.pc_var,
                      command=self._on_pc, text_color=TEXT, progress_color=ACCENT,
                      fg_color=CARD_HI, font=ctk.CTkFont(size=13)
                      ).pack(anchor="w", padx=18, pady=(0, 14))

    def _build_chat(self) -> None:
        self.chat = ctk.CTkScrollableFrame(self, fg_color=CARD, corner_radius=24)
        self.chat.pack(fill="both", expand=True, padx=22, pady=(0, 10))
        self._bubble("assistant", "Привет! Нажмите на шар, затем скажите «Пиби» "
                                  "или напишите команду ниже.")

    def _build_input(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 6))
        self.entry = ctk.CTkEntry(row, placeholder_text="Напишите команду…", height=46,
                                  corner_radius=23, border_width=0, fg_color=CARD,
                                  text_color=TEXT, font=ctk.CTkFont(size=14))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _e: self._send())
        ctk.CTkButton(row, text="➤", width=46, height=46, corner_radius=23,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._send).pack(side="left", padx=(8, 0))

    def _build_log(self) -> None:
        self.log_btn = ctk.CTkButton(self, text="Журнал ▸", height=24, fg_color="transparent",
                                     hover_color=BG, text_color=MUTED,
                                     font=ctk.CTkFont(size=12), command=self._toggle_log)
        self.log_btn.pack(pady=(0, 10))
        self.log_box = ctk.CTkTextbox(self, height=160, fg_color=CARD, text_color=MUTED,
                                      font=ctk.CTkFont(family="Consolas", size=11),
                                      corner_radius=16)
        self.log_box.configure(state="disabled")

    # -- сообщения и статус

    def _bubble(self, who: str, text: str) -> None:
        is_user = who == "user"
        wrap = ctk.CTkFrame(self.chat, fg_color="transparent")
        wrap.pack(fill="x", padx=8, pady=5)
        bubble = ctk.CTkFrame(wrap, fg_color=USER_BUBBLE if is_user else CARD_HI,
                              corner_radius=18)
        bubble.pack(anchor="e" if is_user else "w")
        ctk.CTkLabel(bubble, text=text, text_color=TEXT, wraplength=290, justify="left",
                     font=ctk.CTkFont(size=14)).pack(padx=14, pady=9)
        self.after(30, self._scroll_chat)

    def _note(self, text: str) -> None:
        ctk.CTkLabel(self.chat, text=text, text_color=MUTED, wraplength=320,
                     font=ctk.CTkFont(size=12)).pack(pady=(2, 6))
        self.after(30, self._scroll_chat)

    def _scroll_chat(self) -> None:
        try:
            self.chat._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _set_status(self, key: str) -> None:
        text, dot_color, orb_color = STATUS.get(key, STATUS["offline"])
        self.dot.configure(text_color=dot_color)
        self.status_label.configure(text=text)
        if key not in ("listening", "thinking", "speaking", "idle"):
            self.hint.configure(text="")
        running = self.engine.is_running()
        self.orb.set_state(orb_color, "Стоп" if running else "Старт",
                           running and key != "offline")

    def _refresh_server_label(self) -> None:
        host = self.settings.get("host") or "сервер не задан"
        port = self.settings.get("port") or DEFAULT_PORT
        self.server_label.configure(text=f"Сервер: {host}:{port}")

    def _frame(self) -> None:
        """Анимация шара и индикатор уровня микрофона (~25 кадров/с)."""
        level = min(1.0, self.engine.level() * 9) if self.engine.is_running() else 0.0
        self.orb.tick(level)
        self.meter.set(level)
        self.after(40, self._frame)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = BUS.get_nowait()
                if kind == "status":
                    self._set_status(payload)
                elif kind == "event":
                    self._on_event(payload)
                elif kind == "note":
                    self._note(payload)
                elif kind == "log":
                    self._log(payload)
                elif kind == "update":
                    self._show_update(payload)
                elif kind == "apply":
                    updater.apply_and_restart(Path(payload))
                    self._on_close()
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def _on_event(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "config":
            self.assistant_name = msg.get("name", self.assistant_name)
            self._set_status("idle")
            if self._teach_alive():
                self.admin("list", {}, quiet=True)
        elif t == "admin_result":
            if msg.get("ok"):
                self.admin_data = msg.get("data") or {}
                if self._teach_alive():
                    self.teach_win.refresh()
            else:
                messagebox.showwarning("Пиби", msg.get("error", "Не удалось сохранить."))
        elif t == "license":
            self._on_license(msg.get("status", "unknown"))
        elif t == "state":
            key = STATE_MAP.get(msg.get("state"))
            if key:
                self._set_status(key)
        elif t == "wake":
            self._set_status("listening")
            self.hint.configure(text="Говорите команду…")
        elif t == "partial":
            self.hint.configure(text=f"… {msg.get('text', '')}")
        elif t in ("reply", "say"):
            text = (msg.get("text") or "").strip()
            if text:
                self._bubble("assistant", text)
                self.hint.configure(text="")

    # -- подписка и обновления

    LICENSE_TEXT = {
        "unknown": "Ключ не найден или указан неверно.",
        "expired": "Подписка закончилась.",
        "revoked": "Доступ отключён администратором.",
    }

    def _on_license(self, status: str) -> None:
        if self.license_shown:
            return
        self.license_shown = True
        text = self.LICENSE_TEXT.get(status, "Ключ не принят сервером.")
        self._note(text)

        def ask() -> None:
            if appcfg.BOT_LINK:
                if messagebox.askyesno("Пиби", text + "\n\nОткрыть Telegram-бот "
                                       "для покупки или продления?"):
                    import webbrowser
                    webbrowser.open(appcfg.BOT_LINK)
            else:
                messagebox.showinfo("Пиби", text)
            self.license_shown = False
        self.after(200, ask)

    def _maybe_install(self) -> None:
        msg = ("Установить Пиби на этот компьютер?\n\n"
               "Программа скопируется в папку Pibi в вашем профиле, ярлык появится "
               "на рабочем столе и в меню «Пуск». Обновления потом ставятся сами.")
        if messagebox.askyesno("Пиби", msg):
            try:
                installer.install()
            except Exception as e:
                messagebox.showwarning("Пиби", f"Не удалось установить: {e}")
                self._open_settings()
                return
            messagebox.showinfo("Пиби", "Готово. Сейчас откроется установленная версия.")
            installer.launch_installed()
            self._on_close()
        else:
            self.settings["skip_install"] = True
            save_settings(self.settings)
            if self.first_run or not self.settings.get("host"):
                self._open_settings()

    def _check_update(self) -> None:
        info = updater.check()
        if info:
            BUS.put(("update", info))

    def _show_update(self, info: dict) -> None:
        self.update_info = info
        ctk.CTkButton(self.header_bar, text=f"⬆ {info['version']}", width=78, height=40,
                      corner_radius=20, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      font=ctk.CTkFont(size=13, weight="bold"), command=self._do_update
                      ).pack(side="right", padx=(0, 8))
        self._note(f"Доступна новая версия {info['version']} — нажмите ⬆ вверху.")

    def _do_update(self) -> None:
        info = self.update_info or {}
        if not appcfg.FROZEN or not info.get("exe"):
            import webbrowser
            webbrowser.open(info.get("page") or f"https://github.com/{appcfg.REPO}/releases/latest")
            return
        if not messagebox.askyesno("Пиби", f"Обновить до {info['version']}? "
                                   "Приложение перезапустится."):
            return
        self._note("Скачиваю обновление…")

        def work() -> None:
            try:
                path = updater.download(info)
            except Exception as e:
                BUS.put(("note", f"Обновление не удалось: {e}"))
                return
            BUS.put(("apply", str(path)))
        threading.Thread(target=work, daemon=True).start()

    def _log(self, line: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # -- действия

    def _toggle(self) -> None:
        if self.engine.is_running():
            self.engine.stop()
            self._set_status("stopped")
            return
        if not self.settings.get("host"):
            self._open_settings()
            return
        self.engine.start(self._url(), self.settings.get("token", ""),
                          self.settings.get("device"))
        self._set_status("connecting")

    def _start_when_idle(self, tries: int = 0) -> None:
        """Ждём, пока предыдущий поток полностью остановится, и запускаем заново."""
        if self.engine.is_running() and tries < 60:
            self.after(100, lambda: self._start_when_idle(tries + 1))
            return
        self._toggle()

    def _url(self) -> str:
        host = self.settings.get("host", "").strip()
        host = re.sub(r"^wss?://", "", host).split("/")[0]
        if ":" not in host:
            host = f"{host}:{self.settings.get('port') or DEFAULT_PORT}"
        return f"ws://{host}/ws"

    def _schedule_save(self) -> None:
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(500, lambda: save_settings(self.settings))

    def _on_gain(self, value: float) -> None:
        gain = round(float(value) / 100.0, 2)
        self.gain_label.configure(text=f"{round(value)}%")
        self.settings["mic_gain"] = gain
        self.engine.set_gain(gain)
        self._schedule_save()

    def _on_pc(self) -> None:
        enabled = bool(self.pc_var.get())
        self.settings["pc_control"] = enabled
        self.engine.set_pc(enabled)
        self._schedule_save()
        self._note("Управление компьютером " + ("включено." if enabled else "выключено."))

    def _send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._bubble("user", text)
        if self.engine.is_running():
            self.engine.send_text(text)
        else:
            self._bubble("assistant", "Сначала нажмите на шар, чтобы подключиться.")

    def _toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_box.pack(fill="x", padx=22, pady=(0, 12))
            self.log_btn.configure(text="Журнал ▾")
        else:
            self.log_box.pack_forget()
            self.log_btn.configure(text="Журнал ▸")

    def _teach_alive(self) -> bool:
        return self.teach_win is not None and self.teach_win.winfo_exists()

    def _open_teach(self) -> None:
        if self._teach_alive():
            self.teach_win.lift()
            return
        self.teach_win = TeachWindow(self)
        self.admin("list", {})

    def admin(self, action: str, data: dict, quiet: bool = False) -> bool:
        """Отправляет правку данных на сервер; True — если сообщение ушло."""
        ok = self.engine.send_json({"type": "admin", "action": action, "data": data})
        if not ok and not quiet:
            messagebox.showinfo("Пиби", "Сначала нажмите на шар, чтобы подключиться к серверу.",
                                parent=self.teach_win if self._teach_alive() else self)
        return ok

    def _open_settings(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Настройки")
        win.geometry("380x600")
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()

        def field(label: str, value: str, show: str = "") -> ctk.CTkEntry:
            ctk.CTkLabel(win, text=label, text_color=MUTED, font=ctk.CTkFont(size=13)
                         ).pack(anchor="w", padx=26, pady=(12, 2))
            e = ctk.CTkEntry(win, height=40, corner_radius=12, border_width=0,
                             fg_color=CARD, text_color=TEXT, show=show)
            e.insert(0, value)
            e.pack(fill="x", padx=26)
            return e

        token_e = field("Ключ подписки (из Telegram-бота)", self.settings.get("token", ""),
                        show="•")
        if appcfg.BOT_LINK:
            def open_bot() -> None:
                import webbrowser
                webbrowser.open(appcfg.BOT_LINK)
            ctk.CTkButton(win, text="⭐ Купить или продлить ключ", height=34, corner_radius=17,
                          fg_color=CARD, hover_color=CARD_HI, text_color=TEXT,
                          command=open_bot).pack(fill="x", padx=26, pady=(8, 0))
        host_e = field("Сервер (обычно менять не нужно)", self.settings.get("host", ""))
        port_e = field("Порт", str(self.settings.get("port") or DEFAULT_PORT))

        ctk.CTkLabel(win, text="Микрофон", text_color=MUTED, font=ctk.CTkFont(size=13)
                     ).pack(anchor="w", padx=26, pady=(12, 2))
        devices = input_devices()
        labels = ["По умолчанию"] + [f"{i}: {n}" for i, n in devices]
        current = self.settings.get("device")
        selected = "По умолчанию"
        for i, n in devices:
            if i == current:
                selected = f"{i}: {n}"
        dev_menu = ctk.CTkOptionMenu(win, values=labels, fg_color=CARD,
                                     button_color=CARD_HI, button_hover_color=ACCENT,
                                     dropdown_fg_color=CARD, text_color=TEXT)
        dev_menu.set(selected)
        dev_menu.pack(fill="x", padx=26)

        def save() -> None:
            host = host_e.get().strip()
            if not host:
                messagebox.showwarning("Пиби", "Укажите IP сервера.", parent=win)
                return
            try:
                port = int(port_e.get().strip() or DEFAULT_PORT)
            except ValueError:
                messagebox.showwarning("Пиби", "Порт — число, например 8000.", parent=win)
                return
            choice = dev_menu.get()
            device = None if choice == "По умолчанию" else int(choice.split(":")[0])
            self.settings.update(host=host, port=port, token=token_e.get().strip(),
                                 device=device)
            save_settings(self.settings)
            self._refresh_server_label()
            win.destroy()
            if self.engine.is_running():
                self.engine.stop()
            self._start_when_idle()

        ctk.CTkButton(win, text="Сохранить и подключиться", height=44, corner_radius=22,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      font=ctk.CTkFont(size=14, weight="bold"), command=save
                      ).pack(fill="x", padx=26, pady=(22, 0))

    # Windows-коды клавиш: V=86, C=67, X=88 — одинаковы при любой раскладке
    _CTRL_KEYS = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>"}

    def _ctrl_keys(self, event):
        """При русской раскладке Ctrl+V — это Ctrl+М, Tk её не знает.
        Латиницу оставляем стандартным обработчикам, чтобы не вставить дважды."""
        if event.keysym.isascii():
            return None
        virtual = self._CTRL_KEYS.get(event.keycode)
        if virtual and event.widget.winfo_class() in ("Entry", "Text"):
            event.widget.event_generate(virtual)
            return "break"
        return None

    def _on_close(self) -> None:
        save_settings(self.settings)
        self.engine.stop()
        self.after(200, self.destroy)


def main() -> None:
    try:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        App().mainloop()
    except Exception:
        ERROR_LOG.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror("Пиби", f"Ошибка запуска. Подробности: {ERROR_LOG}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
