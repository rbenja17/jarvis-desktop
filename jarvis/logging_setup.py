from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from jarvis.config import data_path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    if getattr(root, "_jarvis_configured", False):
        return
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    main_file = RotatingFileHandler(
        data_path("logs", "jarvis.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    main_file.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(main_file)

    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        console.setLevel(logging.DEBUG if verbose else logging.INFO)
        root.addHandler(console)

    actions = logging.getLogger("jarvis.acciones")
    actions_file = RotatingFileHandler(
        data_path("logs", "acciones.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    actions_file.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    actions.addHandler(actions_file)

    for noisy in ("httpx", "httpcore", "websockets", "urllib3", "telegram", "comtypes", "aiohttp.access", "primp", "ddgs", "googleapiclient", "spotipy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._jarvis_configured = True  # type: ignore[attr-defined]
