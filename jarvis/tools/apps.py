from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import time
from typing import Annotated, Literal

import psutil
import win32con
import win32gui
import win32process

from jarvis.config import data_path, get_config
from jarvis.textutil import best_match, normalize
from jarvis.tools import _keys
from jarvis.tools.registry import ToolError, tool

log = logging.getLogger(__name__)

CACHE_MAX_AGE = 24 * 3600
PROTECTED = {
    "system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "svchost.exe", "dwm.exe", "fontdrvhost.exe", "explorer.exe", "python.exe", "pythonw.exe",
}

_PS_START_APPS = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
)


def start_apps(force: bool = False) -> list[dict]:
    path = data_path("cache", "apps.json")
    if not force and path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE:
        try:
            return json.loads(path.read_text("utf-8"))
        except ValueError:
            pass
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_START_APPS],
        capture_output=True,
        timeout=40,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    raw = json.loads(proc.stdout.decode("utf-8", errors="replace") or "[]")
    if isinstance(raw, dict):
        raw = [raw]
    apps = [{"name": d["Name"], "id": d["AppID"]} for d in raw if d.get("Name") and d.get("AppID")]
    path.write_text(json.dumps(apps, ensure_ascii=False), "utf-8")
    return apps


def _aliases() -> dict[str, str]:
    return {normalize(k): v for k, v in get_config().section("apps").get("alias", {}).items()}


def resolve_app(query: str, apps: list[dict] | None = None) -> tuple[dict | None, list[str]]:
    target = _aliases().get(normalize(query), query)
    apps = apps if apps is not None else start_apps()
    best, candidates = best_match(target, apps, key=lambda a: a["name"])
    return best, [a["name"] for a, score in candidates if score >= 55][:4]


def launch(app: dict) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['id']}"])


@tool(
    "Abre una aplicacion instalada por su nombre (Spotify, Chrome, Word, Calculadora, Reloj, Microsoft To Do, etc.) "
    "y trae su ventana al frente, devolviendo los botones y controles principales visibles para poder usarla."
)
def abrir_app(nombre: Annotated[str, "Nombre de la app tal como lo dijo el usuario"]) -> dict:
    app, suggestions = resolve_app(nombre)
    if app is None:
        app, suggestions = resolve_app(nombre, start_apps(force=True))
    if app is None:
        if suggestions:
            raise ToolError(f"No encontre '{nombre}'. Parecidas: {', '.join(suggestions)}.")
        raise ToolError(f"No encontre ninguna app llamada '{nombre}'.")
    launch(app)
    result: dict = {"abierta": app["name"]}
    try:
        window = wait_window(app["name"], seconds=3.0) or wait_window(nombre, seconds=1.0)
        if window is not None:
            focus(window["hwnd"])
            time.sleep(0.35)
            result["ventana"] = window["titulo"]
            from jarvis.tools import screen

            summary = screen.uia_summary(window["hwnd"])
            if summary.get("controles"):
                result["controles_visibles"] = summary["controles"][:25]
    except Exception:
        log.debug("No se pudo enfocar o inspeccionar la ventana de %s", app["name"], exc_info=True)
    return result


def _is_cloaked(hwnd: int) -> bool:
    value = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))
    except OSError:
        return False
    return value.value != 0


