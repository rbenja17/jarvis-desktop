from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import queue
import threading
import time
import wave
from collections import deque

from jarvis.audio import ducking, tts
from jarvis.audio.io import IN_RATE, AudioIO, chime, rms_level
from jarvis.brain.text import QuotaExceeded, TextBrain
from jarvis.config import env, get_config, load_config
from jarvis.services import services, ui
from jarvis.tools import confirm, history, registry

log = logging.getLogger(__name__)

LIVE_RETRY_AFTER_QUOTA = 30 * 60
MIC_CHECK_SECONDS = 2.0
MIC_STALL_SECONDS = 3.0
RESUME_GAP_SECONDS = 30.0
DEFAULT_MIC_CHECK_SECONDS = 5.0
MUSIC_TOOLS = frozenset(
    {"spotify_reproducir", "spotify_volumen", "spotify_control", "control_multimedia", "volumen_app",
     "fijar_volumen", "cambiar_volumen", "silenciar"}
)


def mic_problem(frame_age: float, wall_gap: float) -> str | None:
    if wall_gap > MIC_CHECK_SECONDS + RESUME_GAP_SECONDS:
        return "la PC volvio de la suspension"
    if frame_age > MIC_STALL_SECONDS:
        return "el microfono dejo de enviar audio"
    return None


