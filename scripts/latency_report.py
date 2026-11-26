"""Latency report from real calls (call_events.kind = 'latency').

Measured per reply: from the moment the system DETECTED the caller stopped speaking
(VAD end of turn) to the moment the first audio of the reply went out (agent state ->
speaking). Also reported from the end of the speech itself, which adds the VAD silence window.
Stage times come from LiveKit's metrics for the same turn.

  python scripts/latency_report.py            -> all calls
  python scripts/latency_report.py --last 3   -> only the 3 most recent calls
"""
import asyncio
import json
import os
import statistics
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv(".env")


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo))


async def main():
    last = int(sys.argv[sys.argv.index("--last") + 1]) if "--last" in sys.argv else None
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        calls = "SELECT id FROM calls ORDER BY started_at DESC" + (f" LIMIT {last}" if last else "")
        rows = await conn.fetch(f"SELECT payload FROM call_events WHERE kind = 'latency' "
                                f"AND call_id IN ({calls})")
    finally:
        await conn.close()
    turns = [json.loads(r["payload"]) for r in rows]
    if not turns:
        print("No latency data yet. Make some calls first.")
        return

    print(f"Turns measured: {len(turns)}\n")
    print(f"{'stage':<38}{'median ms':>10}{'p95 ms':>10}{'n':>6}")
    for key, label in [("total_ms", "TOTAL (detected stop -> first audio)"),
                       ("from_speech_end_ms", "  same, from speech end"),
                       ("end_of_turn_ms", "end-of-turn (incl. STT)"),
                       ("stt_ms", "speech to text"),
                       ("llm_ttft_ms", "LLM first token"),
                       ("tts_ttfb_ms", "TTS first audio")]:
        vals = [t[key] for t in turns if t.get(key) is not None and t[key] >= 0]
        if vals:
            print(f"{label:<38}{statistics.median(vals):>10.0f}{pct(vals, 95):>10}{len(vals):>6}")
    tool_turns = [t["total_ms"] for t in turns if t.get("tool_calls")]
    plain = [t["total_ms"] for t in turns if not t.get("tool_calls")]
    if tool_turns:
        print(f"\nTurns without tool calls: median {statistics.median(plain):.0f} ms (n={len(plain)})"
              if plain else "")
        print(f"Turns with tool calls:    median {statistics.median(tool_turns):.0f} ms "
              f"(n={len(tool_turns)})")


asyncio.run(main())
