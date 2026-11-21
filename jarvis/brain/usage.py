from __future__ import annotations

import datetime as dt

from jarvis import store

FILE = "uso.json"
KEEP_DAYS = 14


def _today() -> str:
    return dt.date.today().isoformat()


def _bump(field: str, amount: float) -> None:
    def apply(data: dict) -> dict:
        day = data.setdefault(_today(), {})
        day[field] = round(day.get(field, 0) + amount, 1)
        for old in sorted(data)[:-KEEP_DAYS]:
            data.pop(old, None)
        return data

    store.update(FILE, {}, apply)


def record_request(model: str) -> None:
    _bump(model, 1)


def record_live_seconds(seconds: float) -> None:
    _bump("voz_en_vivo_segundos", seconds)


def record_quota_hit(model: str) -> None:
    _bump(f"cupo_agotado:{model}", 1)


def today() -> dict:
    return dict(store.load(FILE, {}).get(_today(), {}))
