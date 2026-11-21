from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

from google import genai
from google.genai import types

from jarvis.audio.echo import EchoGate, frame_level
from jarvis.audio.io import AudioIO
from jarvis.brain import usage
from jarvis.brain.prompt import build_system_prompt
from jarvis.brain.text import QuotaExceeded, is_quota_error
from jarvis.config import get_config
from jarvis.tools import confirm, registry
from jarvis.tools.registry import ToolContext

log = logging.getLogger(__name__)

MAX_SESSION_SECONDS = 14 * 60
END_GRACE_SECONDS = 8.0
PREBUFFER_SECONDS = 0.2
OUTPUT_CHECK_SECONDS = 4.0
SPEAKER_MIN_LEVEL = 300.0
SPEAKER_FACTOR = 4.0
FALSE_INTERRUPT_SECONDS = 3.0
ECHO_SIMILARITY = 80
MAX_MIC_EXTENSION_SECONDS = 18.0
TOOL_THINKING_RESET_SECONDS = 4.0
MAX_RECONNECTS = 2


def is_echo_of(user_text: str, jarvis_text: str) -> bool:
    from rapidfuzz import fuzz

    from jarvis.textutil import normalize

    user, said = normalize(user_text), normalize(jarvis_text)
    if len(user) < 8 or not said:
        return False
    return fuzz.partial_ratio(user, said) >= ECHO_SIMILARITY


def _rate_from_mime(mime: str | None, default: int = 24000) -> int:
    match = re.search(r"rate=(\d+)", mime or "")
    return int(match.group(1)) if match else default


