from __future__ import annotations

import asyncio
import contextvars
import functools
import importlib
import inspect
import json
import logging
import time
import types as pytypes
import typing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Literal, get_args, get_origin

log = logging.getLogger(__name__)
audit = logging.getLogger("jarvis.acciones")

MODES_ALL = frozenset({"voz", "texto"})

TOOL_MODULES = (
    "jarvis.tools.confirm",
    "jarvis.tools.session",
    "jarvis.tools.apps",
    "jarvis.tools.audio_ctl",
    "jarvis.tools.media",
    "jarvis.tools.spotify",
    "jarvis.tools.bluetooth",
    "jarvis.tools.system",
    "jarvis.tools.web",
    "jarvis.tools.files",
    "jarvis.tools.screen",
    "jarvis.tools.productivity",
    "jarvis.tools.instructions",
    "jarvis.tools.history",
    "jarvis.tools.info",
    "jarvis.tools.phone",
    "jarvis.tools.google_ws",
    "jarvis.tools.briefing",
    "jarvis.tools.routines",
    "jarvis.tools.usage_tool",
)


class ToolError(Exception):
    """Error esperado que se devuelve al modelo como {"error": mensaje}."""


@dataclass(frozen=True)
class ToolContext:
    channel: str = "texto"
    user_text: str = ""

    @property
    def unattended(self) -> bool:
        return self.channel in {"programada", "rutina"}


current_context: contextvars.ContextVar[ToolContext] = contextvars.ContextVar(
    "jarvis_tool_ctx", default=ToolContext()
)


@dataclass
class ToolSpec:
    name: str
    description: str
    func: Callable[..., Any]
    schema: dict[str, Any]
    confirm: bool = False
    timeout: float = 20.0
    modes: frozenset[str] = MODES_ALL

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.func)


_REGISTRY: dict[str, ToolSpec] = {}

_TYPE_MAP = {str: "STRING", int: "INTEGER", float: "NUMBER", bool: "BOOLEAN"}


def _schema_for(annotation: Any) -> dict[str, Any]:
    description = None
    if get_origin(annotation) is Annotated:
        base, *meta = get_args(annotation)
        description = next((m for m in meta if isinstance(m, str)), None)
        annotation = base

    origin = get_origin(annotation)
    if origin in (typing.Union, pytypes.UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        schema = _schema_for(non_none[0]) if non_none else {"type": "STRING"}
    elif origin is Literal:
        schema = {"type": "STRING", "enum": [str(v) for v in get_args(annotation)]}
    elif origin in (list, tuple) or annotation in (list, tuple):
        args = get_args(annotation)
        schema = {"type": "ARRAY", "items": _schema_for(args[0]) if args else {"type": "STRING"}}
    else:
        schema = {"type": _TYPE_MAP.get(annotation, "STRING")}

    if description:
        schema["description"] = description
    return schema


def build_schema(func: Callable[..., Any]) -> dict[str, Any]:
    hints = typing.get_type_hints(func, include_extras=True)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in inspect.signature(func).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[pname] = _schema_for(hints.get(pname, str))
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    schema: dict[str, Any] = {"type": "OBJECT", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def tool(
    description: str,
    *,
    confirm: bool = False,
    timeout: float = 20.0,
    modes: frozenset[str] | set[str] = MODES_ALL,
    name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = ToolSpec(
            name=name or func.__name__,
            description=description.strip(),
            func=func,
            schema=build_schema(func),
            confirm=confirm,
            timeout=timeout,
            modes=frozenset(modes),
        )
        _REGISTRY[spec.name] = spec
        return func

    return decorator


def load_all() -> None:
    for module in TOOL_MODULES:
        try:
            importlib.import_module(module)
        except Exception:
            log.exception("[ERROR] No se pudo cargar %s", module)


def get(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def all_specs(mode: str | None = None) -> list[ToolSpec]:
    return [s for s in _REGISTRY.values() if mode is None or mode in s.modes]


def declarations(mode: str, exclude: set[str] | frozenset[str] = frozenset()) -> list[Any]:
    from google.genai import types

    out = []
    for spec in all_specs(mode):
        if spec.name in exclude:
            continue
        kwargs: dict[str, Any] = {"name": spec.name, "description": spec.description}
        if spec.schema.get("properties"):
            kwargs["parameters"] = spec.schema
        out.append(types.FunctionDeclaration(**kwargs))
    return out


def _init_worker() -> None:
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass


_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="jarvis-tool", initializer=_init_worker)


async def in_worker(func: Callable[..., Any], *args: Any) -> Any:
    loop = asyncio.get_running_loop()
    cctx = contextvars.copy_context()
    return await loop.run_in_executor(_executor, functools.partial(cctx.run, func, *args))


def _coerce(value: Any, schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    try:
        if kind == "INTEGER" and not isinstance(value, bool):
            return int(round(float(value)))
        if kind == "NUMBER":
            return float(value)
        if kind == "BOOLEAN" and isinstance(value, str):
            return value.strip().lower() in {"true", "1", "si", "sí", "yes"}
        if kind == "STRING" and not isinstance(value, str):
            return str(value)
        if kind == "ARRAY" and isinstance(value, str):
            return [value]
    except (TypeError, ValueError):
        return value
    return value


def _clean_args(spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
    props = spec.schema.get("properties", {})
    return {k: _coerce(v, props[k]) for k, v in args.items() if k in props and v is not None}


def _normalize(result: Any) -> dict[str, Any]:
    if result is None:
        result = {"ok": True}
    elif not isinstance(result, dict):
        result = {"resultado": result}
    return json.loads(json.dumps(result, ensure_ascii=False, default=str))


def _short(value: Any, limit: int = 220) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def call(name: str, args: dict[str, Any] | None = None, ctx: ToolContext | None = None) -> dict[str, Any]:
    spec = _REGISTRY.get(name)
    ctx = ctx or current_context.get()
    if spec is None:
        audit.info("[ERROR] %s herramienta desconocida: %s", ctx.channel, name)
        return {"error": f"Herramienta desconocida: {name}"}
    clean = _clean_args(spec, dict(args or {}))
    if spec.confirm:
        from jarvis.tools import confirm

        return confirm.request(spec, clean, ctx)
    return await execute(spec, clean, ctx)


async def execute(spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    token = current_context.set(ctx)
    started = time.monotonic()
    status = "[OK]"
    try:
        if spec.is_async:
            result = await asyncio.wait_for(spec.func(**args), spec.timeout)
        else:
            loop = asyncio.get_running_loop()
            cctx = contextvars.copy_context()
            job = loop.run_in_executor(_executor, functools.partial(cctx.run, spec.func, **args))
            result = await asyncio.wait_for(job, spec.timeout)
        result = _normalize(result)
        if "error" in result:
            status = "[ERROR]"
    except asyncio.TimeoutError:
        status = "[TIMEOUT]"
        result = {"error": f"La accion {spec.name} tardo demasiado y se cancelo."}
    except ToolError as exc:
        status = "[ERROR]"
        result = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - se informa al modelo y queda en el log
        status = "[ERROR]"
        log.exception("Fallo la herramienta %s", spec.name)
        result = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        current_context.reset(token)
    elapsed = (time.monotonic() - started) * 1000
    audit.info("%s %s %s %s -> %s (%.0f ms)", status, ctx.channel, spec.name, _short(args), _short(result), elapsed)
    return result


def run_sync(name: str, args: dict[str, Any] | None = None, ctx: ToolContext | None = None) -> dict[str, Any]:
    return asyncio.run(call(name, args, ctx))
