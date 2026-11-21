from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger(__name__)

OUT_RATE = 24000


def _clean(text: str) -> str:
    text = re.sub(r"[*_#`>|]", " ", text)
    text = re.sub(r"https?://\S+", "el enlace", text)
    return re.sub(r"\s+", " ", text).strip()


async def edge_tts_pcm(text: str, voice: str) -> bytes:
    import edge_tts
    import miniaudio

    mp3 = bytearray()
    communicate = edge_tts.Communicate(_clean(text), voice)
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            mp3.extend(chunk["data"])
    if not mp3:
        raise RuntimeError("edge-tts no devolvio audio")
    decoded = miniaudio.decode(bytes(mp3), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=OUT_RATE)
    return decoded.samples.tobytes()


def _sapi_speak(text: str) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    for token in voice.GetVoices():
        if "es" in token.GetAttribute("Language").lower() or "0a" in token.GetAttribute("Language").lower():
            voice.Voice = token
            break
    voice.Speak(_clean(text))


async def synthesize(text: str, voice: str) -> bytes | None:
    try:
        return await asyncio.wait_for(edge_tts_pcm(text, voice), timeout=20)
    except Exception as exc:
        log.warning("edge-tts no disponible (%s); uso la voz de Windows", exc)
        await asyncio.to_thread(_sapi_speak, text)
        return None
