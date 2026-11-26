"""Booking logic: availability, book, reschedule, cancel.

Rules this module enforces:
  * Every query is scoped by business_id -> one business never sees another's data.
  * Times are interpreted in the business's own timezone (e.g. Asia/Kolkata).
  * A slot is valid only if it falls inside the staff member's working hours,
    is aligned to the slot size, is in the future and is not on a closure day.
  * Double booking is prevented by the database itself (partial unique index
    `bookings_no_double_booking`). We never "check then insert" and trust it;
    we insert and let Postgres reject the loser of any race.
  * A caller can only see / change bookings that match the phone number they give.

All public functions return plain dicts, ready to be sent back to the LLM as a
tool result.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

import asyncpg

MAX_DAYS_AHEAD = 60          # don't book further out than this
SEARCH_DAYS_FOR_NEXT = 14    # how far to look for alternatives


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class BookingError(Exception):
    """A problem to explain to the caller (bad date, unknown service...)."""


def normalize_phone(phone: str) -> str:
    """Keep digits only; compare on the last 10 (Indian mobile numbers)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        raise BookingError("Please give a 10 digit phone number.")
    return digits[-10:]


def parse_date(value: str) -> dt.date:
    """The LLM is told to always send YYYY-MM-DD (it gets a calendar in its prompt)."""
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        raise BookingError(f"I couldn't understand the date '{value}'.")


def parse_time(value: str) -> dt.time:
    """Accepts '17:00', '5 PM', '5:30pm', '10.30 am'."""
    v = value.strip().lower().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", v)
    if not m:
        raise BookingError(f"I couldn't understand the time '{value}'.")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise BookingError(f"'{value}' is not a valid time.")
    return dt.time(hour, minute)


def fmt_time(t: dt.datetime | dt.time) -> str:
    """17:00 -> '5:00 PM' (how the agent will speak it)."""
    return t.strftime("%I:%M %p").lstrip("0")


def calendar_hint(tz_name: str, now: dt.datetime | None = None, days: int = 14) -> str:
    """Text for the system prompt so the LLM can turn 'next Tuesday' into a date.

    Doing this in code (not leaving it to the LLM's guess) is what makes
    relative dates reliable.
    """
    tz = ZoneInfo(tz_name)
    now = (now or dt.datetime.now(tz)).astimezone(tz)
    today = now.date()
    lines = [f"Right now it is {now:%A %d %B %Y, %I:%M %p} ({tz_name})."]
    for i in range(days):
        d = today + dt.timedelta(days=i)
        label = "today" if i == 0 else "tomorrow" if i == 1 else ""
        lines.append(f"{d:%a} {d.isoformat()} {label}".rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lookups (always filtered by business_id)
# ---------------------------------------------------------------------------
async def _business(conn, business_id) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, timezone, slot_minutes, free_cancel_hours FROM businesses WHERE id = $1",
        business_id,
    )
    if row is None:
        raise BookingError("Unknown business.")
    return row


async def _find_service(conn, business_id, service_name: str) -> asyncpg.Record:
    """Match a spoken service name to exactly one service of THIS business."""
    rows = await conn.fetch(
        """SELECT sv.id, sv.name, sv.staff_id, st.name AS staff_name
             FROM services sv JOIN staff st ON st.id = sv.staff_id
            WHERE sv.business_id = $1 AND sv.active AND st.active""",
        business_id,
    )
    wanted = service_name.strip().lower()
    exact = [r for r in rows if r["name"].lower() == wanted]
    if exact:
        return exact[0]
    partial = [r for r in rows if wanted in r["name"].lower() or r["name"].lower() in wanted]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(r["name"] for r in partial)
        raise BookingError(f"Which one do you mean: {names}?")
    raise BookingError(f"We don't offer '{service_name}'.")


