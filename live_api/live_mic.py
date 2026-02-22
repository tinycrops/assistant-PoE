import asyncio
import os

from dotenv import load_dotenv
from google import genai
import pyaudio


# --- environment ---
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("Missing GEMINI_API_KEY in environment or .env")


# --- pyaudio config ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

pya = pyaudio.PyAudio()


# --- Live API config ---
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": "You are a helpful and friendly AI assistant.",
}

client = genai.Client(api_key=api_key)

audio_queue_output: asyncio.Queue[bytes] = asyncio.Queue()
audio_queue_mic: asyncio.Queue[dict] = asyncio.Queue(maxsize=20)
audio_stream = None
playback_stream = None


async def listen_audio() -> None:
    """Listens for audio and puts it into the mic audio queue."""
    global audio_stream
    mic_info = pya.get_default_input_device_info()
    audio_stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=SEND_SAMPLE_RATE,
        input=True,
        input_device_index=mic_info["index"],
        frames_per_buffer=CHUNK_SIZE,
    )
    kwargs = {"exception_on_overflow": False} if __debug__ else {}
    while True:
        data = await asyncio.to_thread(audio_stream.read, CHUNK_SIZE, **kwargs)
        await audio_queue_mic.put(
            {"data": data, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
        )


async def send_realtime(session) -> None:
    """Sends audio from the mic audio queue to the GenAI session."""
    while True:
        msg = await audio_queue_mic.get()
        await session.send_realtime_input(audio=msg)


async def receive_audio(session) -> None:
    """Receives responses from GenAI and puts audio data into the speaker audio queue."""
    async for response in session.receive():
        server_content = response.server_content
        if server_content and server_content.model_turn:
            for part in server_content.model_turn.parts:
                if part.inline_data and isinstance(part.inline_data.data, bytes):
                    audio_queue_output.put_nowait(part.inline_data.data)

        # Only flush buffered playback if the server explicitly interrupted this turn.
        if server_content and getattr(server_content, "interrupted", False):
            while not audio_queue_output.empty():
                audio_queue_output.get_nowait()


async def play_audio() -> None:
    """Plays audio from the speaker audio queue."""
    global playback_stream
    playback_stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=RECEIVE_SAMPLE_RATE,
        output=True,
    )
    while True:
        bytestream = await audio_queue_output.get()
        await asyncio.to_thread(playback_stream.write, bytestream)


async def run() -> None:
    """Main function to run the audio loop."""
    tasks = []
    try:
        async with client.aio.live.connect(model=MODEL, config=CONFIG) as live_session:
            print("Connected to Gemini. Start speaking!")
            tasks = [
                asyncio.create_task(send_realtime(live_session)),
                asyncio.create_task(listen_audio()),
                asyncio.create_task(receive_audio(live_session)),
                asyncio.create_task(play_audio()),
            ]
            await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

        if audio_stream:
            audio_stream.stop_stream()
            audio_stream.close()
        if playback_stream:
            playback_stream.stop_stream()
            playback_stream.close()
        pya.terminate()
        print("\nConnection closed.")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Interrupted by user.")
