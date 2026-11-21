from __future__ import annotations

import re
from typing import Annotated

from rapidfuzz import fuzz

from jarvis import store
from jarvis.config import data_path
from jarvis.textutil import best_match, normalize
from jarvis.tools.registry import ToolError, tool

FILE = "instrucciones.md"
MAX_INSTRUCTIONS = 60
SAME_RULE = 90
HEADER = (
    "# Instrucciones para JARVIS\n\n"
    "Reglas permanentes que JARVIS sigue siempre, en todas las charlas. Se agregan solas cuando usted\n"
    "le indica cómo quiere que haga algo, y también puede editarlas acá: una por línea, empezando con «- ».\n\n"
)
_RULE_START = re.compile(r"^(cuando|siempre|nunca|no |si |para |antes|usar|use |usa )", re.IGNORECASE)


def _path():
    return data_path(FILE)


def load() -> list[str]:
    path = _path()
    if not path.exists():
        _migrate_from_memory()
    if not path.exists():
        return []
    rules = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ")) and line[2:].strip():
            rules.append(line[2:].strip())
    return rules


def save(rules: list[str]) -> None:
    body = "".join(f"- {rule}\n" for rule in rules[-MAX_INSTRUCTIONS:])
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(HEADER + body, "utf-8")
    tmp.replace(path)


def _migrate_from_memory() -> None:
    from jarvis.tools.productivity import MEMORY

    facts = store.load(MEMORY, [])
    moved = [f["dato"] for f in facts if _RULE_START.match(f["dato"].strip())]
    if not moved:
        return
    save(moved)
    store.update(MEMORY, [], lambda xs: [f for f in xs if f["dato"] not in moved])


def _similar(a: str, b: str) -> float:
    return float(fuzz.token_set_ratio(normalize(a), normalize(b)))


def add(rule: str, replaces: str = "") -> tuple[str, str | None]:
    rule = " ".join(rule.split()).strip()
    if not rule:
        raise ToolError("La instrucción está vacía.")
    rule = rule[0].upper() + rule[1:]
    rules = load()
    old = None
    if replaces.strip():
        old, _ = best_match(replaces, rules, key=lambda r: r, threshold=60)
    if old is None:
        old = next((r for r in rules if _similar(r, rule) >= SAME_RULE), None)
    if old is not None:
        rules[rules.index(old)] = rule
    else:
        rules.append(rule)
    save(rules)
    return rule, old


def as_prompt(limit: int = 4000) -> list[str]:
    rules, total = [], 0
    for rule in load():
        total += len(rule)
        if total > limit:
            break
        rules.append(rule)
    return rules


@tool(
    "Guarda una instruccion permanente de como el usuario quiere que usted haga algo, para cumplirla en "
    "todas las charlas futuras. Uselo SIN preguntar cuando el usuario indique como hacer algo de ahora en "
    "adelante o lo corrija ('cuando te pida X hace Y', 'siempre', 'nunca', 'la proxima vez', 'no uses...', "
    "'eso hacelo desde...'). Redactela como regla clara en tercera persona."
)
def guardar_instruccion(
    instruccion: Annotated[str, "La regla, ej. 'Para subir o bajar el volumen de Spotify, usar spotify_volumen (la app), no el volumen de Windows'"],
    reemplaza: Annotated[str, "Si corrige una instruccion anterior, texto aproximado de esa instruccion"] = "",
) -> dict:
    rule, old = add(instruccion, reemplaza)
    result = {"guardada": rule}
    if old and old != rule:
        result["reemplazo_a"] = old
    return result


@tool("Lista las instrucciones permanentes que el usuario le dio.")
def ver_instrucciones() -> dict:
    rules = load()
    return {"instrucciones": rules} if rules else {"instrucciones": [], "nota": "Todavía no hay instrucciones guardadas."}


@tool("Borra una instruccion permanente (cuando el usuario dice que ya no la siga).")
def borrar_instruccion(cual: Annotated[str, "Texto aproximado de la instruccion"]) -> dict:
    rules = load()
    target, _ = best_match(cual, rules, key=lambda r: r, threshold=55)
    if target is None:
        raise ToolError("No tengo ninguna instrucción parecida.")
    save([r for r in rules if r != target])
    return {"borrada": target}
