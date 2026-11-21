from __future__ import annotations

import datetime as dt
import json
from typing import Annotated

import requests

from jarvis.config import data_path, get_config
from jarvis.textutil import normalize
from jarvis.tools.registry import ToolError, tool

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]

WMO = {
    0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla con escarcha", 51: "llovizna leve", 53: "llovizna", 55: "llovizna intensa",
    56: "llovizna helada", 57: "llovizna helada intensa", 61: "lluvia leve", 63: "lluvia", 65: "lluvia fuerte",
    66: "lluvia helada", 67: "lluvia helada fuerte", 71: "nevada leve", 73: "nevada", 75: "nevada fuerte",
    77: "granizo fino", 80: "chaparrones leves", 81: "chaparrones", 82: "chaparrones fuertes",
    85: "nevadas leves", 86: "nevadas fuertes", 95: "tormenta", 96: "tormenta con granizo", 99: "tormenta fuerte con granizo",
}


def now_text(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    return f"{DIAS[now.weekday()]} {now.day} de {MESES[now.month - 1]} de {now.year}, {now:%H:%M}"


@tool("Fecha y hora exactas de ahora.")
def fecha_hora() -> dict:
    now = dt.datetime.now()
    return {"ahora": now_text(now), "iso": now.isoformat(timespec="minutes")}


def _geocode(city: str) -> dict:
    cache_file = data_path("cache", "geocoding.json")
    cache = json.loads(cache_file.read_text("utf-8")) if cache_file.exists() else {}
    key = normalize(city)
    if key in cache:
        return cache[key]
    resp = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "es", "format": "json"},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        raise ToolError(f"No encontre la ciudad '{city}'.")
    r = results[0]
    place = {"nombre": r["name"], "pais": r.get("country", ""), "lat": r["latitude"], "lon": r["longitude"]}
    cache[key] = place
    cache_file.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
    return place


def weather(city: str = "", days: int = 1) -> dict:
    city = city.strip() or str(get_config().get("usuario.ciudad", "") or "").strip()
    if not city:
        raise ToolError("No hay una ciudad configurada. Indique la ciudad o configúrela en Configuración.")
    place = _geocode(city)
    days = max(1, min(7, int(days)))
    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["lat"],
            "longitude": place["lon"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "timezone": "auto",
            "forecast_days": days,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    cur = data["current"]
    daily = data["daily"]
    forecast = []
    for i, day in enumerate(daily["time"]):
        date = dt.date.fromisoformat(day)
        forecast.append(
            {
                "dia": "hoy" if i == 0 else DIAS[date.weekday()],
                "max": round(daily["temperature_2m_max"][i]),
                "min": round(daily["temperature_2m_min"][i]),
                "lluvia_probabilidad": daily["precipitation_probability_max"][i],
                "cielo": WMO.get(daily["weather_code"][i], "variable"),
            }
        )
    return {
        "ciudad": f"{place['nombre']}, {place['pais']}".strip(", "),
        "ahora": {
            "temperatura": round(cur["temperature_2m"]),
            "sensacion": round(cur["apparent_temperature"]),
            "humedad": cur["relative_humidity_2m"],
            "viento_kmh": round(cur["wind_speed_10m"]),
            "cielo": WMO.get(cur["weather_code"], "variable"),
        },
        "pronostico": forecast,
    }


@tool("Clima actual y pronostico (hasta 7 dias) de una ciudad. Sin ciudad usa la del usuario.")
def clima(
    ciudad: Annotated[str, "Ciudad; vacio = ciudad configurada"] = "",
    dias: Annotated[int, "Dias de pronostico, 1 a 7"] = 1,
) -> dict:
    return weather(ciudad, dias)


@tool("Cotizacion del dolar en Argentina: oficial, blue, MEP (bolsa), contado con liqui, tarjeta y cripto.")
def cotizacion_dolar() -> dict:
    resp = requests.get("https://dolarapi.com/v1/dolares", timeout=10)
    resp.raise_for_status()
    rates = {}
    for item in resp.json():
        rates[item["nombre"]] = {"compra": item.get("compra"), "venta": item.get("venta")}
    updated = max((i.get("fechaActualizacion", "") for i in resp.json()), default="")
    return {"cotizaciones_en_pesos": rates, "actualizado": updated[:16].replace("T", " ") + " UTC"}
