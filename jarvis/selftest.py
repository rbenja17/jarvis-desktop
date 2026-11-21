from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any, Callable

from jarvis.config import env, get_config

_results: list[tuple[str, str]] = []


def _check(label: str, func: Callable[[], Any]) -> None:
    try:
        detail = func()
        badge = "[OK]"
        if isinstance(detail, tuple):
            badge, detail = detail
    except Exception as exc:  # noqa: BLE001
        badge, detail = "[ERROR]", f"{type(exc).__name__}: {exc}"
        if get_config().get("debug", False):
            traceback.print_exc()
    _results.append((badge, label))
    print(f"{badge:<9} {label}: {detail}")


def run_selftest(play_sound: bool = True) -> int:
    from jarvis.tools import registry

    registry.load_all()
    print("JARVIS selftest\n" + "-" * 60)

    _check("Herramientas registradas", lambda: f"{len(registry.all_specs())} (voz {len(registry.all_specs('voz'))}, texto {len(registry.all_specs('texto'))})")
    _check("Esquemas para Gemini", lambda: f"{len(registry.declarations('voz'))} declaraciones validas")

    def keys() -> Any:
        found = {name: bool(env(name)) for name in ("GEMINI_API_KEY", "SPOTIFY_CLIENT_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "JARVIS_HTTP_TOKEN")}
        missing = [k for k, v in found.items() if not v]
        return ("[OK]" if not missing else "[FALTA]", "faltan: " + ", ".join(missing) if missing else "todas presentes")

    _check("Claves en .env", keys)

    from jarvis.tools import apps, audio_ctl, bluetooth, google_ws, info, media, spotify, system

    _check("Apps del menu Inicio", lambda: f"{len(apps.start_apps())} apps indexadas")
    _check("Busqueda de app 'spotify'", lambda: (apps.resolve_app("spotify")[0] or {}).get("name", "no encontrada"))
    _check("Ventanas abiertas", lambda: f"{len(apps.list_windows())}")
    _check("Volumen", lambda: "{}% silenciado={} salida={}".format(*audio_ctl.get_volume(), audio_ctl.default_output().FriendlyName))
    _check("Salidas de audio", lambda: ", ".join(f"{d.FriendlyName}{' (auriculares)' if audio_ctl.is_headphones(d) else ''}" for d in audio_ctl.output_devices()))
    _check("Que esta sonando", lambda: asyncio.run(media.now_playing()) or "nada")
    _check("Bluetooth emparejados", lambda: ", ".join(f"{d['nombre']} ({'conectado' if d['conectado'] else 'desconectado'})" for d in asyncio.run(bluetooth.paired_devices())))
    _check("Bluetooth audio (KS)", lambda: f"{len(bluetooth.bt_audio_filters())} endpoints con control directo")
    _check("Estado del sistema", system.system_status)

    def brightness() -> Any:
        import screen_brightness_control as sbc

        return f"{sbc.get_brightness()[0]}%"

    _check("Brillo", brightness)
    def weather_check() -> Any:
        if not str(get_config().get("usuario.ciudad", "") or "").strip():
            return ("[FALTA]", "configure su ciudad en Configuración > General")
        w = info.weather()
        return f"{w['ciudad']}: {w['ahora']['temperatura']} grados, {w['ahora']['cielo']}"

    _check("Clima", weather_check)
    _check("Spotify vinculado", lambda: ("[OK]", "si") if spotify.is_linked() else ("[FALTA]", "Configuración > Integraciones"))
    _check("Google vinculado", lambda: ("[OK]", "si") if google_ws.is_linked() else ("[FALTA]", "opcional: Configuración > Integraciones"))

    def wake() -> Any:
        from jarvis.audio.wakeword import WakeWord

        ww = WakeWord(get_config().get("activacion.modelo_wake_word", "hey_jarvis"))
        ww.process(b"\x00" * 2560)
        return "modelo cargado"

    _check("Palabra de activacion", wake)

    def microphone() -> Any:
        import numpy as np
        import sounddevice as sd

        if sd.default.device[0] < 0:
            from pycaw.pycaw import AudioUtilities

            disabled = [d.FriendlyName for d in AudioUtilities.GetAllDevices(data_flow=1, device_state=2)]
            hint = f" Deshabilitados: {', '.join(disabled)}." if disabled else ""
            return ("[ERROR]", "Windows no tiene ningun microfono habilitado (Configuracion > Sistema > Sonido > Entrada)." + hint)
        data = sd.rec(int(2 * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        peak = int(np.abs(data).max())
        name = sd.query_devices(kind="input")["name"]
        return ("[OK]" if peak > 50 else "[SILENCIO]", f"{name}, pico {peak} en 2 s")

    _check("Microfono (2 s)", microphone)

    if play_sound:
        def sound() -> Any:
            from jarvis.audio.io import AudioIO, chime

            out = AudioIO(lambda _f: None)
            out.play(chime("activar", 0.3))
            time.sleep(0.6)
            out.close()
            return "sonido de activacion reproducido"

        _check("Parlantes", sound)

    def gemini() -> Any:
        key = env("GEMINI_API_KEY")
        if not key:
            return ("[FALTA]", "sin clave")
        from jarvis.brain.text import make_client

        client = make_client(key)
        names = [m.name for m in client.models.list()]
        cfg = get_config()
        wanted = [cfg.get("gemini.modelo_voz")] + list(cfg.get("gemini.modelos_texto", []))
        wanted = [w for w in wanted if "live" not in w]
        missing = [w for w in wanted if not any(n.endswith(w) for n in names)]
        return ("[OK]" if not missing else "[AVISO]", "modelos disponibles" if not missing else f"no figuran: {', '.join(missing)}")

    _check("Gemini (lista de modelos, sin gastar cupo)", gemini)

    print("-" * 60)
    errors = [label for badge, label in _results if badge == "[ERROR]"]
    print(f"{len(_results) - len(errors)}/{len(_results)} sin errores." + (f" Con error: {', '.join(errors)}" if errors else ""))
    return 1 if errors else 0
