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

CALENDAR_COLORS = {
    "lavanda": "1",
    "lila": "1",
    "salvia": "2",
    "verde claro": "2",
    "menta": "2",
    "uva": "3",
    "violeta": "3",
    "morado": "3",
    "purpura": "3",
    "flamenco": "4",
    "rosa": "4",
    "rosado": "4",
    "banana": "5",
    "amarillo": "5",
    "mandarina": "6",
    "naranja": "6",
    "pavo real": "7",
    "celeste": "7",
    "turquesa": "7",
    "cian": "7",
    "grafito": "8",
    "gris": "8",
    "arandano": "9",
    "azul": "9",
    "albahaca": "10",
    "verde": "10",
    "tomate": "11",
    "rojo": "11",
}
COLOR_NAMES = {
    "1": "lavanda",
    "2": "verde claro",
    "3": "violeta",
    "4": "rosa",
    "5": "amarillo",
    "6": "naranja",
    "7": "celeste",
    "8": "gris",
    "9": "azul",
    "10": "verde",
    "11": "rojo",
}


def _resolve_color_id(color: str) -> str:
    raw = color.strip()
    if not raw:
        return ""
    if raw in COLOR_NAMES:
        return raw
    norm = normalize(raw)
    if norm in CALENDAR_COLORS:
        return CALENDAR_COLORS[norm]
    match, _ = best_match(norm, list(CALENDAR_COLORS.keys()), key=lambda k: k, threshold=65)
    if match:
        return CALENDAR_COLORS[match]
    valid = ", ".join(COLOR_NAMES.values())
    raise ToolError(f"Color '{color}' no reconocido. Colores disponibles: {valid}.")

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


def _list_events_in_window(
    service,
    calendars: list[dict],
    start: dt.datetime,
    end: dt.datetime,
    query: str = "",
    max_results: int = 250,
) -> list[tuple[dict, dict]]:
    out: list[tuple[dict, dict]] = []
    for cal in calendars:
        page_token: str | None = None
        collected = 0
        while collected < max_results:
            kwargs: dict = {
                "calendarId": cal["id"],
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": min(250, max_results - collected),
            }
            if query.strip():
                kwargs["q"] = query.strip()
            if page_token:
                kwargs["pageToken"] = page_token
            resp = service.events().list(**kwargs).execute()
            items = resp.get("items", [])
            for ev in items:
                if ev.get("status") != "cancelled" and "start" in ev:
                    out.append((cal, ev))
                    collected += 1
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return out


def _matches_title(query: str, summary: str) -> bool:
    q = normalize(query)
    s = normalize(summary)
    if not q or not s:
        return False
    if q in s or s in q:
        return True
    hit, _ = best_match(q, [s], key=lambda x: x, threshold=75)
    return hit is not None


def _find_event(
    service,
    titulo: str = "",
    fecha: str = "",
    evento_id: str = "",
    dias: int = 180,
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

    pairs = _list_events_in_window(service, calendars, start, end, max_results=250)
    candidates = [(cal["id"], ev) for cal, ev in pairs]

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
                f"Hay {len(same)} eventos con ese titulo. Indique la fecha (AAAA-MM-DD), un rango desde/hasta o el evento_id: {listing}."
            )
    return match


def _find_events_in_range(service, titulo: str, desde: str, hasta: str) -> list[tuple[str, dict]]:
    clean_title = titulo.strip()
    if not clean_title:
        raise ToolError("Para editar o borrar varios eventos en un rango, indique el titulo del evento.")
    d_start = dt.date.fromisoformat(desde.strip()[:10])
    d_end = dt.date.fromisoformat(hasta.strip()[:10])
    if d_end < d_start:
        raise ToolError("La fecha 'hasta' no puede ser anterior a 'desde'.")
    start = dt.datetime(d_start.year, d_start.month, d_start.day).astimezone()
    end = dt.datetime(d_end.year, d_end.month, d_end.day).astimezone() + dt.timedelta(days=1)
    calendars = _writable_calendars(service)
    pairs = _list_events_in_window(service, calendars, start, end, max_results=500)
    matched = [(cal["id"], ev) for cal, ev in pairs if _matches_title(clean_title, ev.get("summary", ""))]
    if not matched:
        raise ToolError(f"No encontre eventos parecidos a '{clean_title}' entre {d_start.isoformat()} y {d_end.isoformat()}.")
    return matched


