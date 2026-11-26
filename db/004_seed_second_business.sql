-- =====================================================================
-- Seed: second business — a car service centre. DATA ONLY, no code change.
-- Different on purpose from the salon: 60 minute slots, 4 hour free
-- cancellation, a lunch break (two rows on the same day), open on Sunday.
-- =====================================================================
BEGIN;

INSERT INTO businesses (slug, name, business_type, timezone, address, phone, slot_minutes,
                        free_cancel_hours, not_offered, greeting)
VALUES ('speedy-wheels', 'Speedy Wheels Car Care', 'car service centre', 'Asia/Kolkata',
        '45, 80 Feet Road, Koramangala 4th Block, Bengaluru', '080 4000 1234', 60, 4,
        ARRAY['tyre sales', 'insurance claims', 'denting and painting', 'pickup and drop'],
        'Hi, you''ve reached Speedy Wheels Car Care. How can I help you today?');

INSERT INTO staff (business_id, name, role)
SELECT b.id, s.name, s.role
FROM businesses b,
     (VALUES ('Imran Khan',  'General service and oil changes'),
             ('Deepa Menon', 'AC repair, diagnostics and wheel alignment'),
             ('Wash Bay',    'Car wash and detailing')) AS s(name, role)
WHERE b.slug = 'speedy-wheels';

-- Imran: Mon-Sat with a lunch break (two blocks). Deepa: Tue-Sat. Wash bay: every day, mornings.
INSERT INTO staff_hours (business_id, staff_id, weekday, start_time, end_time)
SELECT st.business_id, st.id, d.weekday, h.start_time, h.end_time
FROM staff st
JOIN businesses b ON b.id = st.business_id AND b.slug = 'speedy-wheels'
JOIN (VALUES ('Imran Khan',  ARRAY[1,2,3,4,5,6],   time '09:00', time '13:00'),
             ('Imran Khan',  ARRAY[1,2,3,4,5,6],   time '14:00', time '18:00'),
             ('Deepa Menon', ARRAY[2,3,4,5,6],     time '10:00', time '17:00'),
             ('Wash Bay',    ARRAY[1,2,3,4,5,6,7], time '08:00', time '12:00'))
     AS h(name, days, start_time, end_time) ON h.name = st.name
CROSS JOIN LATERAL unnest(h.days) AS d(weekday);

INSERT INTO services (business_id, staff_id, name, price_inr, price_is_from, price_note)
SELECT st.business_id, st.id, v.service, v.price, v.is_from, v.note
FROM staff st
JOIN businesses b ON b.id = st.business_id AND b.slug = 'speedy-wheels'
JOIN (VALUES ('General service',  'Imran Khan',  3500, true,  'Exact price depends on the car model'),
             ('Oil change',       'Imran Khan',  1800, false, NULL),
             ('AC gas refill',    'Deepa Menon', 2200, false, NULL),
             ('Diagnostics scan', 'Deepa Menon',  700, false, NULL),
             ('Wheel alignment',  'Deepa Menon',  900, false, NULL),
             ('Basic wash',       'Wash Bay',     500, false, NULL),
             ('Full detailing',   'Wash Bay',    3000, false, NULL))
     AS v(service, staff, price, is_from, note) ON v.staff = st.name;

INSERT INTO faqs (business_id, topic, answer)
SELECT b.id, f.topic, f.answer
FROM businesses b,
     (VALUES ('Payment',       'Cash, UPI and cards are accepted.'),
             ('Waiting area',  'There is an air-conditioned waiting lounge with Wi-Fi.'),
             ('Warranty',      'Service work has a 30 day or 1,000 km warranty, whichever comes first.'),
             ('Parking',       'Free parking for customers inside the service centre.'),
             ('Cancellation',  'Cancellation is free up to 4 hours before the appointment.'))
     AS f(topic, answer)
WHERE b.slug = 'speedy-wheels';

COMMIT;
