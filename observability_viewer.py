#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "logs" / "build_intel_runs"
EVENTS_PATH = ROOT / "logs" / "dspy_observability.jsonl"

HTML = """<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>Build Observability</title>
<style>
:root { --bg:#0f172a; --panel:#111827; --muted:#94a3b8; --text:#e5e7eb; --accent:#22d3ee; --line:#1f2937; }
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-monospace,SFMono-Regular,Menlo,monospace; background: linear-gradient(180deg,#0b1226,#111827); color: var(--text); }
.container { display:grid; grid-template-columns:360px 1fr; min-height:100vh; }
.sidebar { border-right:1px solid var(--line); padding:12px; background:#0b1220; overflow:auto; }
.main { padding:16px; overflow:auto; }
.h { margin:0 0 8px 0; font-size:16px; }
.m { color: var(--muted); font-size:12px; }
.item { border:1px solid var(--line); border-radius:8px; padding:10px; margin:8px 0; cursor:pointer; }
.item:hover,.item.active { border-color: var(--accent); background: rgba(34,211,238,0.08); }
.kv { display:grid; grid-template-columns:180px 1fr; gap:6px 10px; margin:8px 0 16px; }
.card { border:1px solid var(--line); border-radius:10px; padding:12px; margin:10px 0; background: rgba(17,24,39,0.7); }
pre { white-space: pre-wrap; word-break: break-word; margin:0; font-size:12px; line-height:1.4; }
.badge { display:inline-block; padding:2px 8px; border:1px solid var(--line); border-radius:999px; margin-right:6px; font-size:11px; }
.row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:8px; }
button { background:#0b1220; color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 10px; cursor:pointer; }
button:hover { border-color: var(--accent); }
</style>
</head>
<body>
<div class=\"container\">
  <aside class=\"sidebar\">
    <h1 class=\"h\">Run Explorer</h1>
    <div class=\"m\" id=\"meta\">Loading...</div>
    <div id=\"runList\"></div>
  </aside>
  <main class=\"main\">
    <div class=\"row\">
      <button id=\"refreshBtn\">Refresh</button>
      <span class=\"m\">Includes prompts, model output, sanitized card, and DSPy events.</span>
    </div>
    <div id=\"content\" class=\"m\">Select a run from the left.</div>
  </main>
</div>
<script>
const runListEl = document.getElementById('runList');
const contentEl = document.getElementById('content');
const metaEl = document.getElementById('meta');
const refreshBtn = document.getElementById('refreshBtn');
let runs = [];
let selected = null;

function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

async function loadRuns() {
  const resp = await fetch('/api/runs');
  const data = await resp.json();
  runs = data.runs || [];
  metaEl.textContent = `${runs.length} run artifacts found`;
  runListEl.innerHTML = runs.map((r, i) => `
    <div class=\"item ${selected===r.file?'active':''}\" data-file=\"${esc(r.file)}\">
      <div><strong>${esc(r.file)}</strong></div>
      <div class=\"m\">${esc(r.generated_at_utc || '-')}</div>
      <div class=\"m\">${esc(r.model || '-')} | ${esc(r.status || '-')}</div>
      <div class=\"m\">${esc(r.character || 'Unknown')} (${esc(r.realm || '?')})</div>
    </div>
  `).join('');
  for (const el of runListEl.querySelectorAll('.item')) {
    el.addEventListener('click', () => showRun(el.getAttribute('data-file')));
  }
  if (!selected && runs[0]) {
    showRun(runs[0].file);
  }
}

async function showRun(file) {
  selected = file;
  await loadRuns();
  const r = await (await fetch('/api/run?file=' + encodeURIComponent(file))).json();
  const events = await (await fetch('/api/events?run_id=' + encodeURIComponent(r.run_id || ''))).json();
  const obs = r.observability || {};
  contentEl.innerHTML = `
    <div class=\"card\">
      <div class=\"row\">
        <span class=\"badge\">run_id: ${esc(r.run_id || '-')}</span>
        <span class=\"badge\">model: ${esc(r.model || '-')}</span>
        <span class=\"badge\">tokens: ${esc(r.actual_total_tokens ?? '-')}</span>
        <span class=\"badge\">obs_events: ${esc(events.events?.length ?? 0)}</span>
      </div>
      <div class=\"kv\">
        <div class=\"m\">Generated</div><div>${esc(r.generated_at_utc || '-')}</div>
        <div class=\"m\">Snapshot</div><div>${esc(r.snapshot_path || '-')}</div>
        <div class=\"m\">Prompt Version</div><div>${esc(r.prompt_version || '-')}</div>
        <div class=\"m\">MLflow Run</div><div>${esc(obs.mlflow_run_id || '-')}</div>
      </div>
    </div>

    <div class=\"card\"><strong>System Prompt</strong><pre>${esc(r.system_prompt || '')}</pre></div>
    <div class=\"card\"><strong>User Prompt</strong><pre>${esc(r.user_prompt || '')}</pre></div>
    <div class=\"card\"><strong>Model Card</strong><pre>${esc(JSON.stringify(r.model_card || {}, null, 2))}</pre></div>
    <div class=\"card\"><strong>Sanitized Card</strong><pre>${esc(JSON.stringify(r.sanitized_card || {}, null, 2))}</pre></div>
    <div class=\"card\"><strong>DSPy Events</strong><pre>${esc(JSON.stringify(events.events || [], null, 2))}</pre></div>
  `;
}

refreshBtn.addEventListener('click', loadRuns);
loadRuns();
</script>
</body>
</html>
"""


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _list_runs() -> list[dict]:
    rows: list[dict] = []
    if not RUNS_DIR.exists():
        return rows
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        doc = _load_json(path)
        summary = doc.get("summary", {}) if isinstance(doc, dict) else {}
        character = summary.get("character", {}) if isinstance(summary, dict) else {}
        token_budget = doc.get("token_budget", {}) if isinstance(doc, dict) else {}
        rows.append(
            {
                "file": path.name,
                "generated_at_utc": doc.get("generated_at_utc"),
                "model": doc.get("model"),
                "status": "posted" if doc.get("posted") else "dry-run",
                "character": character.get("name"),
                "realm": character.get("realm"),
                "actual_total_tokens": token_budget.get("actual_total_tokens"),
            }
        )
    return rows


