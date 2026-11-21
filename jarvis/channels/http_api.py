from __future__ import annotations

import hmac
import logging
import socket

from aiohttp import web

from jarvis.config import env, get_config

log = logging.getLogger(__name__)


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class HttpChannel:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.token = env("JARVIS_HTTP_TOKEN")
        self.runner: web.AppRunner | None = None

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=256 * 1024)
        app.router.add_post("/comando", self.handle_command)
        app.router.add_get("/salud", self.handle_health)
        return app

    async def start(self) -> None:
        if not self.token:
            log.warning("[INACTIVO] Servidor para Siri desactivado: falta JARVIS_HTTP_TOKEN en .env")
            return
        cfg = get_config()
        host = cfg.get("canales.http_host", "0.0.0.0")
        port = int(cfg.get("canales.http_puerto", 8765))
        self.runner = web.AppRunner(self.build_app(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, host, port).start()
        log.info("[ACTIVE] Servidor para Siri en http://%s:%s/comando", local_ip(), port)

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else header.strip()
        return bool(self.token) and hmac.compare_digest(supplied.encode(), self.token.encode())

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def handle_command(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            log.warning("[BLOQUEADO] Pedido HTTP sin token valido desde %s", request.remote)
            return web.json_response({"error": "No autorizado"}, status=401)
        try:
            if request.content_type == "application/json":
                payload = await request.json()
                text = str(payload.get("texto", "")).strip()
            else:
                text = (await request.text()).strip()
        except ValueError:
            return web.json_response({"error": "JSON invalido"}, status=400)
        if not text:
            return web.json_response({"error": "Falta el campo texto"}, status=400)
        answer = await self.engine.handle_text(text, "http")
        return web.json_response({"respuesta": answer})
