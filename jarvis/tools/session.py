from __future__ import annotations

from jarvis.services import services
from jarvis.tools.registry import tool


@tool(
    "Termina la conversacion de voz y vuelve a esperar 'hey jarvis'. Usela cuando el usuario se despide "
    "o dice que no necesita nada mas ('gracias, eso es todo', 'nada mas', 'chau').",
    modes={"voz"},
)
async def terminar_conversacion() -> dict:
    engine = services.engine
    if engine is not None:
        engine.request_session_end()
    return {"ok": True, "nota": "Despidase en pocas palabras."}


@tool(
    "Abre la ventana para entrenar 'hey jarvis' con la voz del usuario (graba unas frases y aprende su forma de "
    "decirlo). Usela si el usuario dice que no lo escucha bien al llamarlo o pide entrenar su voz.",
    modes={"voz", "texto"},
)
async def entrenar_mi_voz() -> dict:
    from jarvis.services import ui

    engine = services.engine
    if engine is not None and engine.mode == "session":
        engine.request_session_end()
    ui("open_training")
    return {"ok": True, "nota": "Digale que se abrio la ventana 'Entrenar mi voz' y que siga los pasos (unos dos minutos)."}
