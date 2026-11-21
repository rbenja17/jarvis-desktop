from __future__ import annotations

import asyncio
import datetime as dt
import re
import uuid
from typing import Annotated, Literal

from jarvis import store
from jarvis.config import get_config
from jarvis.services import services
from jarvis.textutil import best_match, normalize
from jarvis.tools import registry
from jarvis.tools.registry import ToolContext, ToolError, tool

ROUTINES_FILE = "rutinas.json"
SCHEDULE_FILE = "programadas.json"
DAY_NAMES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DAY_SHORT = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
WINDOW_MINUTES = 15


def all_routines() -> dict[str, list[dict]]:
    routines: dict[str, list[dict]] = {}
    for name, steps in get_config().section("rutinas").items():
        if isinstance(steps, list):
            routines[name.replace("_", " ")] = steps
    for name, steps in store.load(ROUTINES_FILE, {}).items():
        routines[name] = steps
    return routines


def _step_label(step: dict) -> str:
    if "herramienta" in step:
        return step["herramienta"]
    if "instruccion" in step:
        return step["instruccion"][:60]
    if "esperar" in step:
        return f"esperar {step['esperar']} s"
    return "paso desconocido"


async def run_routine(name: str) -> dict:
    routines = all_routines()
    match, _ = best_match(name, list(routines), key=lambda n: n, threshold=70)
    if match is None:
        raise ToolError(f"No existe la rutina '{name}'. Disponibles: {', '.join(routines) or 'ninguna'}.")
    ctx = ToolContext(channel="rutina")
    results = []
    for step in routines[match]:
        if "herramienta" in step:
            result = await registry.call(step["herramienta"], step.get("args", {}), ctx)
        elif "instruccion" in step:
            brain = services.text_brain
            if brain is None:
                result = {"error": "Sin cerebro de texto disponible."}
            else:
                result = {"respuesta": await brain.ask(step["instruccion"], channel="rutina", remember=False)}
        elif "esperar" in step:
            await asyncio.sleep(min(30.0, float(step["esperar"])))
            result = {"ok": True}
        else:
            result = {"error": "Paso invalido"}
        results.append({"paso": _step_label(step), **result})
    return {"rutina": match, "pasos": results}


@tool("Ejecuta una rutina guardada por su nombre (ej. 'modo trabajo', 'modo pelicula').", timeout=150)
async def ejecutar_rutina(nombre: Annotated[str, "Nombre de la rutina"]) -> dict:
    return await run_routine(nombre)


@tool("Lista las rutinas disponibles y sus pasos.")
def listar_rutinas() -> dict:
    return {"rutinas": {name: [_step_label(s) for s in steps] for name, steps in all_routines().items()}}


@tool(
    "Crea o reemplaza una rutina con instrucciones en lenguaje natural que se ejecutan en orden, "
    "ej. nombre 'modo estudio', instrucciones 'abrir Notion, volumen al 20, playlist Lo-Fi'."
)
def crear_rutina(
    nombre: Annotated[str, "Nombre corto de la rutina"],
    instrucciones: Annotated[list[str], "Lista de acciones, una por elemento"],
) -> dict:
    steps = [{"instruccion": text.strip()} for text in instrucciones if text.strip()]
    if not steps:
        raise ToolError("La rutina necesita al menos una instruccion.")
    key = normalize(nombre)
    store.update(ROUTINES_FILE, {}, lambda data: {**data, key: steps})
    return {"rutina": key, "pasos": len(steps)}


@tool("Elimina una rutina creada por voz o texto (las de config.toml se editan en ese archivo).")
def eliminar_rutina(nombre: Annotated[str, "Nombre de la rutina"]) -> dict:
    saved = store.load(ROUTINES_FILE, {})
    match, _ = best_match(nombre, list(saved), key=lambda n: n, threshold=70)
    if match is None:
        raise ToolError("No encontre esa rutina entre las creadas por voz.")
    store.update(ROUTINES_FILE, {}, lambda data: {k: v for k, v in data.items() if k != match})
    return {"eliminada": match}