def events(
    days: int = 1,
    from_now: bool = False,
    desde: str = "",
    hasta: str = "",
    buscar: str = "",
) -> list[dict]:
    service = _service("calendar", "v3")
    now = dt.datetime.now().astimezone()
    if desde.strip():
        d_start = dt.date.fromisoformat(desde.strip()[:10])
        start = dt.datetime(d_start.year, d_start.month, d_start.day).astimezone()
    else:
        start = now if from_now else now.replace(hour=0, minute=0, second=0, microsecond=0)
    if hasta.strip():
        d_end = dt.date.fromisoformat(hasta.strip()[:10])
        end = dt.datetime(d_end.year, d_end.month, d_end.day).astimezone() + dt.timedelta(days=1)
    else:
        end = start.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=max(1, days))

    calendars = service.calendarList().list(minAccessRole="reader").execute().get("items", [])
    calendars = [c for c in calendars if c.get("selected") or c.get("primary")]
    pairs = _list_events_in_window(service, calendars, start, end, query=buscar, max_results=100)
    out: list[dict] = []
    for cal, ev in pairs:
        if buscar.strip() and not _matches_title(buscar, ev.get("summary", "") + " " + ev.get("description", "")):
            continue
        begin, all_day = _event_time(ev["start"])
        item: dict = {
            "id": ev.get("id", ""),
            "titulo": ev.get("summary", "(sin titulo)"),
            "fecha": begin.strftime("%Y-%m-%d"),
            "dia": begin.strftime("%d/%m"),
            "hora": "todo el dia" if all_day else begin.strftime("%H:%M"),
            "lugar": ev.get("location", ""),
            "calendario": "" if cal.get("primary") else cal.get("summary", ""),
            "_orden": begin.isoformat(),
        }
        cid = ev.get("colorId")
        if cid in COLOR_NAMES:
            item["color"] = COLOR_NAMES[cid]
        out.append(item)
    out.sort(key=lambda e: e["_orden"])
    for e in out:
        e.pop("_orden")
    return out


@tool("Agenda de Google Calendar: eventos de hoy, de los proximos dias o en un rango de fechas (con filtro opcional).")
def agenda(
    dias: Annotated[int, "Cuantos dias mirar desde hoy (1 = solo hoy); ignorado si se pasa hasta"] = 1,
    desde_ahora: Annotated[bool, "Solo eventos que todavia no empezaron"] = False,
    desde: Annotated[str, "Fecha inicio AAAA-MM-DD opcional (ej. '2026-12-01')"] = "",
    hasta: Annotated[str, "Fecha fin AAAA-MM-DD inclusive opcional (ej. '2027-02-28')"] = "",
    buscar: Annotated[str, "Filtrar por texto en titulo o descripcion (opcional)"] = "",
) -> dict:
    items = events(dias, desde_ahora, desde=desde, hasta=hasta, buscar=buscar)
    return {"eventos": items} if items else {"eventos": [], "nota": "No hay eventos en ese periodo."}


