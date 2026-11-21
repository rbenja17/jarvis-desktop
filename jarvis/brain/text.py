from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors, types

from jarvis.brain import usage
from jarvis.brain.prompt import build_system_prompt
from jarvis.config import get_config
from jarvis.tools import registry
from jarvis.tools.registry import ToolContext

log = logging.getLogger(__name__)

MAX_ROUNDS = 12
HISTORY_TURNS = 8
HISTORY_TTL = 30 * 60
MODEL_TIMEOUT = 16.0
VISION_TIMEOUT = 12.0
TRANSIENT_COOLDOWN = 120.0


def _recent_lines() -> list[str]:
    try:
        from jarvis.tools.history import recent

        return recent(8)
    except Exception:
        return []
RETRYABLE = {408, 500, 502, 503, 504}
FAST = types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)
NO_AFC = types.AutomaticFunctionCallingConfig(disable=True)


def make_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})


class QuotaExceeded(Exception):
    """Todos los modelos configurados agotaron su cupo gratuito por ahora."""


def is_quota_error(exc: BaseException) -> bool:
    text = str(exc)
    return getattr(exc, "code", None) == 429 or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()


def _next_pacific_midnight() -> float:
    now = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    midnight = (now + dt.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return time.time() + (midnight - now).total_seconds()


class TextBrain:
    def __init__(self, client: genai.Client, models: list[str] | None = None) -> None:
        self.client = client
        self.models = models or list(get_config().get("gemini.modelos_texto", ["gemini-3.5-flash-lite"]))
        self._blocked_until: dict[str, float] = {}
        self._transient_until: dict[str, float] = {}
        self._histories: dict[str, tuple[float, list[types.Content]]] = {}


    def _available_models(self) -> list[str]:
        now = time.time()
        unblocked = [m for m in self.models if self._blocked_until.get(m, 0) <= now]
        ready = [m for m in unblocked if self._transient_until.get(m, 0) <= now]
        if ready:
            return ready
        return sorted(unblocked, key=lambda m: self._transient_until.get(m, 0))

    async def generate(
        self,
        contents: Any,
        config: types.GenerateContentConfig,
        timeout: float = MODEL_TIMEOUT,
    ) -> types.GenerateContentResponse:
        last_exc: Exception | None = None
        saw_quota = False
        for model in self._available_models():
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(model=model, contents=contents, config=config),
                    timeout=timeout,
                )
                self._transient_until.pop(model, None)
                usage.record_request(model)
                return response
            except asyncio.TimeoutError as exc:
                last_exc = exc
                self._transient_until[model] = time.time() + TRANSIENT_COOLDOWN
                log.warning("[TIMEOUT] %s tardo mas de %.0f s; pruebo el siguiente", model, timeout)
                continue
            except errors.APIError as exc:
                last_exc = exc
                if is_quota_error(exc):
                    saw_quota = True
                    usage.record_quota_hit(model)
                    daily = "PerDay" in str(exc) or "per day" in str(exc).lower()
                    self._blocked_until[model] = _next_pacific_midnight() if daily else time.time() + 60
                    log.warning("[CUPO] %s agotado (%s); pruebo el siguiente", model, "diario" if daily else "por minuto")
                    continue
                if exc.code in RETRYABLE:
                    self._transient_until[model] = time.time() + TRANSIENT_COOLDOWN
                    log.warning("[ERROR] %s respondio %s; pruebo el siguiente", model, exc.code)
                    continue
                raise
        if saw_quota or not self._available_models():
            raise QuotaExceeded("Sin cupo disponible en los modelos de texto configurados.") from last_exc
        if last_exc is not None:
            raise last_exc
        raise QuotaExceeded("Sin cupo disponible en los modelos de texto configurados.")

    def _config(self, recent: list[str] | None = None) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=build_system_prompt("texto", recent),
            tools=[types.Tool(function_declarations=registry.declarations("texto"))],
            automatic_function_calling=NO_AFC,
            temperature=0.6,
        )

    def _history(self, channel: str) -> list[types.Content]:
        stamp, history = self._histories.get(channel, (0.0, []))
        if time.time() - stamp > HISTORY_TTL:
            return []
        return list(history)

    def _remember(self, channel: str, user_text: str, answer: str) -> None:
        history = self._history(channel)
        history += [
            types.Content(role="user", parts=[types.Part.from_text(text=user_text)]),
            types.Content(role="model", parts=[types.Part.from_text(text=answer)]),
        ]
        self._histories[channel] = (time.time(), history[-HISTORY_TURNS * 2 :])

    def forget(self, channel: str) -> None:
        self._histories.pop(channel, None)


    async def ask(
        self,
        text: str = "",
        channel: str = "texto",
        audio: bytes | None = None,
        mime: str = "audio/ogg",
        remember: bool = True,
    ) -> str:
        parts: list[types.Part] = []
        if audio:
            parts.append(types.Part.from_bytes(data=audio, mime_type=mime))
            if not text:
                text = "(Mensaje de voz del usuario: responda a lo que pide.)"
        parts.append(types.Part.from_text(text=text))
        contents: list[types.Content] = self._history(channel) if remember else []
        recent = _recent_lines() if remember and not contents else None
        contents.append(types.Content(role="user", parts=parts))

        ctx = ToolContext(channel=channel, user_text=text)
        config = self._config(recent)
        answer = ""
        for _ in range(MAX_ROUNDS):
            response = await self.generate(contents, config)
            candidate = response.candidates[0] if response.candidates else None
            calls = response.function_calls or []
            if candidate is None or candidate.content is None:
                answer = "No obtuve respuesta del modelo."
                break
            if not calls:
                answer = (response.text or "").strip()
                break
            contents.append(candidate.content)
            results = []
            for call in calls:
                result = await registry.call(call.name, dict(call.args or {}), ctx)
                results.append(
                    types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response=result))
                )
            contents.append(types.Content(role="user", parts=results))
        else:
            answer = "Hice varias acciones pero no llegué a una respuesta final."

        if remember:
            self._remember(channel, text if not audio else "[mensaje de voz] " + text, answer or "(sin respuesta)")
        return answer or "Listo."

    async def compose_briefing(self, data: dict) -> str:
        treat = get_config().get("usuario.tratamiento", "señor")
        instructions = (
            "Usted es JARVIS, un mayordomo elegante con humor sutil. Redacte el resumen del día para leer en voz alta: "
            f"entre 80 y 150 palabras, trate al usuario de usted y llámelo «{treat}», sin listas, sin markdown, sin emojis. "
            "Orden: el 'saludo' exacto de los datos, clima, agenda, recordatorios y tareas, correos y, al final, los dos titulares "
            "más relevantes de la lista 'titulares' en una frase cada uno. Omita lo que no aparezca en los datos: si no hay "
            "'titulares', no mencione noticias. Nunca invente información."
        )
        config = types.GenerateContentConfig(
            system_instruction=instructions,
            temperature=0.7,
            automatic_function_calling=NO_AFC,
        )
        response = await self.generate(f"Datos del día:\n{data}", config)
        return (response.text or "").strip()


    async def describe_screen(self, jpeg: bytes, question: str = "") -> str:
        prompt = (
            "Es una captura de la pantalla del usuario. Léala con precisión, sin inventar nada que no se vea, y responda "
            "en español con una lista breve: app o página abierta; opciones o valores seleccionados (por ejemplo, el modelo "
            "elegido); menús desplegados con sus opciones; botones y campos principales con su texto y ubicación aproximada; "
            "textos importantes o errores. Copie los textos tal como aparecen. Sin introducciones."
            + (f" Responda también: {question}" if question else "")
        )
        config = types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=450,
            thinking_config=FAST,
            automatic_function_calling=NO_AFC,
        )
        contents = [types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), types.Part.from_text(text=prompt)]
        response = await self.generate(contents, config, timeout=VISION_TIMEOUT)
        return (response.text or "").strip()

    async def locate_on_screen(self, jpeg: bytes, target: str) -> dict:
        schema = {
            "type": "object",
            "properties": {
                "encontrado": {"type": "boolean"},
                "x": {"type": "integer", "description": "Centro horizontal, 0 = borde izquierdo, 1000 = borde derecho"},
                "y": {"type": "integer", "description": "Centro vertical, 0 = borde superior, 1000 = borde inferior"},
                "detalle": {"type": "string", "description": "Qué elemento es y su texto visible"},
            },
            "required": ["encontrado", "x", "y", "detalle"],
        }
        prompt = (
            f"Busque en esta captura de pantalla el elemento: «{target}». Si está visible, indique el centro exacto "
            "del elemento para hacer clic, en coordenadas normalizadas de 0 a 1000 sobre el ancho (x) y el alto (y) "
            "de la imagen. Si no está visible, encontrado=false y en detalle diga qué se ve en su lugar."
        )
        config = types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=FAST,
            automatic_function_calling=NO_AFC,
        )
        contents = [types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), types.Part.from_text(text=prompt)]
        response = await self.generate(contents, config, timeout=VISION_TIMEOUT)
        try:
            return json.loads(response.text or "{}")
        except ValueError:
            return {"encontrado": False, "detalle": "Respuesta invalida del modelo de vision."}
