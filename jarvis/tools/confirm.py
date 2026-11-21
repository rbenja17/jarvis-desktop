from __future__ import annotations

import asyncio
import itertools
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from jarvis.tools.registry import ToolContext, ToolSpec, current_context, execute, tool

TTL_SECONDS = 45.0
WAIT_FOR_TRANSCRIPT = 2.5

AFFIRM = re.compile(
    r"\b(s[ií]|sip|confirm[oa]|confirmado|confirmar|dale|adelante|hazlo|h[aá]celo|proced[ae]|"
    r"de acuerdo|ok|okay|afirmativo|claro|por supuesto|correcto|exacto|venga|segur[oa])\b",
    re.IGNORECASE,
)
NEGATE = re.compile(
    r"\b(no|cancel[ao]|cancelar|para|detente|espera|olv[ií]dalo|negativo|mejor no|nada)\b",
    re.IGNORECASE,
)


@dataclass
class Pending:
    spec: ToolSpec
    args: dict[str, Any]
    channel: str
    created: float
    description: str
    seq: int = 0


_lock = threading.Lock()
_pending: Pending | None = None
_seq = itertools.count(1)
_utterances: deque[tuple[int, str, str]] = deque(maxlen=30)


def _describe(spec: ToolSpec, args: dict[str, Any]) -> str:
    detail = ", ".join(f"{k}={v}" for k, v in args.items())
    return f"{spec.name}({detail})" if detail else spec.name


def note_user_text(text: str, channel: str) -> None:
    if text and text.strip():
        with _lock:
            _utterances.append((next(_seq), channel, text.strip()))


def is_affirmative(text: str) -> bool:
    return bool(AFFIRM.search(text)) and not NEGATE.search(text)


def pending() -> Pending | None:
    with _lock:
        if _pending and time.monotonic() - _pending.created > TTL_SECONDS:
            return None
        return _pending


def request(spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    global _pending
    if ctx.unattended:
        return {"error": "Las acciones peligrosas no se ejecutan en tareas programadas ni rutinas."}
    description = _describe(spec, args)
    with _lock:
        _pending = Pending(spec, args, ctx.channel, time.monotonic(), description, next(_seq))
    return {
        "requiere_confirmacion": True,
        "accion_pendiente": description,
        "instruccion": (
            "No se ejecuto todavia. Pregunte al usuario, en una frase breve, si confirma esta accion. "
            "Si responde que si, llame a confirmar_accion. Si dice que no, llame a cancelar_accion."
        ),
    }


def _find_affirmation(p: Pending) -> str | None:
    with _lock:
        for seq, channel, text in reversed(_utterances):
            if seq <= p.seq:
                break
            if channel == p.channel:
                return text if is_affirmative(text) else None
    return None


@tool("Ejecuta la accion peligrosa pendiente, solo despues de que el usuario dijo claramente que si.")
async def confirmar_accion() -> dict:
    global _pending
    ctx = current_context.get()
    p = pending()
    if p is None:
        return {"error": "No hay ninguna accion pendiente de confirmar (o ya vencio)."}
    if p.channel != ctx.channel:
        return {"error": "La confirmacion debe llegar por el mismo canal donde se pidio la accion."}

    deadline = time.monotonic() + WAIT_FOR_TRANSCRIPT
    answer = _find_affirmation(p)
    while answer is None and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        answer = _find_affirmation(p)
    if answer is None:
        return {"error": "No escuche una confirmacion explicita del usuario. Preguntele de nuevo."}

    with _lock:
        _pending = None
    return await execute(p.spec, p.args, ToolContext(channel=ctx.channel, user_text=answer))


@tool("Cancela la accion peligrosa pendiente.")
def cancelar_accion() -> dict:
    global _pending
    with _lock:
        had = _pending is not None
        _pending = None
    return {"cancelada": had}


def reset() -> None:
    global _pending
    with _lock:
        _pending = None
        _utterances.clear()