class LiveConversation:
    def __init__(
        self,
        client: genai.Client,
        audio: AudioIO,
        mic_queue: asyncio.Queue,
        *,
        barge_in: bool,
        check_barge_in: Callable[[], Awaitable[bool]] | None = None,
        recent: list[str] | None = None,
        on_tool: Callable[[str], None] = lambda name: None,
        on_state: Callable[[str], None] = lambda s: None,
        on_subtitle: Callable[[str, str], None] = lambda who, text: None,
    ) -> None:
        cfg = get_config()
        self.client = client
        self.audio = audio
        self.mic_queue = mic_queue
        self.barge_in = barge_in
        self.check_barge_in = check_barge_in
        self.gate = EchoGate(profile="auriculares")
        self.speaker_gate = EchoGate(min_level=SPEAKER_MIN_LEVEL, factor=SPEAKER_FACTOR, profile="parlantes")
        self.speaker_barge_in = bool(cfg.get("audio.interrumpir_con_parlantes", True))
        self.interruptions = 0
        self.false_interruptions = 0
        self._interrupted_at: float | None = None
        self._said_before_interrupt = ""
        audio.gaps = 0
        self.recent = recent or []
        self.on_tool = on_tool
        self.on_state = on_state
        self.on_subtitle = on_subtitle
        self.model = cfg.get("gemini.modelo_voz", "gemini-3.8-live")
        self.voice = cfg.get("gemini.voz", "Charon")
        self.search = bool(cfg.get("gemini.busqueda_google", False))
        self.inactivity = float(cfg.get("activacion.segundos_inactividad", 22))
        self.pause_ms = int(cfg.get("activacion.pausa_para_responder_ms", 1800))

        self.transcript: list[str] = []
        self.started = time.monotonic()
        self.last_activity = time.monotonic()
        self._last_server_activity = self.last_activity
        self._user_text = ""
        self._last_user_line = ""
        self._model_text = ""
        self._end_requested_at: float | None = None
        self._turn_done_after_end = False
        self._end_now = asyncio.Event()
        self._speaking = False
        self.session = None
        self._tools_running = 0
        self._noise_level = 25.0
        self._speech_frames = 0
        self._pending_user_turn = False
        self._awaiting_tool_followup = False
        self._last_tool_response_at = 0.0
        self._last_tool_summary = ""


    def build_config(self) -> types.LiveConnectConfig:
        exclude = {"buscar_informacion"} if self.search else set()
        tools = [types.Tool(function_declarations=registry.declarations("voz", exclude))]
        if self.search:
            tools.append(types.Tool(google_search=types.GoogleSearch()))
        context_lines = (self.recent + self.transcript)[-12:]
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=build_system_prompt("voz", context_lines, live_search=self.search),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice))
            ),
            tools=tools,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=300,
                    silence_duration_ms=self.pause_ms,
                ),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        )


    def request_end(self) -> None:
        if self._end_requested_at is None:
            self._end_requested_at = time.monotonic()
            self._turn_done_after_end = False

    def end_now(self) -> None:
        self.audio.clear()
        self._end_now.set()


    def _should_reconnect(self) -> str | None:
        if self._end_now.is_set() or self._end_requested_at is not None:
            return None
        if time.monotonic() - self.started >= MAX_SESSION_SECONDS:
            return None
        if self._awaiting_tool_followup and self._last_tool_summary:
            return (
                f"(Se reconectó la sesión tras ejecutar herramientas: {self._last_tool_summary}. "
                "Continúe con la tarea si faltan pasos o informe el resultado al usuario en una frase.)"
            )
        pending_text = self._user_text.strip()
        if pending_text:
            return pending_text
        return None

    async def run(self) -> None:
        reconnects = 0
        resume_prompt: str | None = None
        try:
            while True:
                try:
                    async with self.client.aio.live.connect(model=self.model, config=self.build_config()) as session:
                        self.session = session
                        log.info("[LIVE] Sesion abierta con %s (interrumpir=%s)", self.model, self.barge_in)
                        if resume_prompt:
                            self.on_state("pensando")
                            await self._send_continuation(session, resume_prompt)
                            resume_prompt = None
                        else:
                            self.on_state("escuchando")
                        tasks = [
                            asyncio.create_task(self._send_audio(session), name="live-send"),
                            asyncio.create_task(self._receive(session), name="live-recv"),
                            asyncio.create_task(self._watchdog(), name="live-watch"),
                        ]
                        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        for task in done:
                            if not task.cancelled() and task.exception() is not None:
                                raise task.exception()
                        if any(t.get_name() == "live-recv" for t in done) and reconnects < MAX_RECONNECTS:
                            resume_prompt = self._should_reconnect()
                            self._close_user_turn()
                            self._close_model_turn()
                            if resume_prompt:
                                reconnects += 1
                                log.warning("[LIVE] Servidor cerro en medio de un turno; reconectando (%d/%d)", reconnects, MAX_RECONNECTS)
                                continue
                        break
                except QuotaExceeded:
                    raise
                except Exception as exc:
                    if is_quota_error(exc):
                        raise QuotaExceeded(str(exc)) from exc
                    resume_prompt = self._should_reconnect() if reconnects < MAX_RECONNECTS else None
                    self._close_user_turn()
                    self._close_model_turn()
                    if resume_prompt:
                        reconnects += 1
                        log.warning(
                            "[LIVE] Corte de conexion en medio de un turno (%s); reconectando (%d/%d)",
                            type(exc).__name__, reconnects, MAX_RECONNECTS,
                        )
                        await asyncio.sleep(0.4)
                        continue
                    raise
        finally:
            self.session = None
            self._close_user_turn()
            self._close_model_turn()
            usage.record_live_seconds(time.monotonic() - self.started)
            log.info(
                "[LIVE] Sesion cerrada (%.0f s, %d interrupciones, %d falsas, %d bloques de eco filtrados, %d cortes de red)",
                time.monotonic() - self.started, self.interruptions, self.false_interruptions,
                self.gate.blocked + self.speaker_gate.blocked, self.audio.gaps,
            )

    async def _send_continuation(self, session, text: str) -> None:
        if session is None or not hasattr(session, "send_client_content"):
            return
        try:
            now = time.monotonic()
            self.last_activity = now
            self._last_server_activity = now
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
                turn_complete=True,
            )
        except Exception:
            log.debug("No se pudo enviar continuacion a Gemini Live", exc_info=True)

    async def _send_audio(self, session) -> None:
        while True:
            chunk = await self.mic_queue.get()
            if self._tools_running > 0:
                continue
            now = time.monotonic()
            playing = self.audio.is_playing(tail=0.3)
            level = frame_level(chunk)
            if playing:
                if self.barge_in:
                    chunk = self.gate.filter(chunk, playing)
                elif self.speaker_barge_in:
                    chunk = self.speaker_gate.filter(chunk, playing)
                else:
                    continue
            else:
                voice_threshold = max(80.0, min(220.0, self._noise_level * 2.5))
                if level >= voice_threshold:
                    self._speech_frames += 1
                    can_extend = (not self.transcript or bool(self._user_text.strip())) and (
                        now - self._last_server_activity < MAX_MIC_EXTENSION_SECONDS
                    )
                    if self._speech_frames >= 3 and can_extend:
                        self.last_activity = now
                        self._pending_user_turn = True
                else:
                    self._noise_level = 0.96 * self._noise_level + 0.04 * level
            await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))

    async def _receive(self, session) -> None:
        while True:
            got_any = False
            async for message in session.receive():
                got_any = True
                await self.handle_message(session, message)
            if not got_any:
                log.info("[LIVE] El servidor cerro la conexion")
                return

    async def handle_message(self, session, message: types.LiveServerMessage) -> None:
        now = time.monotonic()
        if message.go_away is not None:
            log.info("[LIVE] El servidor pidio cerrar pronto")
            self.request_end()

        if message.tool_call and message.tool_call.function_calls:
            self._pending_user_turn = False
            self._awaiting_tool_followup = False
            self._speech_frames = 0
            self._close_user_turn()
            self.on_state("pensando")
            self.last_activity = now
            self._last_server_activity = now
            ctx = ToolContext(channel="voz", user_text=self._last_user_line)
            responses = []
            self._tools_running += 1
            try:
                for call in message.tool_call.function_calls:
                    result = await registry.call(call.name, dict(call.args or {}), ctx)
                    responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result))
                    try:
                        self.on_tool(call.name)
                    except Exception:
                        log.debug("Fallo el aviso de herramienta", exc_info=True)
            finally:
                self._tools_running -= 1
                done_now = time.monotonic()
                self.last_activity = done_now
                self._last_server_activity = done_now
            await session.send_tool_response(function_responses=responses)
            self._last_tool_response_at = time.monotonic()
            self._last_tool_summary = "; ".join(
                f"{r.name}: {registry._short(r.response, 350)}" for r in responses
            )
            self._awaiting_tool_followup = self._end_requested_at is None
            return

        content = message.server_content
        if content is None:
            return
        if content.interrupted:
            self.interruptions += 1
            gate = self._active_gate
            log.info(
                "[LIVE] Interrumpido mientras hablaba (%s, microfono %.0f, umbral %.0f)",
                gate.profile, gate.last_level, gate.threshold,
            )
            self._interrupted_at = now
            self._said_before_interrupt = self._model_text
            self.audio.clear()
            self._close_model_turn()
            self.on_state("escuchando")
            self._check_interruption()
        if content.input_transcription and content.input_transcription.text:
            self._user_text += content.input_transcription.text
            self._check_interruption()
            confirm.note_user_text(self._user_text, "voz")
            self.on_subtitle("usuario", self._user_text.strip())
            self.last_activity = now
            self._last_server_activity = now
            self._pending_user_turn = True
        if content.model_turn and content.model_turn.parts:
            self._pending_user_turn = False
            self._awaiting_tool_followup = False
            self._last_tool_response_at = 0.0
            self._speech_frames = 0
            for part in content.model_turn.parts:
                blob = part.inline_data
                if blob and blob.data:
                    self._close_user_turn()
                    self.audio.play(blob.data, rate=_rate_from_mime(blob.mime_type), prebuffer=PREBUFFER_SECONDS)
                    if not self._speaking:
                        self._speaking = True
                        self.on_state("hablando")
            self.last_activity = now
            self._last_server_activity = now
        if content.output_transcription and content.output_transcription.text:
            self._pending_user_turn = False
            self._awaiting_tool_followup = False
            self._last_tool_response_at = 0.0
            self._model_text += content.output_transcription.text
            self.on_subtitle("jarvis", self._model_text.strip())
        if content.turn_complete:
            self._close_model_turn()
            self.last_activity = now
            self._last_server_activity = now
            self._awaiting_tool_followup = False
            self._pending_user_turn = False
            self._speech_frames = 0
            if self._end_requested_at is not None:
                self._turn_done_after_end = True
            elif not self.audio.is_playing(tail=0.2) and not self._tools_running:
                self._speaking = False
                self._last_tool_response_at = 0.0
                self.on_state("escuchando")

    @property
    def _active_gate(self) -> EchoGate:
        return self.gate if self.barge_in else self.speaker_gate

    def _check_interruption(self) -> None:
        if self._interrupted_at is None or len(self._user_text.strip()) < 12:
            return
        if is_echo_of(self._user_text, self._said_before_interrupt):
            self._false_interruption("se escucho a si mismo")
        else:
            self._interrupted_at = None

    def _false_interruption(self, reason: str) -> None:
        self._interrupted_at = None
        self.false_interruptions += 1
        gate = self._active_gate
        gate.factor = min(gate.factor * 1.5, 12.0)
        log.info("[LIVE] Interrupcion falsa (%s): compuerta de %s mas exigente (x%.1f)", reason, gate.profile, gate.factor)
        if not self.barge_in and self.speaker_barge_in and self.false_interruptions >= 2:
            self.speaker_barge_in = False
            log.info("[LIVE] Con parlantes ya no se lo interrumpe en esta charla: hay demasiado eco")

    async def _refresh_barge_in(self) -> None:
        if self.check_barge_in is None:
            return
        try:
            value = bool(await asyncio.wait_for(self.check_barge_in(), timeout=1.5))
        except Exception:
            return
        if value != self.barge_in:
            log.info("[LIVE] Cambio la salida de audio: interrumpir=%s", value)
            self.barge_in = value

    async def _watchdog(self) -> None:
        next_output_check = time.monotonic() + OUTPUT_CHECK_SECONDS
        while True:
            try:
                await asyncio.wait_for(self._end_now.wait(), timeout=0.25)
                return
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            if now >= next_output_check:
                next_output_check = now + OUTPUT_CHECK_SECONDS
                await self._refresh_barge_in()
            if (
                self._interrupted_at is not None
                and not self._user_text.strip()
                and now - self._interrupted_at > FALSE_INTERRUPT_SECONDS
            ):
                self._false_interruption("nadie hablo")
            playing = self.audio.is_playing(tail=0.2)
            if playing or self._tools_running:
                self.last_activity = now
                self._last_server_activity = now
            elif self._speaking:
                self._speaking = False
                self.on_state("escuchando")
            elif self._last_tool_response_at > 0 and now - self._last_tool_response_at >= TOOL_THINKING_RESET_SECONDS:
                self._last_tool_response_at = 0.0
                self._awaiting_tool_followup = False
                self.on_state("escuchando")

            if self._end_requested_at is not None and not playing:
                if self._turn_done_after_end or now - self._end_requested_at > END_GRACE_SECONDS:
                    return
            if now - self.last_activity > self.inactivity:
                log.info("[LIVE] Fin por inactividad")
                return
            if now - self.started > MAX_SESSION_SECONDS:
                log.info("[LIVE] Fin por duracion maxima")
                return


    def _close_user_turn(self) -> None:
        if self._interrupted_at is not None and not self._user_text.strip():
            self._false_interruption("nadie hablo")
        self._interrupted_at = None
        text = self._user_text.strip()
        if text:
            self.transcript.append(f"Usuario: {text}")
            self._last_user_line = text
        self._user_text = ""

    def _close_model_turn(self) -> None:
        text = self._model_text.strip()
        if text:
            self.transcript.append(f"Jarvis: {text}")
        self._model_text = ""
