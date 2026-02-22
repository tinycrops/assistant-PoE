# Gemini Live API Mic Starter (Python)

Minimal starter for Gemini Live API based on the `mic-stream` example.

## Prereqs

- Python 3.11+
- A Gemini API key with Live API access
- Linux audio tools/libs (for mic + playback, PyAudio):
  - Debian/Ubuntu:
    - `sudo apt-get install -y portaudio19-dev python3-pyaudio alsa-utils`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set GEMINI_API_KEY
```

## Run

```bash
source .venv/bin/activate
python live_mic.py
```

Speak into your mic. You should hear Gemini audio replies.

## Notes

- Input format: `audio/pcm;rate=16000` (mono, 16-bit PCM)
- Output format: 24kHz mono PCM played through PyAudio
- Default model in this starter: `gemini-2.5-flash-native-audio-preview-12-2025`
- Override model via `GEMINI_MODEL` in `.env`
- Use headphones to avoid feedback/echo loops

## Advanced Handoff Script

Detailed internals and debugging guide for the tool-handoff architecture:

- `docs/VOICE_HANDOFF_PROCESS.md`
