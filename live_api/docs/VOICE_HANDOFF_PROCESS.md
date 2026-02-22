# Voice Handoff Process (Detailed)

This document explains the full behavior of `voice_tool_button_gpt_brain_handoff.py` in implementation-level detail.

## 1) Problem This Script Solves

The system separates responsibilities across two model roles:

- `MainVoiceAgent` (Gemini Live): always-on listener, wake-phrase gate, tool caller.
- `Brain` (`gpt-5.2` via OpenAI Responses API): higher-quality text reasoning.
- `ReaderAgent` (Gemini Live): isolated, fresh-session voice narrator for final speech.

The design goal is stability and clarity:

- Main session listens and routes.
- Brain thinks (for complex prompts).
- Reader speaks.
- Main session is restarted after a handoff narration, so the next user turn begins from a known-clean state.

## 2) Runtime Dependencies

From `requirements.txt` and runtime imports:

- `google-genai`
- `openai`
- `python-dotenv`
- `pyaudio`
- `websockets`

Audio stack assumptions:

- Input: 16kHz mono PCM, 16-bit (`paInt16`)
- Output: 24kHz mono PCM, 16-bit (`paInt16`)

On Linux, ALSA/JACK warnings are common and often non-fatal if audio still works.

## 3) Environment Contract

Required:

- `GEMINI_API_KEY`
- `OPENAI_API_KEY`

Optional:

- `GEMINI_MODEL` (default: `gemini-2.5-flash-native-audio-preview-12-2025`)
- `BRAIN_MODEL` (default: `gpt-5.2`)
- `WAKE_PHRASE` (default: `computer`)
- `WORKSPACE_ROOT` (default: `/home/ath/Desktop/live_api`)

Hard-coded in this script:

- `PLAY_MAIN_AUDIO = False` to prevent dual narrator overlap.

## 4) High-Level Architecture

### 4.1 Main Live Loop

`run()` creates one Gemini Live session with tools and transcription enabled.

Concurrent tasks:

- `send_audio(session)`: mic capture + realtime audio upload
- `receive_loop(session)`: server events, transcriptions, tool calls
- `play_audio()`: writes queued PCM to playback stream

The loop auto-reconnects on websocket closures (`ConnectionClosed`).

### 4.2 Tooling Layer

Main agent can call local functions:

- `ask_brain(query)`
- `get_time()`
- `list_files(path)`
- `read_file_head(path, lines)`
- `echo(text)`

`run_tool()` dispatches and captures exceptions as structured error payloads.

### 4.3 Handoff Narration Layer

For selected tool outputs (`ask_brain`, `get_time`) the script:

1. starts a dedicated `ReaderAgent` live session,
2. reads a supplied script verbatim,
3. closes the main session when reader completes,
4. triggers reconnect to restore fresh listening state.

## 5) Shared State and Concurrency Controls

Global synchronization/state:

- `audio_queue_output`: bounded queue of PCM bytes for playback (`maxsize=256`)
- `tool_response_lock`: serializes `send_tool_response()` writes
- `reader_lock`: ensures only one ReaderAgent session at a time
- `reader_active`: event signaling "reader speaking" (mic is drained but not uploaded)
- `processed_function_call_ids`: dedupe set for repeated tool call ids
- `current_reader_task`: currently active reader handoff task

Why these exist:

- Prevent duplicate tool handling.
- Prevent overlapping voice sessions.
- Prevent microphone upload while the script is being narrated.
- Avoid queue cross-contamination between old/new responses.

## 6) Audio Pipeline Details

### 6.1 Capture

`send_audio(session)`:

- opens default input device,
- reads `CHUNK_SIZE=1024` frames in a loop,
- skips upstream upload if `reader_active` is set,
- sends realtime input with `audio/pcm;rate=16000`.

The mic is still drained even while reader speaks. This avoids local input buffer overflows.

### 6.2 Playback

`play_audio()`:

- opens output stream at 24kHz,
- pulls byte chunks from `audio_queue_output`,
- writes bytes to device in a thread via `asyncio.to_thread`.

### 6.3 Queue Hygiene

`clear_output_queue()` drains pending audio before a new handoff narration starts, so stale fragments are not mixed into the next spoken result.

## 7) Main Agent Behavior Contract

The `system_instruction` for `MainVoiceAgent` encodes these constraints:

- Ignore speech unless wake phrase is present.
- After wake phrase, call exactly one tool before speaking.
- Use `get_time` immediately for time/date requests.
- Use `ask_brain` for complex/general knowledge.
- After `ask_brain` returns with `do_not_speak_further=true`, MainVoiceAgent must not continue narration.

This policy reduces accidental direct model speech in the main loop.

## 8) Tool Call Handling Path

`handle_tool_calls(session, tool_call)` performs:

1. dedupe by `fc.id` using `processed_function_call_ids`,
2. log call name and args,
3. execute tool (`ask_brain` has explicit 20s timeout),
4. log tool result,
5. branch by tool type:

- `ask_brain` success:
  - `start_reader_handoff(session, result["script"])`
  - return synthetic tool response: `handled_by_reader_agent`

- `get_time` success:
  - `start_reader_handoff(session, f"The time is ...")`
  - same synthetic response shape

- all else:
  - pass raw tool output back to main session

