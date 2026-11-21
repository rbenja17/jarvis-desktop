from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    state = Signal(str)
    level = Signal(str, float)
    subtitle = Signal(str, str)
    notify = Signal(str, str)
    open_training = Signal()
    open_settings = Signal()
