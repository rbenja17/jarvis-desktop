from __future__ import annotations

import logging
import platform
import threading
import time
from typing import Annotated, Literal

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyPKCE

from jarvis.config import data_path, env
from jarvis.textutil import best_match, normalize
from jarvis.tools.registry import ToolError, tool

log = logging.getLogger(__name__)

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = " ".join(
    [
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "playlist-read-private",
        "playlist-read-collaborative",
        "user-library-read",
        "user-library-modify",
    ]
)
LIKED_WORDS = {"me gusta", "mis me gusta", "favoritas", "mis favoritas", "liked", "liked songs", "canciones que me gustan"}

_lock = threading.Lock()
_playlists_cache: tuple[float, list[dict]] = (0.0, [])


def auth_manager(open_browser: bool = False) -> SpotifyPKCE:
    client_id = env("SPOTIFY_CLIENT_ID")
    if not client_id:
        raise ToolError("Spotify no esta configurado: falta SPOTIFY_CLIENT_ID (Configuracion > Integraciones).")
    return SpotifyPKCE(
        client_id=client_id,
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(data_path("tokens", "spotify.json"))),
        open_browser=open_browser,
    )


def client() -> spotipy.Spotify:
    manager = auth_manager()
    token = manager.validate_token(manager.cache_handler.get_cached_token())
    if not token:
        raise ToolError("Spotify no esta vinculado. Vinculalo desde Configuracion > Integraciones (o scripts\\spotify_login.py).")
    return spotipy.Spotify(auth_manager=manager, requests_timeout=10, retries=2)


def is_linked() -> bool:
    try:
        client()
        return True
    except ToolError:
        return False


def my_playlists(sp: spotipy.Spotify) -> list[dict]:
    global _playlists_cache
    with _lock:
        ts, cached = _playlists_cache
        if cached and time.time() - ts < 600:
            return cached
    items: list[dict] = []
    page = sp.current_user_playlists(limit=50)
    while page and len(items) < 300:
        items.extend(p for p in page.get("items", []) if p)
        page = sp.next(page) if page.get("next") else None
    with _lock:
        _playlists_cache = (time.time(), items)
    return items


def match_playlist(query: str, playlists: list[dict]) -> dict | None:
    best, _ = best_match(query, playlists, key=lambda p: p.get("name", ""), threshold=80)
    return best


def _pick_device(devices: list[dict]) -> dict | None:
    if not devices:
        return None
    active = next((d for d in devices if d.get("is_active")), None)
    if active:
        return active
    host = normalize(platform.node())
    computer = [d for d in devices if d.get("type") == "Computer"]
    same_host = next((d for d in computer if normalize(d.get("name", "")) == host), None)
    return same_host or (computer[0] if computer else devices[0])


def ensure_device(sp: spotipy.Spotify) -> dict:
    device = _pick_device(sp.devices().get("devices", []))
    if device:
        return device
    from jarvis.tools import apps

    app, _ = apps.resolve_app("Spotify")
    if app is None:
        raise ToolError("No hay ningun dispositivo de Spotify activo y no encontre la app instalada.")
    apps.launch(app)
    for _ in range(20):
        time.sleep(1)
        device = _pick_device(sp.devices().get("devices", []))
        if device:
            return device
    raise ToolError("Abri Spotify pero todavia no aparece como dispositivo. Intente de nuevo en unos segundos.")


def _start(sp: spotipy.Spotify, device: dict, **kwargs) -> None:
    try:
        sp.start_playback(device_id=device["id"], **kwargs)
    except spotipy.SpotifyException as exc:
        if exc.http_status not in (403, 404):
            raise
        sp.transfer_playback(device["id"], force_play=False)
        time.sleep(0.8)
        sp.start_playback(device_id=device["id"], **kwargs)


def _first(results: dict, kind: str) -> dict | None:
    items = (results.get(f"{kind}s") or {}).get("items") or []
    return next((i for i in items if i), None)


@tool(
    "Reproduce musica en Spotify: una playlist (primero busca entre las del usuario), una cancion, "
    "un album o un artista. Para 'mis me gusta' use tipo playlist y consulta 'me gusta'.",
    timeout=40,
)
def spotify_reproducir(
    consulta: Annotated[str, "Nombre de la playlist, cancion, album o artista"],
    tipo: Literal["playlist", "cancion", "album", "artista"] = "playlist",
) -> dict:
    sp = client()
    device = ensure_device(sp)
    query = consulta.strip()

    if tipo == "playlist":
        if normalize(query) in LIKED_WORDS:
            user = sp.current_user()["id"]
            _start(sp, device, context_uri=f"spotify:user:{user}:collection")
            return {"reproduciendo": "Tus me gusta", "dispositivo": device["name"]}
        mine = match_playlist(query, my_playlists(sp))
        found = mine or _first(sp.search(q=query, type="playlist", limit=5), "playlist")
        if not found:
            raise ToolError(f"No encontre la playlist '{query}'.")
        _start(sp, device, context_uri=found["uri"])
        return {"reproduciendo": found["name"], "propia": mine is not None, "dispositivo": device["name"]}

    if tipo == "cancion":
        track = _first(sp.search(q=query, type="track", limit=5), "track")
        if not track:
            raise ToolError(f"No encontre la cancion '{query}'.")
        _start(sp, device, context_uri=track["album"]["uri"], offset={"uri": track["uri"]})
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        return {"reproduciendo": track["name"], "artista": artists, "dispositivo": device["name"]}

    kind = "album" if tipo == "album" else "artist"
    item = _first(sp.search(q=query, type=kind, limit=5), kind)
    if not item:
        raise ToolError(f"No encontre {tipo} '{query}'.")
    _start(sp, device, context_uri=item["uri"])
    return {"reproduciendo": item["name"], "tipo": tipo, "dispositivo": device["name"]}