6. send aggregated tool responses only if non-empty (`if function_responses:` guard).

The non-empty guard prevents invalid/unsupported operations that previously triggered policy-violation closes.

## 9) Reader Handoff Flow (Critical)

### 9.1 `start_reader_handoff(session, script)`

- cancels prior reader task if still active,
- waits for cancellation completion,
- clears output queue,
- starts `reader_then_restart_main(session, script)` task,
- attaches done callback for error logging.

### 9.2 `reader_then_restart_main(session, script)`

- calls `speak_with_fresh_reader(script)`,
- after narration completes, executes `await session.close()` on MainVoiceAgent session.

This intentional close forces the outer reconnect loop to establish a fresh listener session for the next command.

### 9.3 `speak_with_fresh_reader(script)`

- acquires `reader_lock`, sets `reader_active`,
- starts new Gemini Live session with strict "read verbatim" instruction,
- sends one user turn: "Read this script verbatim: ...",
- streams reader audio chunks to `audio_queue_output`,
- logs output transcription as `[reader_spoken]`,
- exits on reader `turn_complete`, clears `reader_active` in `finally`.

## 10) Receive Loop Behavior

`receive_loop(session)` tracks per-turn state:

- `turn_saw_tool_call`
- `turn_text_parts`

Steps per event:

- log `[heard]` from input transcription.
- collect `output_transcription` text into `turn_text_parts`.
- on `tool_call`: set `turn_saw_tool_call=True`, dispatch async handler.
- on `turn_complete`:
  - if no tool call but text exists, invoke reader handoff on collected text,
  - reset turn state.

This fallback handles cases where model emits text directly despite instructions.

## 11) Reconnect Lifecycle

`run()` loops forever:

- connect session,
- run three core tasks,
- wait until first exception,
- cancel pending tasks,
- on `ConnectionClosed`, sleep 1s and reconnect,
- close/release audio streams every cycle,
- terminate PyAudio only when process exits.

This makes policy errors and network closures recoverable without manual restart.

## 12) Why Earlier Failures Happened

Observed classes of failures and fixes:

1. Overlapping speech (main + reader)
- Cause: main output and reader output both reached playback.
- Fix: `PLAY_MAIN_AUDIO=False`, reader-only narration in handoff mode.

2. Duplicated/intelligible corruption
- Cause: repeated tool calls + stale queue content + overlapping sessions.
- Fix: function-call dedupe, queue flush before reader, single-reader task control.

3. Mid-response cutoffs
- Cause: websocket keepalive/policy closures and no recovery loop.
- Fix: reconnect loop around entire session lifecycle.

4. Crash on policy violation (`1008`)
- Cause: unhandled `ConnectionClosedError` path or unsupported operation edge cases.
- Fix: catch base `ConnectionClosed`; avoid empty `send_tool_response`.

5. Not listening after answer
- Cause: post-handoff main session state could remain degraded.
- Fix: explicit `session.close()` after reader completes, then reconnect cleanly.

## 13) Logging Guide

Common log meanings:

- `[heard] ...`: streaming transcription of user audio input.
- `[tool_call] ...`: main model requested a local tool.
- `[tool_result] ...`: local tool output.
- `[reader] start fresh reader session`: narration handoff began.
- `[reader_spoken] ...`: transcribed output from reader.
- `[reader] done`: reader turn completed.
- `[handoff] restarting main listener session`: intentional close/reconnect trigger.
- `[session_error] ... Reconnecting ...`: websocket closed; auto-recovery.
- `[mic] streaming...`: periodic health indicator from audio upload loop.

## 14) Operational Test Protocol

Use this repeatable check:

1. Start script.
2. Ask simple request: `computer what time is it`.
3. Confirm spoken response appears and completes.
4. Wait for reconnect log and ask second request.
5. Ask complex request requiring `ask_brain`.
6. Verify handoff narration and subsequent reconnect.
7. Repeat multiple cycles without restarting process.

Success criteria:

- no overlapping speech,
- no garbling from stale chunks,
- no dead listener state after response,
- no fatal traceback on websocket close.

## 15) Known Non-Blocking Noise

ALSA/JACK startup warnings shown on this machine are common in mixed audio environments:

- `Unknown PCM ...`
- `jack server is not running ...`
- `/dev/dsp` not found

If microphone capture and playback still function, these messages can be treated as informational.

## 16) Current Tradeoffs

- Forced reconnect after each reader handoff adds slight latency but gives deterministic session recovery.
- Reader verbatim speaking can be long if script is long; `ask_brain` prompt currently constrains length.
- Time responses are routed through reader for consistency, not fastest possible response.

## 17) Next Hardening Options

If future instability appears, prioritize in this order:

1. Playback underrun hardening
- Reopen output stream on repeated write errors.
- Tune `frames_per_buffer` and queue size for device.

2. Session state metrics
- Add counters for reconnects, tool latency, handoff duration.

3. Watchdog
- If no `[heard]` activity for N seconds while mic is streaming, force session recycle.

4. Structured logs
- Emit JSON logs for machine parsing and timeline analysis.

## 18) File References

- Core implementation: `voice_tool_button_gpt_brain_handoff.py`
- Starter baseline docs: `README.md`
- This detailed process doc: `docs/VOICE_HANDOFF_PROCESS.md`
