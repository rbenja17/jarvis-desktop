from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from rapidfuzz import fuzz

from jarvis.config import BASE_DIR, data_path, get_config
from jarvis.textutil import normalize
from jarvis.tools.registry import ToolError, tool

MAX_DEPTH = 4
MAX_ENTRIES_PER_ROOT = 15000
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "appdata", "$recycle.bin", ".cache", "dist", "build"}


def known_folders() -> dict[str, Path]:
    folders: dict[str, Path] = {}
    try:
        from win32com.shell import shell, shellcon

        ids = {
            "escritorio": shellcon.FOLDERID_Desktop,
            "documentos": shellcon.FOLDERID_Documents,
            "descargas": shellcon.FOLDERID_Downloads,
            "imagenes": shellcon.FOLDERID_Pictures,
            "musica": shellcon.FOLDERID_Music,
            "videos": shellcon.FOLDERID_Videos,
        }
        for name, fid in ids.items():
            try:
                folders[name] = Path(shell.SHGetKnownFolderPath(fid))
            except Exception:
                pass
    except ImportError:
        pass
    home = Path.home()
    for name, sub in {"escritorio": "Desktop", "documentos": "Documents", "descargas": "Downloads",
                      "imagenes": "Pictures", "musica": "Music", "videos": "Videos"}.items():
        folders.setdefault(name, home / sub)
    folders["usuario"] = home
    folders["capturas"] = folders["imagenes"] / "Jarvis"
    folders["jarvis"] = BASE_DIR
    folders["notas"] = data_path("notas.md").parent
    for extra in get_config().get("archivos.carpetas_extra", []) or []:
        path = Path(os.path.expandvars(extra)).expanduser()
        folders[normalize(path.name)] = path
    return folders


def search_roots() -> list[Path]:
    folders = known_folders()
    roots = [folders[k] for k in ("escritorio", "documentos", "descargas", "imagenes", "musica", "videos")]
    roots += [Path(os.path.expandvars(p)).expanduser() for p in get_config().get("archivos.carpetas_extra", []) or []]
    unique: list[Path] = []
    for root in roots:
        if root.exists() and root not in unique:
            unique.append(root)
    return unique


def find_files(query: str, roots: list[Path], limit: int = 5) -> list[tuple[Path, float]]:
    q = normalize(query)
    scored: list[tuple[Path, float]] = []
    for root in roots:
        seen = 0
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            if depth >= MAX_DEPTH:
                dirnames[:] = []
            for name in filenames + dirnames:
                seen += 1
                stem = normalize(Path(name).stem)
                score = 100.0 if stem == q else float(max(fuzz.partial_ratio(q, stem), fuzz.token_set_ratio(q, stem)))
                if score >= 75:
                    scored.append((Path(dirpath) / name, score))
            if seen > MAX_ENTRIES_PER_ROOT:
                break
    scored.sort(key=lambda p: (-p[1], len(str(p[0]))))
    return scored[:limit]


@tool("Abre una carpeta: escritorio, documentos, descargas, imagenes, musica, videos, capturas, usuario o una ruta.")
def abrir_carpeta(nombre: Annotated[str, "Carpeta conocida o ruta completa"]) -> dict:
    folders = known_folders()
    path = folders.get(normalize(nombre))
    if path is None:
        candidate = Path(os.path.expandvars(nombre)).expanduser()
        if candidate.exists():
            path = candidate
    if path is None:
        raise ToolError(f"No conozco la carpeta '{nombre}'. Conocidas: {', '.join(folders)}.")
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(path)
    return {"abierta": str(path)}


@tool(
    "Busca archivos o carpetas por nombre en Escritorio, Documentos, Descargas, Imagenes, Musica y Videos; "
    "opcionalmente abre el mejor resultado.",
    timeout=40,
)
def buscar_archivo(
    nombre: Annotated[str, "Nombre o parte del nombre del archivo"],
    abrir: Annotated[bool, "Abrir el mejor resultado"] = False,
) -> dict:
    results = find_files(nombre, search_roots())
    if not results:
        raise ToolError(f"No encontre archivos parecidos a '{nombre}'.")
    if abrir:
        os.startfile(results[0][0])
    return {
        "resultados": [{"ruta": str(p), "coincidencia": round(s)} for p, s in results],
        "abierto": str(results[0][0]) if abrir else None,
    }


@tool("Abre un archivo por su ruta completa con el programa predeterminado.")
def abrir_archivo(ruta: Annotated[str, "Ruta completa del archivo"]) -> dict:
    path = Path(os.path.expandvars(ruta)).expanduser()
    if not path.exists():
        raise ToolError("Ese archivo no existe.")
    os.startfile(path)
    return {"abierto": str(path)}
