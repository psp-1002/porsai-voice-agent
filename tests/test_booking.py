"""Booking logic tests against a real Postgres.

Calendar used (fixed NOW = Thu 26 Nov 2026, 08:00 IST):
  Fri 27, Sat 28, Sun 29 (closed), Mon 30 Nov, Tue 1 Dec, Wed 2 Dec
"""
import asyncio
import datetime as dt

from app import booking
from tests.conftest import IST, NOW

PHONE_A = "98450 11111"
PHONE_B = "+91 98450 22222"


async def test_facial_on_tuesday_offers_kavyas_next_day(pool, salon):
    r = await booking.check_availability(pool, salon, "facial", "2026-12-01", now=NOW)
    assert r["ok"] and r["free_times"] == []
    assert "does not work on Tuesday" in r["reason"]
    assert r["next_options"][0]["weekday"] == "Wednesday"
    assert r["next_options"][0]["times"][0] == "9:00 AM"


async def test_closed_on_sunday(pool, salon):
    for service in ["Haircut (men)", "hair spa", "facial"]:
        r = await booking.check_availability(pool, salon, service, "2026-11-29", now=NOW)
        assert r["free_times"] == []


async def test_hair_colour_thursday_slots(pool, salon):
    r = await booking.check_availability(pool, salon, "hair colour", "2026-11-26", now=NOW)
    assert r["staff"] == "Rahul Shetty"
    assert r["free_times"] == ["4:00 PM", "4:30 PM", "5:00 PM", "5:30 PM",
                               "6:00 PM", "6:30 PM", "7:00 PM", "7:30 PM"]


async def test_past_slots_hidden(pool, salon):
    later = dt.datetime(2026, 11, 26, 17, 10, tzinfo=IST)
    r = await booking.check_availability(pool, salon, "hair spa", "2026-11-26", now=later)
    assert r["free_times"][0] == "5:30 PM"


async def test_ambiguous_and_unknown_service(pool, salon):
    r = await booking.check_availability(pool, salon, "haircut", "2026-11-27", now=NOW)
    assert not r["ok"] and "Which one" in r["error"]
    r = await booking.check_availability(pool, salon, "nail art", "2026-11-27", now=NOW)
    assert not r["ok"] and "don't offer" in r["error"]


async def test_book_then_slot_disappears(pool, salon):
    r = await booking.book(pool, salon, "Haircut (men)", "2026-11-27", "10 am",
                           "Ravi", PHONE_A, now=NOW)
    assert r["ok"] and r["staff"] == "Anita Rao" and r["time"] == "10:00 AM"
    a = await booking.check_availability(pool, salon, "Haircut (women)", "2026-11-27", now=NOW)
    assert "10:00 AM" not in a["free_times"]          # same stylist, so gone for every service


async def test_cannot_book_outside_hours(pool, salon):
    r = await booking.book(pool, salon, "facial", "2026-11-27", "3 pm", "Ravi", PHONE_A, now=NOW)
    assert not r["ok"] and "not available" in r["error"]
    r = await booking.book(pool, salon, "facial", "2026-11-27", "9:15", "Ravi", PHONE_A, now=NOW)
    assert not r["ok"]                                # not aligned to a 30 min slot


async def test_no_double_booking_under_concurrency(pool, salon):
    """20 callers try the same slot at the same instant. Exactly one wins."""
    attempts = [
        booking.book(pool, salon, "Haircut (women)" if i % 2 else "Haircut (men)",
                     "2026-11-28", "11:00", f"Caller {i}", f"98450{i:05d}", now=NOW)
        for i in range(20)
    ]
    results = await asyncio.gather(*attempts)
    winners = [r for r in results if r["ok"]]
    losers = [r for r in results if not r["ok"]]
    assert len(winners) == 1
    assert all(r["error"] == "slot_taken" for r in losers)
    count = await pool.fetchval(
        "SELECT count(*) FROM bookings WHERE status = 'booked' "
        "AND slot_start = '2026-11-28 11:00+05:30'")
    assert count == 1


async def test_only_own_bookings_are_visible(pool, salon):
    await booking.book(pool, salon, "hair spa", "2026-11-28", "17:00", "Meena", PHONE_A, now=NOW)
    mine = await booking.my_bookings(pool, salon, PHONE_A, now=NOW)
    theirs = await booking.my_bookings(pool, salon, PHONE_B, now=NOW)
    assert len(mine["bookings"]) == 1
    assert theirs["bookings"] == []
    # Someone else can't cancel Meena's booking by guessing the time.
    r = await booking.cancel(pool, salon, PHONE_B, "2026-11-28", "5 pm", now=NOW)
    assert not r["ok"]


async def test_late_cancellation_needs_confirmation(pool, salon):
    await booking.book(pool, salon, "hair spa", "2026-11-28", "5 PM", "Meena", PHONE_A, now=NOW)
    four_pm = dt.datetime(2026, 11, 28, 16, 0, tzinfo=IST)
    r = await booking.cancel(pool, salon, PHONE_A, "2026-11-28", "5 PM", now=four_pm)
    assert r["error"] == "needs_confirmation" and r["free_cancellation"] is False
    r = await booking.cancel(pool, salon, PHONE_A, "2026-11-28", "5 PM",
                             confirm_not_free=True, now=four_pm)
    assert r["ok"] and r["free_cancellation"] is False


async def test_free_cancellation_frees_the_slot(pool, salon):
    await booking.book(pool, salon, "hair spa", "2026-11-28", "5 PM", "Meena", PHONE_A, now=NOW)
    r = await booking.cancel(pool, salon, PHONE_A, "2026-11-28", "5 PM", now=NOW)
    assert r["ok"] and r["free_cancellation"] is True
    r = await booking.book(pool, salon, "hair spa", "2026-11-28", "5 PM", "Arjun", PHONE_B, now=NOW)
    assert r["ok"]                                    # cancelled slot can be rebooked


async def test_reschedule(pool, salon):
    await booking.book(pool, salon, "facial", "2026-11-27", "9:00", "Ria", PHONE_A, now=NOW)
    await booking.book(pool, salon, "facial", "2026-11-27", "10:00", "Sam", PHONE_B, now=NOW)
    # Moving onto Sam's slot fails and Ria's booking stays put.
    r = await booking.reschedule(pool, salon, PHONE_A, "2026-11-27", "9:00",
                                 "2026-11-27", "10:00", now=NOW)
    assert r["error"] == "slot_taken"
    r = await booking.reschedule(pool, salon, PHONE_A, "2026-11-27", "9:00",
                                 "2026-11-30", "11:30", now=NOW)
    assert r["ok"] and r["weekday"] == "Monday"
    mine = await booking.my_bookings(pool, salon, PHONE_A, now=NOW)
    assert [(b["date"], b["time"]) for b in mine["bookings"]] == [("2026-11-30", "11:30 AM")]


def test_calendar_hint_resolves_next_tuesday():
    text = booking.calendar_hint("Asia/Kolkata", now=NOW)
    assert "Thursday 26 November 2026" in text
    assert "Tue 2026-12-01" in text
