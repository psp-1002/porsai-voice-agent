#!/usr/bin/env bash
# All-in-one start (used on Hugging Face Spaces): TTS server, web server, agent worker.
# If any of them dies, exit so the platform restarts the container.
set -u

uvicorn app.tts_server:app --host 127.0.0.1 --port 8890 &
uvicorn web.server:app --host 0.0.0.0 --port "${PORT:-7860}" &

# Wait until the TTS server is up before the agent takes calls
for i in $(seq 1 30); do curl -sf http://127.0.0.1:8890/health >/dev/null && break; sleep 1; done

python -m app.agent start &

wait -n
echo "A process exited; stopping container so it restarts."
exit 1
