---
title: Sunrise Salon Voice Receptionist
emoji: 📞
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# Voice Receptionist: open source, multi-business

A browser voice receptionist for any appointment-based business. The caller opens the
call page, presses **Call** and talks. The agent answers questions from the business's
own data and **books, reschedules and cancels** appointments against a real database.

**Live:** https://awaken-enforced-cradle.ngrok-free.dev/call/sunrise-salon
**Second business (data only):** https://awaken-enforced-cradle.ngrok-free.dev/call/speedy-wheels

> **Hosting note (honest):** the live demo runs on my own laptop with `docker compose up`,
> exposed through an ngrok tunnel with a permanent free domain. I don't have a credit card,
> which the free cloud VMs require (e.g. Oracle Cloud), and Hugging Face Docker Spaces are no
> longer free.
> Nothing is laptop-specific: the same `docker compose up` runs on any VM.

---

## Contents
1. [Quick start](#quick-start)
2. [Architecture](#architecture)
3. [Open source stack and where it runs](#open-source-stack-and-where-it-runs)
4. [How the key requirements are met](#how-the-key-requirements-are-met)
5. [Latency](#latency)
6. [Tests](#tests)
7. [Free-tier rate limits](#free-tier-rate-limits)
8. [Scaling to 1,000 businesses](#scaling-to-1000-businesses)
9. [Working with the customer](#working-with-the-customer)
10. [What's missing / known issues](#whats-missing--known-issues)
11. [AI tools used](#ai-tools-used)

---

## Quick start

```bash
cp .env.example .env            # fill in DATABASE_URL, LIVEKIT_*, GROQ_API_KEY
# run db/*.sql in order on your Postgres (Supabase SQL editor is fine)
docker compose up -d --build    # web (:8000), agent worker, Piper TTS
open http://localhost:8000      # -> /call/sunrise-salon
```

Free accounts needed: **Supabase** (Postgres), **LiveKit Cloud** (audio transport), **Groq**
(Whisper + LLM). No card required for any of them.

Tests:
```bash
docker compose --profile test up -d testdb      # throwaway Postgres on :5433
pip install -r requirements.txt
pytest -v                                       # 16 deterministic + 16 LLM conversation tests
```

---

## Architecture

```
 Browser (call page)                 LiveKit Cloud (WebRTC)               Agent worker (LiveKit Agents)
 ───────────────────                 ──────────────────────               ──────────────────────────────
 press Call ──► POST /api/token ──► web server makes room
                                     "call--<business-slug>--<id>"
 mic audio  ═══════════════════════► room ════════════════════════════►  Silero VAD (local)
                                                                          │  end of speech / barge-in
                                                                          ▼
                                                                         Whisper large-v3-turbo (Groq)
                                                                          ▼
                                                                         gpt-oss-120b (Groq) + tools ──► Postgres
                                                                          ▼                              (Supabase)
 speaker    ◄═══════════════════════ room ◄════════════════════════════  Piper TTS (self-hosted, :8890)
                                                                          │
                                                                          └─► call_events (transcript, tool calls, latency)
```

| Piece | File | Job |
|---|---|---|
| Web server | `web/server.py` | Serves `/call/<slug>`, creates a private LiveKit room + token per call |
| Call page | `web/static/call.html` | Call / hang-up, live captions, agent state |
| Agent | `app/agent.py` | Pipeline, tools, interruption + silence handling, logging |
| Business → prompt | `app/business.py` | Loads ONE business from the DB and builds the instructions |
| Booking logic | `app/booking.py` | Availability, book, reschedule, cancel, all scoped by `business_id` |
| TTS server | `app/tts_server.py` | Piper behind an OpenAI-compatible `/v1/audio/speech` |
| Call logs | `app/call_log.py`, `scripts/show_call.py` | Transcript + tool calls per call |
| Schema + data | `db/*.sql` | Schema, Sunrise Salon, staff messages, second business |

---

## Open source stack and where it runs

| Layer | What | Licence | Where it runs |
|---|---|---|---|
| Voice agent framework | **LiveKit Agents** 1.9 (Python) | Apache-2.0 | My machine (Docker) |
| Audio transport | LiveKit (WebRTC) | Apache-2.0 server | LiveKit Cloud free tier |
| Voice activity detection | **Silero VAD** | MIT | Locally, in the agent |
| Speech to text | **Whisper large-v3-turbo**, fallback large-v3 | MIT (open weights) | Groq free tier |
| LLM | **gpt-oss-120b** → gpt-oss-20b → Qwen3.8-27B (automatic fallback) | Apache-2.0 (open weights) | Groq free tier |
| Text to speech | **Piper** (`en-us-lessac-medium`) | GPL-3.0 code | Self-hosted container |
| Database | **PostgreSQL** | PostgreSQL licence | Supabase free tier (Mumbai) |

No closed voice platforms, no closed speech or LLM APIs, nothing paid.

**Why Piper and not Kokoro:** I measured both on a 2-vCPU machine. Kokoro (ONNX) took
1–2 s per sentence, slower than real time, which would blow the 1.2 s target on any free VM.
Piper took ~0.2 s. Kokoro is still supported (`TTS_PROVIDER=kokoro`) for stronger hardware.

---

## How the key requirements are met

### The call
- **Interruption:** Silero VAD runs on the caller's audio while the agent speaks. After
  **0.3 s** of caller speech the agent stops talking (`interruption.min_duration = 0.3`,
  VAD mode). False interruptions (a cough) resume after 1.5 s.
- **Silence:** after **12 s** of silence the agent asks "Are you still there?". After another
  12 s it says goodbye and ends the call (`_wire_silence_handling`). Configurable via
  `SILENCE_TIMEOUT`.
- **Fast replies:** VAD end-of-speech after 0.35 s of silence, 0.2 s endpointing delay,
  **preemptive generation** (the LLM starts on the transcript before the turn is confirmed),
  Groq's fast inference, and Piper's ~50–200 ms first audio.
- **Many calls at once:** each call is its own LiveKit room and its own agent **process**
  (LiveKit's job model). The web server is stateless. The practical ceiling today is the
  Groq free-tier rate limit (see below), not the code.

### Bookings: never double-booked
The database enforces it, not the application:

```sql
CREATE UNIQUE INDEX bookings_no_double_booking
    ON bookings (staff_id, slot_start) WHERE status = 'booked';
```

`book()` doesn't "check then insert", because two calls could both pass the check. It inserts, and
if Postgres raises `unique_violation`, it tells the caller the slot was just taken and
offers the remaining free times. The index is **partial**, so cancelled slots can be rebooked.
Rescheduling is a single `UPDATE`: if the new slot is taken, the original booking is
untouched. `tests/test_booking.py::test_no_double_booking_under_concurrency` fires **20
simultaneous** bookings at one slot; exactly one succeeds.

- **Real availability first:** the prompt requires `check_availability` before offering or
  confirming a time. `book()` validates the slot against working hours, the slot grid,
  closures and "not in the past" anyway.
- **Dates in the business's timezone:** "tomorrow" or "next Tuesday" are not left to the
  LLM's arithmetic. `calendar_hint()` puts the next 14 days (with weekdays, in the business's
  timezone) into the prompt, and tools take `YYYY-MM-DD`. Times are stored as `timestamptz`.

### Answers and safety
- The prompt allows answers **only** from the BUSINESS INFO block and tool results. Unknown
  things (memberships) → "I don't have that information" + `take_message` to staff
  (`staff_messages` table). "From" prices are never turned into exact quotes.
- **Privacy is enforced in the tools, not just the prompt:** no tool ever returns another
  customer's name or number. `check_availability` only returns free times, and bookings can
  only be found, cancelled or moved with the caller's own phone number. Even a successful
  prompt injection has no customer data to leak.
- Prompt tricks and off-topic requests are refused and steered back (tested).

### Built for more than one business
- Every business-specific fact (hours, staff, prices, FAQs, not-offered list, policies,
  greeting, timezone, slot length) is a database row. `app/business.py` turns it into the
  prompt **at the start of each call**, so a data change applies to the **next call with no
  redeploy**.
- **Isolation:** every table carries `business_id`; foreign keys are composite
  `(id, business_id)`, so a booking can't reference another business's staff or service.
  Every query filters by the business resolved from the room name. Supabase's public API is
  locked with Row Level Security.
- **Second business:** *Speedy Wheels Car Care* (`db/004_seed_second_business.sql`). It is
  data only, with no code change, and took ~10 minutes. It deliberately differs: 60-minute
  slots, 4-hour free cancellation, a lunch break, open on Sunday.
  `tests/test_multi_business.py` proves neither business can see the other's services,
  bookings or prompt.

---

## Latency

**Method:** every reply is timed from the moment the caller stopped speaking to the moment
the first audio of the reply was sent (agent state → `speaking`). Stage times come from
LiveKit's metrics for the same turn. Every turn is logged to `call_events` and summarised by
`python scripts/latency_report.py` (raw output in [`docs/latency.txt`](docs/latency.txt)).

**Measured on the live system:** 26 turns over 2 real calls (questions, then book → reschedule → cancel),
browser in Bengaluru → LiveKit Cloud (India South) → agent on my laptop → Groq (US).

| Stage | Median | p95 |
|---|---|---|
| **Total: end of speech → first audio of reply** | **1,725 ms** | **6,018 ms** |
| End of turn (VAD silence + speech-to-text) | 585 ms | 681 ms |
| Speech to text (Whisper on Groq) | 580 ms | 679 ms |
| LLM first token (gpt-oss-120b on Groq) | 552 ms | 702 ms |
| Turns **without** a tool call (n=20) | 1,586 ms | |
| Turns **with** a tool call (n=6) | 2,614 ms | |

**Honest reading:**
- **These numbers miss the targets** (1.2 s median, 2 s p95).
- **Measurement start point:** this run started the clock at the *end of speech*, which is
  ~350 ms (the VAD silence window) *before* the system *detects* the caller stopped, the start
  point the brief defines. I've fixed the logging to record both (`total_ms` from detection,
  `from_speech_end_ms` from speech end). I haven't re-measured with the corrected start point
  yet; I expect it to be up to a few hundred ms lower, still above 1.2 s.
- **Where the time goes:** speech-to-text and the LLM are each ~550–600 ms, mostly network
  round trips from India to Groq's US datacentres plus queueing on the free tier. TTS (Piper,
  self-hosted) is the smallest stage: I measured ~40–400 ms to first audio depending on
  sentence length. LiveKit's OpenAI-compatible TTS plugin doesn't report TTS time for my
  server, so it's not in the table.
- **The p95** comes from **tool-call turns** (checking or booking needs two LLM round trips)
  and **free-tier rate limits** (a 429 triggers the fallback to the next model).

**How I'd get under 1.2 s / 2 s:**
1. **Self-host STT and LLM close to the caller** (Whisper-turbo + gpt-oss-20b on one GPU in an
   Indian region, vLLM). This removes ~400–600 ms of network time and all rate-limit waits.
2. **Streaming STT** (transcripts while the caller is still talking) so end-of-turn doesn't
   wait for a whole-utterance transcription.
3. **Filler on tool turns** ("Let me check that…") spoken immediately while the tool runs, so
   the caller hears something within the target even on booking turns.
4. A smaller/faster LLM for simple FAQ turns, with the 120B reserved for booking flows.

---

## Tests

```bash
pytest -v                               # everything (full output in docs/test_results.txt)
pytest tests/test_booking.py            # booking logic + concurrency
pytest tests/test_multi_business.py     # isolation between businesses
pytest tests/test_conversations.py -v   # simulated conversations (needs GROQ_API_KEY)
```

| Suite | What it proves | Result |
|---|---|---|
| `test_booking.py` (13) | availability, hours, Sundays, past slots, ambiguous services, book/cancel/reschedule, late-cancel confirmation, **20 concurrent bookings → exactly 1** | all pass, every run |
| `test_multi_business.py` (3) | second business works from data only; no cross-business data | all pass, every run |
| `test_conversations.py` (16) | the real agent (real prompt, tools, LLM) answers **every test question in the brief**, refuses tricks, a **multi-turn booking** lands exactly one row, and a real booking's customer is **never revealed** | 30–32 of 32 per run (see below) |

**Honest note on the conversation tests:** they use a real LLM and an LLM judge, so they are
not fully deterministic. Across my last runs, 0–2 of the 16 failed, on different questions
each time (e.g. the agent offered a time before calling `check_availability`, or gave a
shorter answer than the judge wanted). Each failure prints the exact conversation
(`CALLER:/AGENT:/TOOL:`), which is how I tightened the prompt. Free-tier rate limits can also
fail a run; tests are paced 6 s apart.

---

## Free-tier rate limits

Limits I hit (Groq free tier, per model): **30 requests/min and 8K tokens/min** for each LLM,
and 20 requests/min for Whisper-turbo. One reply sends ~2.3K prompt tokens, so a single
model handles roughly 3–4 replies a minute.

What I did:
- **Fallback across models**, each with its own limit: gpt-oss-120b → gpt-oss-20b →
  Qwen3.8-27B for the LLM, Whisper-turbo → Whisper-large-v3 for STT.
- **Cache-friendly prompt:** everything that changes per call (today's date and time) is at the
  very end, so the long fixed part is cached. On Groq, cached tokens don't count toward
  rate limits.
- **No silent failures:** if every model is busy, the agent says "Sorry, I'm having a little
  trouble right now. Could you say that again in a moment?" and the error is logged.
- Self-hosted TTS, so voice output has no rate limit at all.

**Result:** a couple of simultaneous conversations work; several people talking at once can
hit the limit. **In production:** Groq's paid tier (higher limits), or self-host the
open-weight LLM (gpt-oss-20b fits one GPU with vLLM) behind the same OpenAI-compatible
interface. Only an environment variable changes.

---

## Scaling to 1,000 businesses

- **Data model already fits:** one Postgres, `business_id` everywhere, composite foreign keys
  and the partial unique index per staff member. 1,000 businesses × a few hundred bookings a day
  is small for Postgres. Add indexes on `(business_id, slot_start)` and partition
  `call_events` by month.
- **Stateless web tier** behind a load balancer; tokens are cheap to mint.
- **Agent workers scale horizontally:** LiveKit dispatches each call to any free worker. I'd
  run N worker containers (or LiveKit's autoscaling) sized by concurrent calls, not by number
  of businesses. Prewarm processes so calls start instantly.
- **Models:** move off free tiers. Self-host Whisper + gpt-oss on GPUs (vLLM), or use
  paid inference with per-business quotas. TTS is already self-hosted; Piper scales on plain
  CPU, Kokoro on a small GPU.
- **Prompt per call** stays small because it holds one business only. Cache business
  snapshots in memory (invalidate on update) to save a DB round trip at call start.
- **Ops:** per-business dashboards from `call_events` (latency, booking rate, failures), alerts
  on error rate, and a "pause agent" flag per business.
- **Onboarding** is a data form (or CSV import) that writes the same tables as
  `db/004_seed_second_business.sql`.

---

## Working with the customer

- **Handover note** for the salon owner: [`docs/HANDOVER.md`](docs/HANDOVER.md)
- **Live changes** (new service, new hours, holiday, price change) are one SQL statement each,
  and the next call uses them: [`docs/LIVE_CHANGES.md`](docs/LIVE_CHANGES.md)
- **Call logs:** every call stores its transcript, every tool call with arguments and result,
  errors and per-turn latency.
  ```bash
  python scripts/show_call.py            # latest calls
  python scripts/show_call.py 3f2a       # one call, by id prefix
  ```
  Example output:
  ```
  16:02:11  CALLER  How much is the oil change?
  16:02:12  AGENT   An oil change is one thousand eight hundred rupees, and it's done by Imran Khan.
  16:02:12    (latency {'total_ms': ..., 'stt_ms': ..., 'llm_ttft_ms': ..., 'tts_ttfb_ms': ...})
  ```

---

## What's missing / known issues

- **Hosted on my laptop** via ngrok (see note at top). If the laptop sleeps, the page is down.
- **Rate-limit ceiling** on the free tier: roughly 2–3 simultaneous conversations.
- **Piper voice** is clear but more robotic than Kokoro; the chosen voice was trained on a
  research dataset, so a commercial deployment should use a commercially licensed voice.
- **Caller identity** is the phone number the caller says; there is no OTP or caller ID, so
  someone who knows your number and booking time could cancel it. A real phone line (SIP)
  would give caller ID.
- Speech recognition occasionally mishears short phrases ("basic wash" → "basic for us"); the
  LLM usually recovers.
- Conversation tests are not fully deterministic (see Tests).
- One service = one 30-minute slot (as specified); multi-slot services would need a range
  constraint (`EXCLUDE USING gist`).
- None of the optional extras (dashboard, SIP, Hindi, calendar sync) are built; I focused on
  the core call, bookings and safety.

---

## AI tools used

I used **Claude (Anthropic)** as a pair programmer for code, tests and documentation. I ran,
tested and debugged everything on my own machine (several fixes came from my test runs:
retired Groq models, rate limits, prompt gaps found by the conversation tests) and can explain
every part of the code.
