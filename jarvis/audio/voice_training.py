from __future__ import annotations

import logging
import pickle
import wave
from pathlib import Path

import numpy as np

from jarvis.config import data_path

log = logging.getLogger(__name__)

RATE = 16000
STEP = 1280
MODEL_NAME = "hey_jarvis_v0.1"
BEST_PER_CLIP = 3
SHIFTS = (0, 320, 640, 960)


def voice_dir() -> Path:
    return data_path("voz", "x").parent


def positives_dir() -> Path:
    return data_path("voz", "positivos", "x").parent


def negatives_dir() -> Path:
    return data_path("voz", "negativos", "x").parent


def verifier_path() -> Path:
    return data_path("voz", "verificador_hey_jarvis.pkl")


def save_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm)


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)


def clear_recordings() -> None:
    for folder in (positives_dir(), negatives_dir()):
        for wav_file in folder.glob("*.wav"):
            wav_file.unlink()


def _frames(oww, audio: np.ndarray) -> list[tuple[float, np.ndarray]]:
    oww.reset()
    padded = np.concatenate([np.zeros(RATE, dtype=np.int16), audio, np.zeros(RATE // 2, dtype=np.int16)])
    out = []
    for i in range(0, len(padded) - STEP, STEP):
        score = float(oww.predict(padded[i : i + STEP])[MODEL_NAME])
        if i >= RATE:
            out.append((score, oww.preprocessor.get_features(oww.model_inputs[MODEL_NAME])))
    return out


def train(model_path: str) -> dict:
    from openwakeword.custom_verifier_model import train_verifier_model
    from openwakeword.model import Model

    positives = sorted(positives_dir().glob("*.wav"))
    negatives = sorted(negatives_dir().glob("*.wav"))
    if len(positives) < 3 or not negatives:
        raise ValueError("Hacen falta al menos 3 grabaciones de «hey jarvis» y una de voz normal.")

    oww = Model(wakeword_models=[model_path], inference_framework="onnx")
    pos_features, base_scores = [], []
    for clip in positives:
        audio = load_wav(clip)
        for shift in SHIFTS:
            frames = sorted(_frames(oww, audio[shift:]), key=lambda f: f[0], reverse=True)[:BEST_PER_CLIP]
            pos_features += [features for _, features in frames]
            base_scores.append(frames[0][0] if frames else 0.0)

    neg_features = []
    for clip in negatives:
        neg_features += [features for _, features in _frames(oww, load_wav(clip))]

    x = np.vstack(pos_features + neg_features)
    y = np.array([1] * len(pos_features) + [0] * len(neg_features))
    pipeline = train_verifier_model(x, y)
    with open(verifier_path(), "wb") as fh:
        pickle.dump(pipeline, fh)

    pos_prob = pipeline.predict_proba(np.vstack(pos_features))[:, 1]
    neg_prob = pipeline.predict_proba(np.vstack(neg_features))[:, 1]
    result = {
        "grabaciones_hey_jarvis": len(positives),
        "grabaciones_otras": len(negatives),
        "puntaje_base_promedio": round(float(np.mean(base_scores)), 2),
        "verificador_en_su_voz": round(float(np.median(pos_prob)), 2),
        "verificador_en_otras_frases": round(float(np.percentile(neg_prob, 99)), 2),
    }
    log.info("[WAKE] Verificador entrenado: %s", result)
    return result
