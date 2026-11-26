# Live changes: ready for the review call

Every change below is **data only**. Run it in the Supabase SQL editor (or edit the row in
the Table Editor). The **next call** picks it up: the agent loads the business at the start of
each call. No restart, no redeploy.

Check the change landed:
```sql
SELECT name, price_inr FROM services
 WHERE business_id = (SELECT id FROM businesses WHERE slug = 'sunrise-salon');
```

### New service
```sql
INSERT INTO services (business_id, staff_id, name, price_inr)
SELECT b.id, s.id, 'Beard trim', 250
  FROM businesses b JOIN staff s ON s.business_id = b.id
 WHERE b.slug = 'sunrise-salon' AND s.name = 'Anita Rao';
```

### Change a price
```sql
UPDATE services SET price_inr = 450
 WHERE name = 'Haircut (men)'
   AND business_id = (SELECT id FROM businesses WHERE slug = 'sunrise-salon');
```

### Change hours (e.g. Anita now 11:00 to 15:00 on Saturdays)
```sql
UPDATE staff_hours SET start_time = '11:00', end_time = '15:00'
 WHERE weekday = 6
   AND staff_id = (SELECT id FROM staff WHERE name = 'Anita Rao');
```

### Open on Sunday (e.g. Anita 10:00 to 14:00)
```sql
INSERT INTO staff_hours (business_id, staff_id, weekday, start_time, end_time)
SELECT business_id, id, 7, '10:00', '14:00' FROM staff WHERE name = 'Anita Rao';
```

### Holiday / closure
```sql
INSERT INTO closures (business_id, closed_on, reason)
SELECT id, '2026-12-25', 'Christmas' FROM businesses WHERE slug = 'sunrise-salon';
```

### Remove a service (keeps history)
```sql
UPDATE services SET active = false WHERE name = 'Clean-up';
```

### New FAQ
```sql
INSERT INTO faqs (business_id, topic, answer)
SELECT id, 'Gift vouchers', 'Gift vouchers are available at the front desk.'
  FROM businesses WHERE slug = 'sunrise-salon';
```

### Change the free-cancellation window
```sql
UPDATE businesses SET free_cancel_hours = 4 WHERE slug = 'sunrise-salon';
```

Then make a call and ask about it: the answer reflects the change.
