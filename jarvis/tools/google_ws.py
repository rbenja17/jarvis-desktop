from __future__ import annotations

import base64
import datetime as dt
import html
import logging
import re
from email.utils import parseaddr
from typing import Annotated, Literal

from jarvis.config import data_path
from jarvis.textutil import best_match, normalize
from jarvis.tools.registry import ToolError, tool

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/tasks",
]
LOGIN_HINT = "Google no esta vinculado. Abrilo desde Configuracion > Integraciones (o ejecuta python scripts\\google_login.py)."


def credentials_file():
    return data_path("google_credentials.json")


def token_file():
    return data_path("tokens", "google.json")


def import_downloaded_client() -> bool:
    import json
    import shutil

    from jarvis.tools.files import known_folders

    downloads = known_folders().get("descargas")
    if downloads is None or not downloads.exists():
        return False
    candidates = sorted(downloads.glob("client_secret*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            if "installed" in json.loads(path.read_text("utf-8")):
                shutil.copyfile(path, credentials_file())
                log.info("Cliente OAuth importado desde %s", path.name)
                return True
        except (OSError, ValueError):
            continue
    return False


def is_linked() -> bool:
    return token_file().exists()


def get_credentials(interactive: bool = False):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if token_file().exists():
        creds = Credentials.from_authorized_user_file(str(token_file()), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file().write_text(creds.to_json(), "utf-8")
            return creds
        except RefreshError:
            log.warning("El token de Google vencio o fue revocado")
            creds = None
    if not interactive:
        raise ToolError(LOGIN_HINT)
    if not credentials_file().exists() and not import_downloaded_client():
        raise ToolError(
            "No encontre el cliente OAuth. Descargue el JSON desde Google Cloud (Clientes > Descargar JSON) "
            f"a la carpeta Descargas, o copielo como {credentials_file()}."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file()), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    token_file().write_text(creds.to_json(), "utf-8")
    return creds


def _service(name: str, version: str):
    from googleapiclient.discovery import build

    return build(name, version, credentials=get_credentials(), cache_discovery=False)


def _event_time(value: dict) -> tuple[dt.datetime, bool]:
    if "dateTime" in value:
        return dt.datetime.fromisoformat(value["dateTime"]).astimezone(), False
    return dt.datetime.fromisoformat(value["date"]).astimezone(), True


def _parse_event_bounds(inicio: str, duracion_minutos: int = 60) -> tuple[dict, dict]:
    text = inicio.strip()
    if len(text) == 10:
        day = dt.date.fromisoformat(text)
        return {"date": day.isoformat()}, {"date": (day + dt.timedelta(days=1)).isoformat()}
    begin = dt.datetime.fromisoformat(text)
    begin = begin.astimezone() if begin.tzinfo is None else begin
    end = begin + dt.timedelta(minutes=max(5, int(duracion_minutos)))
    return {"dateTime": begin.isoformat()}, {"dateTime": end.isoformat()}


def _event_duration_minutes(ev: dict) -> int:
    start_val = ev.get("start") or {}
    end_val = ev.get("end") or {}
    if "dateTime" in start_val and "dateTime" in end_val:
        try:
            begin = dt.datetime.fromisoformat(start_val["dateTime"])
            end = dt.datetime.fromisoformat(end_val["dateTime"])
            mins = round((end - begin).total_seconds() / 60)
            if mins >= 5:
                return mins
        except ValueError:
            pass
    return 60


def _format_event_summary(ev: dict) -> str:
    begin, all_day = _event_time(ev["start"])
    when = f"{begin:%Y-%m-%d} (todo el dia)" if all_day else f"{begin:%Y-%m-%d %H:%M}"
    return f"'{ev.get('summary', '(sin titulo)')}' ({when}, id={ev.get('id', '')})"


def _writable_calendars(service) -> list[dict]:
    calendars = service.calendarList().list(minAccessRole="writer").execute().get("items", [])
    selected = [c for c in calendars if c.get("selected") or c.get("primary")]
    return selected or [{"id": "primary", "primary": True, "summary": ""}]


def _find_event(
    service,
    titulo: str = "",
    fecha: str = "",
    evento_id: str = "",
    dias: int = 60,
) -> tuple[str, dict]:
    calendars = _writable_calendars(service)
    clean_id = evento_id.strip()
    if clean_id:
        ordered = sorted(calendars, key=lambda c: not c.get("primary"))
        for cal in ordered:
            try:
                ev = service.events().get(calendarId=cal["id"], eventId=clean_id).execute()
                if ev and ev.get("status") != "cancelled":
                    return cal["id"], ev
            except Exception:
                continue
        raise ToolError(f"No encontre ningun evento editable con id '{clean_id}'.")

    clean_title = titulo.strip()
    clean_date = fecha.strip()[:10]
    if not clean_title and not clean_date:
        raise ToolError("Indique el titulo, la fecha o el evento_id del evento.")

    now = dt.datetime.now().astimezone()
    if clean_date:
        day = dt.date.fromisoformat(clean_date)
        start = dt.datetime(day.year, day.month, day.day).astimezone()
        end = start + dt.timedelta(days=1)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=max(1, dias))

    candidates: list[tuple[str, dict]] = []
    for cal in calendars:
        items = (
            service.events()
            .list(
                calendarId=cal["id"],
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
            .get("items", [])
        )
        for ev in items:
            if ev.get("status") != "cancelled" and "start" in ev:
                candidates.append((cal["id"], ev))

    if not candidates:
        suffix = f" el {clean_date}" if clean_date else f" en los proximos {dias} dias"
        raise ToolError(f"No encontre eventos editables{suffix}.")

    if not clean_title:
        if len(candidates) == 1:
            return candidates[0]
        listing = "; ".join(_format_event_summary(ev) for _, ev in candidates[:5])
        raise ToolError(f"Hay varios eventos el {clean_date}. Indique cual: {listing}.")

    match, _ = best_match(clean_title, candidates, key=lambda pair: pair[1].get("summary", ""), threshold=55)
    if match is None:
        suffix = f" el {clean_date}" if clean_date else ""
        raise ToolError(f"No encontre ningun evento parecido a '{clean_title}'{suffix}.")

    if not clean_date:
        norm_target = normalize(match[1].get("summary", ""))
        same = [pair for pair in candidates if normalize(pair[1].get("summary", "")) == norm_target]
        if len(same) > 1:
            listing = "; ".join(_format_event_summary(ev) for _, ev in same[:5])
            raise ToolError(
                f"Hay {len(same)} eventos con ese titulo. Indique la fecha (AAAA-MM-DD) o el evento_id: {listing}."
            )
    return match


def events(days: int = 1, from_now: bool = False) -> list[dict]:
    service = _service("calendar", "v3")
    now = dt.datetime.now().astimezone()
    start = now if from_now else now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=max(1, days))
    calendars = service.calendarList().list(minAccessRole="reader").execute().get("items", [])
    calendars = [c for c in calendars if c.get("selected") or c.get("primary")]
    out: list[dict] = []
    for cal in calendars:
        items = (
            service.events()
            .list(
                calendarId=cal["id"],
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=25,
            )
            .execute()
            .get("items", [])
        )
        for ev in items:
            begin, all_day = _event_time(ev["start"])
            out.append(
                {
                    "id": ev.get("id", ""),
                    "titulo": ev.get("summary", "(sin titulo)"),
                    "fecha": begin.strftime("%Y-%m-%d"),
                    "dia": begin.strftime("%d/%m"),
                    "hora": "todo el dia" if all_day else begin.strftime("%H:%M"),
                    "lugar": ev.get("location", ""),
                    "calendario": "" if cal.get("primary") else cal.get("summary", ""),
                    "_orden": begin.isoformat(),
                }
            )
    out.sort(key=lambda e: e["_orden"])
    for e in out:
        e.pop("_orden")
    return out


@tool("Agenda de Google Calendar: eventos de hoy o de los proximos dias.")
def agenda(
    dias: Annotated[int, "Cuantos dias mirar desde hoy (1 = solo hoy)"] = 1,
    desde_ahora: Annotated[bool, "Solo eventos que todavia no empezaron"] = False,
) -> dict:
    items = events(dias, desde_ahora)
    return {"eventos": items} if items else {"eventos": [], "nota": "No hay eventos en ese periodo."}


@tool("Crea un evento en Google Calendar.")
def crear_evento(
    titulo: Annotated[str, "Titulo del evento"],
    inicio: Annotated[str, "Fecha y hora local AAAA-MM-DDTHH:MM, o AAAA-MM-DD si es de dia completo"],
    duracion_minutos: Annotated[int, "Duracion en minutos"] = 60,
    descripcion: Annotated[str, "Detalle opcional"] = "",
    lugar: Annotated[str, "Lugar opcional"] = "",
) -> dict:
    service = _service("calendar", "v3")
    start_body, end_body = _parse_event_bounds(inicio, duracion_minutos)
    body: dict = {
        "summary": titulo,
        "description": descripcion,
        "location": lugar,
        "start": start_body,
        "end": end_body,
    }
    created = service.events().insert(calendarId="primary", body=body).execute()
    return {"creado": created.get("summary"), "inicio": inicio.strip(), "id": created.get("id", "")}


@tool(
    "Edita o reprograma un evento existente en Google Calendar (titulo, fecha/hora, duracion, "
    "descripcion o lugar). Busca por evento_id o por titulo aproximado y fecha opcional."
)
def editar_evento(
    titulo: Annotated[str, "Titulo actual aproximado del evento (opcional si pasa evento_id)"] = "",
    fecha: Annotated[str, "Fecha actual del evento AAAA-MM-DD para desambiguar (opcional)"] = "",
    evento_id: Annotated[str, "ID exacto del evento obtenido de agenda (opcional)"] = "",
    nuevo_titulo: Annotated[str, "Nuevo titulo para el evento (opcional)"] = "",
    nuevo_inicio: Annotated[str, "Nueva fecha/hora AAAA-MM-DDTHH:MM o AAAA-MM-DD si es dia completo (opcional)"] = "",
    duracion_minutos: Annotated[int, "Nueva duracion en minutos; 0 mantiene la duracion actual"] = 0,
    descripcion: Annotated[str, "Nueva descripcion (opcional)"] = "",
    lugar: Annotated[str, "Nuevo lugar (opcional)"] = "",
) -> dict:
    if not any([nuevo_titulo.strip(), nuevo_inicio.strip(), duracion_minutos > 0, descripcion.strip(), lugar.strip()]):
        raise ToolError("No se indico ningun cambio para el evento.")
    service = _service("calendar", "v3")
    cal_id, ev = _find_event(service, titulo=titulo, fecha=fecha, evento_id=evento_id)
    old_summary = ev.get("summary", "(sin titulo)")
    updated_body = dict(ev)

    if nuevo_titulo.strip():
        updated_body["summary"] = nuevo_titulo.strip()
    if descripcion.strip():
        updated_body["description"] = descripcion.strip()
    if lugar.strip():
        updated_body["location"] = lugar.strip()

    if nuevo_inicio.strip():
        mins = duracion_minutos if duracion_minutos > 0 else _event_duration_minutes(ev)
        updated_body["start"], updated_body["end"] = _parse_event_bounds(nuevo_inicio, mins)
    elif duracion_minutos > 0:
        start_val = ev.get("start") or {}
        if "dateTime" not in start_val:
            raise ToolError(
                "Para cambiar la duracion de un evento de dia completo, indique tambien nuevo_inicio con hora (AAAA-MM-DDTHH:MM)."
            )
        begin = dt.datetime.fromisoformat(start_val["dateTime"])
        updated_body["end"] = {"dateTime": (begin + dt.timedelta(minutes=max(5, int(duracion_minutos)))).isoformat()}

    updated = service.events().update(calendarId=cal_id, eventId=ev["id"], body=updated_body).execute()
    begin, all_day = _event_time(updated["start"])
    return {
        "antes": old_summary,
        "titulo": updated.get("summary", old_summary),
        "fecha": begin.strftime("%Y-%m-%d"),
        "hora": "todo el dia" if all_day else begin.strftime("%H:%M"),
        "lugar": updated.get("location", ""),
        "id": updated.get("id", ev["id"]),
    }


@tool(
    "Elimina o cancela un evento existente en Google Calendar. Busca por evento_id o por titulo "
    "aproximado y fecha opcional AAAA-MM-DD si hay varios eventos con ese nombre."
)
def eliminar_evento(
    titulo: Annotated[str, "Titulo aproximado del evento a borrar (opcional si pasa evento_id)"] = "",
    fecha: Annotated[str, "Fecha del evento AAAA-MM-DD para desambiguar (opcional)"] = "",
    evento_id: Annotated[str, "ID exacto del evento obtenido de agenda (opcional)"] = "",
) -> dict:
    service = _service("calendar", "v3")
    cal_id, ev = _find_event(service, titulo=titulo, fecha=fecha, evento_id=evento_id)
    begin, all_day = _event_time(ev["start"])
    service.events().delete(calendarId=cal_id, eventId=ev["id"]).execute()
    return {
        "eliminado": ev.get("summary", "(sin titulo)"),
        "fecha": begin.strftime("%Y-%m-%d"),
        "hora": "todo el dia" if all_day else begin.strftime("%H:%M"),
    }


QUERIES = {
    "no_leidos": "is:unread in:inbox category:primary",
    "importantes": "is:unread is:important in:inbox",
    "recientes": "in:inbox newer_than:2d",
}


def _sender(raw: str) -> str:
    name, addr = parseaddr(raw)
    return name or addr


def list_mail(query: str, count: int = 5) -> tuple[list[dict], int]:
    service = _service("gmail", "v1")
    listing = service.users().messages().list(userId="me", q=query, maxResults=max(1, min(10, count))).execute()
    out: list[dict] = []
    for ref in listing.get("messages", []):
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"])
            .execute()
        )
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        out.append(
            {
                "id": msg["id"],
                "de": _sender(headers.get("from", "")),
                "asunto": headers.get("subject", "(sin asunto)"),
                "resumen": html.unescape(msg.get("snippet", ""))[:160],
            }
        )
    return out, int(listing.get("resultSizeEstimate", len(out)))


@tool("Revisa Gmail: correos no leidos, importantes o recientes (remitente, asunto y un resumen corto).")
def correos(
    filtro: Literal["no_leidos", "importantes", "recientes"] = "no_leidos",
    cantidad: Annotated[int, "Maximo 10"] = 5,
) -> dict:
    items, total = list_mail(QUERIES[filtro], cantidad)
    return {"total_aprox": total, "correos": items}


@tool("Busca correos en Gmail con la sintaxis de Gmail (ej. 'from:banco', 'factura newer_than:7d').")
def buscar_correo(
    consulta: Annotated[str, "Busqueda de Gmail"],
    cantidad: Annotated[int, "Maximo 10"] = 5,
) -> dict:
    items, total = list_mail(consulta, cantidad)
    return {"total_aprox": total, "correos": items}


def _plain_text(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if data and mime in ("text/plain", "text/html"):
        text = base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
        if mime == "text/html":
            text = re.sub(r"<(script|style).*?</\1>", " ", text, flags=re.S | re.I)
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        return re.sub(r"\s+", " ", text).strip()
    parts = payload.get("parts", [])
    for preferred in ("text/plain", "text/html"):
        for part in parts:
            if part.get("mimeType") == preferred or part.get("parts"):
                text = _plain_text(part)
                if text:
                    return text
    return ""


@tool("Lee el contenido de un correo de Gmail por su id (obtenido con correos o buscar_correo).")
def leer_correo(id: Annotated[str, "Id del correo"]) -> dict:
    service = _service("gmail", "v1")
    msg = service.users().messages().get(userId="me", id=id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    body = _plain_text(msg.get("payload", {}))
    return {
        "de": _sender(headers.get("from", "")),
        "asunto": headers.get("subject", ""),
        "fecha": headers.get("date", ""),
        "texto": body[:2500],
        "recortado": len(body) > 2500,
    }


LOCAL_TASKS = "tareas.json"


def _local_pending() -> list[dict]:
    from jarvis import store

    items = store.load(LOCAL_TASKS, [])
    return [
        {
            "titulo": t["titulo"],
            "vence": t.get("vence", ""),
            "notas": t.get("notas", ""),
            "lista": "To-Do local",
            "_local": True,
            "_id": t["id"],
        }
        for t in items
        if not t.get("completada")
    ]


def pending_tasks(limit: int = 20) -> list[dict]:
    out: list[dict] = []
    if is_linked():
        try:
            service = _service("tasks", "v1")
            for tasklist in service.tasklists().list(maxResults=10).execute().get("items", []):
                items = (
                    service.tasks()
                    .list(tasklist=tasklist["id"], showCompleted=False, maxResults=limit)
                    .execute()
                    .get("items", [])
                )
                for t in items:
                    if not t.get("title"):
                        continue
                    out.append(
                        {
                            "titulo": t["title"],
                            "vence": (t.get("due") or "")[:10],
                            "notas": t.get("notes", ""),
                            "lista": tasklist.get("title", ""),
                            "_lista_id": tasklist["id"],
                            "_id": t["id"],
                        }
                    )
        except Exception:
            log.debug("No se pudieron leer las tareas de Google Tasks; usando lista local", exc_info=True)
    out.extend(_local_pending())
    return out[:limit]


def _public(task: dict) -> dict:
    return {k: v for k, v in task.items() if not k.startswith("_") and v != ""}


@tool("Lista las tareas pendientes del usuario (su to-do list en Google Tasks y local).")
def tareas_pendientes() -> dict:
    tasks = [_public(t) for t in pending_tasks(30)]
    return {"tareas": tasks} if tasks else {"tareas": [], "nota": "No hay tareas pendientes en su to-do list."}


@tool("Agrega una tarea a la lista de tareas (to-do list), con fecha de vencimiento y notas opcionales.")
def crear_tarea(
    titulo: Annotated[str, "Que hay que hacer"],
    vence: Annotated[str, "Fecha AAAA-MM-DD opcional"] = "",
    notas: Annotated[str, "Detalle opcional"] = "",
) -> dict:
    import uuid
    from jarvis import store

    clean_title = titulo.strip()
    if not clean_title:
        raise ToolError("El título de la tarea está vacío.")
    due_str = vence.strip()[:10] if vence.strip() else ""
    if due_str:
        due_str = dt.date.fromisoformat(due_str).isoformat()
    if is_linked():
        try:
            service = _service("tasks", "v1")
            body: dict = {"title": clean_title, "notes": notas}
            if due_str:
                body["due"] = f"{due_str}T00:00:00.000Z"
            service.tasks().insert(tasklist="@default", body=body).execute()
            return {"creada": clean_title, "vence": due_str or None}
        except Exception:
            log.debug("Fallo Google Tasks al crear tarea; guardando en lista local", exc_info=True)
    item = {
        "id": uuid.uuid4().hex[:6],
        "titulo": clean_title,
        "vence": due_str,
        "notas": notas.strip(),
        "completada": False,
        "creada": dt.datetime.now().isoformat(timespec="seconds"),
    }
    store.update(LOCAL_TASKS, [], lambda items: items + [item])
    return {"creada": clean_title, "vence": due_str or None}


@tool("Marca como completada una tarea de la lista de tareas (to-do list) buscandola por su titulo.")
def completar_tarea(titulo: Annotated[str, "Titulo aproximado de la tarea"]) -> dict:
    from jarvis import store

    tasks = pending_tasks(50)
    match, _ = best_match(titulo, tasks, key=lambda t: t["titulo"], threshold=58)
    if match is None:
        raise ToolError(f"No encontre ninguna tarea pendiente parecida a '{titulo}'.")
    if match.get("_local"):
        store.update(
            LOCAL_TASKS,
            [],
            lambda items: [{**t, "completada": True} if t["id"] == match["_id"] else t for t in items],
        )
    else:
        _service("tasks", "v1").tasks().patch(
            tasklist=match["_lista_id"], task=match["_id"], body={"status": "completed"}
        ).execute()
    return {"completada": match["titulo"]}


@tool("Elimina o borra una tarea de la lista de tareas (to-do list) buscandola por su titulo.")
def eliminar_tarea(titulo: Annotated[str, "Titulo aproximado de la tarea a borrar"]) -> dict:
    from jarvis import store

    tasks = pending_tasks(50)
    match, _ = best_match(titulo, tasks, key=lambda t: t["titulo"], threshold=58)
    if match is None:
        raise ToolError(f"No encontre ninguna tarea pendiente parecida a '{titulo}'.")
    if match.get("_local"):
        store.update(LOCAL_TASKS, [], lambda items: [t for t in items if t["id"] != match["_id"]])
    else:
        _service("tasks", "v1").tasks().delete(tasklist=match["_lista_id"], task=match["_id"]).execute()
    return {"eliminada": match["titulo"]}


@tool("Edita o renombra una tarea pendiente de la lista de tareas (to-do list).")
def editar_tarea(
    titulo: Annotated[str, "Titulo actual aproximado de la tarea"],
    nuevo_titulo: Annotated[str, "Nuevo titulo para la tarea (opcional)"] = "",
    vence: Annotated[str, "Nueva fecha de vencimiento AAAA-MM-DD (opcional)"] = "",
    notas: Annotated[str, "Nuevas notas o detalle (opcional)"] = "",
) -> dict:
    from jarvis import store

    tasks = pending_tasks(50)
    match, _ = best_match(titulo, tasks, key=lambda t: t["titulo"], threshold=58)
    if match is None:
        raise ToolError(f"No encontre ninguna tarea pendiente parecida a '{titulo}'.")
    new_title = nuevo_titulo.strip() or match["titulo"]
    due_str = dt.date.fromisoformat(vence.strip()[:10]).isoformat() if vence.strip() else match.get("vence", "")
    new_notes = notas.strip() if notas.strip() else match.get("notas", "")
    if match.get("_local"):
        store.update(
            LOCAL_TASKS,
            [],
            lambda items: [
                {**t, "titulo": new_title, "vence": due_str, "notas": new_notes} if t["id"] == match["_id"] else t
                for t in items
            ],
        )
    else:
        patch_body: dict = {"title": new_title}
        if due_str:
            patch_body["due"] = f"{due_str}T00:00:00.000Z"
        if notas.strip():
            patch_body["notes"] = new_notes
        _service("tasks", "v1").tasks().patch(
            tasklist=match["_lista_id"], task=match["_id"], body=patch_body
        ).execute()
    return {"antes": match["titulo"], "tarea": new_title, "vence": due_str or None}
