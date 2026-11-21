from __future__ import annotations

import asyncio
import ctypes
import io
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from rapidfuzz import fuzz

from jarvis.config import get_config
from jarvis.services import services
from jarvis.textutil import normalize
from jarvis.tools.registry import ToolError, in_worker, tool

log = logging.getLogger(__name__)

MAX_WIDTH = 1600
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL = 0x0800

CONTROL_TYPES: dict[int, str] = {
    50000: "boton",
    50002: "casilla",
    50003: "selector",
    50004: "campo",
    50005: "enlace",
    50007: "item",
    50011: "menu",
    50013: "opcion",
    50015: "deslizador",
    50016: "numero",
    50019: "pestana",
    50020: "texto",
    50024: "item",
    50031: "boton",
}

_PREFIX_RE = re.compile(
    r"^(el |la |los |las |un |una )?(boton|pestana|opcion|campo|cuadro|casilla|menu|selector|enlace|item|icono|seccion)( de| del| que dice)?\s+",
    re.IGNORECASE,
)
_uia_local = threading.local()


@dataclass
class Shot:
    jpeg: bytes
    left: int
    top: int
    width: int
    height: int


def _dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def _allowed() -> None:
    if not get_config().get("pantalla.permitir", True):
        raise ToolError("Ver la pantalla esta desactivado en config.toml ([pantalla] permitir = false).")


def _get_uia():
    uia = getattr(_uia_local, "uia", None)
    if uia is not None:
        return uia
    import comtypes.client

    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation)
    _uia_local.uia = uia
    return uia


def _target_hwnd(ventana: str = "") -> tuple[int, str]:
    import win32gui

    from jarvis.tools import apps

    if ventana.strip():
        win = apps.wait_window(ventana, seconds=2.0)
        if win is None:
            app, _ = apps.resolve_app(ventana)
            if app is not None:
                apps.launch(app)
                win = apps.wait_window(app["name"], seconds=4.0) or apps.wait_window(ventana, seconds=1.5)
        if win is not None:
            apps.focus(win["hwnd"])
            time.sleep(0.25)
            return win["hwnd"], win["titulo"]
    hwnd = win32gui.GetForegroundWindow()
    return hwnd, win32gui.GetWindowText(hwnd) if hwnd else ""


def _candidate_hwnds(hwnd: int) -> list[int]:
    import win32gui

    out = [hwnd]

    def _child(ch: int, _: object) -> bool:
        try:
            cls = win32gui.GetClassName(ch)
            if cls in ("Windows.UI.Core.CoreWindow", "Microsoft.UI.Content.DesktopChildSiteBridge"):
                out.append(ch)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _child, None)
    except Exception:
        pass
    return out