class Engine:
    def __init__(self) -> None:
        self.cfg = get_config()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mode = "idle"
        self.state = "reposo"
        self.muted = False
        self.audio: AudioIO | None = None
        self.wake = None
        self.hotkey = None
        self.telegram = None
        self.http = None
        self.scheduler = None
        self.live = None
        self.client = None
        self.text_brain: TextBrain | None = None
        self.recent: deque[str] = deque(maxlen=12)
        self.ducker = ducking.Ducker()

        key = env("GEMINI_API_KEY")
        if key:
            from jarvis.brain.text import make_client

            self.client = make_client(key)
            self.text_brain = TextBrain(self.client)

        self._stop_event: asyncio.Event | None = None
        self._mic_q: asyncio.Queue | None = None
        self._wake_q: queue.Queue = queue.Queue(maxsize=64)
        self._closing = threading.Event()
        self._session_task: asyncio.Task | None = None
        self._fallback_end = False
        self._live_blocked_until = 0.0
        self._announce_lock: asyncio.Lock | None = None
        self._background: set[asyncio.Task] = set()
        self._capture_buf: list[bytes] = []


    def run_forever(self) -> None:
        try:
            import pythoncom

            pythoncom.CoInitialize()
        except Exception:
            pass
        asyncio.run(self.main())

    async def main(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._announce_lock = asyncio.Lock()
        self._mic_q = asyncio.Queue(maxsize=600)
        services.loop = self.loop
        services.engine = self
        services.text_brain = self.text_brain

        registry.load_all()
        if self.client is None:
            log.warning("[ERROR] Falta GEMINI_API_KEY en .env: JARVIS no podra conversar")
        try:
            history.prune()
            self.recent = deque(history.recent(12), maxlen=12)
            await registry.in_worker(ducking.restore_leftovers)
        except Exception:
            log.exception("No se pudo cargar el historial o restaurar el volumen")
        await self._start_audio()
        self._start_hotkey()
        await self._start_channels()

        from jarvis.scheduler import Scheduler

        self.scheduler = Scheduler(self)
        self.spawn(self.scheduler.run())
        self.set_state("reposo")
        log.info("[ACTIVE] JARVIS listo")
        try:
            await self._stop_event.wait()
        finally:
            await self._shutdown()

    def stop(self) -> None:
        if self.loop is not None and self._stop_event is not None:
            self.loop.call_soon_threadsafe(self._stop_event.set)

    async def _shutdown(self) -> None:
        log.info("Cerrando JARVIS")
        self._closing.set()
        self.end_session_now()
        for task in list(self._background):
            task.cancel()
        if self.telegram is not None:
            await self.telegram.stop()
        if self.http is not None:
            await self.http.stop()
        if self.hotkey is not None:
            self.hotkey.stop()
        if self.audio is not None:
            self.audio.close()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def set_state(self, state: str) -> None:
        if self.muted and state == "reposo":
            state = "silenciado"
        self.state = state
        ui("state", state)


    async def _start_audio(self) -> None:
        self.audio = AudioIO(self._on_mic_frame, self._on_level)
        self.audio.start()
        if not self.audio.has_input:
            self.spawn(self._wait_for_microphone())
        self.spawn(self._mic_watchdog())
        if self.cfg.get("activacion.wake_word", True):
            try:
                self.wake = await asyncio.to_thread(self._make_wake)
                threading.Thread(target=self._wake_loop, name="jarvis-wake", daemon=True).start()
                log.info("[ACTIVE] Palabra de activacion: hey jarvis")
            except Exception:
                log.exception("[ERROR] No se pudo cargar la palabra de activacion")

    def _make_wake(self):
        from jarvis.audio import voice_training
        from jarvis.audio.wakeword import WakeWord

        verifier = voice_training.verifier_path()
        return WakeWord(
            self.cfg.get("activacion.modelo_wake_word", "hey_jarvis"),
            float(self.cfg.get("activacion.umbral", 0.38)),
            float(self.cfg.get("activacion.filtro_voz", 0.5)),
            str(verifier) if verifier.exists() else None,
            float(self.cfg.get("activacion.umbral_con_su_voz", 0.60)),
            int(self.cfg.get("activacion.bloques_consecutivos", 2)),
        )

    async def reload_wakeword(self) -> None:
        if not self.cfg.get("activacion.wake_word", True):
            self.wake = None
            return
        first_time = self.wake is None
        self.wake = await asyncio.to_thread(self._make_wake)
        if first_time:
            threading.Thread(target=self._wake_loop, name="jarvis-wake", daemon=True).start()

    async def reload_settings(self) -> None:
        self.cfg = load_config(reload_env=True)
        key = env("GEMINI_API_KEY")
        if key:
            from jarvis.brain.text import make_client

            self.client = make_client(key)
            self.text_brain = TextBrain(self.client)
            services.text_brain = self.text_brain
            self._live_blocked_until = 0.0
        else:
            self.client = None
            self.text_brain = None
            services.text_brain = None

        if self.hotkey is not None:
            try:
                self.hotkey.stop()
            except Exception:
                log.debug("No se pudo detener el atajo anterior", exc_info=True)
            self.hotkey = None
        self._start_hotkey()

        if self.telegram is not None:
            try:
                await self.telegram.stop()
            except Exception:
                log.debug("No se pudo detener Telegram anterior", exc_info=True)
            self.telegram = None
            services.telegram = None
        if self.http is not None:
            try:
                await self.http.stop()
            except Exception:
                log.debug("No se pudo detener HTTP anterior", exc_info=True)
            self.http = None
        await self._start_channels()

        try:
            await self.reload_wakeword()
        except Exception:
            log.exception("[ERROR] No se pudo recargar la palabra de activacion")
        log.info("[ACTIVE] Configuracion recargada")


    def begin_capture(self) -> bool:
        if self.mode != "idle" or self.audio is None or not self.audio.has_input:
            return False
        self._capture_buf = []
        self.mode = "captura"
        return True

    def end_capture(self) -> bytes:
        data = b"".join(getattr(self, "_capture_buf", []))
        self._capture_buf = []
        if self.mode == "captura":
            self.mode = "idle"
            if self.wake is not None:
                self.wake.reset()
        return data

    async def _wait_for_microphone(self) -> None:
        ui("notify", "JARVIS", "No detecto ningún micrófono habilitado. Funciono por texto y Telegram hasta que lo active.")
        while not self.audio.has_input:
            await asyncio.sleep(30)
            if self.mode != "idle":
                continue
            if await asyncio.to_thread(self.audio.refresh_devices) and self.audio.open_input():
                ui("notify", "JARVIS", "Micrófono detectado. Ya puede decir «hey jarvis».")

    async def _mic_watchdog(self) -> None:
        wall = time.time()
        failures = 0
        next_try = 0.0
        next_default_check = time.monotonic() + DEFAULT_MIC_CHECK_SECONDS
        while not self._closing.is_set():
            await asyncio.sleep(MIC_CHECK_SECONDS)
            now_wall = time.time()
            gap, wall = now_wall - wall, now_wall
            audio = self.audio
            if audio is None or not audio.has_input:
                continue
            reason = mic_problem(audio.frame_age(), gap)
            if reason is None and time.monotonic() >= next_default_check:
                next_default_check = time.monotonic() + DEFAULT_MIC_CHECK_SECONDS
                reason = await self._default_mic_changed()
            if reason is None:
                failures = 0
                continue
            if time.monotonic() < next_try and "suspension" not in reason:
                continue
            if reason.startswith("cambio"):
                log.info("[ACTIVE] %s: cambiando de microfono", reason.capitalize())
            else:
                log.warning("[ERROR] %s: reabriendo el microfono", reason.capitalize())
            ok = await asyncio.to_thread(audio.restart_input)
            if self.wake is not None:
                self.wake.reset()
            if ok:
                failures += 1
                next_try = time.monotonic() + min(60.0, 5.0 * 2 ** (failures - 1))
            else:
                self.spawn(self._wait_for_microphone())

    async def _default_mic_changed(self) -> str | None:
        if self.mode != "idle" or self.audio.output_open:
            return None
        from jarvis.tools import audio_ctl

        try:
            name = await asyncio.wait_for(registry.in_worker(audio_ctl.default_input_name), timeout=3)
        except Exception:
            return None
        if name and not audio_ctl.same_device(name, self.audio.input_name):
            return f"cambio el microfono predeterminado de Windows a {name}"
        return None

    def _start_hotkey(self) -> None:
        combo = self.cfg.get("activacion.atajo", "ctrl+alt+j")
        if not combo:
            return
        from jarvis.audio.hotkey import GlobalHotkey

        self.hotkey = GlobalHotkey(combo, lambda: self.loop.call_soon_threadsafe(self.activate, "atajo"))
        self.hotkey.start()

    async def _start_channels(self) -> None:
        if self.cfg.get("canales.telegram", True) and env("TELEGRAM_BOT_TOKEN"):
            from jarvis.channels.telegram_bot import TelegramChannel

            self.telegram = TelegramChannel(self)
            try:
                await self.telegram.start()
                services.telegram = self.telegram
            except Exception:
                log.exception("[ERROR] No se pudo iniciar el bot de Telegram")
        if self.cfg.get("canales.http", True):
            from jarvis.channels.http_api import HttpChannel

            self.http = HttpChannel(self)
            try:
                await self.http.start()
            except Exception:
                log.exception("[ERROR] No se pudo iniciar el servidor HTTP")


    def _on_level(self, source: str, level: float) -> None:
        if source == "out" or self.mode == "session":
            ui("level", source, level)

    def _on_mic_frame(self, frame: bytes) -> None:
        if self.muted or self.loop is None:
            return
        if self.mode == "session":
            self.loop.call_soon_threadsafe(self._push_mic, frame)
        elif self.mode == "captura":
            self._capture_buf.append(frame)
        elif self.mode == "idle" and self.wake is not None:
            try:
                self._wake_q.put_nowait(frame)
            except queue.Full:
                pass

    def _push_mic(self, frame: bytes) -> None:
        if self._mic_q.full():
            self._mic_q.get_nowait()
        self._mic_q.put_nowait(frame)

    def _drain_mic(self) -> None:
        while not self._mic_q.empty():
            self._mic_q.get_nowait()

    def _wake_loop(self) -> None:
        while not self._closing.is_set():
            try:
                frame = self._wake_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.mode != "idle" or self.muted:
                continue
            try:
                detected = self.wake.process(frame)
            except Exception:
                log.exception("Error en la palabra de activacion")
                continue
            if detected and self.mode == "idle":
                self.mode = "session"
                self.loop.call_soon_threadsafe(self.activate, "voz", True)


    def activate(self, source: str = "voz", preset: bool = False) -> None:
        if not preset and self.mode != "idle":
            if self.mode == "session" and source in ("atajo", "interfaz"):
                self.end_session_now()
            return
        if self.audio is None or not self.audio.has_input or self.client is None or self.muted:
            self.mode = "idle"
            if self.client is None:
                ui("open_settings")
                self.spawn(self.announce("Falta configurar la clave de Gemini. Ábralo desde Configuración en la bandeja."))
            elif self.audio is not None and not self.audio.has_input:
                self.spawn(self.announce("No detecto ningún micrófono habilitado en Windows."))
            return
        self.mode = "session"
        self._fallback_end = False
        self._session_task = self.spawn(self._run_session(source))

    async def _run_session(self, source: str) -> None:
        volume = float(self.cfg.get("audio.volumen_sonidos", 0.35))
        self.audio.play(chime("activar", volume))
        self.set_state("conectando")
        log.info("[SESION] Inicio por %s", source)
        await self._duck_music()
        try:
            use_live = self.cfg.get("gemini.modo_voz", "live") == "live" and time.time() >= self._live_blocked_until
            if use_live:
                try:
                    await self._live_session()
                except QuotaExceeded as exc:
                    log.warning("[CUPO] Gemini Live sin cupo: %s", exc)
                    self._live_blocked_until = time.time() + LIVE_RETRY_AFTER_QUOTA
                    await self._fallback_session(intro="La voz en vivo agotó su cupo gratuito. Sigo en modo respaldo, señor.")
            else:
                await self._fallback_session()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[ERROR] Fallo la sesion de voz")
            self.set_state("error")
            self.audio.clear()
            self.audio.play(chime("error", volume))
            await asyncio.sleep(1.5)
        finally:
            self.live = None
            self._drain_mic()
            if self.wake is not None:
                self.wake.reset()
            await self._restore_music()
            self.audio.play(chime("desactivar", volume))
            ui("subtitle", "", "")
            self.mode = "idle"
            self.set_state("reposo")

    async def _duck_music(self) -> None:
        if not self.cfg.get("audio.bajar_musica", True):
            return
        try:
            level = float(self.cfg.get("audio.nivel_musica", 0.25))
            lowered = await registry.in_worker(self.ducker.duck, level)
            if lowered:
                log.info("[AUDIO] Musica bajada mientras conversa: %s", ", ".join(lowered))
        except Exception:
            log.debug("No se pudo bajar la musica", exc_info=True)

    async def _restore_music(self) -> None:
        if not self.ducker.active:
            return
        try:
            await registry.in_worker(self.ducker.restore)
        except Exception:
            log.exception("No se pudo restaurar el volumen de la musica")

    def _after_tool(self, name: str) -> None:
        if name in MUSIC_TOOLS and self.ducker.active:
            self.spawn(self._restore_music())

    async def _barge_in(self) -> bool:
        setting = str(self.cfg.get("audio.interrumpir", "auto")).lower()
        if setting in ("si", "sí", "true"):
            return True
        if setting in ("no", "false"):
            return False
        from jarvis.tools import audio_ctl

        try:
            return bool(await registry.in_worker(audio_ctl.is_headphones))
        except Exception:
            return False

    async def _live_session(self) -> None:
        from jarvis.brain.live import LiveConversation

        self.live = LiveConversation(
            self.client,
            self.audio,
            self._mic_q,
            barge_in=await self._barge_in(),
            check_barge_in=self._barge_in,
            recent=list(self.recent),
            on_tool=self._after_tool,
            on_state=self.set_state,
            on_subtitle=lambda who, text: ui("subtitle", who, text),
        )
        try:
            await self.live.run()
        finally:
            self._remember(self.live.transcript, "voz")

    def _remember(self, lines: list[str], channel: str) -> None:
        try:
            history.append(lines, channel)
            self.recent = deque(history.recent(12), maxlen=12)
        except Exception:
            log.exception("No se pudo guardar el historial")
            self.recent.extend(lines[-8:])

    async def _fallback_session(self, intro: str | None = None) -> None:
        if self.text_brain is None:
            return
        if intro:
            await self.speak(intro)
        while not self._fallback_end:
            self.set_state("escuchando")
            wav = await self._record_utterance(float(self.cfg.get("activacion.segundos_inactividad", 35)))
            if wav is None:
                break
            self.set_state("pensando")
            try:
                reply = await self.text_brain.ask(channel="voz", audio=wav, mime="audio/wav")
            except QuotaExceeded:
                await self.speak("Señor, se agotó el cupo gratuito de Gemini por ahora.")
                break
            ui("subtitle", "jarvis", reply)
            self._remember(["Usuario: (mensaje de voz)", f"Jarvis: {reply}"], "voz")
            await self.speak(reply)

    async def _record_utterance(self, timeout: float) -> bytes | None:
        self._drain_mic()
        deadline = time.monotonic() + timeout
        pre: deque[bytes] = deque(maxlen=4)
        frames: list[bytes] = []
        noise, loud, silent = 0.02, 0, 0
        pause_frames = max(6, int(self.cfg.get("activacion.pausa_para_responder_ms", 2400)) // 80)
        speaking = False
        while True:
            if self._fallback_end:
                return None
            try:
                frame = await asyncio.wait_for(self._mic_q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if not speaking and time.monotonic() > deadline:
                    return None
                continue
            level = rms_level(frame)
            if not speaking:
                pre.append(frame)
                noise = 0.95 * noise + 0.05 * level
                loud = loud + 1 if level > max(0.06, noise * 3) else 0
                if loud >= 2:
                    speaking = True
                    frames = list(pre)
                elif time.monotonic() > deadline:
                    return None
                continue
            frames.append(frame)
            silent = silent + 1 if level < max(0.04, noise * 2) else 0
            if silent >= pause_frames or len(frames) > 2250:
                break
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(IN_RATE)
            wav.writeframes(b"".join(frames))
        return buffer.getvalue()

    def request_session_end(self) -> None:
        if self.live is not None:
            self.live.request_end()
        else:
            self._fallback_end = True

    def end_session_now(self) -> None:
        self._fallback_end = True
        if self.live is not None:
            self.live.end_now()
        if self.audio is not None:
            self.audio.clear()

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        if self.muted:
            self.end_session_now()
        self.set_state("silenciado" if self.muted else "reposo")
        return self.muted


    async def speak(self, text: str) -> None:
        if self.audio is None or not text:
            return
        self.set_state("hablando")
        pcm = await tts.synthesize(text, self.cfg.get("audio.voz_respaldo", "es-MX-JorgeNeural"))
        if pcm:
            self.audio.play(pcm)
            await self.audio.wait_done()

    def _quiet_hours(self, now: dt.datetime | None = None) -> bool:
        start = str(self.cfg.get("avisos.silencio_desde", "") or "")
        end = str(self.cfg.get("avisos.silencio_hasta", "") or "")
        if not start or not end:
            return False
        current = (now or dt.datetime.now()).strftime("%H:%M")
        return (start <= current < end) if start < end else (current >= start or current < end)

    async def announce(
        self,
        text: str,
        voice: bool = True,
        telegram: bool = False,
        popup: bool = True,
        title: str = "JARVIS",
    ) -> None:
        log.info("[AVISO] %s: %s", title, text)
        if popup:
            ui("notify", title, text)
        if telegram and self.telegram is not None and self.telegram.can_send:
            try:
                await self.telegram.send_text(f"[{title}] {text}")
            except Exception:
                log.exception("No se pudo enviar el aviso por Telegram")
        if not voice or self.audio is None or self.muted or self._quiet_hours():
            return
        async with self._announce_lock:
            for _ in range(360):
                if self.mode == "idle":
                    break
                await asyncio.sleep(0.5)
            else:
                return
            self.mode = "announce"
            try:
                await self._duck_music()
                self.audio.play(chime("alerta", float(self.cfg.get("audio.volumen_sonidos", 0.35))))
                await self.audio.wait_done(5)
                ui("subtitle", "jarvis", text)
                await self.speak(text)
            finally:
                await self._restore_music()
                if self.wake is not None:
                    self.wake.reset()
                self.mode = "idle"
                self.set_state("reposo")


    async def handle_text(self, text: str, channel: str, audio: bytes | None = None, mime: str = "audio/ogg") -> str:
        confirm.note_user_text(text, channel)
        if self.text_brain is None:
            return "Falta configurar la clave de Gemini (GEMINI_API_KEY). Ábrela en Configuración desde la bandeja de JARVIS."
        try:
            reply = await self.text_brain.ask(text, channel=channel, audio=audio, mime=mime)
            self._remember([f"Usuario: {text or '(nota de voz)'}", f"Jarvis: {reply}"], channel)
            return reply
        except QuotaExceeded:
            return "Señor, se agotó el cupo gratuito de Gemini por ahora. Intente más tarde."
        except Exception as exc:
            log.exception("Error atendiendo un mensaje de %s", channel)
            return f"Hubo un error al procesar el pedido: {type(exc).__name__}."

    async def briefing_now(self) -> str:
        from jarvis.tools import briefing

        return await briefing.deliver(self)
