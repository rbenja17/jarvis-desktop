from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import psutil

from jarvis.config import get_config
from jarvis.services import services
from jarvis.tools import briefing, productivity, routines

log = logging.getLogger(__name__)

TICK_SECONDS = 5
MONITOR_SECONDS = 60


class Scheduler:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._last_monitor = 0.0
        self._battery_warned = False
        self._disk_warned_on = ""
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run(self) -> None:
        await asyncio.sleep(8)
        now = dt.datetime.now()
        if briefing.startup_due(now):
            self.deliver_briefing("resumen al iniciar", now)
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Error en el planificador")
            await asyncio.sleep(TICK_SECONDS)

    def deliver_briefing(self, label: str, now: dt.datetime) -> None:
        briefing.mark_delivered(now)
        self._spawn(self._safe(briefing.deliver(self.engine), label))

    async def _safe(self, coro, label: str) -> None:
        try:
            await coro
        except Exception:
            log.exception("Fallo %s", label)

    async def tick(self, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now()
        treat = get_config().get("usuario.tratamiento", "señor")

        for reminder in productivity.pop_due(now):
            late = now - dt.datetime.fromisoformat(reminder["vence"]) > dt.timedelta(minutes=5)
            if reminder["tipo"] == "temporizador":
                text = f"{treat.capitalize()}, {reminder['texto'].lower()}."
            else:
                prefix = "recordatorio atrasado" if late else "le recuerdo"
                text = f"{treat.capitalize()}, {prefix}: {reminder['texto']}."
            self._spawn(self._safe(self.engine.announce(text, telegram=True, title="Recordatorio"), "recordatorio"))

        for task in routines.pop_due_tasks(now):
            self._spawn(self._safe(self.run_task(task), f"tarea {task['id']}"))

        if briefing.scheduled_due(now):
            self.deliver_briefing("resumen programado", now)

        if time.monotonic() - self._last_monitor >= MONITOR_SECONDS:
            self._last_monitor = time.monotonic()
            await self.monitors(now)

    async def run_task(self, task: dict) -> None:
        brain = services.text_brain
        if brain is None:
            log.warning("Tarea programada sin cerebro de texto (falta GEMINI_API_KEY)")
            return
        answer = await brain.ask(task["instruccion"], channel="programada", remember=False)
        await self.engine.announce(
            answer,
            voice=task["entrega"] in ("voz", "ambos"),
            telegram=task["entrega"] in ("telegram", "ambos"),
            title="Tarea programada",
        )

    async def monitors(self, now: dt.datetime) -> None:
        cfg = get_config()
        threshold = int(cfg.get("monitores.bateria_baja", 20))
        battery = psutil.sensors_battery()
        if battery is not None and threshold > 0:
            if not battery.power_plugged and battery.percent <= threshold and not self._battery_warned:
                self._battery_warned = True
                treat = cfg.get("usuario.tratamiento", "señor")
                await self.engine.announce(
                    f"{treat.capitalize()}, la batería está en {round(battery.percent)} por ciento. Le sugiero conectar el cargador.",
                    telegram=bool(cfg.get("monitores.avisar_al_celular", False)),
                    title="Batería baja",
                )
            elif battery.power_plugged or battery.percent > threshold + 5:
                self._battery_warned = False

        min_gb = float(cfg.get("monitores.disco_minimo_gb", 10))
        today = now.date().isoformat()
        if min_gb > 0 and self._disk_warned_on != today:
            free_gb = psutil.disk_usage("C:\\").free / 1e9
            if free_gb < min_gb:
                self._disk_warned_on = today
                await self.engine.announce(
                    f"Aviso: quedan {free_gb:.1f} gigabytes libres en el disco C.",
                    voice=False,
                    telegram=False,
                    title="Disco casi lleno",
                )
