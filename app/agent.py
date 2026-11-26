"""The voice agent (LiveKit Agents worker).

Flow of one call:
  browser presses Call -> web server makes a LiveKit room "call--<slug>--<id>"
  -> LiveKit dispatches this worker into the room -> we read <slug>, load that
  business from the database, build the instructions, and start the session.

Pipeline:  caller audio -> Silero VAD -> Groq Whisper (STT) -> Groq Llama (LLM, with tools)
           -> Piper (TTS, self-hosted) -> caller

Run locally:   python -m app.agent dev
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import asyncpg
from dotenv import load_dotenv
from livekit.agents import (Agent, AgentServer, AgentSession, JobContext, JobProcess,
                            RunContext, cli, function_tool, llm, metrics, stt, tts)
from livekit.plugins import groq, openai, silero

from app import booking
from app.business import Business, build_instructions, load_business
from app.call_log import CallLogger

load_dotenv(".env")
log = logging.getLogger("receptionist")

SILENCE_TIMEOUT = float(os.getenv("SILENCE_TIMEOUT", "12"))   # seconds before "are you still there?"


# ---------------------------------------------------------------------------
# Models (all open source; served by Groq free tier or self-hosted)
# ---------------------------------------------------------------------------
def build_stt(vad):
    # Two Whisper models: each has its own Groq rate limit, so the second
    # one takes over if the first is rate limited.
    return stt.FallbackAdapter([
        groq.STT(model="whisper-large-v3-turbo", language="en"),
        groq.STT(model="whisper-large-v3", language="en"),
    ], vad=vad)  # Whisper isn't streaming: the VAD cuts audio into utterances for it


def build_llm():
    # Open-weight models on Groq. Llama 3.x was retired from Groq's free tier in Aug 2026.
    # Each model has its OWN free-tier limit (30 requests / 8K tokens per minute), so the
    # FallbackAdapter moves to the next model when one is rate limited or down.
    models = os.getenv("GROQ_LLM_MODELS",
                       "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b")
    llms = []
    for m in (x.strip() for x in models.split(",") if x.strip()):
        # gpt-oss: low reasoning keeps replies fast. Qwen: turn thinking off.
        effort = "none" if m.startswith("qwen") else "low"
        # Replies are 1-2 spoken sentences; the cap keeps each request small (free-tier budget).
        llms.append(groq.LLM(model=m, temperature=0.3, reasoning_effort=effort,
                             max_completion_tokens=int(os.getenv("LLM_MAX_TOKENS", "400"))))
    return llm.FallbackAdapter(llms, attempt_timeout=5.0)


def build_tts():
    provider = os.getenv("TTS_PROVIDER", "piper")
    if provider == "kokoro":
        # Kokoro-82M via Kokoro-FastAPI. Nicer voice, but too slow on a small CPU.
        return openai.TTS(base_url=os.getenv("KOKORO_URL", "http://localhost:8880/v1"),
                          api_key="not-needed", model="kokoro",
                          voice=os.getenv("KOKORO_VOICE", "af_heart"),
                          response_format="pcm", sample_rate=24000)
    if provider == "groq":
        # Orpheus on Groq: only 10 requests/min on the free tier, so not the default.
        return groq.TTS(model="canopylabs/orpheus-v1-english",
                        voice=os.getenv("GROQ_TTS_VOICE", "autumn"))
    # Default: Piper, self-hosted (app/tts_server.py). ~0.2 s per sentence on 2 vCPUs.
    return openai.TTS(base_url=os.getenv("TTS_URL", "http://localhost:8890/v1"),
                      api_key="not-needed", model="piper", voice="default",
                      response_format="pcm",
                      sample_rate=int(os.getenv("TTS_SAMPLE_RATE", "16000")))


# ---------------------------------------------------------------------------
# The receptionist: instructions + tools. Every tool is scoped to ONE business.
# ---------------------------------------------------------------------------
class Receptionist(Agent):
    def __init__(self, biz: Business, pool, call_id=None):
        super().__init__(instructions=build_instructions(biz))
        self.biz = biz
        self.pool = pool
        self.call_id = call_id

    @function_tool()
    async def check_availability(self, context: RunContext, service: str, date: str) -> str:
        """Check free appointment times for a service on a date. Always call this before
        offering or confirming a time.

        Args:
            service: The service name, e.g. "Haircut (men)" or "facial".
            date: The date as YYYY-MM-DD, taken from the CALENDAR.
        """
        return json.dumps(await booking.check_availability(self.pool, self.biz.id, service, date))

    @function_tool()
    async def book_appointment(self, context: RunContext, service: str, date: str, time: str,
                               customer_name: str, customer_phone: str) -> str:
        """Book an appointment, only after checking availability and after the caller
        confirmed the details.

        Args:
            service: The service name.
            date: YYYY-MM-DD.
            time: HH:MM in 24-hour time, e.g. 17:00.
            customer_name: The caller's name.
            customer_phone: The caller's 10-digit phone number.
        """
        return json.dumps(await booking.book(self.pool, self.biz.id, service, date, time,
                                             customer_name, customer_phone, call_id=self.call_id))

    @function_tool()
    async def find_my_bookings(self, context: RunContext, customer_phone: str) -> str:
        """List the caller's own upcoming bookings, found by THEIR phone number.

        Args:
            customer_phone: The caller's 10-digit phone number.
        """
        return json.dumps(await booking.my_bookings(self.pool, self.biz.id, customer_phone))

    @function_tool()
    async def cancel_appointment(self, context: RunContext, customer_phone: str, date: str,
                                 time: str, confirm_not_free: bool = False) -> str:
        """Cancel the caller's booking.

        Args:
            customer_phone: Phone number the booking was made with.
            date: Booking date, YYYY-MM-DD.
            time: Booking time, HH:MM 24-hour.
            confirm_not_free: Set true ONLY after the caller agreed to a cancellation that is not free.
        """
        return json.dumps(await booking.cancel(self.pool, self.biz.id, customer_phone, date, time,
                                               confirm_not_free=confirm_not_free))

    @function_tool()
    async def reschedule_appointment(self, context: RunContext, customer_phone: str,
                                     old_date: str, old_time: str,
                                     new_date: str, new_time: str) -> str:
        """Move the caller's booking to a new time with the same staff member.
        Check availability for the new time first.

        Args:
            customer_phone: Phone number the booking was made with.
            old_date: Current booking date, YYYY-MM-DD.
            old_time: Current booking time, HH:MM 24-hour.
            new_date: New date, YYYY-MM-DD.
            new_time: New time, HH:MM 24-hour.
        """
        return json.dumps(await booking.reschedule(self.pool, self.biz.id, customer_phone,
                                                   old_date, old_time, new_date, new_time))

    @function_tool()
    async def take_message(self, context: RunContext, message: str,
                           caller_name: str = "", caller_phone: str = "") -> str:
        """Pass a question or request to the staff when you don't have the answer.

        Args:
            message: What the caller wants to know or ask.
            caller_name: The caller's name, if given.
            caller_phone: The caller's phone number, if given, so staff can call back.
        """
        await self.pool.execute(
            """INSERT INTO staff_messages (business_id, call_id, caller_name, caller_phone, message)
               VALUES ($1, $2, $3, $4, $5)""",
            self.biz.id, self.call_id, caller_name, caller_phone, message)
        return json.dumps({"ok": True, "message": "Passed to staff."})


# ---------------------------------------------------------------------------
# Worker entrypoint
# ---------------------------------------------------------------------------
server = AgentServer()


def prewarm(proc: JobProcess):
    # Load the VAD model once per process, not once per call.
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=0.35)


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    # Room name is "call--<business-slug>--<random>"
    room_name = ctx.job.room.name
    parts = room_name.split("--")
    slug = parts[1] if len(parts) >= 3 and parts[0] == "call" else None
    if not slug:
        log.warning("ignoring room %s", room_name)
        return

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3,
                                     statement_cache_size=0)
    biz = await load_business(pool, slug)
    if biz is None:
        log.warning("unknown business %s", slug)
        await pool.close()
        return

    calllog = CallLogger(pool, biz.id, room_name)
    call_id = await calllog.start()
    ctx.log_context_fields = {"business": slug, "call_id": str(call_id)}

    vad = ctx.proc.userdata["vad"]
    session = AgentSession(
        vad=vad,
        stt=build_stt(vad),
        llm=build_llm(),
        tts=build_tts(),
        user_away_timeout=SILENCE_TIMEOUT,
        turn_handling={
            "turn_detection": "vad",
            "endpointing": {"mode": "fixed", "min_delay": 0.2, "max_delay": 3.0},
            # Stop talking as soon as the caller has spoken for 0.3 s.
            "interruption": {"enabled": True, "mode": "vad", "min_duration": 0.3,
                             "resume_false_interruption": True,
                             "false_interruption_timeout": 1.5},
            # Start the LLM on the transcript while we're still confirming end of turn.
            "preemptive_generation": {"enabled": os.getenv("PREEMPTIVE", "1") == "1"},
        },
    )

    _wire_logging(session, calllog)
    _wire_error_handling(session, calllog)
    _wire_silence_handling(session, ctx)

    async def _on_shutdown():
        await calllog.close()
        await pool.close()
    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(agent=Receptionist(biz, pool, call_id), room=ctx.room)
    await ctx.connect()
    session.say(biz.greeting)


def _wire_logging(session: AgentSession, calllog: CallLogger):
    """Transcript, tool calls and per-turn latency -> call_events."""
    # detected_at = when the system DETECTED the caller stopped (the assignment's start point)
    # speech_end_at = when the speech itself ended (earlier, by the VAD silence window)
    turn = {"detected_at": None, "speech_end_at": None, "stages": {}}

    @session.on("conversation_item_added")
    def _item(ev):
        item = ev.item
        if getattr(item, "type", None) == "message" and item.role in ("user", "assistant"):
            calllog.log("user" if item.role == "user" else "agent",
                        {"text": item.text_content, "interrupted": item.interrupted})

    @session.on("function_tools_executed")
    def _tools(ev):
        for call, out in zip(ev.function_calls, ev.function_call_outputs):
            calllog.log("tool_call", {"name": call.name, "arguments": call.arguments})
            calllog.log("tool_result", {"name": call.name,
                                        "output": out.output if out else None,
                                        "is_error": out.is_error if out else None})
            turn["stages"]["tool_calls"] = turn["stages"].get("tool_calls", 0) + 1

    @session.on("metrics_collected")
    def _metrics(ev):
        m = ev.metrics
        st = turn["stages"]
        if isinstance(m, metrics.EOUMetrics):
            st.setdefault("stt_ms", round(m.transcription_delay * 1000))
            st.setdefault("end_of_turn_ms", round(m.end_of_utterance_delay * 1000))
        elif isinstance(m, metrics.LLMMetrics):
            st.setdefault("llm_ttft_ms", round(m.ttft * 1000))
        elif isinstance(m, metrics.TTSMetrics) and m.ttfb >= 0:   # -1 = not reported
            st.setdefault("tts_ttfb_ms", round(m.ttfb * 1000))

    @session.on("user_state_changed")
    def _user(ev):
        if ev.old_state == "speaking" and ev.new_state == "listening":
            turn["detected_at"] = ev.created_at
            turn["speech_end_at"] = ev.speech_timestamp or ev.created_at
            turn["stages"] = {}

    @session.on("agent_state_changed")
    def _agent(ev):
        if ev.new_state == "speaking" and turn["detected_at"]:
            # First audio of the reply is going out to the caller.
            calllog.log("latency", {
                "total_ms": round((ev.created_at - turn["detected_at"]) * 1000),
                "from_speech_end_ms": round((ev.created_at - turn["speech_end_at"]) * 1000),
                **turn["stages"]})
            turn["detected_at"] = None


def _wire_error_handling(session: AgentSession, calllog: CallLogger):
    """If every model fails (e.g. all rate limited), say so instead of going silent."""
    @session.on("error")
    def _err(ev):
        calllog.log("error", {"source": type(ev.source).__name__, "error": str(ev.error)[:500]})
        if isinstance(ev.source, llm.LLM):
            session.say("Sorry, I'm having a little trouble right now. "
                        "Could you say that again in a moment?")


def _wire_silence_handling(session: AgentSession, ctx: JobContext):
    """Silent for SILENCE_TIMEOUT -> 'are you still there?'.
    Still silent after another SILENCE_TIMEOUT -> polite goodbye and hang up.
    Any speech from the caller cancels this."""
    state: dict = {"task": None, "hanging_up": False}

    async def _check_in():
        await session.say("Are you still there? Take your time, I'm here whenever you're ready.")
        await asyncio.sleep(SILENCE_TIMEOUT)
        state["hanging_up"] = True          # from here on, finish the goodbye and hang up
        await session.say("I haven't heard anything for a while, so I'll end the call now. "
                          "Please call back anytime. Goodbye!", allow_interruptions=False)
        await ctx.delete_room()
        ctx.shutdown(reason="caller silent")

    @session.on("user_state_changed")
    def _away(ev):
        if ev.new_state == "away" and state["task"] is None:
            state["task"] = asyncio.create_task(_check_in())
        elif ev.new_state == "speaking" and state["task"] is not None and not state["hanging_up"]:
            state["task"].cancel()
            state["task"] = None


if __name__ == "__main__":
    cli.run_app(server)
