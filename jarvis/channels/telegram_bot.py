from __future__ import annotations

import logging
from pathlib import Path

from jarvis.config import env, env_list

log = logging.getLogger(__name__)

MAX_LEN = 4000
HELP = (
    "JARVIS a su servicio. Puede escribirme o mandarme notas de voz con lo que necesite en la PC.\n"
    "Comandos rápidos (no usan cupo de Gemini):\n"
    "/estado - batería, CPU, memoria y disco\n"
    "/captura - captura de pantalla\n"
    "/sonando - qué está sonando en la PC\n"
    "/resumen - resumen del día\n"
    "/olvidar - empieza una conversación nueva"
)


class TelegramChannel:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.token = env("TELEGRAM_BOT_TOKEN")
        self.allowed: set[int] = set()
        for raw in env_list("TELEGRAM_CHAT_IDS"):
            try:
                self.allowed.add(int(raw))
            except ValueError:
                log.warning("TELEGRAM_CHAT_IDS tiene un valor invalido: %s", raw)
        self.app = None

    @property
    def can_send(self) -> bool:
        return self.app is not None and bool(self.allowed)

    async def start(self, polling: bool = True) -> None:
        from telegram.ext import Application, CommandHandler, MessageHandler, filters

        if not self.token:
            return
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(CommandHandler(["start", "ayuda", "help"], self._on_start))
        self.app.add_handler(CommandHandler("estado", self._on_status))
        self.app.add_handler(CommandHandler("captura", self._on_screenshot))
        self.app.add_handler(CommandHandler("sonando", self._on_playing))
        self.app.add_handler(CommandHandler("resumen", self._on_briefing))
        self.app.add_handler(CommandHandler("olvidar", self._on_forget))
        self.app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._on_voice))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        await self.app.initialize()
        if polling:
            await self.app.start()
            await self.app.updater.start_polling(drop_pending_updates=True)
            log.info("[ACTIVE] Bot de Telegram (%d chats autorizados)", len(self.allowed))

    async def stop(self) -> None:
        if self.app is None:
            return
        try:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()
        except Exception:
            log.debug("Error al cerrar Telegram", exc_info=True)


    async def send_text(self, text: str) -> bool:
        if not self.can_send:
            return False
        for chat_id in self.allowed:
            await self.app.bot.send_message(chat_id=chat_id, text=text[:MAX_LEN])
        return True

    async def send_photo(self, path: Path, caption: str = "") -> bool:
        if not self.can_send:
            return False
        for chat_id in self.allowed:
            with open(path, "rb") as fh:
                await self.app.bot.send_photo(chat_id=chat_id, photo=fh, caption=caption[:1000])
        return True

    async def send_document(self, path: Path) -> bool:
        if not self.can_send:
            return False
        for chat_id in self.allowed:
            with open(path, "rb") as fh:
                await self.app.bot.send_document(chat_id=chat_id, document=fh, filename=Path(path).name)
        return True


    def _authorized(self, update) -> bool:
        chat = update.effective_chat
        ok = chat is not None and chat.id in self.allowed
        if not ok and chat is not None:
            log.warning("[BLOQUEADO] Mensaje de Telegram de un chat no autorizado: %s", chat.id)
        return ok

    async def _on_start(self, update, context) -> None:
        chat_id = update.effective_chat.id
        if chat_id in self.allowed:
            await update.message.reply_text(HELP)
            return
        log.warning("[BLOQUEADO] /start desde el chat %s", chat_id)
        await update.message.reply_text(
            f"Este asistente es privado. Su chat_id es {chat_id}. Si usted es el dueño, agréguelo a "
            "TELEGRAM_CHAT_IDS en el archivo .env de JARVIS y reinícielo."
        )

    async def _reply(self, update, text: str) -> None:
        for start in range(0, max(1, len(text)), MAX_LEN):
            await update.message.reply_text(text[start : start + MAX_LEN] or "Listo.")

    async def _on_text(self, update, context) -> None:
        if not self._authorized(update):
            return
        await update.effective_chat.send_action("typing")
        channel = f"telegram:{update.effective_chat.id}"
        await self._reply(update, await self.engine.handle_text(update.message.text, channel))

    async def _on_voice(self, update, context) -> None:
        if not self._authorized(update):
            return
        media = update.message.voice or update.message.audio
        await update.effective_chat.send_action("typing")
        file = await media.get_file()
        data = bytes(await file.download_as_bytearray())
        mime = getattr(media, "mime_type", None) or "audio/ogg"
        channel = f"telegram:{update.effective_chat.id}"
        await self._reply(update, await self.engine.handle_text("", channel, audio=data, mime=mime))

    async def _on_status(self, update, context) -> None:
        if not self._authorized(update):
            return
        from jarvis.tools.registry import in_worker
        from jarvis.tools.system import system_status

        status = await in_worker(system_status)
        lines = [f"{key.replace('_', ' ')}: {value}" for key, value in status.items()]
        await self._reply(update, "\n".join(lines))

    async def _on_screenshot(self, update, context) -> None:
        if not self._authorized(update):
            return
        from jarvis.tools.registry import in_worker
        from jarvis.tools.system import take_screenshot

        path = await in_worker(take_screenshot)
        with open(path, "rb") as fh:
            await update.message.reply_photo(photo=fh, caption=path.name)

    async def _on_playing(self, update, context) -> None:
        if not self._authorized(update):
            return
        from jarvis.tools.media import now_playing

        info = await now_playing()
        if not info:
            await self._reply(update, "No hay nada reproduciéndose en la PC.")
            return
        await self._reply(update, f"{info['titulo']} - {info['artista']} ({info['app']}, {info['estado']})")

    async def _on_briefing(self, update, context) -> None:
        if not self._authorized(update):
            return
        from jarvis.tools import briefing
        from jarvis.tools.registry import in_worker

        await update.effective_chat.send_action("typing")
        data = await in_worker(briefing.collect)
        await self._reply(update, await briefing.compose(data))

    async def _on_forget(self, update, context) -> None:
        if not self._authorized(update):
            return
        if self.engine.text_brain is not None:
            self.engine.text_brain.forget(f"telegram:{update.effective_chat.id}")
        await self._reply(update, "Entendido. Empezamos de cero.")
