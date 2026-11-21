from __future__ import annotations

import ctypes
import datetime as dt
import os
import subprocess
import time
from pathlib import Path
from typing import Annotated, Literal

import psutil
import win32clipboard
import win32con

from jarvis.textutil import normalize
from jarvis.tools import _keys
from jarvis.tools.registry import ToolError, tool

SETTINGS_PAGES = {
    "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "red": "ms-settings:network-status",
    "internet": "ms-settings:network-status",
    "pantalla": "ms-settings:display",
    "sonido": "ms-settings:sound",
    "energia": "ms-settings:powersleep",
    "bateria": "ms-settings:batterysaver",
    "actualizaciones": "ms-settings:windowsupdate",
    "windows update": "ms-settings:windowsupdate",
    "notificaciones": "ms-settings:notifications",
    "aplicaciones": "ms-settings:appsfeatures",
    "apps": "ms-settings:appsfeatures",
    "inicio": "ms-settings:startupapps",
    "privacidad": "ms-settings:privacy",
    "personalizacion": "ms-settings:personalization",
    "fondo": "ms-settings:personalization-background",
    "colores": "ms-settings:colors",
    "almacenamiento": "ms-settings:storagesense",
    "mouse": "ms-settings:mousetouchpad",
    "teclado": "ms-settings:typing",
    "idioma": "ms-settings:regionlanguage",
    "hora": "ms-settings:dateandtime",
    "cuentas": "ms-settings:yourinfo",
    "impresoras": "ms-settings:printers",
    "proyectar": "ms-settings:project",
    "concentracion": "ms-settings:focus",
    "modo avion": "ms-settings:network-airplanemode",
}


def _pictures_dir() -> Path:
    try:
        from win32com.shell import shell, shellcon

        return Path(shell.SHGetKnownFolderPath(shellcon.FOLDERID_Pictures))
    except Exception:
        return Path.home() / "Pictures"


@tool("Fija el brillo de la pantalla (0 a 100).")
def fijar_brillo(nivel: Annotated[int, "Porcentaje de 0 a 100"]) -> dict:
    import screen_brightness_control as sbc

    level = max(0, min(100, int(nivel)))
    sbc.set_brightness(level)
    return {"brillo": level}


@tool("Sube o baja el brillo de la pantalla. Positivo sube, negativo baja.")
def cambiar_brillo(cambio: Annotated[int, "Puntos a sumar o restar, ej. 20 o -20"] = 20) -> dict:
    import screen_brightness_control as sbc

    current = sbc.get_brightness()[0]
    level = max(0, min(100, current + int(cambio)))
    sbc.set_brightness(level)
    return {"antes": current, "brillo": level}


def system_status() -> dict:
    battery = psutil.sensors_battery()
    disk = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    uptime = dt.timedelta(seconds=int(time.time() - psutil.boot_time()))
    status = {
        "cpu_porcentaje": psutil.cpu_percent(interval=0.4),
        "ram_porcentaje": psutil.virtual_memory().percent,
        "disco_libre_gb": round(disk.free / 1e9, 1),
        "encendida_hace": str(uptime),
    }
    if battery is not None:
        status["bateria_porcentaje"] = round(battery.percent)
        status["cargando"] = bool(battery.power_plugged)
        if not battery.power_plugged and battery.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
            status["bateria_restante"] = str(dt.timedelta(seconds=int(battery.secsleft)))
    return status


@tool("Estado de la PC: bateria, si esta cargando, CPU, memoria RAM, disco libre y tiempo encendida.")
def estado_sistema() -> dict:
    return system_status()


@tool("Bloquea la PC (pantalla de inicio de sesion).")
def bloquear_pc() -> dict:
    ctypes.windll.user32.LockWorkStation()
    return {"ok": True}


