"""Per-call logging: transcript, every tool call and its result, latency.

Writes go through a queue so logging never slows down the conversation.
To explain a bad call to the customer:  python scripts/show_call.py <call_id>
"""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger("call_log")


class CallLogger:
    def __init__(self, pool, business_id, room_name: str):
        self.pool = pool
        self.business_id = business_id
        self.room_name = room_name
        self.call_id = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._writer: asyncio.Task | None = None

    async def start(self):
        self.call_id = await self.pool.fetchval(
            "INSERT INTO calls (business_id, room_name) VALUES ($1, $2) RETURNING id",
            self.business_id, self.room_name)
        self._writer = asyncio.create_task(self._write_loop())
        return self.call_id

    def log(self, kind: str, payload: dict):
        """Non-blocking: queue the event and return immediately."""
        self._queue.put_nowait((kind, payload))

    async def _write_loop(self):
        while True:
            kind, payload = await self._queue.get()
            if kind is None:
                break
            try:
                await self.pool.execute(
                    "INSERT INTO call_events (call_id, kind, payload) VALUES ($1, $2, $3::jsonb)",
                    self.call_id, kind, json.dumps(payload, default=str))
            except Exception:
                log.exception("failed to write call event")

    async def close(self):
        if self._writer is None:
            return
        self._queue.put_nowait((None, None))
        await self._writer
        await self.pool.execute("UPDATE calls SET ended_at = now() WHERE id = $1", self.call_id)