def list_windows() -> list[dict]:
    windows: list[dict] = []
    own_pid = os.getpid()

    def callback(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or title == "Program Manager":
            return True
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return True
        if _is_cloaked(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == own_pid:
            return True
        try:
            exe = psutil.Process(pid).name()
        except psutil.Error:
            exe = ""
        windows.append({"hwnd": hwnd, "titulo": title, "proceso": exe, "pid": pid})
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def find_window(query: str) -> tuple[dict | None, bool]:
    target = _aliases().get(normalize(query), query)
    windows = list_windows()
    if not windows:
        return None, False
    by_exe, exe_scores = best_match(target, windows, key=lambda w: w["proceso"].removesuffix(".exe"), threshold=85)
    by_title, title_scores = best_match(target, windows, key=lambda w: w["titulo"], threshold=72)
    exe_score = exe_scores[0][1] if exe_scores else 0
    title_score = title_scores[0][1] if title_scores else 0
    if by_exe and exe_score >= title_score:
        return by_exe, True
    if by_title:
        return by_title, False
    if by_exe:
        return by_exe, True
    return None, False


def _foreground() -> dict:
    hwnd = win32gui.GetForegroundWindow()
    return {"hwnd": hwnd, "titulo": win32gui.GetWindowText(hwnd)}


VK_F24 = 0x87


def focus(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    foreground = win32gui.GetForegroundWindow()
    if foreground == hwnd:
        return True
    own_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    attached = bool(fg_thread and fg_thread != own_thread and user32.AttachThreadInput(fg_thread, own_thread, True))
    try:
        win32gui.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, own_thread, False)
    if win32gui.GetForegroundWindow() != hwnd:
        _keys.tap(VK_F24)
        user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    return win32gui.GetForegroundWindow() == hwnd


def wait_window(query: str, seconds: float = 8.0) -> dict | None:
    deadline = time.monotonic() + seconds
    while True:
        window, _ = find_window(query)
        if window is not None or time.monotonic() >= deadline:
            return window
        time.sleep(0.4)


@tool("Cierra una aplicacion o ventana abierta por su nombre (cierre normal, puede pedir guardar).")
def cerrar_app(nombre: Annotated[str, "App o titulo de ventana"]) -> dict:
    window, by_process = find_window(nombre)
    if window is None:
        raise ToolError(f"No veo ninguna ventana abierta de '{nombre}'.")
    targets = [w for w in list_windows() if w["proceso"] == window["proceso"]] if by_process else [window]
    for w in targets:
        win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
    return {"cerradas": [w["titulo"] for w in targets]}


@tool(
    "Fuerza el cierre de una app colgada matando su proceso. Se pierden los cambios sin guardar.",
    confirm=True,
)
def forzar_cierre_app(nombre: Annotated[str, "App o proceso"]) -> dict:
    window, _ = find_window(nombre)
    exe = window["proceso"] if window else ""
    if not exe:
        procs = list(psutil.process_iter(["name"]))
        match, _ = best_match(nombre, procs, key=lambda p: (p.info["name"] or "").removesuffix(".exe"), threshold=85)
        exe = match.info["name"] if match else ""
    if not exe:
        raise ToolError(f"No encontre el proceso de '{nombre}'.")
    if exe.lower() in PROTECTED:
        raise ToolError(f"Por seguridad no fuerzo el cierre de {exe}.")
    killed = 0
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").lower() == exe.lower():
            try:
                proc.kill()
                killed += 1
            except psutil.Error:
                pass
    return {"proceso": exe, "procesos_terminados": killed}


@tool("Lista las ventanas abiertas con su titulo y programa.")
def listar_ventanas() -> dict:
    return {"ventanas": [{"titulo": w["titulo"][:80], "programa": w["proceso"]} for w in list_windows()][:30]}


@tool("Maneja una ventana: enfocar (traer al frente), minimizar, maximizar o restaurar. Sin nombre usa la ventana activa.")
def ventana(
    accion: Literal["enfocar", "minimizar", "maximizar", "restaurar"],
    nombre: Annotated[str, "App o titulo de la ventana; vacio = ventana activa"] = "",
) -> dict:
    if nombre:
        window, _ = find_window(nombre)
        if window is None:
            raise ToolError(f"No veo ninguna ventana de '{nombre}'.")
    else:
        window = _foreground()
    hwnd = window["hwnd"]
    if accion == "enfocar":
        focus(hwnd)
    else:
        cmd = {"minimizar": win32con.SW_MINIMIZE, "maximizar": win32con.SW_MAXIMIZE, "restaurar": win32con.SW_RESTORE}[accion]
        win32gui.ShowWindow(hwnd, cmd)
    return {"ventana": window["titulo"], "accion": accion}


@tool("Minimiza todo y muestra el escritorio (Win+D).")
def mostrar_escritorio() -> dict:
    _keys.press_combo("win+d")
    return {"ok": True}
