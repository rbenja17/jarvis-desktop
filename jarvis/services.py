from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Coroutine

log = logging.getLogger(__name__)


@dataclass
class Services:
    loop: asyncio.AbstractEventLoop | None = None
    engine: Any = None
    telegram: Any = None
    text_brain: Any = None
    bridge: Any = None


services = Services()


def submit(coro: Coroutine[Any, Any, Any]) -> Future | None:
    if services.loop is None or services.loop.is_closed():
        coro.close()
        return None
    return asyncio.run_coroutine_threadsafe(coro, services.loop)


def run(coro: Coroutine[Any, Any, Any], timeout: float = 30.0) -> Any:
    fut = submit(coro)
    if fut is None:
        raise RuntimeError("El motor de JARVIS no esta en marcha.")
    return fut.result(timeout)


def ui(signal_name: str, *args: Any) -> None:
    bridge = services.bridge
    if bridge is None:
        return
    try:
        getattr(bridge, signal_name).emit(*args)
    except Exception:
        log.debug("No se pudo emitir %s", signal_name, exc_info=True)
