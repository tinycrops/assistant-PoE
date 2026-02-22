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
from websockets.exceptions import ConnectionClosed


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
PLAY_MAIN_AUDIO = False


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
reader_lock = asyncio.Lock()
reader_active = asyncio.Event()
processed_function_call_ids: set[str] = set()
current_reader_task: asyncio.Task | None = None


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
        "Return a concise spoken script. No markdown, no bullet points, no preamble. "
        "Keep it under 80 words unless explicitly asked for detail."
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
        "description": "Use GPT-5.2 to produce a final spoken script for complex questions.",
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
        "description": "Read first N lines of a text file in workspace.",
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


def clear_output_queue() -> None:
    while not audio_queue_output.empty():
        try:
            audio_queue_output.get_nowait()
        except asyncio.QueueEmpty:
            break


async def speak_with_fresh_reader(script: str) -> None:
    # Fresh session per response: cleared context, only reads provided script.
    async with reader_lock:
        reader_active.set()
        try:
            log("[reader] start fresh reader session")
            config = {
                "response_modalities": ["AUDIO"],
                "output_audio_transcription": {},
                "temperature": 0,
                "system_instruction": (
                    "You are ReaderAgent. Read the provided script verbatim. "
                    "Do not add, remove, summarize, or modify words. "
                    "Output only spoken audio."
                ),
            }

            async with gemini_client.aio.live.connect(model=GEMINI_MODEL, config=config) as reader:
                await reader.send_client_content(
                    turns={
                        "role": "user",
                        "parts": [{"text": f"Read this script verbatim: {script}"}],
                    },
                    turn_complete=True,
                )

                async for response in reader.receive():
                    server_content = getattr(response, "server_content", None)
                    if not server_content:
                        continue

                    if server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            inline_data = getattr(part, "inline_data", None)
                            if inline_data and isinstance(inline_data.data, bytes):
                                await audio_queue_output.put(inline_data.data)

                    if getattr(server_content, "output_transcription", None):
                        t = server_content.output_transcription.text
                        if t:
                            log(f"[reader_spoken] {t}")

                    if getattr(server_content, "turn_complete", False):
                        log("[reader] done")
                        break
        finally:
            reader_active.clear()


async def reader_then_restart_main(session, script: str) -> None:
    await speak_with_fresh_reader(script)
    log("[handoff] restarting main listener session")
    try:
        await session.close()
    except Exception as exc:  # noqa: BLE001
        log(f"[handoff_error] session close failed: {exc}")


async def start_reader_handoff(session, script: str) -> None:
    global current_reader_task
    if current_reader_task and not current_reader_task.done():
        current_reader_task.cancel()
        try:
            await current_reader_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log(f"[reader_error] {exc}")

    clear_output_queue()
    current_reader_task = asyncio.create_task(reader_then_restart_main(session, script))

    def _done(t: asyncio.Task) -> None:
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc:
            log(f"[reader_error] {exc}")

    current_reader_task.add_done_callback(_done)


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
        # Keep draining mic, but do not upload while ReaderAgent is speaking.
        if reader_active.is_set():
            continue
        await session.send_realtime_input(
            audio={"data": data, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
        )
        chunks_sent += 1
        if chunks_sent % 120 == 0:
            log("[mic] streaming...")


async def play_audio() -> None:
    global playback_stream
    playback_stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=RECEIVE_SAMPLE_RATE,
        output=True,
        frames_per_buffer=CHUNK_SIZE,
    )
    while True:
        bytestream = await audio_queue_output.get()
        try:
            await asyncio.to_thread(playback_stream.write, bytestream)
        except Exception as exc:  # noqa: BLE001
            log(f"[audio_error] playback write failed: {exc}")