def parse_days(text: str) -> list[int]:
    t = normalize(text)
    if "fin de semana" in t or "fines de semana" in t:
        return [5, 6]
    found = [i for i, name in enumerate(DAY_NAMES) if re.search(rf"\b({name}|{DAY_SHORT[i]})\b", t)]
    if found:
        positions = [m.start() for i in found for m in [re.search(rf"\b({DAY_NAMES[i]}|{DAY_SHORT[i]})\b", t)]]
        if len(found) == 2 and re.search(r"\b\w+ (a|hasta) \w+\b", t):
            a, b = (d for _, d in sorted(zip(positions, found)))
            return list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
        return found
    if any(w in t for w in ("laborable", "habiles", "semana")):
        return list(range(5))
    if not t or any(w in t for w in ("todos", "diario", "cada dia", "siempre")):
        return list(range(7))
    raise ToolError("No entendi los dias. Use 'todos', 'lunes a viernes', 'fines de semana' o nombres de dias.")


def _parse_hour(hora: str) -> str:
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*$", hora or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise ToolError("Hora invalida; use HH:MM en formato 24 horas.")
    return f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"


def _days_label(days: list[int]) -> str:
    if len(days) == 7:
        return "todos los dias"
    if days == [0, 1, 2, 3, 4]:
        return "lunes a viernes"
    if days == [5, 6]:
        return "fines de semana"
    return ", ".join(DAY_NAMES[d] for d in days)


@tool(
    "Programa una tarea que Jarvis hara solo a una hora y dias dados, ej. 'dame el clima y mi agenda' "
    "todos los dias a las 08:00, o 'revisa si tengo correos importantes' de lunes a viernes a las 18:00. "
    "El resultado se dice en voz alta y/o se manda al celular."
)
def programar_tarea(
    instruccion: Annotated[str, "Que debe hacer Jarvis, como se lo pediria el usuario"],
    hora: Annotated[str, "HH:MM en 24 horas"],
    dias: Annotated[str, "'todos', 'lunes a viernes', 'fines de semana' o dias concretos"] = "todos",
    entrega: Literal["voz", "telegram", "ambos"] = "ambos",
) -> dict:
    task = {
        "id": uuid.uuid4().hex[:6],
        "instruccion": instruccion.strip(),
        "hora": _parse_hour(hora),
        "dias": parse_days(dias),
        "entrega": entrega,
        "ultima": "",
    }
    store.update(SCHEDULE_FILE, [], lambda items: items + [task])
    return {"id": task["id"], "hora": task["hora"], "dias": _days_label(task["dias"]), "instruccion": task["instruccion"]}


@tool("Lista las tareas programadas.")
def listar_tareas_programadas() -> dict:
    return {
        "tareas": [
            {"id": t["id"], "instruccion": t["instruccion"], "hora": t["hora"], "dias": _days_label(t["dias"]), "entrega": t["entrega"]}
            for t in store.load(SCHEDULE_FILE, [])
        ]
    }


@tool("Elimina una tarea programada por su id o por su descripcion.")
def eliminar_tarea_programada(cual: Annotated[str, "Id o descripcion aproximada"]) -> dict:
    items = store.load(SCHEDULE_FILE, [])
    target = next((t for t in items if t["id"] == cual.strip()), None)
    if target is None:
        target, _ = best_match(cual, items, key=lambda t: t["instruccion"], threshold=55)
    if target is None:
        raise ToolError("No encontre esa tarea programada.")
    store.update(SCHEDULE_FILE, [], lambda xs: [t for t in xs if t["id"] != target["id"]])
    return {"eliminada": target["instruccion"]}


def pop_due_tasks(now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    due: list[dict] = []

    def mark(items: list[dict]) -> list[dict]:
        for t in items:
            if t.get("ultima") == today or now.weekday() not in t["dias"]:
                continue
            hour, minute = (int(p) for p in t["hora"].split(":"))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now < target + dt.timedelta(minutes=WINDOW_MINUTES):
                t["ultima"] = today
                due.append(dict(t))
        return items

    store.update(SCHEDULE_FILE, [], mark)
    return due
