from __future__ import annotations

from typing import Literal

from jarvis.tools import _keys
from jarvis.tools.registry import tool

_STATUS = {0: "cerrado", 1: "abierto", 2: "cambiando", 3: "detenido", 4: "reproduciendo", 5: "en pausa"}


@tool("Controla la reproduccion de cualquier app (Spotify, YouTube, VLC...) con las teclas multimedia.")
def control_multimedia(accion: Literal["play_pausa", "siguiente", "anterior", "detener"]) -> dict:
    _keys.tap(_keys.VK[accion])
    return {"accion": accion}


async def now_playing() -> dict | None:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    manager = await Manager.request_async()
    session = manager.get_current_session()
    if session is None:
        sessions = list(manager.get_sessions())
        playing = [s for s in sessions if int(s.get_playback_info().playback_status) == 4]
        session = (playing or sessions or [None])[0]
    if session is None:
        return None
    props = await session.try_get_media_properties_async()
    status = int(session.get_playback_info().playback_status)
    app = session.source_app_user_model_id or ""
    return {
        "titulo": props.title,
        "artista": props.artist,
        "album": props.album_title,
        "estado": _STATUS.get(status, str(status)),
        "app": app.split("!")[0].split(".")[-1] if "!" in app else app.removesuffix(".exe"),
    }


@tool("Dice que cancion o video esta sonando ahora mismo en la PC (titulo, artista, app).")
async def que_esta_sonando() -> dict:
    info = await now_playing()
    return info or {"resultado": "No hay nada reproduciendose."}
