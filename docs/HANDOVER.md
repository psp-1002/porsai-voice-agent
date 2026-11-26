# Your voice receptionist: handover note for Sunrise Salon

**Your call page:** https://awaken-enforced-cradle.ngrok-free.dev/call/sunrise-salon
Put this link (or a "Call us" button pointing to it) on your website.

## What it does
- Answers the phone **from your website, any time**, in English.
- Answers questions about your **services, prices, stylists, hours, address, payment,
  parking, walk-ins and late arrival**, using only the information you gave us.
- **Books, moves and cancels** appointments. It always checks the real diary first and
  **never books the same slot twice**, even if two people call at the same moment.
- Tells callers about your **cancellation rule** (free up to 2 hours before).
- If it doesn't know something, it **says so** and takes a message for you.

## What it won't do
- Make up prices, offers or services. For hair colour it says "from Rs 2,500, depending on
  hair length" and never quotes an exact price.
- Share **anyone's phone number** (staff or customers) or say **who has booked** a slot.
- Change or cancel a booking unless the caller gives the **phone number it was made with**.
- Chat about things unrelated to the salon.

## How to change hours, prices or FAQs
Everything the receptionist knows is in your data, **not in its programming**. A change is
used on the **very next call**. No restart needed.

1. Sign in to your database at **supabase.com** → your project → **Table Editor**.
2. Open the right table and edit it like a spreadsheet, then **Save**:

| To change… | Table | What to edit |
|---|---|---|
| A price | `services` | `price_inr` |
| Add a service | `services` | new row: name, stylist, price |
| Remove a service | `services` | untick `active` |
| A stylist's hours | `staff_hours` | `start_time` / `end_time` (one row per day) |
| A holiday or day off | `closures` | new row: the date (leave stylist empty = whole salon) |
| An FAQ answer | `faqs` | `answer` |
| Things you don't offer | `businesses` | `not_offered` |
| The greeting | `businesses` | `greeting` |

Not comfortable doing it yourself? Send me the change on WhatsApp and I'll make it within
the day.

## Messages for you
When a caller asks something the receptionist can't answer, it saves a message. Check them in
**Table Editor → `staff_messages`** (caller's name, number, question), call them back, and set
`status` to `done`.

## If something goes wrong
| What you see | What to do |
|---|---|
| The call page doesn't open, or says "couldn't connect" | Message me. The system needs a restart. |
| The receptionist said something wrong | Note the **time of the call** and send it to me. Every call is recorded (words and every booking action), so I can see exactly what happened and fix it. |
| A booking looks wrong | Check `bookings` in the Table Editor. You can cancel by setting `status` to `cancelled`. |
| It says it's "having a little trouble" | It's briefly busy. Callers can try again in a minute. If it keeps happening, tell me. |

**Contact:** Sourav Prakash · [7077576634/ WhatsApp] · [souravprusty2001@gmail.com]