async def _working_slots(conn, biz, staff_id, day: dt.date) -> list[dt.datetime]:
    """All slot start times (timezone-aware) the staff member works on `day`."""
    closed = await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM closures
                           WHERE business_id = $1 AND closed_on = $2
                             AND (staff_id IS NULL OR staff_id = $3))""",
        biz["id"], day, staff_id,
    )
    if closed:
        return []
    hours = await conn.fetch(
        """SELECT start_time, end_time FROM staff_hours
            WHERE business_id = $1 AND staff_id = $2 AND weekday = $3
            ORDER BY start_time""",
        biz["id"], staff_id, day.isoweekday(),
    )
    tz = ZoneInfo(biz["timezone"])
    step = dt.timedelta(minutes=biz["slot_minutes"])
    slots = []
    for h in hours:
        cur = dt.datetime.combine(day, h["start_time"], tzinfo=tz)
        end = dt.datetime.combine(day, h["end_time"], tzinfo=tz)
        while cur + step <= end:
            slots.append(cur)
            cur += step
    return slots


async def _free_slots(conn, biz, staff_id, day: dt.date, now: dt.datetime) -> list[dt.datetime]:
    slots = [s for s in await _working_slots(conn, biz, staff_id, day) if s > now]
    if not slots:
        return []
    taken = {
        r["slot_start"]
        for r in await conn.fetch(
            """SELECT slot_start FROM bookings
                WHERE business_id = $1 AND staff_id = $2 AND status = 'booked'
                  AND slot_start >= $3 AND slot_start <= $4""",
            biz["id"], staff_id, slots[0], slots[-1],
        )
    }
    return [s for s in slots if s not in taken]


async def _next_options(conn, biz, staff_id, after: dt.date, now, max_days=2, per_day=4):
    """A few alternative days with free slots, for 'that day doesn't work'."""
    options = []
    for i in range(1, SEARCH_DAYS_FOR_NEXT + 1):
        d = after + dt.timedelta(days=i)
        free = await _free_slots(conn, biz, staff_id, d, now)
        if free:
            options.append({"date": d.isoformat(), "weekday": f"{d:%A}",
                            "times": [fmt_time(s) for s in free[:per_day]]})
            if len(options) >= max_days:
                break
    return options


def _now(biz, now):
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(biz["timezone"]))


def _check_day_in_range(day: dt.date, now: dt.datetime):
    if day < now.date():
        raise BookingError("That date is in the past.")
    if day > now.date() + dt.timedelta(days=MAX_DAYS_AHEAD):
        raise BookingError(f"We only take bookings up to {MAX_DAYS_AHEAD} days ahead.")


# ---------------------------------------------------------------------------
# Public API — these become the agent's tools
# ---------------------------------------------------------------------------
async def check_availability(pool, business_id, service: str, date: str, now=None) -> dict:
    try:
        async with pool.acquire() as conn:
            biz = await _business(conn, business_id)
            now = _now(biz, now)
            day = parse_date(date)
            _check_day_in_range(day, now)
            svc = await _find_service(conn, business_id, service)
            free = await _free_slots(conn, biz, svc["staff_id"], day, now)
            result = {
                "ok": True, "service": svc["name"], "staff": svc["staff_name"],
                "date": day.isoformat(), "weekday": f"{day:%A}",
                "free_times": [fmt_time(s) for s in free],
            }
            if not free:
                works = bool(await _working_slots(conn, biz, svc["staff_id"], day))
                result["reason"] = ("fully booked or already past" if works
                                    else f"{svc['staff_name']} does not work on {day:%A}")
                result["next_options"] = await _next_options(conn, biz, svc["staff_id"], day, now)
            return result
    except BookingError as e:
        return {"ok": False, "error": str(e)}


