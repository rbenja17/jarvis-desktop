from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from jarvis.tools import productivity, routines
from jarvis.tools.registry import ToolError

NOW = dt.datetime(2026, 11, 17, 10, 0)


def test_parse_when_variants():
    assert productivity.parse_when("", 15, NOW) == NOW + dt.timedelta(minutes=15)
    assert productivity.parse_when("2026-11-18T09:30", 0, NOW) == dt.datetime(2026, 11, 18, 9, 30)
    assert productivity.parse_when("18:00", 0, NOW) == dt.datetime(2026, 11, 17, 18, 0)
    assert productivity.parse_when("08:00", 0, NOW) == dt.datetime(2026, 11, 18, 8, 0)
    with pytest.raises(ToolError):
        productivity.parse_when("2026-11-16T09:00", 0, NOW)


def test_reminders_lifecycle():
    item = productivity.add_reminder("llamar a mama", NOW + dt.timedelta(minutes=5))
    assert [r["id"] for r in productivity.list_reminders()] == [item["id"]]
    assert productivity.pop_due(NOW) == []
    due = productivity.pop_due(NOW + dt.timedelta(minutes=6))
    assert [r["texto"] for r in due] == ["llamar a mama"]
    assert productivity.list_reminders() == []


def test_cancel_reminder_by_text():
    productivity.add_reminder("sacar la basura", NOW + dt.timedelta(hours=1))
    assert productivity.cancelar_recordatorio("basura")["cancelado"] == "sacar la basura"


def test_memory_facts_deduplicate():
    productivity.recordar_dato("Mi playlist para trabajar es Deep Focus")
    productivity.recordar_dato("mi playlist para trabajar es deep focus")
    assert len(productivity.memory_facts()) == 1
    productivity.olvidar_dato("playlist para trabajar")
    assert productivity.memory_facts() == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("todos", [0, 1, 2, 3, 4, 5, 6]),
        ("lunes a viernes", [0, 1, 2, 3, 4]),
        ("fines de semana", [5, 6]),
        ("todos los lunes", [0]),
        ("martes y jueves", [1, 3]),
        ("dias de semana", [0, 1, 2, 3, 4]),
        ("viernes a lunes", [4, 5, 6, 0]),
    ],
)
def test_parse_days(text, expected):
    assert routines.parse_days(text) == expected


def test_scheduled_task_runs_once_per_day():
    routines.programar_tarea("dame el clima", "09:55", "todos")
    assert routines.pop_due_tasks(dt.datetime(2026, 11, 17, 9, 50)) == []
    due = routines.pop_due_tasks(dt.datetime(2026, 11, 17, 10, 0))
    assert [t["instruccion"] for t in due] == ["dame el clima"]
    assert routines.pop_due_tasks(dt.datetime(2026, 11, 17, 10, 1)) == []
    assert len(routines.pop_due_tasks(dt.datetime(2026, 11, 18, 9, 56))) == 1


def test_routine_runs_tool_steps(monkeypatch):
    from jarvis import config

    cfg = config.get_config()
    cfg._data["rutinas"] = {"modo_prueba": [{"herramienta": "fecha_hora"}, {"esperar": 0}]}
    result = asyncio.run(routines.run_routine("modo prueba"))
    assert result["rutina"] == "modo prueba"
    assert "ahora" in result["pasos"][0]


def test_briefing_waits_for_time_and_presence(monkeypatch):
    import datetime as dt

    from jarvis import config
    from jarvis.tools import briefing

    config.get_config()._data["resumen"] = {"hora": "08:00", "hasta_hora": 12, "esperar_presencia": True}
    day = dt.datetime(2026, 11, 18, 7, 59)
    assert not briefing.scheduled_due(day, idle_seconds=5)
    assert not briefing.scheduled_due(day.replace(hour=8, minute=10), idle_seconds=900)
    assert briefing.scheduled_due(day.replace(hour=9, minute=40), idle_seconds=30)
    assert not briefing.scheduled_due(day.replace(hour=12, minute=5), idle_seconds=5)
    briefing.mark_delivered(day.replace(hour=8, minute=30))
    assert not briefing.scheduled_due(day.replace(hour=9, minute=40), idle_seconds=5)


