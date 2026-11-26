---
title: Sunrise Salon Voice Receptionist
emoji: 📞
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# Voice Receptionist (open source)

A browser voice receptionist for any appointment-based business. The caller opens the
call page, presses **Call** and talks. The agent answers questions from the business's
data and books, reschedules and cancels appointments.

> Full README (architecture, latency numbers, tests, scaling note, what's missing) is being written.

## Run locally

```bash
cp .env.example .env          # fill in your keys
docker compose up -d --build  # web on http://localhost:8000, agent, Piper TTS
```
