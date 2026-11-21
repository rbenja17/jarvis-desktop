from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

MODS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


def parse_combo(combo: str) -> tuple[int, int]:
    from jarvis.tools._keys import _vk_for

    mods = 0
    key = None
    for part in combo.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        else:
            key = _vk_for(part)
    if key is None:
        raise ValueError(f"Atajo sin tecla principal: {combo}")
    return mods, key


class GlobalHotkey(threading.Thread):
    def __init__(self, combo: str, callback: Callable[[], None]) -> None:
        super().__init__(name="jarvis-hotkey", daemon=True)
        self.combo = combo
        self.callback = callback
        self._thread_id = 0
        self.registered = threading.Event()

    def run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        mods, vk = parse_combo(self.combo)
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            log.error("[ERROR] El atajo %s ya lo usa otro programa", self.combo)
            return
        self.registered.set()
        log.info("[ACTIVE] Atajo %s", self.combo)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.callback()
                except Exception:
                    log.exception("Error en el atajo")
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
