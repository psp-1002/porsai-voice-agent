-- Questions the agent couldn't answer ("do you have a membership plan?")
-- are passed to staff here instead of the agent making something up.
CREATE TABLE IF NOT EXISTS staff_messages (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id   uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    call_id       uuid REFERENCES calls(id) ON DELETE SET NULL,
    caller_name   text,
    caller_phone  text,
    message       text NOT NULL,
    status        text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'done')),
    created_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE staff_messages ENABLE ROW LEVEL SECURITY;
