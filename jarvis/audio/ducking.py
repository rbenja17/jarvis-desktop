from __future__ import annotations

import logging
import os
import threading

from jarvis import store

log = logging.getLogger(__name__)

STATE_FILE = "musica_bajada.json"
ACTIVE = 1


def _sessions():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetAllSessions()


class Ducker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._saved: dict[str, dict] = {}

    @property
    def active(self) -> bool:
        return bool(self._saved)

    def duck(self, level: float = 0.25) -> list[str]:
        own = os.getpid()
        lowered = []
        with self._lock:
            for session in _sessions():
                try:
                    pid = session.ProcessId
                    if session.Process is None or pid == own or str(pid) in self._saved or session.State != ACTIVE:
                        continue
                    volume = session.SimpleAudioVolume
                    original = float(volume.GetMasterVolume())
                    if original < 0.05:
                        continue
                    target = round(original * level, 3)
                    volume.SetMasterVolume(target, None)
                    name = session.Process.name()
                    self._saved[str(pid)] = {"app": name, "original": original, "bajado": target}
                    lowered.append(name)
                except Exception:
                    log.debug("No se pudo bajar una app", exc_info=True)
            if lowered:
                store.save(STATE_FILE, self._saved)
        return lowered

    def restore(self) -> list[str]:
        with self._lock:
            saved, self._saved = self._saved, {}
            if not saved:
                return []
            restored = _restore(saved)
            store.save(STATE_FILE, {})
        return restored


def _restore(saved: dict[str, dict]) -> list[str]:
    restored = []
    for session in _sessions():
        try:
            item = saved.get(str(session.ProcessId))
            if item is None or session.Process is None or session.Process.name() != item["app"]:
                continue
            volume = session.SimpleAudioVolume
            if abs(float(volume.GetMasterVolume()) - item["bajado"]) < 0.02:
                volume.SetMasterVolume(item["original"], None)
                restored.append(item["app"])
        except Exception:
            log.debug("No se pudo restaurar una app", exc_info=True)
    return restored


def restore_leftovers() -> list[str]:
    saved = store.load(STATE_FILE, {})
    if not saved:
        return []
    restored = _restore(saved)
    store.save(STATE_FILE, {})
    if restored:
        log.info("[OK] Volumen restaurado tras un cierre inesperado: %s", ", ".join(restored))
    return restored
