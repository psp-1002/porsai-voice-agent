# One image for everything:
#  - Hugging Face Spaces runs it as ONE container (start.sh starts all three processes)
#  - docker-compose runs the same image as three services (web, agent, tts)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces run containers as user 1000
RUN useradd -m -u 1000 user
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Piper voice model (open source, MIT): downloaded at build time, not stored in git
RUN mkdir -p /app/models && curl -sSLf \
      https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-lessac-medium.tar.gz \
    | tar xz -C /app/models

COPY --chown=user . .
RUN chown -R user /app
USER user
ENV HOME=/home/user PORT=7860

EXPOSE 7860
CMD ["bash", "start.sh"]