def uia_elements(hwnd: int | None = None, limit: int = 350) -> list[dict[str, Any]]:
    import win32gui

    _dpi_aware()
    target = hwnd or win32gui.GetForegroundWindow()
    if not target:
        return []
    try:
        uia = _get_uia()
    except Exception:
        log.debug("No se pudo inicializar UIAutomation", exc_info=True)
        return []

    seen: set[tuple[str, str, int, int]] = set()
    elements: list[dict[str, Any]] = []
    cond = uia.CreateTrueCondition()

    for h in _candidate_hwnds(target):
        try:
            root = uia.ElementFromHandle(h)
            arr = root.FindAll(4, cond)
        except Exception:
            continue
        count = min(limit, arr.Length if arr else 0)
        for i in range(count):
            try:
                el = arr.GetElement(i)
                ctype = int(el.CurrentControlType)
                kind = CONTROL_TYPES.get(ctype)
                if kind is None:
                    continue
                if el.CurrentIsOffscreen:
                    continue
                rect = el.CurrentBoundingRectangle
                w = int(rect.right - rect.left)
                ht = int(rect.bottom - rect.top)
                if w < 6 or ht < 6:
                    continue
                name = (el.CurrentName or "").strip()
                help_text = (el.CurrentHelpText or "").strip()
                aid = (el.CurrentAutomationId or "").strip()
                label = name or help_text
                if not label and not aid:
                    continue
                cx = int(rect.left + w // 2)
                cy = int(rect.top + ht // 2)
                key = (kind, label or aid, cx // 8, cy // 8)
                if key in seen:
                    continue
                seen.add(key)
                elements.append(
                    {
                        "tipo": kind,
                        "nombre": label,
                        "aid": aid,
                        "x": cx,
                        "y": cy,
                        "w": w,
                        "h": ht,
                        "interactivo": kind != "texto",
                    }
                )
            except Exception:
                continue
    return elements


def uia_summary(hwnd: int | None = None) -> dict[str, Any]:
    import win32gui

    target = hwnd or win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(target) if target else ""
    elems = uia_elements(target)
    controls: list[str] = []
    texts: list[str] = []
    for el in elems:
        label = el["nombre"]
        if not label or len(label) > 90:
            continue
        if el["interactivo"]:
            entry = f"{el['tipo']}: {label}"
            if entry not in controls:
                controls.append(entry)
        else:
            if label not in texts and len(label) >= 2:
                texts.append(label)
    return {"ventana": title, "controles": controls[:45], "textos": texts[:25]}


def clean_target_query(target: str) -> str:
    norm = normalize(target)
    cleaned = _PREFIX_RE.sub("", norm).strip()
    return cleaned or norm


def score_uia_element(query: str, el: dict[str, Any]) -> float:
    raw_q = normalize(query)
    clean_q = clean_target_query(query)
    name = normalize(el.get("nombre", ""))
    aid = normalize(el.get("aid", ""))
    kind = normalize(el.get("tipo", ""))
    if not name and not aid:
        return 0.0

    candidates = [c for c in (name, f"{kind} {name}".strip(), aid) if c]
    best = 0.0
    for q in (clean_q, raw_q):
        if not q:
            continue
        for cand in candidates:
            if q == cand:
                score = 100.0
            elif len(q) >= 4 and (q in cand or cand in q):
                ratio = min(len(q), len(cand)) / max(len(q), len(cand))
                score = 86.0 + 12.0 * ratio
            else:
                score = max(
                    float(fuzz.WRatio(q, cand)),
                    float(fuzz.token_set_ratio(q, cand)) * 0.95,
                )
            if score > best:
                best = score
    if el.get("interactivo"):
        best += 3.0
    return best


def uia_locate(hwnd: int | None, target: str, threshold: float = 76.0) -> dict[str, Any] | None:
    elems = uia_elements(hwnd)
    if not elems:
        return None
    scored = sorted(((score_uia_element(target, el), el) for el in elems), key=lambda pair: pair[0], reverse=True)
    best_score, best_el = scored[0]
    if best_score < threshold:
        return None
    label = best_el["nombre"] or best_el["aid"]
    return {
        "encontrado": True,
        "x": best_el["x"],
        "y": best_el["y"],
        "detalle": f"{best_el['tipo']}: {label}",
        "via": "uia",
    }


def capture() -> Shot:
    import win32api
    import win32gui
    from PIL import Image, ImageGrab

    _dpi_aware()
    monitor = win32api.MonitorFromWindow(win32gui.GetForegroundWindow(), 1)
    left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Monitor"]
    image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True).convert("RGB")
    if image.width > MAX_WIDTH:
        image = image.resize((MAX_WIDTH, round(image.height * MAX_WIDTH / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return Shot(buffer.getvalue(), left, top, right - left, bottom - top)


def to_screen(shot: Shot, x_norm: float, y_norm: float) -> tuple[int, int]:
    x = shot.left + round(max(0.0, min(1000.0, x_norm)) / 1000 * (shot.width - 1))
    y = shot.top + round(max(0.0, min(1000.0, y_norm)) / 1000 * (shot.height - 1))
    return x, y


def click_at(x: int, y: int, action: str) -> None:
    import win32api

    _dpi_aware()
    win32api.SetCursorPos((x, y))
    time.sleep(0.06)
    if action == "mover":
        return
    down, up = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if action == "clic_derecho" else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
    for _ in range(2 if action == "doble_clic" else 1):
        win32api.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.03)
        win32api.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.08)
    time.sleep(0.35)


def _brain():
    if services.text_brain is None:
        raise ToolError("Falta la clave de Gemini para ver la pantalla.")
    return services.text_brain


async def locate_and_click(
    elemento: str,
    accion: str = "clic",
    ventana: str = "",
) -> dict[str, Any]:
    hwnd, title = await in_worker(_target_hwnd, ventana)
    uia_hit = await in_worker(uia_locate, hwnd, elemento)
    if uia_hit is not None:
        x, y = int(uia_hit["x"]), int(uia_hit["y"])
        await in_worker(click_at, x, y, accion)
        after = await in_worker(uia_summary, hwnd)
        out: dict[str, Any] = {
            "hecho": accion,
            "elemento": uia_hit["detalle"],
            "posicion": [x, y],
        }
        if title:
            out["ventana"] = title
        if after.get("controles"):
            out["controles_ahora"] = after["controles"][:25]
        return out

    shot = await in_worker(capture)
    found = await _brain().locate_on_screen(shot.jpeg, elemento)
    if not found.get("encontrado"):
        after = await in_worker(uia_summary, hwnd)
        hint = f" Controles disponibles: {', '.join(after['controles'][:15])}." if after.get("controles") else ""
        raise ToolError(f"No veo '{elemento}' en la pantalla. {found.get('detalle', '')}{hint}".strip())
    x, y = to_screen(shot, float(found.get("x", 0)), float(found.get("y", 0)))
    await in_worker(click_at, x, y, accion)
    after = await in_worker(uia_summary, hwnd)
    out = {"hecho": accion, "elemento": found.get("detalle", elemento), "posicion": [x, y]}
    if after.get("controles"):
        out["controles_ahora"] = after["controles"][:25]
    return out


@tool(
    "Mira la pantalla o una ventana concreta. Devuelve la lista exacta de botones, pestanas, campos y casillas "
    "interactivas de la aplicacion junto con la descripcion visual. Usela cuando le pidan ver algo ('mira esto', "
    "'que dice este error') o para explorar los controles de una app antes de interactuar.",
    timeout=45,
)
async def ver_pantalla(
    pregunta: Annotated[str, "Que hay que mirar o responder sobre la pantalla"] = "",
    ventana: Annotated[str, "App o ventana que traer al frente antes de mirar (opcional, ej. 'Reloj')"] = "",
) -> dict:
    _allowed()
    hwnd, _ = await in_worker(_target_hwnd, ventana)
    summary, shot = await asyncio.gather(in_worker(uia_summary, hwnd), in_worker(capture))
    result: dict[str, Any] = {}
    if summary.get("ventana"):
        result["ventana"] = summary["ventana"]
    if summary.get("controles"):
        result["controles_interactivos"] = summary["controles"][:35]
    try:
        result["pantalla"] = await _brain().describe_screen(shot.jpeg, pregunta)
    except Exception as exc:
        if not summary.get("controles") and not summary.get("textos"):
            raise
        log.warning("Vision de pantalla fallo (%s); se devuelven controles UIA", exc)
        result["textos_visibles"] = summary.get("textos", [])
        result["pantalla"] = (
            f"Ventana activa: {summary.get('ventana', '')}. "
            f"Controles: {', '.join(summary.get('controles', [])[:20])}. "
            f"Textos: {', '.join(summary.get('textos', [])[:15])}."
        )
    return result


@tool(
    "Hace clic en un elemento visible en una aplicacion o en la pantalla: un boton, una pestana, una casilla, un "
    "menu, una opcion, un enlace o un campo (ej. 'Iniciar sesión de concentración', 'Temporizador', 'Agregar una tarea'). "
    "Con 'ventana' primero trae esa app al frente. Ubica el control al instante por UI Automation (o vision) y devuelve "
    "los controles que quedan visibles despues del clic.",
    timeout=45,
)
async def clic_en_pantalla(
    elemento: Annotated[str, "Nombre o descripcion del boton, pestana, campo o elemento a tocar"],
    accion: Literal["clic", "doble_clic", "clic_derecho", "mover"] = "clic",
    ventana: Annotated[str, "App o ventana donde hacer clic (opcional, ej. 'Reloj', 'Microsoft To Do')"] = "",
) -> dict:
    _allowed()
    return await locate_and_click(elemento, accion, ventana)


@tool(
    "Abre un menu o selector visible en la pantalla y elige una opcion, todo en un paso (mas rapido que mirar y "
    "hacer clic por separado). Ej.: menu 'el selector de modelo que dice Hydrogen', opcion 'Flash Lite'.",
    timeout=60,
)
async def elegir_en_pantalla(
    menu: Annotated[str, "El menu, selector o boton que despliega las opciones, con su texto visible"],
    opcion: Annotated[str, "La opcion a elegir, con su texto visible"],
    ventana: Annotated[str, "App o ventana donde esta el menu (opcional)"] = "",
) -> dict:
    _allowed()
    first = await locate_and_click(menu, "clic", ventana)
    await asyncio.sleep(0.45)
    second = await locate_and_click(opcion, "clic")
    out: dict[str, Any] = {"menu": first.get("elemento", menu), "elegida": second.get("elemento", opcion)}
    if second.get("controles_ahora"):
        out["controles_ahora"] = second["controles_ahora"]
    return out


@tool("Desplaza la pagina o ventana bajo el mouse hacia arriba o hacia abajo (rueda del mouse).")
def desplazar_pantalla(
    direccion: Literal["arriba", "abajo"] = "abajo",
    cantidad: Annotated[int, "Pasos de rueda, 1 a 20"] = 5,
) -> dict:
    import win32api

    steps = max(1, min(20, int(cantidad)))
    win32api.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, (120 if direccion == "arriba" else -120) * steps, 0)
    return {"desplazado": direccion, "pasos": steps}


def _adjust_focus_minutes(hwnd: int, target_minutes: int) -> bool:
    from jarvis.tools import _keys, system

    elems = uia_elements(hwnd)
    if not elems:
        return False
    for el in elems:
        name_norm = normalize(el["nombre"])
        aid_norm = normalize(el["aid"])
        if el["tipo"] in ("campo", "numero") and (
            any(k in name_norm or k in aid_norm for k in ("minuto", "duracion", "tiempo", "duration", "minute", "period"))
            or re.fullmatch(r"\d{1,3}(\s*min(utos)?)?", name_norm)
        ):
            click_at(el["x"], el["y"], "doble_clic")
            time.sleep(0.1)
            _keys.press_combo("ctrl+a")
            time.sleep(0.05)
            system.set_clipboard(str(target_minutes))
            _keys.press_combo("ctrl+v")
            time.sleep(0.1)
            _keys.tap(_keys.VK["enter"])
            time.sleep(0.25)
            return True

    up = uia_locate(hwnd, "Aumentar tiempo", threshold=70) or uia_locate(hwnd, "Más tiempo", threshold=70)
    down = uia_locate(hwnd, "Reducir tiempo", threshold=70) or uia_locate(hwnd, "Menos tiempo", threshold=70)
    current = None
    for el in elems:
        m = re.search(r"\b(\d{1,3})\s*(?:min|minutos)\b", normalize(el["nombre"]))
        if m:
            val = int(m.group(1))
            if 5 <= val <= 240:
                current = val
                break
    if current is not None and (up or down) and current != target_minutes:
        btn = up if target_minutes > current else down
        if btn:
            steps = min(16, max(1, round(abs(target_minutes - current) / 15)))
            for _ in range(steps):
                click_at(int(btn["x"]), int(btn["y"]), "clic")
            return True
    return False


@tool(
    "Maneja la aplicacion Reloj de Windows en pantalla: inicia o detiene una sesion de concentracion (con duracion "
    "en minutos opcional), inicia o detiene el cronometro o el temporizador visual de la app Reloj, o agrega/completa "
    "una tarea en el panel To Do dentro de Reloj. Usela siempre que el usuario pida una sesion de concentracion o "
    "quiera ver el temporizador/cronometro corriendo en la aplicacion Reloj.",
    timeout=45,
)
async def reloj_windows(
    modo: Literal["concentracion", "temporizador", "cronometro"] = "concentracion",
    accion: Literal["iniciar", "detener", "abrir", "agregar_tarea", "completar_tarea"] = "iniciar",
    minutos: Annotated[int, "Duracion en minutos (ej. 30, 45, 60); 0 = dejar la actual"] = 0,
    tarea: Annotated[str, "Titulo de la tarea para agregar_tarea o completar_tarea en el panel To Do de Reloj"] = "",
) -> dict:
    _allowed()
    from jarvis.tools import _keys, system

    hwnd, title = await in_worker(_target_hwnd, "Reloj")
    if not hwnd:
        raise ToolError("No pude abrir ni encontrar la ventana de la aplicación Reloj.")
    await asyncio.sleep(0.4)

    tab_targets = {
        "concentracion": ["Sesiones de concentración", "Concentración", "Focus sessions"],
        "temporizador": ["Temporizador", "Timer"],
        "cronometro": ["Cronómetro", "Stopwatch"],
    }[modo]

    tab_clicked = None
    for tab in tab_targets:
        hit = await in_worker(uia_locate, hwnd, tab, 72.0)
        if hit is not None:
            await in_worker(click_at, int(hit["x"]), int(hit["y"]), "clic")
            tab_clicked = hit["detalle"]
            break
    if tab_clicked is None:
        try:
            res = await locate_and_click(tab_targets[0], "clic")
            tab_clicked = res.get("elemento")
        except Exception:
            pass
    await asyncio.sleep(0.35)

    result: dict[str, Any] = {"ventana": title or "Reloj", "modo": modo, "accion": accion}
    if tab_clicked:
        result["seccion"] = tab_clicked

    if accion == "abrir":
        summary = await in_worker(uia_summary, hwnd)
        result["controles_visibles"] = summary.get("controles", [])[:25]
        return result

    if accion == "agregar_tarea" and tarea.strip():
        await locate_and_click("Agregar una tarea", "clic")
        await asyncio.sleep(0.2)
        await in_worker(system.set_clipboard, tarea.strip())
        await in_worker(_keys.press_combo, "ctrl+v")
        await asyncio.sleep(0.15)
        await in_worker(_keys.tap, _keys.VK["enter"])
        result["tarea_agregada"] = tarea.strip()
        return result

    if accion == "completar_tarea" and tarea.strip():
        clicked = await locate_and_click(tarea.strip(), "clic")
        result["tarea_completada"] = clicked.get("elemento", tarea.strip())
        return result

    if modo == "concentracion" and minutos > 0 and accion == "iniciar":
        adjusted = await in_worker(_adjust_focus_minutes, hwnd, int(minutos))
        result["minutos"] = int(minutos)
        result["duracion_ajustada"] = adjusted

    if accion == "iniciar":
        start_candidates = {
            "concentracion": ["Iniciar sesión de concentración", "Iniciar sesión", "Iniciar", "Start focus session"],
            "temporizador": ["Iniciar", "Reproducir", "Start"],
            "cronometro": ["Iniciar", "Reproducir", "Start"],
        }[modo]
    else:
        start_candidates = {
            "concentracion": ["Detener sesión de concentración", "Finalizar sesión", "Pausar", "Detener", "Stop"],
            "temporizador": ["Pausar", "Detener", "Reiniciar", "Pause"],
            "cronometro": ["Pausar", "Detener", "Pause"],
        }[modo]

    clicked_btn = None
    for candidate in start_candidates:
        hit = await in_worker(uia_locate, hwnd, candidate, 74.0)
        if hit is not None:
            await in_worker(click_at, int(hit["x"]), int(hit["y"]), "clic")
            clicked_btn = hit["detalle"]
            break
    if clicked_btn is None:
        res = await locate_and_click(start_candidates[0], "clic")
        clicked_btn = res.get("elemento", start_candidates[0])

    result["boton_pulsado"] = clicked_btn
    summary = await in_worker(uia_summary, hwnd)
    if summary.get("controles"):
        result["controles_ahora"] = summary["controles"][:20]
    return result
