-- =====================================================================
-- Voice Receptionist — database schema (PostgreSQL 15+)
-- Every row that belongs to a business carries business_id, so one
-- business can never see another's data. Nothing business-specific
-- lives in code or prompts: it all lives here.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for gen_random_uuid()

-- ---------------------------------------------------------------------
-- 1. Businesses (one row per customer of ours, e.g. Sunrise Salon)
-- ---------------------------------------------------------------------
CREATE TABLE businesses (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                    text UNIQUE NOT NULL,          -- used in the call URL: /call/sunrise-salon
    name                    text NOT NULL,
    business_type           text NOT NULL,                 -- 'salon', 'car service', ...
    timezone                text NOT NULL,                 -- IANA name, e.g. 'Asia/Kolkata'
    address                 text NOT NULL,
    phone                   text,                          -- business's public number (optional)
    slot_minutes            int  NOT NULL DEFAULT 30 CHECK (slot_minutes > 0),
    free_cancel_hours       int  NOT NULL DEFAULT 2  CHECK (free_cancel_hours >= 0),
    not_offered             text[] NOT NULL DEFAULT '{}',  -- things the agent must say "we don't do"
    greeting                text,                          -- first sentence the agent says
    created_at              timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 2. Staff (stylists, mechanics, instructors ...)
-- ---------------------------------------------------------------------
CREATE TABLE staff (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id  uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name         text NOT NULL,
    role         text NOT NULL,                 -- 'Haircuts and styling'
    active       boolean NOT NULL DEFAULT true,
    UNIQUE (business_id, name),
    UNIQUE (id, business_id)                    -- lets other tables enforce "same business"
);

-- Weekly working hours. One row per staff member per working day.
-- weekday uses ISO numbering: 1 = Monday ... 7 = Sunday.
CREATE TABLE staff_hours (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id  uuid NOT NULL,
    staff_id     uuid NOT NULL,
    weekday      int  NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    start_time   time NOT NULL,                 -- local time in the business's timezone
    end_time     time NOT NULL,
    CHECK (end_time > start_time),
    FOREIGN KEY (staff_id, business_id) REFERENCES staff(id, business_id) ON DELETE CASCADE,
    UNIQUE (staff_id, weekday, start_time)
);

-- One-off closures: holidays, a stylist on leave. staff_id NULL = whole business closed.
CREATE TABLE closures (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id  uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    staff_id     uuid,
    closed_on    date NOT NULL,
    reason       text,
    FOREIGN KEY (staff_id, business_id) REFERENCES staff(id, business_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- 3. Services and FAQs
-- ---------------------------------------------------------------------
CREATE TABLE services (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id     uuid NOT NULL,
    staff_id        uuid NOT NULL,              -- who performs it
    name            text NOT NULL,
    price_inr       int  NOT NULL CHECK (price_inr >= 0),
    price_is_from   boolean NOT NULL DEFAULT false,   -- true => "From Rs X"
    price_note      text,                             -- "depends on hair length"
    slots           int  NOT NULL DEFAULT 1 CHECK (slots = 1),  -- spec: one slot per booking
    active          boolean NOT NULL DEFAULT true,
    UNIQUE (business_id, name),
    UNIQUE (id, business_id),
    FOREIGN KEY (staff_id, business_id) REFERENCES staff(id, business_id) ON DELETE CASCADE
);

CREATE TABLE faqs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id  uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    topic        text NOT NULL,                 -- 'Payment'
    answer       text NOT NULL,                 -- 'Cash, UPI and cards.'
    UNIQUE (business_id, topic)
);

-- ---------------------------------------------------------------------
-- 4. Bookings — the table that must never double book
-- ---------------------------------------------------------------------
CREATE TABLE bookings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id     uuid NOT NULL,
    staff_id        uuid NOT NULL,
    service_id      uuid NOT NULL,
    customer_name   text NOT NULL,
    customer_phone  text NOT NULL,              -- used to find "my booking" later
    slot_start      timestamptz NOT NULL,       -- stored in UTC, shown in business timezone
    status          text NOT NULL DEFAULT 'booked'
                    CHECK (status IN ('booked', 'cancelled', 'done')),
    call_id         uuid,                       -- which call created it (for call logs)
    created_at      timestamptz NOT NULL DEFAULT now(),
    cancelled_at    timestamptz,
    FOREIGN KEY (staff_id, business_id)   REFERENCES staff(id, business_id),
    FOREIGN KEY (service_id, business_id) REFERENCES services(id, business_id)
);

-- THE double-booking guard.
-- Only one *active* booking per staff member per slot. Cancelled rows are
-- ignored, so a cancelled slot can be booked again. If two calls insert the
-- same slot at the same instant, Postgres lets exactly one succeed and the
-- other gets a unique_violation error — no matter what the app code does.
CREATE UNIQUE INDEX bookings_no_double_booking
    ON bookings (staff_id, slot_start)
    WHERE status = 'booked';

CREATE INDEX bookings_lookup_by_phone ON bookings (business_id, customer_phone);

-- ---------------------------------------------------------------------
-- 5. Call logs — transcript + every tool call, per call
-- ---------------------------------------------------------------------
CREATE TABLE calls (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id  uuid NOT NULL REFERENCES businesses(id),
    room_name    text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    ended_at     timestamptz
);

CREATE TABLE call_events (
    id        bigserial PRIMARY KEY,
    call_id   uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    ts        timestamptz NOT NULL DEFAULT now(),
    kind      text NOT NULL CHECK (kind IN
              ('user', 'agent', 'tool_call', 'tool_result', 'latency', 'error')),
    payload   jsonb NOT NULL
);

CREATE INDEX call_events_by_call ON call_events (call_id, ts);
