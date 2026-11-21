from __future__ import annotations

import logging
import os
import pickle
import time
from collections import deque
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

COOLDOWN = 2.0
NEAR_MISS = 0.4
REPORT_EVERY = 300.0
SPEECH_LEVEL = 150


def _model_path(name: str) -> str:
    if name.endswith(".onnx") and Path(name).exists():
        return name
    import openwakeword
    import openwakeword.utils

    from jarvis.config import IS_FROZEN, data_path

    folder = Path(openwakeword.__file__).parent / "resources" / "models"
    version = "v0.1"
    bundled = folder / f"{name}_{version}.onnx"
    if bundled.exists() and (folder / "melspectrogram.onnx").exists() and (folder / "silero_vad.onnx").exists():
        return str(bundled)

    if IS_FROZEN:
        user_models = data_path("models", "x").parent
        user_path = user_models / f"{name}_{version}.onnx"
        if not user_path.exists():
            log.info("Descargando el modelo de palabra de activacion %s en %s...", name, user_models)
            openwakeword.utils.download_models([f"{name}_{version}"], target_directory=str(user_models))
        return str(user_path)

    log.info("Descargando el modelo de palabra de activacion %s...", name)
    openwakeword.utils.download_models([f"{name}_{version}"])
    return str(bundled)


def model_path(name: str = "hey_jarvis") -> str:
    return _model_path(name)


class WakeWord:
    def __init__(
        self,
        name: str = "hey_jarvis",
        threshold: float = 0.38,
        vad_threshold: float = 0.5,
        verifier: str | None = None,
        verifier_threshold: float = 0.60,
        confirm_frames: int = 2,
    ) -> None:
        from openwakeword.model import Model

        os.environ.setdefault("ORT_DISABLE_ALL_LOGS", "1")
        path = _model_path(name)
        self.label = Path(path).stem
        self.vad_threshold = float(vad_threshold)
        self.model = Model(wakeword_models=[path], inference_framework="onnx", vad_threshold=self.vad_threshold)
        self.threshold = float(threshold)
        self.confirm_frames = max(1, int(confirm_frames))
        self.verifier = None
        self.verifier_threshold = float(verifier_threshold)
        if verifier and Path(verifier).exists():
            with open(verifier, "rb") as fh:
                self.verifier = pickle.load(fh)
        self._levels: deque[float] = deque(maxlen=12)
        self.last_verifier = 0.0
        self._hits = 0
        self._reset_pending = False
        self._cooldown_until = 0.0
        self.last_score = 0.0
        self._near_miss_at = 0.0
        self._peak = 0.0
        self._peak_at = 0.0
        self._peak_detail = (0.0, 0.0)
        self._frames = 0
        self._level_sum = 0.0
        self._report_at = time.monotonic() + REPORT_EVERY
        log.info(
            "[WAKE] Modelo %s, umbral %.2f, confirmacion %d bloques%s",
            self.label,
            self.threshold,
            self.confirm_frames,
            f" + su voz entrenada (umbral {self.verifier_threshold:.2f})" if self.verifier is not None else "",
        )

    @property
    def personal(self) -> bool:
        return self.verifier is not None

    def _has_speech(self) -> bool:
        if self.vad_threshold <= 0:
            return True
        vad = getattr(self.model, "vad", None)
        buf = getattr(vad, "prediction_buffer", None) if vad is not None else None
        if not buf:
            return False
        recent = list(buf)[-8:]
        return bool(recent) and float(max(recent)) >= self.vad_threshold

    def _verify(self) -> float:
        if self.verifier is None or not self._levels or max(self._levels) < SPEECH_LEVEL:
            return 0.0
        if not self._has_speech():
            return 0.0
        features = self.model.preprocessor.get_features(self.model.model_inputs[self.label])
        return float(self.verifier.predict_proba(features)[0][-1])

    def process(self, frame: bytes) -> bool:
        if self._reset_pending:
            self._reset_pending = False
            self.model.reset()
            self._levels.clear()
            self._hits = 0
        audio = np.frombuffer(frame, dtype=np.int16)
        base = float(max(self.model.predict(audio).values() or [0.0]))
        level = float(np.abs(audio.astype(np.int32)).mean()) if audio.size else 0.0
        self._levels.append(level)
        mine = self._verify()
        self.last_verifier = mine
        ratio = max(base / self.threshold, mine / self.verifier_threshold if self.verifier is not None else 0.0)
        score = max(base, mine)
        self.last_score = score
        now = time.monotonic()
        self._frames += 1
        self._level_sum += level

        if ratio >= 1.0 and now >= self._cooldown_until:
            self._hits += 1
            if self._hits >= self.confirm_frames:
                self._cooldown_until = now + COOLDOWN
                self._hits = 0
                self.model.reset()
                self._levels.clear()
                log.info("[WAKE] hey jarvis detectado (base %.2f, su voz %.2f)", base, mine)
                return True
        else:
            self._hits = 0

        if ratio >= NEAR_MISS:
            if ratio > self._peak:
                self._peak, self._peak_at, self._peak_detail = ratio, now, (base, mine)
        elif self._peak and now - self._peak_at > 0.6:
            if now - self._near_miss_at > 1.0:
                log.info("[WAKE] casi: base %.2f, su voz %.2f (%.0f %% del umbral)", *self._peak_detail, self._peak * 100)
                self._near_miss_at = now
            self._peak = 0.0

        if now >= self._report_at:
            avg = self._level_sum / max(1, self._frames)
            log.info("[WAKE] escuchando: %d bloques en %.0f s, nivel medio del microfono %.0f", self._frames, REPORT_EVERY, avg)
            self._frames, self._level_sum, self._report_at = 0, 0.0, now + REPORT_EVERY
        return False

    def reset(self) -> None:
        self._reset_pending = True
        self._cooldown_until = time.monotonic() + 0.5