async def handle_tool_calls(session, tool_call) -> None:
    function_responses = []

    for fc in tool_call.function_calls:
        if fc.id and fc.id in processed_function_call_ids:
            continue
        if fc.id:
            processed_function_call_ids.add(fc.id)

        args = fc.args if isinstance(fc.args, dict) else {}
        log(f"[tool_call] {fc.name} args={json.dumps(args, ensure_ascii=True)}")

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

        if fc.name == "ask_brain" and result.get("ok") and result.get("script"):
            # ReaderAgent is the only narrator for complex answers.
            await start_reader_handoff(session, result["script"])
            function_responses.append(
                types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response={
                        "ok": True,
                        "status": "handled_by_reader_agent",
                        "do_not_speak_further": True,
                    },
                )
            )
        elif fc.name == "get_time" and result.get("ok") and result.get("time"):
            await start_reader_handoff(session, f"The time is {result['time']}.")
            function_responses.append(
                types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response={
                        "ok": True,
                        "status": "handled_by_reader_agent",
                        "do_not_speak_further": True,
                    },
                )
            )
        else:
            function_responses.append(
                types.FunctionResponse(id=fc.id, name=fc.name, response=result)
            )

    if function_responses:
        async with tool_response_lock:
            await session.send_tool_response(function_responses=function_responses)


async def receive_loop(session) -> None:
    turn_saw_tool_call = False
    turn_text_parts: list[str] = []

    async for response in session.receive():
        server_content = getattr(response, "server_content", None)

        if server_content and server_content.model_turn:
            # Avoid duplicate playback with ReaderAgent; keep main-agent audio muted by default.
            if PLAY_MAIN_AUDIO:
                for part in server_content.model_turn.parts:
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data and isinstance(inline_data.data, bytes):
                        await audio_queue_output.put(inline_data.data)

        if server_content and getattr(server_content, "input_transcription", None):
            text = server_content.input_transcription.text
            if text:
                log(f"[heard] {text}")

        # Suppress main-agent spoken logs in handoff mode; ReaderAgent is authoritative voice.
        if server_content and getattr(server_content, "output_transcription", None):
            t = server_content.output_transcription.text
            if t:
                turn_text_parts.append(t)
                log(f"[model_text] {t}")

        if server_content and getattr(server_content, "turn_complete", False):
            log("[turn] complete")
            if (not turn_saw_tool_call) and turn_text_parts:
                text = "".join(turn_text_parts).strip()
                if text:
                    log("[handoff] reading direct model output via ReaderAgent")
                    await start_reader_handoff(session, text)
            turn_saw_tool_call = False
            turn_text_parts = []

        if getattr(response, "tool_call", None):
            turn_saw_tool_call = True
            asyncio.create_task(handle_tool_calls(session, response.tool_call))


async def run() -> None:
    global audio_stream, playback_stream
    config = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "tools": [{"function_declarations": FUNCTION_DECLARATIONS}],
        "temperature": 0,
        "system_instruction": (
            f"You are MainVoiceAgent. Wake phrase is '{WAKE_PHRASE}'. "
            "If wake phrase is absent, do not respond. "
            "After wake phrase is present, you must call exactly one tool before any spoken response. "
            "For current time/date questions, call get_time immediately. "
            "For complex/general knowledge, call ask_brain. "
            "After ask_brain returns with do_not_speak_further=true, do not provide any further spoken content. "
            "ReaderAgent is the only narrator for that answer. "
            "For local file/time requests, use local tools and answer briefly."
        ),
    }

    reconnect_delay_s = 1.0
    try:
        while True:
            tasks = []
            try:
                async with gemini_client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
                    log(
                        f"Connected. Wake phrase: '{WAKE_PHRASE}'. Brain model: {BRAIN_MODEL}. Mode: handoff"
                    )
                    tasks = [
                        asyncio.create_task(send_audio(session)),
                        asyncio.create_task(receive_loop(session)),
                        asyncio.create_task(play_audio()),
                    ]

                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                    for t in pending:
                        t.cancel()
                    for t in done:
                        exc = t.exception()
                        if exc:
                            raise exc

            except ConnectionClosed as exc:
                log(f"[session_error] {exc}. Reconnecting in {reconnect_delay_s:.1f}s...")
                await asyncio.sleep(reconnect_delay_s)
                continue
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()

                if audio_stream:
                    try:
                        audio_stream.stop_stream()
                        audio_stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                    audio_stream = None
                if playback_stream:
                    try:
                        playback_stream.stop_stream()
                        playback_stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                    playback_stream = None
                log("Disconnected.")
    finally:
        pya.terminate()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log("Interrupted by user.")
