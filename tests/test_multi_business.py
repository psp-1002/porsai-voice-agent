"""Two businesses in one database: each call only ever sees its own business.

Speedy Wheels was added with data only (db/004_seed_second_business.sql).
"""
from app import booking
from app.business import build_instructions, load_business
from tests.conftest import NOW

PHONE = "98450 33333"


async def wheels_id(pool):
    return await pool.fetchval("SELECT id FROM businesses WHERE slug = 'speedy-wheels'")


async def test_second_business_works_with_data_only(pool):
    wheels = await wheels_id(pool)
    # Thu 26 Nov: Imran works 9-1 and 2-6 with 60 minute slots -> lunch hour is not offered.
    r = await booking.check_availability(pool, wheels, "oil change", "2026-11-26", now=NOW)
    assert r["staff"] == "Imran Khan"
    assert "1:00 PM" not in r["free_times"]
    assert r["free_times"][:2] == ["9:00 AM", "10:00 AM"] and "2:00 PM" in r["free_times"]
    # Open on Sunday for washes (the salon is closed on Sundays)
    r = await booking.check_availability(pool, wheels, "basic wash", "2026-11-29", now=NOW)
    assert r["free_times"] == ["8:00 AM", "9:00 AM", "10:00 AM", "11:00 AM"]


async def test_businesses_never_see_each_other(pool, salon):
    wheels = await wheels_id(pool)
    # The salon can't book a car service, and the garage can't book a haircut.
    r = await booking.check_availability(pool, salon, "oil change", "2026-11-27", now=NOW)
    assert not r["ok"] and "don't offer" in r["error"]
    r = await booking.check_availability(pool, wheels, "Haircut (men)", "2026-11-27", now=NOW)
    assert not r["ok"]
    # A booking at the salon is invisible from the garage, even with the same phone number.
    assert (await booking.book(pool, salon, "hair spa", "2026-11-28", "17:00",
                               "Meena", PHONE, now=NOW))["ok"]
    assert (await booking.my_bookings(pool, wheels, PHONE, now=NOW))["bookings"] == []
    assert len((await booking.my_bookings(pool, salon, PHONE, now=NOW))["bookings"]) == 1


async def test_prompt_only_contains_its_own_business(pool):
    garage = build_instructions(await load_business(pool, "speedy-wheels"), now=NOW)
    salon = build_instructions(await load_business(pool, "sunrise-salon"), now=NOW)
    assert "Speedy Wheels" in garage and "Anita" not in garage and "Sunrise" not in garage
    assert "Sunrise Salon" in salon and "Imran" not in salon
    assert "Closed every" not in garage          # open all week (wash bay works Sundays)
