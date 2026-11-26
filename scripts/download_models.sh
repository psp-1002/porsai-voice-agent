#!/usr/bin/env bash
# Download the Piper voice model for running the TTS server outside Docker.
set -e
mkdir -p models
curl -sSLf https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-lessac-medium.tar.gz \
  | tar xz -C models
ls -la models
