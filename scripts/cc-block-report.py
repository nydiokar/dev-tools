#!/usr/bin/env python3
"""
cc-block-report — Parse Claude Code JSONL logs and generate a visual HTML report
for the current ccusage billing block.

Usage:
  python scripts/cc-block-report.py                    # auto-detect active block
  python scripts/cc-block-report.py --since 2026-06-07T10:00:00Z --until 2026-06-07T15:00:00Z
  python scripts/cc-block-report.py --open              # auto-detect + open in browser
"""

import json
import subprocess
import sys
import webbrowser
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

CLAUDEDATA = Path.home() / ".claude" / "projects"
BUCKET_MINUTES = 15
TOKEN_KEYS = [
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
]


def parse_time(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def find_usage(obj):
    found = []
    if isinstance(obj, dict):
        if "usage" in obj and isinstance(obj["usage"], dict):
            found.append(obj["usage"])
        for v in obj.values():
            found.extend(find_usage(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(find_usage(v))
    return found


def find_model(obj):
    if isinstance(obj, dict):
        model = obj.get("model") or obj.get("modelName")
        if model:
            return model
        for v in obj.values():
            m = find_model(v)
            if m:
                return m
    elif isinstance(obj, list):
        for v in obj:
            m = find_model(v)
            if m:
                return m
    return None


def bucket_time(dt):
    minute = (dt.minute // BUCKET_MINUTES) * BUCKET_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0)


def get_active_block():
    """Run ccusage and extract the current active block window."""
    try:
        result = subprocess.run(
            ["npx", "ccusage@latest", "claude", "blocks", "--recent", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        for block in data.get("blocks", []):
            if block.get("isActive"):
                return block["startTime"], block["endTime"]
        # fallback: use last non-gap block
        for block in reversed(data.get("blocks", [])):
            if not block.get("isGap", True):
                return block["startTime"], block["endTime"]
    except Exception as e:
        print(f"Warning: could not get active block from ccusage: {e}", file=sys.stderr)
    # ultimate fallback: last 5 hours
    now = datetime.now(timezone.utc)
    return (now - timedelta(hours=5)).isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate a Claude Code block token report")
    parser.add_argument("--since", help="Block start time (ISO8601)")
    parser.add_argument("--until", help="Block end time (ISO8601)")
    parser.add_argument("--open", action="store_true", help="Open HTML report in browser")
    args = parser.parse_args()

    if args.since and args.until:
        block_start_str = args.since
        block_end_str = args.until
    else:
        block_start_str, block_end_str = get_active_block()
        print(f"Auto-detected active block: {block_start_str} -> {block_end_str}")

    start = parse_time(block_start_str)
    end = parse_time(block_end_str)

    rows = {}
    session_totals = defaultdict(lambda: {
        "input": 0, "output": 0, "cache_create": 0, "cache_read": 0, "buckets": set(),
    })
    session_meta = {}
    session_models = defaultdict(set)

    for project_dir in sorted(CLAUDEDATA.iterdir()):
        if not project_dir.is_dir():
            continue
        project = project_dir.name

        for file in sorted(project_dir.rglob("*.jsonl")):
            sid = file.stem
            if sid == "project_config":
                continue

            try:
                lines = file.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue

            first_seen = None
            last_seen = None

            for line in lines:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue

                ts = parse_time(
                    rec.get("timestamp") or rec.get("created_at")
                    or rec.get("datetime") or rec.get("ts")
                )
                if not ts:
                    continue

                if first_seen is None or ts < first_seen:
                    first_seen = ts
                if last_seen is None or ts > last_seen:
                    last_seen = ts

                if not (start <= ts < end):
                    continue

                usages = find_usage(rec)
                if not usages:
                    continue

                bucket = bucket_time(ts).isoformat().replace("+00:00", "Z")
                key = (bucket, project, sid)

                if key not in rows:
                    rows[key] = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}

                for usage in usages:
                    rows[key]["input"] += int(usage.get("input_tokens") or 0)
                    rows[key]["output"] += int(usage.get("output_tokens") or 0)
                    rows[key]["cache_create"] += int(usage.get("cache_creation_input_tokens") or 0)
                    rows[key]["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)

                model = find_model(rec)
                if model:
                    session_models[sid].add(model)

            if first_seen:
                session_meta[sid] = {
                    "project": project,
                    "first_seen": first_seen.isoformat().replace("+00:00", "Z"),
                    "last_seen": last_seen.isoformat().replace("+00:00", "Z"),
                }

    for (bucket, project, sid), usage in rows.items():
        st = session_totals[sid]
        st["project"] = project
        st["input"] += usage["input"]
        st["output"] += usage["output"]
        st["cache_create"] += usage["cache_create"]
        st["cache_read"] += usage["cache_read"]
        st["buckets"].add(bucket)

    if not session_totals:
        print("No session data found in the block window.")
        sys.exit(0)

    # ── Write CSV ──
    csv_path = Path("cc-block-report.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("bucket_utc,project,session,input,output,cache_create,cache_read,total\n")
        for (bucket, project, sid), usage in sorted(rows.items()):
            total = sum(usage.values())
            f.write(
                f"{bucket},{project},{sid},"
                f"{usage['input']},{usage['output']},{usage['cache_create']},{usage['cache_read']},{total}\n"
            )
    print(f"CSV: {csv_path} ({len(rows)} bucket-rows)")

    # ── Write HTML ──
    html_path = Path("cc-block-report.html")

    block_total = sum(
        v["input"] + v["output"] + v["cache_create"] + v["cache_read"]
        for v in session_totals.values()
    )

    max_bucket_total = max(
        (v["input"] + v["output"] + v["cache_create"] + v["cache_read"])
        for v in rows.values()
    ) if rows else 1

    max_session_total = max(
        v["input"] + v["output"] + v["cache_create"] + v["cache_read"]
        for v in session_totals.values()
    ) if session_totals else 1

    session_list = sorted(
        session_totals.items(),
        key=lambda x: x[1]["input"] + x[1]["output"] + x[1]["cache_create"] + x[1]["cache_read"],
        reverse=True,
    )

    active_sessions = sum(
        1 for _, v in session_list
        if (v["input"] + v["output"] + v["cache_create"] + v["cache_read"]) > 100000
        or len(v["buckets"]) >= 3
    )
    oneoff_sessions = len(session_list) - active_sessions

    elapsed_mins = max(1, (datetime.now(timezone.utc) - start).total_seconds() / 60)
    burn_rate = block_total / elapsed_mins

    block_label = f"{block_start_str[:19].replace('T', ' ')} &ndash; {block_end_str[:19].replace('T', ' ')}"
    block_id = block_start_str[:13].replace("T", "-")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccusage Block Report — {block_id}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; padding: 2rem; }}
  h1 {{ font-size: 1.6rem; margin-bottom: .25rem; }}
  .subtitle {{ color: #8b949e; margin-bottom: 2rem; font-size: .9rem; }}
  .summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.2rem; }}
  .card .label {{ font-size: .75rem; text-transform: uppercase; color: #8b949e; letter-spacing: .05em; }}
  .card .value {{ font-size: 1.5rem; font-weight: 600; margin-top: .3rem; }}
  .card .value.green {{ color: #3fb950; }}
  .card .value.orange {{ color: #d29922; }}
  .card .value.red {{ color: #f85149; }}
  .card .value.blue {{ color: #58a6ff; }}
  .card .value.purple {{ color: #bc8cff; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: .5rem; }}
  th {{ text-align: left; padding: .6rem .8rem; font-size: .75rem; text-transform: uppercase; color: #8b949e; border-bottom: 2px solid #30363d; letter-spacing: .05em; white-space: nowrap; }}
  td {{ padding: .6rem .8rem; border-bottom: 1px solid #21262d; font-size: .85rem; }}
  tr:hover td {{ background: #1c2128; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .project-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: .75rem; font-weight: 500; white-space: nowrap; }}
  .p-default {{ background: #8b949e22; color: #8b949e; border: 1px solid #8b949e44; }}
  .session-id {{ font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: .75rem; color: #8b949e; }}
  .bar-container {{ width: 100px; height: 6px; background: #21262d; border-radius: 3px; display: inline-block; vertical-align: middle; margin-left: 6px; }}
  .bar-fill {{ height: 100%; border-radius: 3px; }}
  .model-tag {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: .7rem; background: #21262d; color: #c9d1d9; margin-right: 3px; }}
  .badge-active {{ background: #3fb95022; color: #3fb950; border: 1px solid #3fb95044; padding: 1px 6px; border-radius: 3px; font-size: .65rem; font-weight: 600; }}
  .badge-oneoff {{ background: #8b949e22; color: #8b949e; border: 1px solid #8b949e44; padding: 1px 6px; border-radius: 3px; font-size: .65rem; font-weight: 600; }}
</style>
</head>
<body>
<h1>ccusage Block Report</h1>
<p class="subtitle">Block: {block_label} (UTC, {elapsed_mins:.0f} min elapsed)</p>

<div class="summary-cards">
  <div class="card"><div class="label">Total Tokens</div><div class="value blue">{block_total:,}</div></div>
  <div class="card"><div class="label">Sessions in Block</div><div class="value green">{len(session_list)}</div></div>
  <div class="card"><div class="label">Active Work</div><div class="value green">{active_sessions}</div></div>
  <div class="card"><div class="label">One-off Tests</div><div class="value orange">{oneoff_sessions}</div></div>
  <div class="card"><div class="label">Burn Rate</div><div class="value purple">{burn_rate:,.0f} tok/min</div></div>
</div>

<h2>Session Breakdown</h2>
<table>
<thead>
<tr><th>Session</th><th>Project</th><th>Type</th><th class="num">Input</th><th class="num">Output</th><th class="num">Cache Create</th><th class="num">Cache Read</th><th class="num">Total</th><th>Buckets</th><th>Models</th></tr>
</thead>
<tbody>
"""

    for sid, v in session_list:
        total = v["input"] + v["output"] + v["cache_create"] + v["cache_read"]
        is_active = total > 100000 or len(v["buckets"]) >= 3
        pct = total / max_session_total
        bar_width = max(4, pct * 100)

        proj = v["project"]
        proj_short = proj.replace("C--Users-Cicada38", "").replace("Projects-", "").replace("-", " ").strip() or "root"

        models = session_models.get(sid, set())
        model_tags = " ".join(f'<span class="model-tag">{m}</span>' for m in sorted(models)) if models else '<span class="model-tag">&mdash;</span>'

        bucket_str = ""
        if v["buckets"]:
            sb = sorted(v["buckets"])
            bucket_str = f"{sb[0][11:16]}&ndash;{sb[-1][11:16]} ({len(sb)})"

        html += f"""<tr>
  <td class="session-id">{sid[:8]}</td>
  <td><span class="project-badge p-default">{proj_short}</span></td>
  <td>{'<span class="badge-active">ACTIVE</span>' if is_active else '<span class="badge-oneoff">ONE-OFF</span>'}</td>
  <td class="num">{v['input']:,}</td>
  <td class="num">{v['output']:,}</td>
  <td class="num">{v['cache_create']:,}</td>
  <td class="num">{v['cache_read']:,}</td>
  <td class="num"><strong>{total:,}</strong><div class="bar-container"><div class="bar-fill" style="width:{bar_width:.0f}%;background:#3fb950"></div></div></td>
  <td>{bucket_str}</td>
  <td>{model_tags}</td>
</tr>
"""

    html += """</tbody></table>
<h2>Bucket Timeline</h2>
<table>
<thead>
<tr><th>Time</th><th>Project</th><th class="num">Input</th><th class="num">Output</th><th class="num">Cache Create</th><th class="num">Cache Read</th><th class="num">Total</th><th>Session</th></tr>
</thead>
<tbody>
"""

    for (bucket, project, sid), usage in sorted(rows.items()):
        total = sum(usage.values())
        pct = total / max_bucket_total
        bar_width = max(4, pct * 100)

        proj_short = project.replace("C--Users-Cicada38", "").replace("Projects-", "").replace("-", " ").strip() or "root"

        html += f"""<tr>
  <td>{bucket[11:16]}</td>
  <td><span class="project-badge p-default">{proj_short}</span></td>
  <td class="num">{usage['input']:,}</td>
  <td class="num">{usage['output']:,}</td>
  <td class="num">{usage['cache_create']:,}</td>
  <td class="num">{usage['cache_read']:,}</td>
  <td class="num"><strong>{total:,}</strong><div class="bar-container"><div class="bar-fill" style="width:{bar_width:.0f}%;background:#58a6ff"></div></div></td>
  <td class="session-id">{sid[:8]}</td>
</tr>
"""

    html += f"""</tbody></table>
<h2>Session IDs</h2>
<table>
<thead>
<tr><th>Short</th><th>Full Session ID</th><th>Project</th><th>First Activity</th><th>Last Activity</th></tr>
</thead>
<tbody>
"""

    for sid, meta in sorted(session_meta.items(), key=lambda x: x[1].get("first_seen", "")):
        proj_short = meta["project"].replace("C--Users-Cicada38", "").replace("Projects-", "").replace("-", " ").strip() or "root"
        if sid in session_totals:
            html += f"""<tr>
  <td class="session-id">{sid[:8]}</td>
  <td class="session-id">{sid}</td>
  <td><span class="project-badge p-default">{proj_short}</span></td>
  <td>{meta['first_seen'][:19].replace('T', ' ')}</td>
  <td>{meta['last_seen'][:19].replace('T', ' ')}</td>
</tr>
"""

    html += """</tbody></table>
<p class="subtitle" style="margin-top:2rem">Generated from Claude Code JSONL logs &bull; 15-min buckets.</p>
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML: {html_path} ({len(session_list)} sessions, {len(rows)} bucket-rows)")

    if args.open:
        webbrowser.open(str(html_path.resolve()))


if __name__ == "__main__":
    main()
