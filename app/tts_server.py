"""Self-hosted text to speech: Piper (open source, fast on CPU).

Exposes an OpenAI-compatible endpoint, so the agent talks to it with the
standard OpenAI TTS client:

    POST /v1/audio/speech  {"input": "...", "response_format": "pcm"}
    -> raw 16-bit mono PCM at the voice's sample rate, streamed sentence by sentence

Why Piper and not Kokoro: on a 2-vCPU free server Kokoro took 1-2 s per sentence
(slower than real time); Piper takes ~0.2 s. That's the difference between meeting
the 1.2 s reply target or not.

Run:  uvicorn app.tts_server:app --port 8890
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from piper import PiperVoice
from pydantic import BaseModel

MODEL = os.getenv("PIPER_MODEL", str(Path(__file__).resolve().parent.parent
                                     / "models" / "en-us-lessac-medium.onnx"))
voice = PiperVoice.load(MODEL)
app = FastAPI()


class SpeechRequest(BaseModel):
    input: str
    model: str | None = None
    voice: str | None = None
    response_format: str | None = "pcm"
    speed: float | None = 1.0


@app.get("/health")
def health():
    return {"ok": True, "sample_rate": voice.config.sample_rate}


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def synth():
        # Runs in a thread so several calls can synthesize at the same time.
        try:
            for chunk in voice.synthesize(req.input):
                loop.call_soon_threadsafe(queue.put_nowait, chunk.audio_int16_bytes)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, synth)

    async def stream():
        while (data := await queue.get()) is not None:
            yield data

    return StreamingResponse(stream(), media_type="audio/pcm")
