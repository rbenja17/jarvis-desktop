from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.genai import errors, types

from jarvis.brain import live as live_mod
from jarvis.brain.text import QuotaExceeded, TextBrain
from jarvis.tools import registry

calls: list[dict] = []


@registry.tool("prueba de volumen", name="_prueba_volumen")
def prueba_volumen(nivel: int) -> dict:
    calls.append({"nivel": nivel})
    return {"volumen": nivel}


def _response(*, text: str | None = None, call: tuple[str, dict] | None = None):
    parts = []
    if call:
        parts.append(types.Part(function_call=types.FunctionCall(id="c1", name=call[0], args=call[1])))
    if text:
        parts.append(types.Part.from_text(text=text))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.seen_models: list[str] = []

    async def generate_content(self, model, contents, config):
        self.seen_models.append(model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(script):
    models = FakeModels(script)
    return SimpleNamespace(aio=SimpleNamespace(models=models)), models


def test_text_brain_runs_function_calls_then_answers():
    calls.clear()
    client, models = _client(
        [_response(call=("_prueba_volumen", {"nivel": 30})), _response(text="Hecho, señor. Volumen al 30.")]
    )
    brain = TextBrain(client)
    answer = asyncio.run(brain.ask("pon el volumen al 30", channel="texto"))
    assert answer == "Hecho, señor. Volumen al 30."
    assert calls == [{"nivel": 30}]
    assert models.seen_models == ["modelo-a", "modelo-a"]


def test_text_brain_falls_back_to_next_model_on_quota():
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})
    client, models = _client([quota, _response(text="Listo.")])
    brain = TextBrain(client)
    assert asyncio.run(brain.ask("hola")) == "Listo."
    assert models.seen_models == ["modelo-a", "modelo-b"]


def test_text_brain_raises_when_all_models_exhausted():
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})
    client, _ = _client([quota, quota])
    brain = TextBrain(client)
    try:
        asyncio.run(brain.ask("hola"))
    except QuotaExceeded:
        pass
    else:
        raise AssertionError("debia agotar el cupo")


class FakeAudio:
    def __init__(self):
        self.played: list[bytes] = []
        self.cleared = 0

    def play(self, pcm, rate=24000, prebuffer=0.0):
        self.played.append(pcm)

    def clear(self):
        self.cleared += 1

    def is_playing(self, tail=0.0):
        return False


class FakeSession:
    def __init__(self):
        self.tool_responses = []

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)


def _live(audio):
    client = SimpleNamespace()
    return live_mod.LiveConversation(client, audio, asyncio.Queue(), barge_in=True)


def test_live_handles_tool_calls_audio_and_interruptions():
    calls.clear()
    audio = FakeAudio()
    conv = _live(audio)
    session = FakeSession()

    async def scenario():
        await conv.handle_message(session, types.LiveServerMessage(
            server_content=types.LiveServerContent(input_transcription=types.Transcription(text="sube el volumen al 40"))
        ))
        await conv.handle_message(session, types.LiveServerMessage(
            tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id="t1", name="_prueba_volumen", args={"nivel": 40})])
        ))
        await conv.handle_message(session, types.LiveServerMessage(
            server_content=types.LiveServerContent(
                model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=b"\x01\x00" * 10, mime_type="audio/pcm;rate=24000"))]),
                output_transcription=types.Transcription(text="Hecho, señor."),
            )
        ))
        await conv.handle_message(session, types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True)))
        await conv.handle_message(session, types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True)))

    asyncio.run(scenario())
    assert calls == [{"nivel": 40}]
    response = session.tool_responses[0][0]
    assert response.id == "t1" and response.response == {"volumen": 40}
    assert audio.played and audio.cleared == 1
    assert conv.transcript == ["Usuario: sube el volumen al 40", "Jarvis: Hecho, señor."]


def test_live_end_request_waits_for_turn_complete():
    conv = _live(FakeAudio())
    conv.request_end()
    assert conv._end_requested_at is not None and not conv._turn_done_after_end
    asyncio.run(conv.handle_message(FakeSession(), types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))))
    assert conv._turn_done_after_end


def test_text_brain_falls_back_and_cools_down_on_timeout_or_503():
    overload = errors.ServerError(503, {"error": {"code": 503, "message": "Overloaded", "status": "UNAVAILABLE"}})
    client, models = _client([asyncio.TimeoutError(), _response(text="Primero."), _response(text="Segundo."), overload])
    brain = TextBrain(client)
    assert asyncio.run(brain.ask("hola")) == "Primero."
    assert models.seen_models == ["modelo-a", "modelo-b"]
    assert asyncio.run(brain.ask("sigue")) == "Segundo."
    assert models.seen_models == ["modelo-a", "modelo-b", "modelo-b"]


def test_live_long_user_speech_keeps_activity_and_suppresses_mic_during_tools():
    import numpy as np

    audio = FakeAudio()
    conv = _live(audio)

    class RecordingSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.sent_audio: list[bytes] = []
            self.continuations: list[types.Content] = []

        async def send_realtime_input(self, *, audio=None, audio_stream_end=None):
            if audio is not None:
                self.sent_audio.append(audio.data)

        async def send_client_content(self, *, turns, turn_complete=True):
            self.continuations.append(turns)

    session = RecordingSession()
    voice_frame = (np.ones(1280, dtype=np.int16) * 400).tobytes()

    async def scenario():
        conv.last_activity = 0.0
        task = asyncio.create_task(conv._send_audio(session))
        conv._tools_running = 1
        await conv.mic_queue.put(voice_frame)
        await asyncio.sleep(0.02)
        assert session.sent_audio == []
        conv._tools_running = 0
        for _ in range(4):
            await conv.mic_queue.put(voice_frame)
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert conv.last_activity > 0.0
        assert conv._pending_user_turn is True
        assert len(session.sent_audio) == 4

        await conv.handle_message(
            session,
            types.LiveServerMessage(
                tool_call=types.LiveServerToolCall(
                    function_calls=[types.FunctionCall(id="t2", name="_prueba_volumen", args={"nivel": 25})]
                )
            ),
        )
        assert conv._awaiting_tool_followup is True
        await conv.handle_message(
            session,
            types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True)),
        )
        assert conv._awaiting_tool_followup is False
        assert session.continuations == []

    asyncio.run(scenario())
