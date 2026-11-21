from __future__ import annotations

import logging
from typing import Annotated

from pycaw.pycaw import DEVICE_STATE, AudioUtilities, EDataFlow, ERole

from jarvis.textutil import best_match, normalize
from jarvis.tools.registry import ToolError, tool

log = logging.getLogger(__name__)

FORM_FACTOR_KEY = "{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E} 0"
HEADPHONE_FORM_FACTORS = {3, 5}
HEADPHONE_HINTS = ("auricular", "headphone", "headset", "buds", "airpods", "fx20", "cascos")


def _endpoint():
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> tuple[int, bool]:
    ep = _endpoint()
    return round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())


def set_volume(level: int) -> int:
    level = max(0, min(100, int(level)))
    ep = _endpoint()
    ep.SetMute(0, None)
    ep.SetMasterVolumeLevelScalar(level / 100, None)
    return level


def output_devices() -> list:
    return AudioUtilities.GetAllDevices(data_flow=EDataFlow.eRender.value, device_state=DEVICE_STATE.ACTIVE.value)


def default_output():
    return AudioUtilities.GetSpeakers()


def default_input_name() -> str:
    device = AudioUtilities.GetMicrophone()
    if device is None:
        return ""
    return AudioUtilities.CreateDevice(device).FriendlyName or ""


def same_device(windows_name: str, portaudio_name: str) -> bool:
    a, b = (windows_name or "").strip()[:31].strip(), (portaudio_name or "").strip()[:31].strip()
    return bool(a) and a == b


def is_headphones(device=None) -> bool:
    try:
        device = device or default_output()
        form = device.properties.get(FORM_FACTOR_KEY)
        if form in HEADPHONE_FORM_FACTORS:
            return True
        name = normalize(device.FriendlyName or "")
        return any(h in name for h in HEADPHONE_HINTS)
    except Exception:
        log.debug("No se pudo detectar el tipo de salida", exc_info=True)
        return False


@tool("Fija el volumen general del sistema (0 a 100).")
def fijar_volumen(nivel: Annotated[int, "Porcentaje de 0 a 100"]) -> dict:
    return {"volumen": set_volume(nivel)}


@tool("Sube o baja el volumen general. Positivo sube, negativo baja (por defecto 10 puntos).")
def cambiar_volumen(cambio: Annotated[int, "Puntos a sumar o restar, ej. 10 o -15"] = 10) -> dict:
    current, _ = get_volume()
    return {"antes": current, "volumen": set_volume(current + cambio)}


@tool("Silencia o reactiva el sonido del sistema.")
def silenciar(silenciar: Annotated[bool, "true silencia, false reactiva"] = True) -> dict:
    _endpoint().SetMute(1 if silenciar else 0, None)
    return {"silenciado": silenciar}


@tool("Dice el volumen actual, si esta silenciado y por donde sale el sonido.")
def estado_volumen() -> dict:
    level, muted = get_volume()
    return {"volumen": level, "silenciado": muted, "salida": default_output().FriendlyName}


@tool(
    "Cambia el volumen de una app concreta (Spotify, Chrome, Discord...) sin tocar el resto. "
    "Use 'nivel' para un porcentaje fijo (0 a 100) o 'cambio' para subir/bajar puntos respecto del actual (ej. -10 o 15)."
)
def volumen_app(
    app: Annotated[str, "Nombre de la app"],
    nivel: Annotated[int, "Porcentaje fijo de 0 a 100 (-1 para usar cambio)"] = -1,
    cambio: Annotated[int, "Puntos a sumar o restar al volumen actual de la app, ej. -10 o 15"] = 0,
) -> dict:
    sessions = [s for s in AudioUtilities.GetAllSessions() if s.Process is not None]
    match, _ = best_match(app, sessions, key=lambda s: s.Process.name().removesuffix(".exe"), threshold=75)
    if match is None:
        raise ToolError(f"'{app}' no esta reproduciendo sonido ahora.")
    before = round(match.SimpleAudioVolume.GetMasterVolume() * 100)
    if cambio != 0 or nivel < 0:
        level = max(0, min(100, before + (int(cambio) if cambio != 0 else 0)))
    else:
        level = max(0, min(100, int(nivel)))
    match.SimpleAudioVolume.SetMasterVolume(level / 100, None)
    return {"app": match.Process.name(), "antes": before, "volumen": level}


@tool("Lista las salidas de audio disponibles (parlantes, auriculares, Bluetooth, HDMI) y cual esta activa.")
def listar_salidas_audio() -> dict:
    current = default_output().id
    return {
        "salidas": [
            {"nombre": d.FriendlyName, "activa": d.id == current, "auriculares": is_headphones(d)}
            for d in output_devices()
        ]
    }


def set_default_output(device) -> None:
    AudioUtilities.SetDefaultDevice(device.id, roles=[ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])


@tool("Cambia la salida de audio predeterminada (ej. 'parlantes', 'auriculares', 'JBL', 'Philips').")
def cambiar_salida_audio(nombre: Annotated[str, "Nombre o parte del nombre del dispositivo"]) -> dict:
    devices = output_devices()
    query = normalize(nombre)
    if query in {"parlantes", "altavoces", "bocinas", "speakers"}:
        pool = [d for d in devices if not is_headphones(d)]
        match = pool[0] if len(pool) == 1 else best_match("altavoces", pool, key=lambda d: d.FriendlyName, threshold=50)[0]
    elif query in {"auriculares", "audifonos", "cascos"}:
        pool = [d for d in devices if is_headphones(d)]
        match = pool[0] if pool else None
    else:
        match, _ = best_match(nombre, devices, key=lambda d: d.FriendlyName, threshold=60)
    if match is None:
        names = ", ".join(d.FriendlyName for d in devices)
        raise ToolError(f"No encontre esa salida. Disponibles: {names}.")
    set_default_output(match)
    return {"salida": match.FriendlyName}