@tool("Crea un evento en Google Calendar (con color opcional).")
def crear_evento(
    titulo: Annotated[str, "Titulo del evento"],
    inicio: Annotated[str, "Fecha y hora local AAAA-MM-DDTHH:MM, o AAAA-MM-DD si es de dia completo"],
    duracion_minutos: Annotated[int, "Duracion en minutos"] = 60,
    descripcion: Annotated[str, "Detalle opcional"] = "",
    lugar: Annotated[str, "Lugar opcional"] = "",
    color: Annotated[str, "Color opcional: rojo, naranja, amarillo, verde, verde claro, celeste, azul, violeta, lavanda, rosa, gris"] = "",
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
    color_id = _resolve_color_id(color)
    if color_id:
        body["colorId"] = color_id
    created = service.events().insert(calendarId="primary", body=body).execute()
    out: dict = {"creado": created.get("summary"), "inicio": inicio.strip(), "id": created.get("id", "")}
    if color_id:
        out["color"] = COLOR_NAMES[color_id]
    return out


@tool(
    "Edita o reprograma uno o varios eventos existentes en Google Calendar: titulo, fecha/hora, duracion, "
    "descripcion, lugar o color (rojo, naranja, amarillo, verde, celeste, azul, violeta, rosa, gris). "
    "Para un evento puntual use evento_id o titulo (+ fecha opcional). Para aplicar cambios (como cambiar el color) "
    "a todos los eventos que coincidan con un titulo en un rango, pase titulo + desde + hasta.",
    timeout=60,
)
def editar_evento(
    titulo: Annotated[str, "Titulo actual aproximado del evento (opcional si pasa evento_id)"] = "",
    fecha: Annotated[str, "Fecha actual del evento AAAA-MM-DD para desambiguar un evento puntual (opcional)"] = "",
    evento_id: Annotated[str, "ID exacto del evento obtenido de agenda (opcional)"] = "",
    desde: Annotated[str, "Fecha inicio AAAA-MM-DD para editar todos los coincidentes en un rango (opcional)"] = "",
    hasta: Annotated[str, "Fecha fin AAAA-MM-DD inclusive para editar en rango (opcional)"] = "",
    nuevo_titulo: Annotated[str, "Nuevo titulo para el evento (opcional)"] = "",
    nuevo_inicio: Annotated[str, "Nueva fecha/hora AAAA-MM-DDTHH:MM o AAAA-MM-DD si es dia completo (opcional)"] = "",
    duracion_minutos: Annotated[int, "Nueva duracion en minutos; 0 mantiene la duracion actual"] = 0,
    descripcion: Annotated[str, "Nueva descripcion (opcional)"] = "",
    lugar: Annotated[str, "Nuevo lugar (opcional)"] = "",
    color: Annotated[str, "Color opcional: rojo, naranja, amarillo, verde, verde claro, celeste, azul, violeta, lavanda, rosa, gris"] = "",
) -> dict:
    color_id = _resolve_color_id(color)
    if not any([nuevo_titulo.strip(), nuevo_inicio.strip(), duracion_minutos > 0, descripcion.strip(), lugar.strip(), color_id]):
        raise ToolError("No se indico ningun cambio para el evento.")
    service = _service("calendar", "v3")

    if desde.strip() and hasta.strip():
        if nuevo_inicio.strip():
            raise ToolError("No se puede fijar una misma fecha 'nuevo_inicio' a multiples eventos en un rango.")
        targets = _find_events_in_range(service, titulo, desde, hasta)
    else:
        targets = [_find_event(service, titulo=titulo, fecha=fecha, evento_id=evento_id)]

    modified: list[dict] = []
    for cal_id, ev in targets:
        old_summary = ev.get("summary", "(sin titulo)")
        updated_body = dict(ev)
        if nuevo_titulo.strip():
            updated_body["summary"] = nuevo_titulo.strip()
        if descripcion.strip():
            updated_body["description"] = descripcion.strip()
        if lugar.strip():
            updated_body["location"] = lugar.strip()
        if color_id:
            updated_body["colorId"] = color_id

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
        item: dict = {
            "antes": old_summary,
            "titulo": updated.get("summary", old_summary),
            "fecha": begin.strftime("%Y-%m-%d"),
            "hora": "todo el dia" if all_day else begin.strftime("%H:%M"),
            "lugar": updated.get("location", ""),
            "id": updated.get("id", ev["id"]),
        }
        if color_id:
            item["color"] = COLOR_NAMES[color_id]
        modified.append(item)

    if len(modified) == 1 and not (desde.strip() and hasta.strip()):
        return modified[0]
    return {
        "actualizados": len(modified),
        "color": COLOR_NAMES[color_id] if color_id else "",
        "eventos": [{"titulo": m["titulo"], "fecha": m["fecha"], "hora": m["hora"]} for m in modified[:15]],
    }


@tool(
    "Elimina o cancela uno o varios eventos existentes en Google Calendar. "
    "Para un evento puntual use evento_id o titulo (+ fecha AAAA-MM-DD). "
    "Para borrar TODAS las ocurrencias de un evento repetido en un periodo (ej. vacaciones), "
    "pase titulo + desde (AAAA-MM-DD) + hasta (AAAA-MM-DD).",
    timeout=60,
)
def eliminar_evento(
    titulo: Annotated[str, "Titulo aproximado del evento a borrar (opcional si pasa evento_id)"] = "",
    fecha: Annotated[str, "Fecha del evento AAAA-MM-DD para borrar un evento puntual (opcional)"] = "",
    evento_id: Annotated[str, "ID exacto del evento obtenido de agenda (opcional)"] = "",
    desde: Annotated[str, "Fecha inicio AAAA-MM-DD para borrar todas las ocurrencias en un rango (usar con hasta)"] = "",
    hasta: Annotated[str, "Fecha fin AAAA-MM-DD inclusive para borrar en rango (usar con desde)"] = "",
) -> dict:
    service = _service("calendar", "v3")
    if desde.strip() and hasta.strip():
        targets = _find_events_in_range(service, titulo, desde, hasta)
        dates: list[str] = []
        for cal_id, ev in targets:
            begin, _ = _event_time(ev["start"])
            service.events().delete(calendarId=cal_id, eventId=ev["id"]).execute()
            dates.append(begin.strftime("%Y-%m-%d"))
        return {
            "eliminados": len(targets),
            "titulo": targets[0][1].get("summary", titulo),
            "desde": desde.strip()[:10],
            "hasta": hasta.strip()[:10],
            "muestra_fechas": dates[:10],
        }

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
