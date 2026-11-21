from __future__ import annotations

import datetime as dt
import json
import threading
from typing import Annotated

from jarvis.config import data_path
from jarvis.textutil import normalize
from jarvis.tools.registry import ToolError, tool

FOLDER = "historial"
KEEP_DAYS = 30
_lock = threading.Lock()


def _file(day: dt.date):
    folder = data_path(FOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{day.isoformat()}.jsonl"


def append(lines: list[str], channel: str, now: dt.datetime | None = None) -> None:
    lines = [line.strip() for line in lines if line and line.strip()]
    if not lines:
        return
    now = now or dt.datetime.now()
    channel = channel.split(":", 1)[0]
    rows = "".join(
        json.dumps({"hora": now.strftime("%H:%M"), "canal": channel, "texto": line}, ensure_ascii=False) + "\n"
        for line in lines
    )
    with _lock, open(_file(now.date()), "a", encoding="utf-8") as fh:
        fh.write(rows)


def _read(day: dt.date) -> list[dict]:
    path = _file(day)
    if not path.exists():
        return []
    rows = []
    for raw in path.read_text("utf-8").splitlines():
        try:
            rows.append(json.loads(raw))
        except ValueError:
            continue
    return rows


def recent(count: int = 12, now: dt.datetime | None = None) -> list[str]:
    today = (now or dt.datetime.now()).date()
    rows = _read(today - dt.timedelta(days=1))[-count:] + _read(today)
    return [f"[{r['hora']}] {r['texto']}" for r in rows[-count:]]


def prune(now: dt.datetime | None = None) -> int:
    limit = (now or dt.datetime.now()).date() - dt.timedelta(days=KEEP_DAYS)
    removed = 0
    for path in data_path(FOLDER).glob("*.jsonl") if data_path(FOLDER).exists() else []:
        try:
            if dt.date.fromisoformat(path.stem) < limit:
                path.unlink()
                removed += 1
        except ValueError:
            continue
    return removed


def _parse_day(fecha: str, today: dt.date) -> dt.date:
    text = normalize(fecha)
    if not text or text == "hoy":
        return today
    if text == "ayer":
        return today - dt.timedelta(days=1)
    if text == "anteayer":
        return today - dt.timedelta(days=2)
    try:
        return dt.date.fromisoformat(fecha.strip()[:10])
    except ValueError as exc:
        raise ToolError("Fecha no válida: use hoy, ayer o AAAA-MM-DD.") from exc


@tool(
    "Consulta charlas anteriores con el usuario (voz, Telegram y texto) de un dia, opcionalmente filtrando "
    "por un tema. Uselo para 'que te pedi ayer', 'de que hablamos esta manana', 'que me dijiste sobre X'."
)
def conversaciones_anteriores(
    fecha: Annotated[str, "hoy, ayer, anteayer o AAAA-MM-DD"] = "hoy",
    buscar: Annotated[str, "Palabra o tema para filtrar (opcional)"] = "",
) -> dict:
    day = _parse_day(fecha, dt.date.today())
    rows = _read(day)
    if buscar.strip():
        words = normalize(buscar).split()
        rows = [r for r in rows if all(w in normalize(r["texto"]) for w in words)]
    if not rows:
        return {"fecha": day.isoformat(), "charlas": [], "nota": "No hay charlas registradas para eso."}
    return {"fecha": day.isoformat(), "charlas": [f"[{r['hora']} {r['canal']}] {r['texto']}" for r in rows[-40:]]}
