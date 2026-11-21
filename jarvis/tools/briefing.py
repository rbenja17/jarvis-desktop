from __future__ import annotations

import datetime as dt
import logging

from jarvis import store
from jarvis.config import get_config
from jarvis.services import services
from jarvis.tools import google_ws, info, productivity, system
from jarvis.tools.registry import tool

log = logging.getLogger(__name__)

STATE_FILE = "estado.json"


def collect() -> dict:
    data: dict = {"fecha": info.now_text(), "saludo": greeting(dt.datetime.now().hour)}
    try:
        weather = info.weather(days=1)
        data["clima"] = {"ciudad": weather["ciudad"], "ahora": weather["ahora"], "hoy": weather["pronostico"][0]}
    except Exception as exc:
        log.info("Resumen sin clima: %s", exc)

    today = dt.date.today().isoformat()
    reminders = [
        {"texto": r["texto"], "hora": r["vence"][11:16]}
        for r in productivity.list_reminders()
        if r["vence"].startswith(today) and r["tipo"] == "recordatorio"
    ]
    if reminders:
        data["recordatorios_de_hoy"] = reminders

    if google_ws.is_linked():
        try:
            data["agenda_de_hoy"] = google_ws.events(1)
        except Exception as exc:
            log.info("Resumen sin agenda: %s", exc)
        try:
            mails, total = google_ws.list_mail(google_ws.QUERIES["no_leidos"], 3)
            data["correos_sin_leer"] = {"total_aprox": total, "ultimos": [{"de": m["de"], "asunto": m["asunto"]} for m in mails]}
        except Exception as exc:
            log.info("Resumen sin correos: %s", exc)
        try:
            due = [t for t in google_ws.pending_tasks() if t["vence"] and t["vence"] <= today]
            if due:
                data["tareas_para_hoy"] = [{"titulo": t["titulo"], "vence": t["vence"]} for t in due]
        except Exception as exc:
            log.info("Resumen sin tareas: %s", exc)

    if get_config().get("resumen.noticias", True):
        try:
            from jarvis.tools.web import google_news

            data["titulares"] = [f"{n['titulo']} ({n['medio']})" for n in google_news("", 6)]
        except Exception as exc:
            log.info("Resumen sin titulares: %s", exc)

    try:
        status = system.system_status()
        if "bateria_porcentaje" in status:
            data["bateria"] = {"porcentaje": status["bateria_porcentaje"], "cargando": status["cargando"]}
    except Exception:
        pass
    return data


def template_text(data: dict) -> str:
    treat = get_config().get("usuario.tratamiento", "señor")
    hello = data.get("saludo") or greeting(dt.datetime.now().hour)
    parts = [f"{hello}, {treat}. Hoy es {data['fecha'].rsplit(',', 1)[0]}."]
    if "clima" in data:
        c = data["clima"]
        parts.append(
            f"En {c['ciudad'].split(',')[0]} hay {c['ahora']['temperatura']} grados, {c['ahora']['cielo']}; "
            f"máxima de {c['hoy']['max']} y mínima de {c['hoy']['min']}, "
            f"con {c['hoy']['lluvia_probabilidad']} por ciento de probabilidad de lluvia."
        )
    events = data.get("agenda_de_hoy")
    if events:
        first = ", ".join(f"{e['titulo']} a las {e['hora']}" if e["hora"] != "todo el dia" else e["titulo"] for e in events[:3])
        parts.append(f"Tiene {len(events)} eventos en la agenda: {first}.")
    elif "agenda_de_hoy" in data:
        parts.append("Su agenda de hoy está libre.")
    for r in data.get("recordatorios_de_hoy", [])[:3]:
        parts.append(f"Recordatorio a las {r['hora']}: {r['texto']}.")
    mails = data.get("correos_sin_leer")
    if mails and mails["total_aprox"]:
        parts.append(f"Tiene alrededor de {mails['total_aprox']} correos sin leer.")
    tasks = data.get("tareas_para_hoy")
    if tasks:
        parts.append(f"Hay {len(tasks)} tareas que vencen hoy, empezando por {tasks[0]['titulo']}.")
    headlines = data.get("titulares", [])[:2]
    if headlines:
        parts.append("En las noticias: " + "; ".join(h.rsplit(" (", 1)[0] for h in headlines) + ".")
    battery = data.get("bateria")
    if battery and not battery["cargando"] and battery["porcentaje"] < 30:
        parts.append(f"La batería está en {battery['porcentaje']} por ciento; conviene enchufar la computadora.")
    return " ".join(parts)


