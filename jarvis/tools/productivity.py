from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from jarvis import store
from jarvis.config import data_path
from jarvis.textutil import best_match, normalize
from jarvis.tools.registry import ToolError, tool

REMINDERS = "recordatorios.json"
MEMORY = "memoria.json"
MAX_FACTS = 40


def parse_when(fecha_hora: str, en_minutos: float, now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now()
    if en_minutos and en_minutos > 0:
        return now + dt.timedelta(minutes=en_minutos)
    text = (fecha_hora or "").strip().replace(" ", "T")
    if not text:
        raise ToolError("Indique la fecha y hora o en cuantos minutos.")
    try:
        if len(text) <= 5 and ":" in text:
            hour, minute = (int(p) for p in text.split(":"))
            when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return when if when > now else when + dt.timedelta(days=1)
        when = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolError("Formato de fecha invalido; use AAAA-MM-DDTHH:MM.") from exc
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    if when <= now:
        raise ToolError("Esa fecha y hora ya paso.")
    return when


def add_reminder(text: str, when: dt.datetime, kind: str = "recordatorio") -> dict:
    item = {
        "id": uuid.uuid4().hex[:6],
        "texto": text.strip(),
        "vence": when.isoformat(timespec="seconds"),
        "tipo": kind,
        "creado": dt.datetime.now().isoformat(timespec="seconds"),
    }
    store.update(REMINDERS, [], lambda items: items + [item])
    return item


def list_reminders() -> list[dict]:
    return sorted(store.load(REMINDERS, []), key=lambda r: r["vence"])


def pop_due(now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now()
    due: list[dict] = []

    def split(items: list[dict]) -> list[dict]:
        keep = []
        for item in items:
            (due if dt.datetime.fromisoformat(item["vence"]) <= now else keep).append(item)
        return keep

    store.update(REMINDERS, [], split)
    return due


def _human_when(iso: str) -> str:
    when = dt.datetime.fromisoformat(iso)
    today = dt.date.today()
    if when.date() == today:
        day = "hoy"
    elif when.date() == today + dt.timedelta(days=1):
        day = "manana"
    else:
        day = when.strftime("%d/%m")
    return f"{day} a las {when:%H:%M}"


@tool(
    "Crea un recordatorio para una fecha y hora (formato AAAA-MM-DDTHH:MM, o solo HH:MM) o dentro de N minutos. "
    "Al vencer, Jarvis lo dice en voz alta y lo manda al celular."
)
def crear_recordatorio(
    texto: Annotated[str, "Que recordar"],
    fecha_hora: Annotated[str, "Fecha y hora local, ej. 2026-11-18T09:30 o 18:00"] = "",
    en_minutos: Annotated[int, "Alternativa: dentro de cuantos minutos"] = 0,
) -> dict:
    item = add_reminder(texto, parse_when(fecha_hora, en_minutos))
    return {"id": item["id"], "texto": item["texto"], "cuando": _human_when(item["vence"])}


@tool("Pone un temporizador (cuenta regresiva) con etiqueta opcional.")
def crear_temporizador(
    minutos: Annotated[int, "Minutos"] = 0,
    segundos: Annotated[int, "Segundos adicionales"] = 0,
    etiqueta: Annotated[str, "Para que es, ej. 'la pasta'"] = "",
) -> dict:
    total = int(minutos) * 60 + int(segundos)
    if total <= 0:
        raise ToolError("El temporizador debe durar al menos un segundo.")
    when = dt.datetime.now() + dt.timedelta(seconds=total)
    text = f"Temporizador terminado: {etiqueta}" if etiqueta else "Temporizador terminado"
    item = add_reminder(text, when, kind="temporizador")
    return {"id": item["id"], "termina": f"{when:%H:%M:%S}", "duracion_segundos": total}


@tool("Lista los recordatorios y temporizadores pendientes.")
def listar_recordatorios() -> dict:
    return {
        "pendientes": [
            {"id": r["id"], "texto": r["texto"], "cuando": _human_when(r["vence"]), "tipo": r["tipo"]}
            for r in list_reminders()
        ]
    }


@tool("Cancela un recordatorio o temporizador por su id o por su texto.")
def cancelar_recordatorio(cual: Annotated[str, "Id o texto aproximado"]) -> dict:
    items = list_reminders()
    target = next((r for r in items if r["id"] == cual.strip()), None)
    if target is None:
        target, _ = best_match(cual, items, key=lambda r: r["texto"], threshold=60)
    if target is None:
        raise ToolError("No encontre ese recordatorio.")
    store.update(REMINDERS, [], lambda xs: [r for r in xs if r["id"] != target["id"]])
    return {"cancelado": target["texto"]}


@tool("Anota algo en las notas del usuario (archivo notas.md).")
def anotar(texto: Annotated[str, "Contenido de la nota"]) -> dict:
    path = data_path("notas.md")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"- [{dt.datetime.now():%Y-%m-%d %H:%M}] {texto.strip()}\n")
    return {"anotado": texto.strip()}


@tool("Lee las ultimas notas guardadas.")
def leer_notas(cantidad: Annotated[int, "Cuantas notas, las mas recientes"] = 10) -> dict:
    path = data_path("notas.md")
    if not path.exists():
        return {"notas": []}
    lines = [ln.strip() for ln in path.read_text("utf-8").splitlines() if ln.strip()]
    return {"notas": lines[-max(1, int(cantidad)):]}


def memory_facts() -> list[str]:
    return [f["dato"] for f in store.load(MEMORY, [])]


@tool(
    "Guarda un dato o preferencia del usuario para recordarlo en futuras conversaciones "
    "(ej. 'mi playlist para trabajar es Deep Focus'). Uselo cuando el usuario pida que lo recuerde."
)
def recordar_dato(dato: Annotated[str, "El dato en una frase"]) -> dict:
    fact = dato.strip()

    def add(items: list[dict]) -> list[dict]:
        items = [f for f in items if normalize(f["dato"]) != normalize(fact)]
        items.append({"dato": fact, "fecha": dt.date.today().isoformat()})
        return items[-MAX_FACTS:]

    store.update(MEMORY, [], add)
    return {"recordado": fact}


@tool("Olvida un dato guardado en la memoria.")
def olvidar_dato(dato: Annotated[str, "Texto aproximado del dato"]) -> dict:
    items = store.load(MEMORY, [])
    target, _ = best_match(dato, items, key=lambda f: f["dato"], threshold=55)
    if target is None:
        raise ToolError("No tengo guardado nada parecido.")
    store.update(MEMORY, [], lambda xs: [f for f in xs if f["dato"] != target["dato"]])
    return {"olvidado": target["dato"]}
