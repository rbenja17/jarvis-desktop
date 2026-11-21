from __future__ import annotations

import json
import os
import threading
from typing import Any

from jarvis.config import data_path

_lock = threading.RLock()


def load(name: str, default: Any) -> Any:
    path = data_path(name)
    with _lock:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text("utf-8"))
        except ValueError:
            return default


def save(name: str, value: Any) -> None:
    path = data_path(name)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _lock:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), "utf-8")
        os.replace(tmp, path)


def update(name: str, default: Any, func) -> Any:
    with _lock:
        value = func(load(name, default))
        save(name, value)
        return value