def test_briefing_at_dawn_does_not_cancel_the_morning_one():
    import datetime as dt

    from jarvis import config, store
    from jarvis.tools import briefing

    config.get_config()._data["resumen"] = {"hora": "08:00", "desde_hora": 5, "hasta_hora": 12}
    day = dt.datetime(2026, 11, 18, 2, 25)
    briefing.mark_delivered(day)
    assert briefing.scheduled_due(day.replace(hour=8, minute=1), idle_seconds=5)
    store.save(briefing.STATE_FILE, {"resumen_ultimo": "2026-11-18"})
    assert briefing.scheduled_due(day.replace(hour=8, minute=1), idle_seconds=5)


def test_briefing_scheduled_before_dawn_is_given_once():
    import datetime as dt

    from jarvis import config
    from jarvis.tools import briefing

    config.get_config()._data["resumen"] = {"hora": "04:30", "desde_hora": 5, "hasta_hora": 12}
    day = dt.datetime(2026, 11, 18, 4, 31)
    assert briefing.scheduled_due(day, idle_seconds=5)
    briefing.mark_delivered(day)
    assert not briefing.scheduled_due(day.replace(minute=32), idle_seconds=5)


def test_briefing_is_not_delivered_twice_on_startup(monkeypatch):
    import datetime as dt
    import time

    from jarvis import config
    from jarvis.scheduler import Scheduler
    from jarvis.tools import briefing

    config.get_config()._data["resumen"] = {"al_iniciar": True, "hora": "08:00", "desde_hora": 5, "hasta_hora": 12}
    delivered = []

    async def fake_deliver(engine):
        delivered.append(engine)

    monkeypatch.setattr(briefing, "deliver", fake_deliver)
    monkeypatch.setattr(briefing, "user_idle_seconds", lambda: 3.0)
    now = dt.datetime(2026, 11, 18, 11, 19)

    async def scenario():
        sched = Scheduler(engine="motor")
        sched._last_monitor = time.monotonic()
        assert briefing.startup_due(now)
        sched.deliver_briefing("resumen al iniciar", now)
        await sched.tick(now)
        await sched.tick(now + dt.timedelta(seconds=5))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert delivered == ["motor"]


def test_local_todo_tasks_crud():
    from jarvis.tools import google_ws

    google_ws.crear_tarea("Preparar presentación del proyecto", vence="2026-11-20", notas="Revisar diapositivas")
    google_ws.crear_tarea("Comprar café")
    pending = google_ws.tareas_pendientes()["tareas"]
    assert [t["titulo"] for t in pending] == ["Preparar presentación del proyecto", "Comprar café"]

    edited = google_ws.editar_tarea("presentación del proyecto", nuevo_titulo="Entregar presentación final")
    assert edited["tarea"] == "Entregar presentación final"

    done = google_ws.completar_tarea("Entregar presentación")
    assert done["completada"] == "Entregar presentación final"

    removed = google_ws.eliminar_tarea("café")
    assert removed["eliminada"] == "Comprar café"
    assert google_ws.tareas_pendientes()["tareas"] == []


def test_uia_locate_matches_clock_and_todo_controls(monkeypatch):
    from jarvis.tools import screen

    fake_elements = [
        {"tipo": "item", "nombre": "Sesiones de concentración", "aid": "FocusSessionItem", "x": 110, "y": 180, "interactivo": True},
        {"tipo": "item", "nombre": "Temporizador", "aid": "TimerItem", "x": 110, "y": 230, "interactivo": True},
        {"tipo": "texto", "nombre": "Iniciar sesión de concentración", "aid": "", "x": 450, "y": 380, "interactivo": False},
        {"tipo": "boton", "nombre": "Iniciar sesión de concentración", "aid": "StartFocusBtn", "x": 450, "y": 385, "interactivo": True},
        {"tipo": "boton", "nombre": "Agregar una tarea", "aid": "AddTaskButton", "x": 700, "y": 520, "interactivo": True},
    ]
    monkeypatch.setattr(screen, "uia_elements", lambda hwnd=None, limit=350: fake_elements)

    hit = screen.uia_locate(123, "el botón Iniciar sesión de concentración")
    assert hit is not None
    assert hit["detalle"] == "boton: Iniciar sesión de concentración"
    assert (hit["x"], hit["y"]) == (450, 385)

    todo_hit = screen.uia_locate(123, "Agregar una tarea")
    assert todo_hit is not None and todo_hit["x"] == 700
