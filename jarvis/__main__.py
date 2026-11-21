from __future__ import annotations

import argparse
import asyncio
import ctypes
import logging
import sys

from jarvis.config import load_config
from jarvis.logging_setup import setup_logging

MUTEX_NAME = "Local\\JarvisAsistentePersonal"
ERROR_ALREADY_EXISTS = 183

log = logging.getLogger("jarvis")


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _single_instance() -> object | None:
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


async def _one_shot(text: str) -> str:
    from jarvis.brain.text import QuotaExceeded, TextBrain, make_client
    from jarvis.config import env
    from jarvis.services import services
    from jarvis.tools import confirm, registry

    key = env("GEMINI_API_KEY")
    if not key:
        return "[ERROR] Falta GEMINI_API_KEY en el archivo .env"
    services.loop = asyncio.get_running_loop()
    registry.load_all()
    brain = TextBrain(make_client(key))
    services.text_brain = brain

    telegram = None
    if env("TELEGRAM_BOT_TOKEN"):
        from jarvis.channels.telegram_bot import TelegramChannel

        telegram = TelegramChannel(engine=None)
        try:
            await telegram.start(polling=False)
            services.telegram = telegram
        except Exception:
            telegram = None
    try:
        confirm.note_user_text(text, "texto")
        return await brain.ask(text, channel="texto", remember=False)
    except QuotaExceeded:
        return "[CUPO] Se agoto el cupo gratuito de Gemini por ahora."
    finally:
        if telegram is not None:
            await telegram.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS, asistente personal de voz")
    parser.add_argument("--texto", help="pedido por texto, sin voz ni interfaz")
    parser.add_argument("--sin-interfaz", action="store_true", help="sin orbe ni bandeja")
    parser.add_argument("--selftest", action="store_true", help="prueba de herramientas de solo lectura")
    parser.add_argument("--sin-sonido", action="store_true", help="selftest sin reproducir sonidos")
    parser.add_argument("--autostart", choices=["on", "off"], help="inicio con Windows")
    parser.add_argument("--verbose", action="store_true", help="registro detallado")
    args = parser.parse_args(argv)

    _utf8_console()
    load_config()
    setup_logging(args.verbose)

    if args.selftest:
        from jarvis.selftest import run_selftest

        return run_selftest(play_sound=not args.sin_sonido)

    if args.autostart:
        from jarvis import autostart

        enabled = autostart.set_enabled(args.autostart == "on")
        print(f"[{'ACTIVE' if enabled else 'OFF'}] Inicio con Windows: {autostart.shortcut_path()}")
        return 0

    if args.texto:
        print(asyncio.run(_one_shot(args.texto)))
        return 0

    mutex = _single_instance()
    if mutex is None:
        log.warning("JARVIS ya esta en ejecucion")
        if sys.stderr is None:
            ctypes.windll.user32.MessageBoxW(None, "JARVIS ya está en ejecución.", "JARVIS", 0x40)
        return 1

    if args.sin_interfaz:
        from jarvis.engine import Engine

        engine = Engine()
        try:
            engine.run_forever()
        except KeyboardInterrupt:
            pass
        return 0

    from jarvis.ui.app import run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