@tool(
    "Apaga, reinicia, suspende, hiberna o cierra la sesion de la PC.",
    confirm=True,
)
def energia(accion: Literal["apagar", "reiniciar", "suspender", "hibernar", "cerrar_sesion"]) -> dict:
    if accion == "apagar":
        subprocess.Popen(["shutdown", "/s", "/t", "5"], creationflags=subprocess.CREATE_NO_WINDOW)
    elif accion == "reiniciar":
        subprocess.Popen(["shutdown", "/r", "/t", "5"], creationflags=subprocess.CREATE_NO_WINDOW)
    elif accion == "cerrar_sesion":
        subprocess.Popen(["shutdown", "/l"], creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        ctypes.windll.powrprof.SetSuspendState(accion == "hibernar", False, False)
    return {"accion": accion, "en": "5 segundos" if accion in ("apagar", "reiniciar") else "ahora"}


@tool("Cancela un apagado o reinicio programado.")
def cancelar_apagado() -> dict:
    proc = subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return {"cancelado": proc.returncode == 0}


@tool("Enciende o apaga el WiFi.")
async def wifi_encender(encender: Annotated[bool, "true enciende, false apaga"] = True) -> dict:
    from jarvis.tools.bluetooth import set_radio

    return {"wifi": await set_radio("wifi", encender)}


def take_screenshot() -> Path:
    from PIL import ImageGrab

    folder = _pictures_dir() / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"captura_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return path


@tool("Saca una captura de pantalla (se guarda en Imagenes\\Jarvis) y opcionalmente la envia al celular por Telegram.")
def captura_pantalla(enviar_al_celular: Annotated[bool, "Enviarla por Telegram"] = False) -> dict:
    path = take_screenshot()
    result: dict = {"archivo": str(path)}
    if enviar_al_celular:
        from jarvis.tools.phone import send_photo

        result["enviada"] = send_photo(path, "Captura de pantalla")
    return result


def get_clipboard() -> str:
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return ""
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


@tool("Lee el texto copiado en el portapapeles.")
def leer_portapapeles() -> dict:
    text = get_clipboard()
    return {"texto": text[:3000], "recortado": len(text) > 3000} if text else {"texto": "", "nota": "El portapapeles no tiene texto."}


@tool("Copia un texto al portapapeles.")
def copiar_al_portapapeles(texto: Annotated[str, "Texto a copiar"]) -> dict:
    set_clipboard(texto)
    return {"copiado": len(texto)}


def open_target(name: str) -> bool:
    import webbrowser

    from jarvis.tools import apps
    from jarvis.tools.web import SITES

    key = normalize(name)
    if key in SITES:
        webbrowser.open(SITES[key])
        return True
    app, _ = apps.resolve_app(name)
    if app is not None:
        apps.launch(app)
        return True
    return False


@tool(
    "Escribe un texto en una app como si el usuario lo tecleara: en el chat de Gemini o ChatGPT, WhatsApp Web, "
    "Word, Microsoft To Do, Reloj, un buscador, etc. Con 'ventana' primero trae esa app al frente (si no esta "
    "abierta, la abre). Con 'campo' primero hace clic en ese cuadro de texto o boton (ej. 'Agregar una tarea', "
    "'Buscar'). Escriba EXACTAMENTE lo que el usuario dicto, sin reformularlo. Con enviar=true presiona Enter.",
    timeout=30,
)
def escribir_texto(
    texto: Annotated[str, "Texto literal a escribir"],
    ventana: Annotated[str, "App o titulo de la ventana donde escribir; vacio = la ventana activa"] = "",
    enviar: Annotated[bool, "Presionar Enter al terminar"] = False,
    campo: Annotated[str, "Nombre del campo o cuadro donde hacer clic antes de escribir (opcional)"] = "",
    limpiar: Annotated[bool, "true para reemplazar el texto previo del campo (Ctrl+A)"] = False,
) -> dict:
    from jarvis.tools import apps, screen

    target = ""
    hwnd = None
    if ventana:
        window = apps.wait_window(ventana, seconds=2)
        if window is None and open_target(ventana):
            window = apps.wait_window(ventana, seconds=15)
            time.sleep(1.5)
        if window is None:
            raise ToolError(f"No encontre ninguna ventana de '{ventana}'. Abrala primero.")
        if not apps.focus(window["hwnd"]):
            raise ToolError(f"Windows no me dejo pasar a '{window['titulo']}'. Haga clic en el cuadro de texto y repita.")
        target = window["titulo"]
        hwnd = window["hwnd"]
        time.sleep(0.35)
    if campo.strip():
        hit = screen.uia_locate(hwnd, campo.strip(), threshold=72.0)
        if hit is not None:
            screen.click_at(int(hit["x"]), int(hit["y"]), "clic")
            time.sleep(0.2)
    if limpiar:
        _keys.press_combo("ctrl+a")
        time.sleep(0.05)
    previous = get_clipboard()
    set_clipboard(texto)
    time.sleep(0.08)
    _keys.press_combo("ctrl+v")
    time.sleep(0.6)
    if enviar:
        _keys.tap(_keys.VK["enter"])
        time.sleep(0.2)
    if previous:
        set_clipboard(previous)
    return {"escrito": len(texto), "ventana": target or "la activa", "enviado": enviar}


@tool(
    "Abre el dictado por voz de Windows (Win+H) para que el usuario dicte un texto largo (un prompt para una IA, "
    "un mensaje, un correo) directamente en el cuadro de texto. Con 'ventana' primero lleva el foco a esa app "
    "(esperando si se esta abriendo). Cierra esta conversacion para que JARVIS no escuche el dictado.",
    modes={"voz"},
    timeout=15,
)
async def dictado_windows(
    ventana: Annotated[str, "App o pagina donde se va a dictar (ej. 'Gemini', 'ChatGPT', 'WhatsApp'); vacio = la activa"] = "",
) -> dict:
    import asyncio

    from jarvis.services import services
    from jarvis.tools import apps
    from jarvis.tools.registry import in_worker

    engine = services.engine

    async def open_after_session() -> None:
        for _ in range(60):
            if engine is None or engine.mode == "idle":
                break
            await asyncio.sleep(0.25)
        if ventana:
            window = await in_worker(apps.wait_window, ventana)
            if window is not None:
                await in_worker(apps.focus, window["hwnd"])
                await asyncio.sleep(0.6)
        await asyncio.sleep(0.4)
        await in_worker(_keys.press_combo, "win+h")

    if engine is not None:
        engine.request_session_end()
        engine.spawn(open_after_session())
    else:
        await open_after_session()
    return {
        "ok": True,
        "nota": "Avise en una sola frase corta que abre el dictado (ej. 'Le abro el dictado, adelante'); "
        "se abre cuando usted termina de hablar.",
    }


@tool("Presiona una combinacion de teclas en la ventana activa, ej. 'ctrl+c', 'alt+tab', 'ctrl+shift+esc', 'f5'.")
def presionar_teclas(combinacion: Annotated[str, "Teclas separadas por +"]) -> dict:
    try:
        _keys.press_combo(combinacion)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"teclas": combinacion}


@tool("Abre una pagina de Configuracion de Windows (bluetooth, wifi, pantalla, sonido, energia, actualizaciones...).")
def abrir_configuracion(pagina: Annotated[str, "Seccion; vacio = inicio de Configuracion"] = "") -> dict:
    from jarvis.tools import apps

    key = normalize(pagina)
    uri = SETTINGS_PAGES.get(key)
    if uri is None:
        uri = next((v for k, v in SETTINGS_PAGES.items() if key and (key in k or k in key)), "ms-settings:")
    os.startfile(uri)
    try:
        win = apps.wait_window("Configuración", seconds=2.5)
        if win is not None:
            apps.focus(win["hwnd"])
            time.sleep(0.35)
    except Exception:
        pass
    return {"abierta": uri}


@tool("Vacia la papelera de reciclaje (no se puede deshacer).", confirm=True)
def vaciar_papelera() -> dict:
    result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
    if result not in (0, -2147418113):
        raise ToolError(f"Windows devolvio el codigo {result}.")
    return {"ok": True}
