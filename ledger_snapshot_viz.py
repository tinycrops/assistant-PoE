#!/usr/bin/env python3
"""Generate a simple HTML report for character ledger snapshot history."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CHARACTERS_DIR = ROOT / "characters"

SERIES = [
    ("Life", "defence.life", "#c2410c"),
    ("Energy Shield", "defence.energy_shield", "#0f766e"),
    ("Total DPS", "offence.total_dps", "#1d4ed8"),
    ("Fire Resist %", "defence.fire_resist_percent", "#dc2626"),
    ("Cold Resist %", "defence.cold_resist_percent", "#0891b2"),
    ("Lightning Resist %", "defence.lightning_resist_percent", "#ca8a04"),
    ("Chaos Resist %", "defence.chaos_resist_percent", "#7c3aed"),
]


def env_first(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value is not None and value != "":
            return value
    return default


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render ledger snapshot history as an HTML report.")
    parser.add_argument("--character", default=env_first("DEFAULT_CHARACTER"), help="Character name")
    parser.add_argument("--output", default=None, help="Output HTML path")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def format_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def format_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def svg_line_chart(entries: list[dict[str, Any]], label: str, stat_key: str, color: str) -> str:
    points: list[tuple[int, float, str]] = []
    for idx, entry in enumerate(sorted(entries, key=lambda item: str(item.get("captured_at_utc", "")))):
        stats = entry.get("stats", {})
        if not isinstance(stats, dict):
            continue
        value = stats.get(stat_key)
        if isinstance(value, (int, float)):
            points.append((idx, float(value), str(entry.get("captured_at_utc", ""))))

    if not points:
        return (
            f"<section class='chart-card'><h3>{html.escape(label)}</h3>"
            "<p class='empty'>No captured values yet.</p></section>"
        )

    width = 720
    height = 220
    padding = 28
    min_value = min(value for _, value, _ in points)
    max_value = max(value for _, value, _ in points)
    spread = max(max_value - min_value, 1.0)
    x_span = max(len(points) - 1, 1)

    coords: list[str] = []
    markers: list[str] = []
    for idx, value, timestamp in points:
        x = padding + ((width - padding * 2) * idx / x_span)
        y = height - padding - ((height - padding * 2) * (value - min_value) / spread)
        coords.append(f"{x:.1f},{y:.1f}")
        markers.append(
            "<circle "
            f"cx='{x:.1f}' cy='{y:.1f}' r='3.5' fill='{color}'>"
            f"<title>{html.escape(format_timestamp(timestamp))}: {html.escape(format_value(value))}</title>"
            "</circle>"
        )

    baseline_y = height - padding
    return (
        f"<section class='chart-card'><h3>{html.escape(label)}</h3>"
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{html.escape(label)} chart'>"
        f"<line x1='{padding}' y1='{baseline_y}' x2='{width - padding}' y2='{baseline_y}' class='axis' />"
        f"<line x1='{padding}' y1='{padding}' x2='{padding}' y2='{baseline_y}' class='axis' />"
        f"<polyline fill='none' stroke='{color}' stroke-width='3' points='{' '.join(coords)}' />"
        f"{''.join(markers)}"
        f"<text x='{padding}' y='20' class='axis-label'>min {html.escape(format_value(min_value))}</text>"
        f"<text x='{width - 130}' y='20' class='axis-label'>max {html.escape(format_value(max_value))}</text>"
        "</svg></section>"
    )


def table_rows(entries: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for entry in entries:
        stats = entry.get("stats", {}) if isinstance(entry.get("stats"), dict) else {}
        counts = entry.get("inventory_counts", {}) if isinstance(entry.get("inventory_counts"), dict) else {}
        artifacts = entry.get("artifacts", {}) if isinstance(entry.get("artifacts"), dict) else {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(format_timestamp(entry.get('captured_at_utc')))}</td>"
            f"<td>{html.escape(format_value(stats.get('defence.life')))}</td>"
            f"<td>{html.escape(format_value(stats.get('defence.energy_shield')))}</td>"
            f"<td>{html.escape(format_value(stats.get('offence.total_dps')))}</td>"
            f"<td>{html.escape(format_value(counts.get('total_items')))}</td>"
            f"<td>{html.escape(str(artifacts.get('snapshot_path', 'n/a')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def build_html(ledger: dict[str, Any], character_name: str) -> str:
    entries = ledger.get("snapshot_history", [])
    if not isinstance(entries, list):
        entries = []
    entries = [entry for entry in entries if isinstance(entry, dict)]

    latest_snapshot = ledger.get("latest_snapshot", {})
    observations = ledger.get("latest_observations", [])
    if not isinstance(observations, list):
        observations = []

    charts = "".join(svg_line_chart(entries, label, stat_key, color) for label, stat_key, color in SERIES)
    observation_items = "".join(f"<li>{html.escape(str(item))}</li>" for item in observations[:8])
    latest_captured = format_timestamp(latest_snapshot.get("captured_at_utc"))
    latest_stats = latest_snapshot.get("stats", {}) if isinstance(latest_snapshot.get("stats"), dict) else {}

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(character_name)} Snapshot Report</title>
  <style>
    :root {{
      --bg: #f4efe7;
      --panel: #fffaf3;
      --ink: #1c1917;
      --muted: #57534e;
      --line: #d6d3d1;
      --accent: #9a3412;
    }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background:
        radial-gradient(circle at top left, rgba(154, 52, 18, 0.14), transparent 28%),
        linear-gradient(180deg, #f8f3ea 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    h1, h2, h3 {{
      font-weight: 600;
      margin: 0 0 12px;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(154, 52, 18, 0.95), rgba(41, 37, 36, 0.92));
      color: #fff7ed;
      border-radius: 20px;
      padding: 24px;
      box-shadow: 0 18px 50px rgba(28, 25, 23, 0.16);
    }}
    .hero p {{
      color: rgba(255, 247, 237, 0.86);
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}
    .stat-card, .chart-card, .table-card, .notes-card {{
      background: var(--panel);
      border: 1px solid rgba(87, 83, 78, 0.16);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 12px 28px rgba(28, 25, 23, 0.06);
    }}
    .stat-value {{
      display: block;
      font-size: 1.8rem;
      color: var(--accent);
      margin-top: 8px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
      margin-top: 22px;
    }}
    .chart-card svg {{
      width: 100%;
      height: auto;
      display: block;
      margin-top: 12px;
      overflow: visible;
    }}
    .axis {{
      stroke: var(--line);
      stroke-width: 1;
    }}
    .axis-label {{
      fill: var(--muted);
      font-size: 12px;
    }}
    ul {{
      margin: 0;
      padding-left: 20px;
      color: var(--muted);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      font-size: 0.95rem;
    }}
    th, td {{
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
    .empty {{
      color: var(--muted);
      font-style: italic;
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>{html.escape(character_name)} Snapshot Report</h1>
      <p>Ledger-backed validation view for captured stat-watch runs.</p>
    </section>

    <section class="stats">
      <article class="stat-card">
        <h2>Snapshots</h2>
        <span class="stat-value">{len(entries)}</span>
      </article>
      <article class="stat-card">
        <h2>Last Capture</h2>
        <span class="stat-value">{html.escape(latest_captured)}</span>
      </article>
      <article class="stat-card">
        <h2>Latest Life</h2>
        <span class="stat-value">{html.escape(format_value(latest_stats.get("defence.life")))}</span>
      </article>
      <article class="stat-card">
        <h2>Latest DPS</h2>
        <span class="stat-value">{html.escape(format_value(latest_stats.get("offence.total_dps")))}</span>
      </article>
    </section>

    <section class="grid">
      {charts}
    </section>

    <section class="grid">
      <article class="notes-card">
        <h2>Latest Observations</h2>
        <ul>{observation_items or "<li>No observations yet.</li>"}</ul>
      </article>
      <article class="table-card">
        <h2>Snapshot Index</h2>
        <table>
          <thead>
            <tr>
              <th>Captured</th>
              <th>Life</th>
              <th>ES</th>
              <th>DPS</th>
              <th>Items</th>
              <th>Archived Snapshot</th>
            </tr>
          </thead>
          <tbody>{table_rows(entries)}</tbody>
        </table>
      </article>
    </section>
  </main>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    if not args.character:
        raise SystemExit("Missing --character or DEFAULT_CHARACTER")

    char_slug = slugify(args.character)
    ledger_path = CHARACTERS_DIR / char_slug / "ledger.json"
    if not ledger_path.exists():
        raise SystemExit(f"Ledger not found: {ledger_path}")

    output_path = Path(args.output) if args.output else ROOT / "reports" / f"{char_slug}_snapshot_report.html"
    ledger = load_json(ledger_path)
    html_doc = build_html(ledger, args.character)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_doc, encoding="utf-8")
    print(f"Saved report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