@tool("Controla Spotify en cualquier dispositivo: pausa, reanudar, siguiente, anterior, aleatorio o repetir.")
def spotify_control(
    accion: Literal[
        "pausa", "reanudar", "siguiente", "anterior", "aleatorio_si", "aleatorio_no",
        "repetir_cancion", "repetir_lista", "repetir_no",
    ],
) -> dict:
    sp = client()
    actions = {
        "pausa": lambda: sp.pause_playback(),
        "reanudar": lambda: sp.start_playback(),
        "siguiente": lambda: sp.next_track(),
        "anterior": lambda: sp.previous_track(),
        "aleatorio_si": lambda: sp.shuffle(True),
        "aleatorio_no": lambda: sp.shuffle(False),
        "repetir_cancion": lambda: sp.repeat("track"),
        "repetir_lista": lambda: sp.repeat("context"),
        "repetir_no": lambda: sp.repeat("off"),
    }
    try:
        actions[accion]()
    except spotipy.SpotifyException as exc:
        if exc.http_status == 404:
            raise ToolError("No hay ninguna reproduccion activa en Spotify.") from exc
        raise
    return {"accion": accion}


@tool(
    "Fija o ajusta el volumen de Spotify (0 a 100) en el dispositivo donde esta sonando. "
    "Use 'nivel' para un porcentaje exacto (ej. 50) o 'cambio' para subir/bajar puntos respecto del volumen actual (ej. -10 o 15)."
)
def spotify_volumen(
    nivel: Annotated[int, "Porcentaje fijo de 0 a 100 (-1 para usar cambio)"] = -1,
    cambio: Annotated[int, "Puntos a sumar o restar al volumen actual de Spotify, ej. -10 o 15"] = 0,
) -> dict:
    sp = client()
    current_vol = None
    try:
        playback = sp.current_playback()
        if playback and playback.get("device"):
            current_vol = playback["device"].get("volume_percent")
    except Exception:
        pass
    if cambio != 0 or nivel < 0:
        base = int(current_vol) if current_vol is not None else 50
        level = max(0, min(100, base + (int(cambio) if cambio != 0 else 0)))
    else:
        level = max(0, min(100, int(nivel)))
    try:
        sp.volume(level)
    except spotipy.SpotifyException as exc:
        raise ToolError("Este dispositivo no permite cambiar el volumen desde Spotify.") from exc
    out: dict = {"volumen_spotify": level}
    if current_vol is not None:
        out["antes"] = int(current_vol)
    return out


@tool("Agrega una cancion a la cola de Spotify.")
def spotify_cola(consulta: Annotated[str, "Cancion y opcionalmente artista"]) -> dict:
    sp = client()
    track = _first(sp.search(q=consulta, type="track", limit=5), "track")
    if not track:
        raise ToolError(f"No encontre la cancion '{consulta}'.")
    sp.add_to_queue(track["uri"])
    return {"en_cola": track["name"], "artista": ", ".join(a["name"] for a in track.get("artists", []))}


@tool("Guarda la cancion que esta sonando en 'Me gusta' de Spotify.")
def spotify_me_gusta() -> dict:
    sp = client()
    current = sp.current_playback()
    item = (current or {}).get("item")
    if not item:
        raise ToolError("No esta sonando nada en Spotify.")
    sp.current_user_saved_tracks_add([item["uri"]])
    return {"guardada": item["name"]}


@tool("Dice que suena en Spotify en cualquier dispositivo (PC, celular, parlante), con volumen actual, aleatorio y repetir.")
def spotify_que_suena() -> dict:
    sp = client()
    current = sp.current_playback()
    if not current or not current.get("item"):
        return {"resultado": "No esta sonando nada en Spotify."}
    item = current["item"]
    device = current.get("device") or {}
    return {
        "cancion": item.get("name"),
        "artista": ", ".join(a["name"] for a in item.get("artists", [])),
        "sonando": current.get("is_playing"),
        "dispositivo": device.get("name"),
        "volumen_spotify": device.get("volume_percent"),
        "aleatorio": current.get("shuffle_state"),
        "repetir": current.get("repeat_state"),
    }


@tool("Lista los dispositivos de Spotify disponibles (PC, celular, parlantes) o pasa la musica a uno de ellos.")
def spotify_dispositivos(
    pasar_a: Annotated[str, "Nombre del dispositivo al que pasar la musica; vacio = solo listar"] = "",
) -> dict:
    sp = client()
    devices = sp.devices().get("devices", [])
    if not pasar_a:
        return {"dispositivos": [{"nombre": d["name"], "tipo": d["type"], "activo": d["is_active"]} for d in devices]}
    match, _ = best_match(pasar_a, devices, key=lambda d: d["name"], threshold=60)
    if match is None:
        raise ToolError(f"No veo '{pasar_a}'. Disponibles: {', '.join(d['name'] for d in devices) or 'ninguno'}.")
    sp.transfer_playback(match["id"], force_play=True)
    return {"sonando_en": match["name"]}
