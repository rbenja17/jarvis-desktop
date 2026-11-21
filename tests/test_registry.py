from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from jarvis.tools import registry
from jarvis.tools.registry import ToolContext, ToolError, build_schema


def sample(
    nombre: Annotated[str, "Nombre"],
    nivel: int = 10,
    modo: Literal["a", "b"] = "a",
    lista: list[str] | None = None,
    activo: bool = True,
) -> dict:
    return {}


def test_schema_from_type_hints():
    schema = build_schema(sample)
    props = schema["properties"]
    assert schema["type"] == "OBJECT"
    assert schema["required"] == ["nombre"]
    assert props["nombre"] == {"type": "STRING", "description": "Nombre"}
    assert props["nivel"]["type"] == "INTEGER"
    assert props["modo"] == {"type": "STRING", "enum": ["a", "b"]}
    assert props["lista"] == {"type": "ARRAY", "items": {"type": "STRING"}}
    assert props["activo"]["type"] == "BOOLEAN"


def test_all_declarations_are_valid_for_gemini():
    voice = registry.declarations("voz")
    text = registry.declarations("texto")
    names = {d.name for d in voice}
    assert "terminar_conversacion" in names
    assert "buscar_informacion" in names and "buscar_informacion" in {d.name for d in text}
    assert "buscar_informacion" not in {d.name for d in registry.declarations("voz", {"buscar_informacion"})}
    assert len(voice) > 60


def test_call_coerces_args_and_normalizes_results():
    @registry.tool("prueba", name="_prueba_suma")
    def suma(a: int, b: int = 1) -> int:
        return a + b

    result = asyncio.run(registry.call("_prueba_suma", {"a": 2.0, "b": "3", "extra": 1}))
    assert result == {"resultado": 5}


def test_tool_errors_are_returned_not_raised():
    @registry.tool("falla", name="_prueba_falla")
    def falla() -> dict:
        raise ToolError("no se pudo")

    assert asyncio.run(registry.call("_prueba_falla")) == {"error": "no se pudo"}
    assert "error" in asyncio.run(registry.call("no_existe"))


def test_async_tools_receive_context():
    seen = {}

    @registry.tool("ctx", name="_prueba_ctx")
    async def ctx_tool() -> dict:
        seen["channel"] = registry.current_context.get().channel
        return {"ok": True}

    asyncio.run(registry.call("_prueba_ctx", {}, ToolContext(channel="telegram:1")))
    assert seen["channel"] == "telegram:1"
