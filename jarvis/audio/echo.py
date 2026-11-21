from __future__ import annotations

import statistics
from collections import deque

import numpy as np

MIN_LEVEL = 180.0
ECHO_FACTOR = 2.5
HANGOVER_FRAMES = 6
WINDOW = 50
WARMUP_FRAMES = 4

_learned: dict[str, float] = {}


def frame_level(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16)
    return float(np.abs(samples.astype(np.int32)).mean()) if samples.size else 0.0


class EchoGate:
    def __init__(
        self,
        min_level: float = MIN_LEVEL,
        factor: float = ECHO_FACTOR,
        hangover: int = HANGOVER_FRAMES,
        profile: str = "auriculares",
    ) -> None:
        self.min_level = min_level
        self.factor = factor
        self.hangover = hangover
        self.profile = profile
        seed = _learned.get(profile, 0.0)
        self._echo: deque[float] = deque([seed] * 10 if seed else [], maxlen=WINDOW)
        self._open = 0
        self.last_level = 0.0
        self.blocked = 0

    @property
    def echo_level(self) -> float:
        return statistics.median(self._echo) if self._echo else 0.0

    @property
    def threshold(self) -> float:
        return max(self.min_level, self.echo_level * self.factor)

    def filter(self, frame: bytes, playing: bool) -> bytes:
        if not playing:
            self._open = 0
            return frame
        level = frame_level(frame)
        self.last_level = level
        threshold = self.threshold
        self._echo.append(level)
        if len(self._echo) >= 10:
            _learned[self.profile] = self.echo_level
        if len(self._echo) < WARMUP_FRAMES:
            self.blocked += 1
            return bytes(len(frame))
        if level >= threshold:
            self._open = self.hangover
            return frame
        if self._open > 0:
            self._open -= 1
            return frame
        self.blocked += 1
        return bytes(len(frame))


def reset_learning() -> None:
    _learned.clear()
