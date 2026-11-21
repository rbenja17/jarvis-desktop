from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

IN_RATE = 16000
OUT_RATE = 24000
FRAME_SAMPLES = 1280
OUTPUT_IDLE_CLOSE = 4.0


def rms_level(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
    return min(1.0, rms / 6000.0)


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(len(samples) * dst_rate / src_rate)
    x_old = np.linspace(0, 1, num=len(samples), endpoint=False)
    x_new = np.linspace(0, 1, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.int16).tobytes()


class AudioIO:
    def __init__(
        self,
        on_frame: Callable[[bytes], None],
        on_level: Callable[[str, float], None] | None = None,
    ) -> None:
        self.on_frame = on_frame
        self.on_level = on_level or (lambda _src, _lvl: None)
        self._in: sd.RawInputStream | None = None
        self._out: sd.RawOutputStream | None = None
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream_lock = threading.Lock()
        self._last_audio = 0.0
        self._last_write = 0.0
        self._last_frame = 0.0
        self.input_name = ""
        self._hold_until = 0.0
        self._hold_bytes = 0
        self._streaming = False
        self._drained_at = 0.0
        self.gaps = 0
        self._level_tick = 0
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap_output, name="jarvis-audio-out", daemon=True)


    def start(self) -> None:
        self._reaper.start()
        self.open_input()

    @property
    def has_input(self) -> bool:
        return self._in is not None

    def open_input(self) -> bool:
        if self._in is not None:
            return True
        try:
            stream = sd.RawInputStream(
                samplerate=IN_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME_SAMPLES,
                callback=self._in_callback,
            )
            stream.start()
        except Exception as exc:
            log.warning("[ERROR] Sin microfono disponible (%s). Revise Configuracion > Sonido > Entrada.", exc)
            return False
        self._last_frame = time.monotonic()
        self._in = stream
        try:
            self.input_name = str(sd.query_devices(kind="input")["name"])
        except Exception:
            self.input_name = ""
        log.info("[ACTIVE] Microfono: %s", self.input_name)
        return True

    @property
    def output_open(self) -> bool:
        return self._out is not None or self.is_playing()

    def frame_age(self) -> float:
        return time.monotonic() - self._last_frame

    def restart_input(self) -> bool:
        with self._stream_lock:
            old, self._in = self._in, None
            if old is not None:
                try:
                    old.abort()
                    old.close()
                except Exception:
                    log.debug("Error cerrando el microfono colgado", exc_info=True)
            if self._out is None and not self.is_playing():
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    log.debug("Error releyendo los dispositivos de audio", exc_info=True)
        return self.open_input()

    def refresh_devices(self) -> bool:
        with self._stream_lock:
            if self._in is not None or self._out is not None or self.is_playing():
                return False
            sd._terminate()
            sd._initialize()
        return True

    def _in_callback(self, indata, frames, time_info, status) -> None:
        data = bytes(indata)
        self._last_frame = time.monotonic()
        self._level_tick += 1
        if self._level_tick % 2 == 0:
            self.on_level("mic", rms_level(data))
        try:
            self.on_frame(data)
        except Exception:
            log.exception("Error procesando audio del microfono")


    def _ensure_output(self) -> None:
        if self._out is not None and self._out.active:
            return
        self._out = sd.RawOutputStream(
            samplerate=OUT_RATE,
            channels=1,
            dtype="int16",
            callback=self._out_callback,
        )
        self._out.start()

    def _out_callback(self, outdata, frames, time_info, status) -> None:
        n = len(outdata)
        with self._lock:
            if self._hold_until:
                if len(self._buf) < self._hold_bytes and time.monotonic() < self._hold_until:
                    outdata[:] = b"\x00" * n
                    return
                self._hold_until = 0.0
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
            if chunk and not self._buf and self._streaming:
                self._drained_at = time.monotonic()
        outdata[: len(chunk)] = chunk
        if len(chunk) < n:
            outdata[len(chunk) :] = b"\x00" * (n - len(chunk))
        if chunk:
            self._last_audio = time.monotonic()
            self.on_level("out", rms_level(chunk))

    def _reap_output(self) -> None:
        while not self._stop.wait(0.5):
            with self._stream_lock:
                out = self._out
                if out is None or self.is_playing():
                    continue
                if time.monotonic() - max(self._last_audio, self._last_write) > OUTPUT_IDLE_CLOSE:
                    try:
                        out.stop()
                        out.close()
                    except Exception:
                        log.debug("Error cerrando la salida de audio", exc_info=True)
                    self._out = None

    def play(self, pcm: bytes, rate: int = OUT_RATE, prebuffer: float = 0.0) -> None:
        if not pcm:
            return
        pcm = resample(pcm, rate, OUT_RATE)
        now = time.monotonic()
        with self._lock:
            if not self._buf:
                self._streaming = prebuffer > 0
                if prebuffer > 0:
                    if self._drained_at and now - self._drained_at < 0.6:
                        self.gaps += 1
                    self._hold_until = now + prebuffer
                    self._hold_bytes = int(prebuffer * OUT_RATE) * 2
                self._drained_at = 0.0
            self._buf.extend(pcm)
            self._last_write = now
        try:
            with self._stream_lock:
                self._ensure_output()
        except Exception:
            log.exception("[ERROR] No se pudo abrir la salida de audio")

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self._hold_until = 0.0
            self._drained_at = 0.0

    def is_playing(self, tail: float = 0.0) -> bool:
        with self._lock:
            if self._buf:
                return True
        return time.monotonic() - self._last_audio < tail

    def pending_seconds(self) -> float:
        with self._lock:
            return len(self._buf) / 2 / OUT_RATE

    async def wait_done(self, timeout: float = 120.0) -> None:
        import asyncio

        deadline = time.monotonic() + timeout
        while self.is_playing(tail=0.15) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    def close(self) -> None:
        self._stop.set()
        for stream in (self._in, self._out):
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        self._in = None
        self._out = None


def _tone(freqs: list[float], dur: float, volume: float, gap: float = 0.0) -> np.ndarray:
    parts = []
    for f in freqs:
        t = np.arange(int(OUT_RATE * dur)) / OUT_RATE
        env = np.minimum(1.0, np.minimum(t / 0.01, (dur - t) / 0.04))
        wave = np.sin(2 * np.pi * f * t) * 0.7 + np.sin(2 * np.pi * f * 2 * t) * 0.15
        parts.append(wave * env)
        if gap:
            parts.append(np.zeros(int(OUT_RATE * gap)))
    return np.concatenate(parts) * volume


def chime(kind: str, volume: float = 0.35) -> bytes:
    if kind == "activar":
        wave = _tone([659.3, 987.8], 0.09, volume)
    elif kind == "desactivar":
        wave = _tone([880.0, 587.3], 0.08, volume * 0.8)
    elif kind == "alerta":
        wave = _tone([1046.5, 1046.5, 1318.5], 0.11, volume, gap=0.05)
    else:
        wave = _tone([311.1, 233.1], 0.15, volume)
    return (np.clip(wave, -1, 1) * 32767).astype(np.int16).tobytes()
