from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from jarvis.services import run, services
from jarvis.textutil import best_match
from jarvis.tools.registry import ToolError, tool

log = logging.getLogger(__name__)

_connections: dict[str, object] = {}


def _telegram():
    if services.telegram is None or not services.telegram.can_send:
        raise ToolError("Telegram no esta configurado (falta el token o el chat autorizado).")
    return services.telegram


def send_text(text: str) -> bool:
    return bool(run(_telegram().send_text(text)))


def send_photo(path: Path, caption: str = "") -> bool:
    return bool(run(_telegram().send_photo(path, caption), timeout=60))


@tool("Envia un mensaje al celular del usuario por Telegram (links, textos, listas para tener a mano).")
async def enviar_al_celular(texto: Annotated[str, "Mensaje a enviar"]) -> dict:
    sent = await _telegram().send_text(texto)
    return {"enviado": bool(sent)}


@tool("Envia un archivo de la PC al celular por Telegram (hasta 50 MB).", timeout=90)
async def enviar_archivo_al_celular(ruta: Annotated[str, "Ruta completa del archivo"]) -> dict:
    path = Path(ruta).expanduser()
    if not path.is_file():
        raise ToolError("Ese archivo no existe.")
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ToolError("El archivo supera los 50 MB que permite Telegram para bots.")
    sent = await _telegram().send_document(path)
    return {"enviado": bool(sent), "archivo": path.name}


@tool(
    "Experimental: hace que la musica del celular (iPhone) suene por los parlantes de la PC via Bluetooth, "
    "o corta esa conexion. El celular debe estar emparejado y con Bluetooth encendido.",
    timeout=40,
)
async def audio_del_celular(
    activar: Annotated[bool, "true conecta, false desconecta"] = True,
    nombre: Annotated[str, "Nombre del celular"] = "iPhone",
) -> dict:
    from winrt.windows.devices.enumeration import DeviceInformation
    from winrt.windows.media.audio import AudioPlaybackConnection, AudioPlaybackConnectionOpenResultStatus

    if not activar:
        closed = 0
        for key in list(_connections):
            try:
                _connections.pop(key).close()
                closed += 1
            except Exception:
                log.debug("Error al cerrar la conexion de audio", exc_info=True)
        return {"desconectadas": closed}

    devices = list(await DeviceInformation.find_all_async_aqs_filter(AudioPlaybackConnection.get_device_selector()))
    match, _ = best_match(nombre, devices, key=lambda d: d.name, threshold=50)
    if match is None:
        raise ToolError("No veo el celular como fuente de audio. Verifique que este emparejado y con Bluetooth encendido.")
    connection = AudioPlaybackConnection.try_create_from_id(match.id)
    if connection is None:
        raise ToolError("Windows no permitio crear la conexion de audio con el celular.")
    await connection.start_async()
    result = await connection.open_async()
    if result.status != AudioPlaybackConnectionOpenResultStatus.SUCCESS:
        connection.close()
        raise ToolError(f"El celular no acepto la conexion de audio ({result.status}).")
    _connections[match.id] = connection
    return {"conectado": match.name, "nota": "Ya puede reproducir musica en el celular y saldra por la PC."}
