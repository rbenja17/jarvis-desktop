from __future__ import annotations

from jarvis.brain import usage
from jarvis.tools.registry import tool


@tool("Cuanto se uso hoy la API gratuita de Gemini (pedidos por modelo y minutos de voz).")
def uso_de_api() -> dict:
    data = usage.today()
    seconds = data.pop("voz_en_vivo_segundos", 0)
    return {
        "pedidos_por_modelo": {k: v for k, v in data.items() if not k.startswith("cupo_agotado")},
        "cupos_agotados": {k.split(":", 1)[1]: v for k, v in data.items() if k.startswith("cupo_agotado")},
        "minutos_de_voz": round(seconds / 60, 1),
        "nota": "Los limites exactos de la capa gratuita se ven en aistudio.google.com (seccion Rate limits).",
    }