async def compose(data: dict) -> str:
    brain = services.text_brain
    if brain is not None:
        try:
            return await brain.compose_briefing(data)
        except Exception as exc:
            log.warning("No se pudo redactar el resumen con Gemini, uso la plantilla: %s", exc)
    return template_text(data)


def _state() -> dict:
    return store.load(STATE_FILE, {})


def already_delivered_today(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now()
    raw = str(_state().get("resumen_ultimo", ""))
    try:
        last = dt.datetime.fromisoformat(raw)
    except ValueError:
        return False
    return last.date() == now.date() and last >= _morning_start(now)


def _scheduled_time(now: dt.datetime) -> dt.datetime | None:
    hour = str(get_config().get("resumen.hora", "") or "").strip()
    try:
        return now.replace(hour=int(hour[:2]), minute=int(hour[3:5]), second=0, microsecond=0)
    except ValueError:
        return None


def _morning_start(now: dt.datetime) -> dt.datetime:
    start = now.replace(hour=int(get_config().get("resumen.desde_hora", 5)), minute=0, second=0, microsecond=0)
    scheduled = _scheduled_time(now)
    return min(start, scheduled) if scheduled else start


def mark_delivered(now: dt.datetime | None = None) -> None:
    stamp = (now or dt.datetime.now()).isoformat(timespec="seconds")
    store.update(STATE_FILE, {}, lambda s: {**s, "resumen_ultimo": stamp})


def greeting(hour: int) -> str:
    if 5 <= hour < 13:
        return "Buenos días"
    return "Buenas tardes" if 13 <= hour < 20 else "Buenas noches"


def startup_due(now: dt.datetime | None = None) -> bool:
    cfg = get_config()
    now = now or dt.datetime.now()
    if not cfg.get("resumen.al_iniciar", True) or already_delivered_today(now):
        return False
    return int(cfg.get("resumen.desde_hora", 5)) <= now.hour < int(cfg.get("resumen.hasta_hora", 12))


def user_idle_seconds() -> float:
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def scheduled_due(now: dt.datetime | None = None, idle_seconds: float | None = None) -> bool:
    cfg = get_config()
    now = now or dt.datetime.now()
    target = _scheduled_time(now)
    if target is None or already_delivered_today(now):
        return False
    if not (target <= now and now.hour < int(cfg.get("resumen.hasta_hora", 12))):
        return False
    if cfg.get("resumen.esperar_presencia", True):
        idle = user_idle_seconds() if idle_seconds is None else idle_seconds
        return idle < 120
    return True


async def deliver(engine) -> str:
    from jarvis.tools.registry import in_worker

    mark_delivered()
    cfg = get_config()
    data = await in_worker(collect)
    text = await compose(data)
    await engine.announce(
        text,
        voice=bool(cfg.get("resumen.voz", True)),
        telegram=bool(cfg.get("resumen.telegram", True)),
        title="Resumen del día",
    )
    return text


@tool(
    "Resumen del dia ('buenos dias', 'que tengo hoy'): fecha, clima, agenda, correos sin leer, tareas, "
    "recordatorios y bateria. Presente el resultado como un parte breve de mayordomo.",
    timeout=45,
)
def resumen_del_dia() -> dict:
    data = collect()
    data["instruccion"] = (
        "Resuma esto en voz alta en 4 a 6 frases, en orden: saludo, clima, agenda, recordatorios y tareas, correos. "
        "Omita lo que no haya. Cierre con los 2 titulares mas relevantes de la lista titulares."
    )
    return data
