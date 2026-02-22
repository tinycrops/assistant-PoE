import asyncio
import datetime as dt
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI
import pyaudio


# --- Environment ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY in environment or .env")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in environment or .env")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
BRAIN_MODEL = os.getenv("BRAIN_MODEL", "gpt-5.2")
WAKE_PHRASE = os.getenv("WAKE_PHRASE", "computer")
WORKSPACE = Path(os.getenv("WORKSPACE_ROOT", "/home/ath/Desktop/live_api")).resolve()


# --- Audio config ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

pya = pyaudio.PyAudio()
audio_stream = None
playback_stream = None
audio_queue_output: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
tool_response_lock = asyncio.Lock()


gemini_client = genai.Client(api_key=GEMINI_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def ts() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


# --- Local tools ---
def tool_get_time(_: dict) -> dict:
    now = dt.datetime.now().astimezone()
    return {"ok": True, "time": now.strftime("%Y-%m-%d %H:%M:%S %Z")}


def tool_list_files(args: dict) -> dict:
    rel = str(args.get("path", "."))
    target = (WORKSPACE / rel).resolve()
    if WORKSPACE not in target.parents and target != WORKSPACE:
        return {"ok": False, "error": "path outside workspace"}
    if not target.exists() or not target.is_dir():
        return {"ok": False, "error": f"directory not found: {target}"}
    entries = sorted(p.name for p in target.iterdir())[:100]
    return {"ok": True, "path": str(target), "entries": entries}


def tool_read_file_head(args: dict) -> dict:
    rel = args.get("path")
    if not rel:
        return {"ok": False, "error": "missing required arg: path"}

    lines = int(args.get("lines", 40))
    lines = max(1, min(lines, 200))

    target = (WORKSPACE / str(rel)).resolve()
    if WORKSPACE not in target.parents and target != WORKSPACE:
        return {"ok": False, "error": "path outside workspace"}
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": f"file not found: {target}"}

    with target.open("r", encoding="utf-8", errors="replace") as f:
        content = "".join(f.readlines()[:lines])
    return {"ok": True, "path": str(target), "content": content}


def tool_echo(args: dict) -> dict:
    return {"ok": True, "text": str(args.get("text", ""))}


def tool_ask_brain(args: dict) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "missing query"}

    prompt = (
        "You are the reasoning engine behind a voice assistant. "
        "Return a concise, factual spoken script. "
        "No markdown, no bullet points, no preamble. "
        "Keep it under 120 words unless explicitly asked for detail."
    )

    resp = openai_client.responses.create(
        model=BRAIN_MODEL,
        input=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": query},
        ],
        temperature=0.2,
    )
    text = (resp.output_text or "").strip()
    return {
        "ok": True,
        "script": text or "I could not generate an answer.",
        "speak_verbatim": True,
    }


TOOL_HANDLERS = {
    "get_time": tool_get_time,
    "list_files": tool_list_files,
    "read_file_head": tool_read_file_head,
    "echo": tool_echo,
    "ask_brain": tool_ask_brain,
}


FUNCTION_DECLARATIONS = [
    {
        "name": "ask_brain",
        "description": "Use GPT-5.2 to answer complex questions.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "The user question"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_time",
        "description": "Get current local date and time.",
    },
    {
        "name": "list_files",
        "description": "List files/folders in a workspace subdirectory.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING"}},
        },
    },
    {
        "name": "read_file_head",
        "description": "Read the first N lines of a text file in workspace.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING"},
                "lines": {"type": "INTEGER"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "echo",
        "description": "Repeat text.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}},
            "required": ["text"],
        },
    },
]


def run_tool(name: str, args: dict) -> dict:
    fn = TOOL_HANDLERS.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return fn(args)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"tool exception: {exc}"}


async def send_audio(session) -> None:
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

    kwargs = {"exception_on_overflow": False}
    chunks_sent = 0
    while True:
        data = await asyncio.to_thread(audio_stream.read, CHUNK_SIZE, **kwargs)
        await session.send_realtime_input(
            audio={"data": data, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
        )
        chunks_sent += 1
        if chunks_sent % 40 == 0:
            log("[mic] streaming...")


async def play_audio() -> None:
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
        try:
            # Use one direct write path to reduce thread churn around PortAudio.
            playback_stream.write(bytestream)
        except Exception as exc:  # noqa: BLE001
            log(f"[audio_error] playback write failed: {exc}")


async def handle_tool_calls(session, tool_call) -> None:
    function_responses = []
    for fc in tool_call.function_calls:
        args = fc.args if isinstance(fc.args, dict) else {}
        log(f"[tool_call] {fc.name} args={json.dumps(args, ensure_ascii=True)}")

        # Run heavy brain call with timeout so loop can't hang forever.
        if fc.name == "ask_brain":
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(run_tool, fc.name, args),
                    timeout=20,
                )
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "ask_brain timed out"}
        else:
            result = run_tool(fc.name, args)

        log(f"[tool_result] {json.dumps(result, ensure_ascii=True)[:1000]}")
        function_responses.append(
            types.FunctionResponse(id=fc.id, name=fc.name, response=result)
        )

    async with tool_response_lock:
        await session.send_tool_response(function_responses=function_responses)


async def receive_loop(session) -> None:
    async for response in session.receive():
        server_content = getattr(response, "server_content", None)

        if server_content and server_content.model_turn:
            for part in server_content.model_turn.parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and isinstance(inline_data.data, bytes):
                    if audio_queue_output.full():
                        try:
                            audio_queue_output.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    audio_queue_output.put_nowait(inline_data.data)

        if server_content and getattr(server_content, "input_transcription", None):
            text = server_content.input_transcription.text
            if text:
                log(f"[heard] {text}")

        if server_content and getattr(server_content, "output_transcription", None):
            text = server_content.output_transcription.text
            if text:
                log(f"[spoken] {text}")

        if server_content and getattr(server_content, "turn_complete", False):
            log("[turn] complete")

        if getattr(response, "tool_call", None):
            asyncio.create_task(handle_tool_calls(session, response.tool_call))


async def run() -> None:
    config = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "tools": [{"function_declarations": FUNCTION_DECLARATIONS}],
        "temperature": 0,
        "system_instruction": (
            f"You are a voice orchestrator. Wake phrase is '{WAKE_PHRASE}'. "
            "If wake phrase is absent, do not respond. "
            "For complex/general knowledge questions, call ask_brain. "
            "You may briefly acknowledge first (for example: 'Hmm, let me think about that.'). "
            "After ask_brain returns, read its returned 'script' to the user. "
            "If 'speak_verbatim' is true, read the script verbatim. "
            "Do not summarize or omit it. "
            "After other tool responses, give a concise spoken answer. "
            "For local file/time requests, use the available local tools."
        ),
    }

    tasks = []
    try:
        async with gemini_client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
            log(f"Connected. Wake phrase: '{WAKE_PHRASE}'. Brain model: {BRAIN_MODEL}.")
            tasks = [
                asyncio.create_task(send_audio(session)),
                asyncio.create_task(receive_loop(session)),
                asyncio.create_task(play_audio()),
            ]
            await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

        if audio_stream:
            audio_stream.stop_stream()
            audio_stream.close()
        if playback_stream:
            playback_stream.stop_stream()
            playback_stream.close()
        pya.terminate()
        log("Disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log("Interrupted by user.")