async def book(pool, business_id, service: str, date: str, time: str,
               customer_name: str, customer_phone: str, call_id=None, now=None) -> dict:
    try:
        async with pool.acquire() as conn:
            biz = await _business(conn, business_id)
            now = _now(biz, now)
            day, t = parse_date(date), parse_time(time)
            _check_day_in_range(day, now)
            phone = normalize_phone(customer_phone)
            if not customer_name.strip():
                raise BookingError("I need a name for the booking.")
            svc = await _find_service(conn, business_id, service)

            slot = dt.datetime.combine(day, t, tzinfo=ZoneInfo(biz["timezone"]))
            if slot <= now:
                raise BookingError("That time has already passed.")
            if slot not in await _working_slots(conn, biz, svc["staff_id"], day):
                raise BookingError(f"{svc['staff_name']} is not available at "
                                   f"{fmt_time(slot)} on {day:%A}.")
            try:
                booking_id = await conn.fetchval(
                    """INSERT INTO bookings (business_id, staff_id, service_id,
                                             customer_name, customer_phone, slot_start, call_id)
                       VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
                    business_id, svc["staff_id"], svc["id"],
                    customer_name.strip(), phone, slot, call_id,
                )
            except asyncpg.UniqueViolationError:
                # Someone else got this slot first (maybe a split second ago).
                free = await _free_slots(conn, biz, svc["staff_id"], day, now)
                return {"ok": False, "error": "slot_taken",
                        "message": f"Sorry, {fmt_time(slot)} was just taken.",
                        "free_times_same_day": [fmt_time(s) for s in free[:6]]}
            return {"ok": True, "booking_ref": str(booking_id)[:8], "service": svc["name"],
                    "staff": svc["staff_name"], "date": day.isoformat(),
                    "weekday": f"{day:%A}", "time": fmt_time(slot),
                    "name": customer_name.strip()}
    except BookingError as e:
        return {"ok": False, "error": str(e)}


async def my_bookings(pool, business_id, customer_phone: str, now=None) -> dict:
    """Upcoming bookings for the caller's own phone number only."""
    try:
        async with pool.acquire() as conn:
            biz = await _business(conn, business_id)
            now = _now(biz, now)
            phone = normalize_phone(customer_phone)
            rows = await conn.fetch(
                """SELECT b.slot_start, sv.name AS service, st.name AS staff
                     FROM bookings b
                     JOIN services sv ON sv.id = b.service_id
                     JOIN staff st    ON st.id = b.staff_id
                    WHERE b.business_id = $1 AND b.customer_phone = $2
                      AND b.status = 'booked' AND b.slot_start > $3
                    ORDER BY b.slot_start""",
                business_id, phone, now,
            )
            tz = ZoneInfo(biz["timezone"])
            return {"ok": True, "bookings": [
                {"service": r["service"], "staff": r["staff"],
                 "date": r["slot_start"].astimezone(tz).date().isoformat(),
                 "weekday": f"{r['slot_start'].astimezone(tz):%A}",
                 "time": fmt_time(r["slot_start"].astimezone(tz))} for r in rows]}
    except BookingError as e:
        return {"ok": False, "error": str(e)}


async def _find_own_booking(conn, biz, phone, day, t):
    slot = dt.datetime.combine(day, t, tzinfo=ZoneInfo(biz["timezone"]))
    row = await conn.fetchrow(
        """SELECT id, staff_id, slot_start FROM bookings
            WHERE business_id = $1 AND customer_phone = $2
              AND slot_start = $3 AND status = 'booked'""",
        biz["id"], phone, slot,
    )
    if row is None:
        raise BookingError("I couldn't find a booking for that phone number at that time.")
    return row


async def cancel(pool, business_id, customer_phone: str, date: str, time: str,
                 confirm_not_free: bool = False, now=None) -> dict:
    """Cancel the caller's booking.

    If it's inside the free-cancellation window we do NOT cancel straight away:
    we return needs_confirmation so the agent can tell the caller it isn't free
    and ask again with confirm_not_free=True.
    """
    try:
        async with pool.acquire() as conn:
            biz = await _business(conn, business_id)
            now = _now(biz, now)
            phone = normalize_phone(customer_phone)
            row = await _find_own_booking(conn, biz, phone, parse_date(date), parse_time(time))
            if row["slot_start"] <= now:
                raise BookingError("That appointment time has already passed.")
            free = row["slot_start"] - now >= dt.timedelta(hours=biz["free_cancel_hours"])
            if not free and not confirm_not_free:
                return {"ok": False, "error": "needs_confirmation", "free_cancellation": False,
                        "message": (f"It can be cancelled, but it is less than "
                                    f"{biz['free_cancel_hours']} hours before the appointment, "
                                    f"so the cancellation is not free. Cancel anyway?")}
            await conn.execute(
                "UPDATE bookings SET status = 'cancelled', cancelled_at = now() "
                "WHERE id = $1 AND business_id = $2",
                row["id"], biz["id"],
            )
            return {"ok": True, "cancelled": True, "free_cancellation": free}
    except BookingError as e:
        return {"ok": False, "error": str(e)}


async def reschedule(pool, business_id, customer_phone: str, old_date: str, old_time: str,
                     new_date: str, new_time: str, now=None) -> dict:
    """Move a booking to a new slot with the same staff member.

    A single UPDATE: if the new slot is taken, the unique index rejects it and
    the original booking stays exactly as it was.
    """
    try:
        async with pool.acquire() as conn:
            biz = await _business(conn, business_id)
            now = _now(biz, now)
            phone = normalize_phone(customer_phone)
            row = await _find_own_booking(conn, biz, phone,
                                          parse_date(old_date), parse_time(old_time))
            day, t = parse_date(new_date), parse_time(new_time)
            _check_day_in_range(day, now)
            new_slot = dt.datetime.combine(day, t, tzinfo=ZoneInfo(biz["timezone"]))
            if new_slot <= now:
                raise BookingError("That new time has already passed.")
            if new_slot not in await _working_slots(conn, biz, row["staff_id"], day):
                raise BookingError(f"That staff member isn't available at "
                                   f"{fmt_time(new_slot)} on {day:%A}.")
            try:
                await conn.execute(
                    "UPDATE bookings SET slot_start = $1 WHERE id = $2 AND business_id = $3",
                    new_slot, row["id"], biz["id"],
                )
            except asyncpg.UniqueViolationError:
                free = await _free_slots(conn, biz, row["staff_id"], day, now)
                return {"ok": False, "error": "slot_taken",
                        "message": f"Sorry, {fmt_time(new_slot)} is already taken.",
                        "free_times_same_day": [fmt_time(s) for s in free[:6]]}
            return {"ok": True, "date": day.isoformat(), "weekday": f"{day:%A}",
                    "time": fmt_time(new_slot)}
    except BookingError as e:
        return {"ok": False, "error": str(e)}
