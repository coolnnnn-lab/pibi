#!/usr/bin/env python3
"""Windows-клиент ассистента: ваш микрофон → сервер → ваши наушники.

Захватывает микрофон (16 кГц, mono, PCM16) и стримит в WebSocket сервера.
Сервер сам делает wake word, VAD, распознавание и мозг; клиент играет
озвучку через устройство вывода по умолчанию (наушники) и выполняет
команды: громкость, медиа-клавиши (пауза/треки), открыть ссылку в браузере.

Запуск (после install_pc.bat):
    python pc/assistant_pc.py                     # микрофон + наушники
    python pc/assistant_pc.py --text "который час"  # одна команда без микрофона
    python pc/assistant_pc.py --no-mic            # режим чата с клавиатуры
    python pc/assistant_pc.py --list-devices      # найти номер микрофона
    python pc/assistant_pc.py --device 1          # конкретный микрофон
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import wave
import webbrowser
from urllib.parse import quote
from io import BytesIO

import httpx
import numpy as np
import sounddevice as sd
import websockets

import pc_control

RATE = 16000            # сервер ожидает 16 кГц
CHUNK_MS = 50           # порция микрофона
CHUNK = RATE * CHUNK_MS // 1000

log = logging.getLogger("assistant.pc")

try:  # Windows-консоль иногда в cp1251 — приводим к UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def download_wav(base: str, url: str) -> tuple[np.ndarray, int] | None:
    """Скачивает WAV озвучки → (int16 данные, частота)."""
    try:
        r = httpx.get(base + url, timeout=30)
        r.raise_for_status()
        with wave.open(BytesIO(r.content), "rb") as w:
            sr = w.getframerate()
            pcm = w.readframes(w.getnframes())
        return np.frombuffer(pcm, dtype="<i2"), sr
    except Exception as e:
        log.warning("Не удалось скачать озвучку %s: %s", url, e)
        return None


def play_pcm(data: np.ndarray, sr: int) -> None:
    """Блокирующее воспроизведение в устройство вывода по умолчанию."""
    sd.play(data, sr)
    sd.wait()


def volume_keys(delta: float) -> None:
    """±delta (1.0 = 100%) → клавиши громкости Windows (шаг 2%)."""
    try:
        import pyautogui
        steps = max(1, round(abs(delta) / 0.02))
        key = "volumeup" if delta > 0 else "volumedown"
        pyautogui.press(key, presses=steps, interval=0.03)
    except Exception as e:
        log.warning("Клавиши громкости недоступны: %s", e)


class Client:
    def __init__(self, args) -> None:
        self.args = args
        self.base = (args.url.replace("ws://", "http://")
                     .replace("wss://", "https://").rsplit("/", 1)[0])
        self.audio_q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.play_q: asyncio.Queue = asyncio.Queue()
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.name = "Пиби"
        self.gain = 1.0            # усиление микрофона (1.0 = без изменений)
        self.level = 0.0           # текущий уровень сигнала 0..1 для индикатора
        self.pc_enabled = True     # разрешено ли серверу управлять этим ПК

    # -------------------------------------------------------------- отправка

    def note(self, text: str) -> None:
        """Короткое системное сообщение (в окне приложения — серая строка в чате)."""
        print(f"⚠ {text}")

    async def send_json(self, obj: dict) -> None:
        if self.ws:
            await self.ws.send(json.dumps(obj, ensure_ascii=False))

    async def audio_sender(self) -> None:
        while True:
            chunk = await self.audio_q.get()
            if self.ws:
                try:
                    await self.ws.send(chunk)
                except Exception:
                    return

    def mic_callback(self, indata, _frames, _time, status) -> None:
        if status:
            log.debug("Микрофон: %s", status)
        gain = getattr(self, "gain", 1.0)
        data = indata
        if abs(gain - 1.0) > 0.01:                # чувствительность микрофона
            data = np.clip(indata.astype(np.int32) * gain, -32768, 32767).astype(np.int16)
        samples = data.astype(np.float32) / 32768.0
        self.level = float(np.sqrt(np.mean(samples * samples)))   # для индикатора
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self.loop
        loop.call_soon_threadsafe(
            self.audio_q.put_nowait, data.tobytes())

    # -------------------------------------------------------------- события

    def on_fatal(self, code: int) -> None:
        print("Сервер не принял ключ доступа (код %s). Проверьте подписку." % code)

    async def handle_event(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "config":
            self.name = msg.get("name", self.name)
            print(f"=== {self.name} на связи. Скажите «{self.name}», затем команду. ===")
        elif t == "state":
            pass  # idle/listening/thinking/speaking — для отладки
        elif t == "partial":
            print(f"  … {msg.get('text')}")
        elif t == "wake":
            print(f"🎤 {self.name}: слушаю")
        elif t in ("reply", "say"):
            text = msg.get("text", "")
            print(f"💬 {text}")
            if msg.get("url"):
                await self.play_q.put(msg["url"])
        elif t == "command":
            await self.handle_command(msg.get("command") or {})
        elif t == "timers":
            pass
        elif t == "widgets":
            pass
        elif t == "pong":
            pass

    async def handle_command(self, cmd: dict) -> None:
        ctype = cmd.get("type")
        if ctype == "stop_say":
            sd.stop()
            while not self.play_q.empty():       # отменяем очередь фраз
                self.play_q.get_nowait()
        elif ctype == "volume":
            await asyncio.to_thread(volume_keys, float(cmd.get("delta") or 0.15))
        elif ctype == "music":
            await self.handle_music(cmd.get("action"), cmd.get("track") or {})
        elif ctype == "pc":
            if not self.pc_enabled:
                self.note("Управление ПК выключено в приложении.")
            else:
                note = await asyncio.to_thread(pc_control.execute, cmd)
                if note:
                    self.note(note)
        else:
            log.info("Неизвестная команда: %s", cmd)

    async def handle_music(self, action: str, track: dict) -> None:
        if action == "play":
            url = track.get("url") or ""
            if url:
                target = url if url.startswith("http") else self.base + url
                webbrowser.open(target)
        elif action in ("pause", "resume"):
            await asyncio.to_thread(_press, "playpause")
        elif action == "next":
            await asyncio.to_thread(_press, "nexttrack")
        elif action == "prev":
            await asyncio.to_thread(_press, "prevtrack")
        elif action == "stop":
            sd.stop()

    # -------------------------------------------------------------- плеер

    async def player_loop(self) -> None:
        while True:
            url = await self.play_q.get()
            wav = await asyncio.to_thread(download_wav, self.base, url)
            if wav is None:
                continue
            data, sr = wav
            await asyncio.to_thread(play_pcm, data, sr)
            await self.send_json({"type": "say_done"})

    # -------------------------------------------------------------- сессия

    async def run_once(self) -> None:
        url = self.args.url + (f"?token={quote(self.args.token)}" if self.args.token else "")
        async with websockets.connect(url, max_size=10 ** 7) as ws:
            self.ws = ws
            reader = asyncio.create_task(self._reader())
            sender = asyncio.create_task(self.audio_sender())
            player = asyncio.create_task(self.player_loop())
            pinger = asyncio.create_task(self._pinger())
            mic_task = None
            stream = None
            if not self.args.no_mic:
                stream = sd.InputStream(
                    samplerate=RATE, channels=1, dtype="int16",
                    blocksize=CHUNK, device=self.args.device,
                    callback=self.mic_callback)
                stream.start()
                print(f"🎙 Микрофон включён ({CHUNK_MS} мс порции). "
                      "Ctrl+C — выход.")
            if self.args.text:
                await asyncio.sleep(0.5)
                await self.send_json({"type": "text", "text": self.args.text})
            elif self.args.no_mic:
                asyncio.create_task(self._stdin_loop())
            try:
                while True:
                    await asyncio.sleep(3600)
            finally:
                for t in (reader, sender, player, pinger, mic_task):
                    if t:
                        t.cancel()
                if stream is not None:
                    stream.stop()
                    stream.close()
                self.ws = None

    async def _reader(self) -> None:
        while True:
            msg = json.loads(await self.ws.recv())
            await self.handle_event(msg)

    async def _pinger(self) -> None:
        while True:
            await asyncio.sleep(20)
            await self.send_json({"type": "ping"})

    async def _stdin_loop(self) -> None:
        print("⌨ Введите текст команды (пустая строка — выход):")
        while True:
            line = await asyncio.to_thread(input, "> ")
            if not line.strip():
                break
            await self.send_json({"type": "text", "text": line.strip()})

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        while True:
            try:
                print(f"Подключаюсь к {self.args.url} …")
                await self.run_once()
            except KeyboardInterrupt:
                print("Выход.")
                return
            except Exception as e:
                code = getattr(getattr(e, "rcvd", None), "code", None)
                if code in (4401, 4403):        # ключ не принят — повторять бессмысленно
                    self.on_fatal(code)
                    return
                log.warning("Связь потеряна (%s); переподключение через 3 с…", e)
                await asyncio.sleep(3)


def _press(key: str) -> None:
    try:
        import pyautogui
        pyautogui.press(key)
    except Exception as e:
        log.warning("Медиа-клавиша %s недоступна: %s", key, e)


def main() -> None:
    ap = argparse.ArgumentParser(description="Windows-клиент ассистента")
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    ap.add_argument("--token", default="")
    ap.add_argument("--device", type=int, default=None,
                    help="индекс микрофона (см. --list-devices)")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--no-mic", action="store_true", help="режим чата с клавиатуры")
    ap.add_argument("--text", help="отправить одну команду и выйти")
    args = ap.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        asyncio.run(Client(args).run())
    except KeyboardInterrupt:
        print("Выход.")


if __name__ == "__main__":
    main()