def _events_for_run(run_id: str) -> list[dict]:
    events: list[dict] = []
    if not run_id or not EVENTS_PATH.exists():
        return events
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("run_id") == run_id:
            events.append(row)
    return events


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, text: str) -> None:
        raw = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/":
            self._html(HTML)
            return

        if parsed.path == "/api/runs":
            self._json({"runs": _list_runs()})
            return

        if parsed.path == "/api/run":
            file_name = (query.get("file") or [""])[0]
            if not file_name or "/" in file_name or ".." in file_name:
                self._json({"error": "invalid file"}, status=400)
                return
            path = RUNS_DIR / file_name
            if not path.exists():
                self._json({"error": "not found"}, status=404)
                return
            doc = _load_json(path)
            token_budget = doc.get("token_budget", {}) if isinstance(doc, dict) else {}
            response = {
                "run_id": None,
                "generated_at_utc": doc.get("generated_at_utc"),
                "prompt_version": doc.get("prompt_version"),
                "model": doc.get("model"),
                "snapshot_path": doc.get("snapshot_path"),
                "system_prompt": doc.get("system_prompt"),
                "user_prompt": doc.get("user_prompt"),
                "model_card": doc.get("model_card"),
                "sanitized_card": doc.get("sanitized_card"),
                "observability": doc.get("observability", {}),
                "actual_total_tokens": token_budget.get("actual_total_tokens"),
            }
            response["run_id"] = _infer_run_id_from_response_id((doc.get("raw_openai_response", {}) or {}).get("id"))
            self._json(response)
            return

        if parsed.path == "/api/events":
            run_id = (query.get("run_id") or [""])[0]
            self._json({"events": _events_for_run(run_id)})
            return

        self._json({"error": "not found"}, status=404)


def _infer_run_id_from_response_id(response_id: str | None) -> str | None:
    if not response_id or not EVENTS_PATH.exists():
        return None
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        if response_id not in line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = row.get("run_id")
        if run_id:
            return str(run_id)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Local observability viewer for build-intel runs")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5051)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Observability viewer running at http://{args.host}:{args.port}")
    print(f"Reading runs from: {RUNS_DIR}")
    print(f"Reading events from: {EVENTS_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
