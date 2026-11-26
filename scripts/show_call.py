"""Print a call's full log: transcript, every tool call + result, latency.

  python scripts/show_call.py            -> list the 10 latest calls
  python scripts/show_call.py <call_id>  -> show one call (a prefix of the id is fine)
"""
import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv(".env")


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        if len(sys.argv) < 2:
            rows = await conn.fetch(
                """SELECT c.id, b.name, c.started_at, c.ended_at,
                          (SELECT count(*) FROM call_events e WHERE e.call_id = c.id AND kind = 'user') AS turns
                     FROM calls c JOIN businesses b ON b.id = c.business_id
                    ORDER BY c.started_at DESC LIMIT 10""")
            for r in rows:
                print(f"{r['id']}  {r['name']:<20} {r['started_at']:%Y-%m-%d %H:%M}  turns={r['turns']}")
            return
        call = await conn.fetchrow("SELECT c.*, b.name, b.timezone FROM calls c JOIN businesses b "
                                   "ON b.id = c.business_id WHERE c.id::text LIKE $1 || '%'",
                                   sys.argv[1])
        if call is None:
            print("No such call"); return
        print(f"Call {call['id']} to {call['name']} at {call['started_at']}\n")
        for e in await conn.fetch("SELECT ts, kind, payload FROM call_events WHERE call_id = $1 "
                                  "ORDER BY ts, id", call["id"]):
            p = json.loads(e["payload"])
            t = f"{e['ts']:%H:%M:%S}"
            if e["kind"] in ("user", "agent"):
                who = "CALLER" if e["kind"] == "user" else "AGENT "
                cut = "  [interrupted]" if p.get("interrupted") else ""
                print(f"{t}  {who}  {p.get('text')}{cut}")
            elif e["kind"] == "tool_call":
                print(f"{t}    -> tool {p['name']}({p['arguments']})")
            elif e["kind"] == "tool_result":
                print(f"{t}    <- {p['name']}: {p['output']}")
            elif e["kind"] == "latency":
                print(f"{t}    (latency {p})")
            else:
                print(f"{t}    [{e['kind']}] {p}")
    finally:
        await conn.close()


asyncio.run(main())
