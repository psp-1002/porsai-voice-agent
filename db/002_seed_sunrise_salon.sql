-- =====================================================================
-- Seed: Sunrise Salon (demo business from the assignment)
-- Pure data — no code. A second business is added the same way.
-- =====================================================================
BEGIN;

INSERT INTO businesses (slug, name, business_type, timezone, address, slot_minutes,
                        free_cancel_hours, not_offered, greeting)
VALUES ('sunrise-salon', 'Sunrise Salon', 'salon', 'Asia/Kolkata',
        '12 Lake Road, Indiranagar, Bengaluru', 30, 2,
        ARRAY['nail art', 'waxing', 'makeup', 'tattoos', 'home service'],
        'Hello, thank you for calling Sunrise Salon. How can I help you today?');

-- Staff
INSERT INTO staff (business_id, name, role)
SELECT b.id, s.name, s.role
FROM businesses b,
     (VALUES ('Anita Rao',    'Haircuts and styling'),
             ('Rahul Shetty', 'Hair colour and hair spa'),
             ('Kavya Nair',   'Facials and clean-ups')) AS s(name, role)
WHERE b.slug = 'sunrise-salon';

-- Working hours (ISO weekday: 1=Mon ... 6=Sat, 7=Sun). Nobody works Sunday => closed Sundays.
INSERT INTO staff_hours (business_id, staff_id, weekday, start_time, end_time)
SELECT st.business_id, st.id, d.weekday, h.start_time, h.end_time
FROM staff st
JOIN businesses b ON b.id = st.business_id AND b.slug = 'sunrise-salon'
JOIN (VALUES ('Anita Rao',    ARRAY[1,2,3,4,5,6], time '10:00', time '14:00'),
             ('Rahul Shetty', ARRAY[2,4,6],       time '16:00', time '20:00'),
             ('Kavya Nair',   ARRAY[1,3,5],       time '09:00', time '13:00'))
     AS h(name, days, start_time, end_time) ON h.name = st.name
CROSS JOIN LATERAL unnest(h.days) AS d(weekday);

-- Services
INSERT INTO services (business_id, staff_id, name, price_inr, price_is_from, price_note)
SELECT st.business_id, st.id, v.service, v.price, v.is_from, v.note
FROM staff st
JOIN businesses b ON b.id = st.business_id AND b.slug = 'sunrise-salon'
JOIN (VALUES ('Haircut (women)',      'Anita Rao',     600, false, NULL),
             ('Haircut (men)',        'Anita Rao',     400, false, NULL),
             ('Blow dry and styling', 'Anita Rao',     500, false, NULL),
             ('Hair colour',          'Rahul Shetty', 2500, true,  'Exact price depends on hair length'),
             ('Hair spa',             'Rahul Shetty', 1200, false, NULL),
             ('Facial',               'Kavya Nair',   1500, false, NULL),
             ('Clean-up',             'Kavya Nair',    800, false, NULL))
     AS v(service, stylist, price, is_from, note) ON v.stylist = st.name;

-- FAQs
INSERT INTO faqs (business_id, topic, answer)
SELECT b.id, f.topic, f.answer
FROM businesses b,
     (VALUES ('Payment',      'We accept cash, UPI and cards.'),
             ('Walk-ins',     'Walk-ins are welcome, but customers with a booking are served first.'),
             ('Parking',      'Street parking only.'),
             ('Late arrival', 'If you are more than 15 minutes late, the booking may be moved to the next free slot.'),
             ('Discounts',    'There are no discounts right now.'),
             ('Cancellation', 'Cancellation is free up to 2 hours before the appointment.'))
     AS f(topic, answer)
WHERE b.slug = 'sunrise-salon';

-- Note: no membership/package rows on purpose — the agent must say it has no info.

COMMIT;
