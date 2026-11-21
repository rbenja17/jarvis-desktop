from __future__ import annotations

import ctypes
import time

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "intro": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "espacio": 0x20,
    "space": 0x20, "backspace": 0x08, "borrar": 0x08, "supr": 0x2E, "delete": 0x2E,
    "inicio": 0x24, "home": 0x24, "fin": 0x23, "end": 0x23, "repag": 0x21, "pageup": 0x21,
    "avpag": 0x22, "pagedown": 0x22, "arriba": 0x26, "up": 0x26, "abajo": 0x28, "down": 0x28,
    "izquierda": 0x25, "left": 0x25, "derecha": 0x27, "right": 0x27, "impr": 0x2C,
    "printscreen": 0x2C, "play_pausa": 0xB3, "siguiente": 0xB0, "anterior": 0xB1, "detener": 0xB2,
    "volumen_mas": 0xAF, "volumen_menos": 0xAE, "mute": 0xAD,
}
for _i in range(1, 13):
    VK[f"f{_i}"] = 0x70 + _i - 1

_EXTENDED = {0x5B, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0xB3, 0xB0, 0xB1, 0xB2, 0xAF, 0xAE, 0xAD}


def _vk_for(key: str) -> int:
    key = key.strip().lower()
    if key in VK:
        return VK[key]
    if len(key) == 1:
        res = ctypes.windll.user32.VkKeyScanW(ord(key))
        if res != -1:
            return res & 0xFF
    raise ValueError(f"Tecla desconocida: {key}")


def _event(vk: int, up: bool) -> None:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    ctypes.windll.user32.keybd_event(vk, 0, flags, 0)


def tap(vk: int) -> None:
    _event(vk, False)
    _event(vk, True)


def press_combo(combo: str) -> None:
    keys = [_vk_for(k) for k in combo.replace(" ", "").split("+") if k]
    if not keys:
        raise ValueError("Combinacion vacia")
    for vk in keys:
        _event(vk, False)
        time.sleep(0.02)
    for vk in reversed(keys):
        _event(vk, True)
        time.sleep(0.02)
