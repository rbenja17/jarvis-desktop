from __future__ import annotations

import asyncio

from jarvis.tools import confirm, registry
from jarvis.tools.registry import ToolContext

calls: list[str] = []


@registry.tool("peligrosa", confirm=True, name="_prueba_peligrosa")
def peligrosa(que: str = "x") -> dict:
    calls.append(que)
    return {"hecho": que}


def _run(coro):
    return asyncio.run(coro)


def test_dangerous_tool_is_not_executed_without_confirmation():
    calls.clear()
    ctx = ToolContext(channel="voz")
    result = _run(registry.call("_prueba_peligrosa", {"que": "apagar"}, ctx))
    assert result["requiere_confirmacion"] is True
    assert calls == []


def test_model_cannot_confirm_by_itself(monkeypatch):
    monkeypatch.setattr(confirm, "WAIT_FOR_TRANSCRIPT", 0.1)
    calls.clear()
    ctx = ToolContext(channel="voz")
    confirm.note_user_text("apaga la computadora, sí", "voz")
    _run(registry.call("_prueba_peligrosa", {"que": "apagar"}, ctx))
    result = _run(registry.call("confirmar_accion", {}, ctx))
    assert "error" in result
    assert calls == []


def test_user_affirmation_after_request_executes():
    calls.clear()
    ctx = ToolContext(channel="voz")
    _run(registry.call("_prueba_peligrosa", {"que": "apagar"}, ctx))
    confirm.note_user_text("Sí, hazlo", "voz")
    result = _run(registry.call("confirmar_accion", {}, ctx))
    assert result == {"hecho": "apagar"}
    assert calls == ["apagar"]
    assert confirm.pending() is None


def test_negative_answer_or_other_channel_does_not_confirm(monkeypatch):
    monkeypatch.setattr(confirm, "WAIT_FOR_TRANSCRIPT", 0.1)
    calls.clear()
    _run(registry.call("_prueba_peligrosa", {}, ToolContext(channel="voz")))
    confirm.note_user_text("no, mejor no", "voz")
    assert "error" in _run(registry.call("confirmar_accion", {}, ToolContext(channel="voz")))
    confirm.note_user_text("sí", "telegram:1")
    assert "error" in _run(registry.call("confirmar_accion", {}, ToolContext(channel="telegram:1")))
    assert calls == []


def test_unattended_contexts_cannot_run_dangerous_actions():
    result = _run(registry.call("_prueba_peligrosa", {}, ToolContext(channel="programada")))
    assert "error" in result
    assert confirm.pending() is None


def test_affirmation_detection():
    assert confirm.is_affirmative("sí, dale")
    assert confirm.is_affirmative("Confirmo")
    assert not confirm.is_affirmative("no")
    assert not confirm.is_affirmative("sí... no, espera")
