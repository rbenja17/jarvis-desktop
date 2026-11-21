from __future__ import annotations

import re
import urllib.parse
import webbrowser
from typing import Annotated, Literal

import requests

from jarvis.textutil import normalize
from jarvis.tools.registry import ToolError, tool

SITES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "correo": "https://mail.google.com",
    "calendario": "https://calendar.google.com",
    "drive": "https://drive.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com",
    "github": "https://github.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "mercado libre": "https://www.mercadolibre.com.ar",
    "mercadolibre": "https://www.mercadolibre.com.ar",
    "maps": "https://maps.google.com",
    "mapas": "https://maps.google.com",
    "traductor": "https://translate.google.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "gemini": "https://gemini.google.com",
    "ai studio": "https://aistudio.google.com",
    "twitch": "https://www.twitch.tv",
    "spotify web": "https://open.spotify.com",
    "disney": "https://www.disneyplus.com",
    "prime video": "https://www.primevideo.com",
}

SEARCH_URLS = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "maps": "https://www.google.com/maps/search/{}",
    "wikipedia": "https://es.wikipedia.org/w/index.php?search={}",
    "mercadolibre": "https://listado.mercadolibre.com.ar/{}",
    "imagenes": "https://www.google.com/search?tbm=isch&q={}",
}

_DOMAIN = re.compile(r"^(https?://)?([\w-]+\.)+[a-z]{2,}(/\S*)?$", re.IGNORECASE)


@tool("Abre un sitio web en el navegador: por nombre conocido (youtube, gmail, whatsapp, netflix...) o por direccion.")
def abrir_web(sitio: Annotated[str, "Nombre del sitio o direccion web"]) -> dict:
    text = sitio.strip()
    key = normalize(text)
    if key in SITES:
        url = SITES[key]
    elif _DOMAIN.match(text.replace(" ", "")):
        url = text.replace(" ", "")
        url = url if url.startswith("http") else f"https://{url}"
    else:
        url = SEARCH_URLS["google"].format(urllib.parse.quote_plus(text))
    webbrowser.open(url)
    return {"abierto": url}


@tool("Busca algo en el navegador (Google, YouTube, Maps, Wikipedia, Mercado Libre o imagenes) y muestra los resultados.")
def buscar_en_navegador(
    consulta: Annotated[str, "Que buscar"],
    donde: Literal["google", "youtube", "maps", "wikipedia", "mercadolibre", "imagenes"] = "google",
) -> dict:
    if donde == "mercadolibre":
        url = SEARCH_URLS[donde].format(urllib.parse.quote(consulta.strip().replace(" ", "-")))
    else:
        url = SEARCH_URLS[donde].format(urllib.parse.quote_plus(consulta))
    webbrowser.open(url)
    return {"abierto": url}


def _clip(text: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def search_web(query: str, max_results: int = 5) -> list[dict]:
    from ddgs import DDGS

    last_exc: Exception | None = None
    for kwargs in ({"region": "wt-wt"}, {"backend": "google"}, {"region": "es-es", "backend": "duckduckgo"}):
        try:
            results = DDGS().text(query, max_results=max_results, **kwargs)
            return [{"titulo": r.get("title"), "resumen": _clip(r.get("body", "")), "url": r.get("href")} for r in results]
        except Exception as exc:
            last_exc = exc
    raise ToolError(f"No encontre resultados en la web ({last_exc}).")


def page_text(url: str, limit: int = 2000) -> str:
    resp = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    resp.raise_for_status()
    if "html" not in resp.headers.get("Content-Type", "html"):
        return ""
    html_text = re.sub(r"<(script|style|noscript|svg|header|footer|nav)\b.*?</\1>", " ", resp.text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html_text)
    import html as html_lib

    return _clip(html_lib.unescape(text), limit)


def google_news(query: str = "", max_results: int = 6) -> list[dict]:
    import xml.etree.ElementTree as ET

    from jarvis.config import get_config

    country = str(get_config().get("resumen.noticias_pais", "AR")).upper()
    params = {"hl": "es-419", "gl": country, "ceid": f"{country}:es-419"}
    url = "https://news.google.com/rss"
    if query:
        url += "/search"
        params["q"] = query
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    out = []
    for item in ET.fromstring(resp.content).findall("./channel/item")[:max_results]:
        source = item.findtext("source") or ""
        title = item.findtext("title") or ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        out.append({"titulo": title, "medio": source, "fecha": (item.findtext("pubDate") or "")[:22], "url": item.findtext("link")})
    return out


def search_news(query: str, max_results: int = 5) -> list[dict]:
    try:
        results = google_news(query, max_results)
        if results:
            return results
    except Exception:
        pass
    from ddgs import DDGS

    for region in ("es-es", "wt-wt"):
        try:
            results = DDGS().news(query, region=region, max_results=max_results)
            return [
                {
                    "titulo": r.get("title"),
                    "medio": r.get("source"),
                    "fecha": (r.get("date") or "")[:16].replace("T", " "),
                    "url": r.get("url"),
                }
                for r in results
            ]
        except Exception:
            continue
    raise ToolError("No encontre noticias sobre eso.")


@tool(
    "Busca informacion actual en internet (noticias, resultados deportivos, precios, cotizaciones, datos, "
    "personas, lugares). Devuelve resultados con resumen y fuente: respondale al usuario con lo encontrado.",
    timeout=30,
)
def buscar_informacion(
    pregunta: Annotated[str, "Que buscar, como en un buscador"],
    noticias: Annotated[bool, "true para buscar noticias recientes"] = False,
) -> dict:
    if noticias:
        return {"resultados": search_news(pregunta), "consulta": pregunta}
    results = search_web(pregunta)
    answer: dict = {"resultados": results, "consulta": pregunta}
    for item in results[:3]:
        try:
            text = page_text(item["url"])
        except Exception:
            continue
        if len(text) > 200:
            answer["contenido_principal"] = {"url": item["url"], "texto": text}
            break
    return answer
