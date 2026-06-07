# dev-tools

Miscellaneous developer utilities.

## Prerequisites

- **Python 3**
- **Node.js** (for `npx ccusage` — only used to auto-detect the active block window)

## Tools

### cc-block-report

Parses Claude Code JSONL logs (`~/.claude/projects/`) and generates a visual HTML report
for the current billing block — shows which sessions burned tokens, active work vs one-off
tests, per-bucket timeline with model tags.

Session classification (active vs one-off):
- **Active**: >100K tokens OR spans 3+ time buckets — engaged work sessions
- **One-off**: quick tests, short prompts, automated loops that didn't sustain

```bash
# From this repo directory:
pnpm block:report           # auto-detect + open browser
pnpm block:report:csv       # CSV + HTML only (headless)

# From any other project directory:
pnpm --prefix ~/Projects/dev-tools block:report

# Or run directly with a custom time window:
python scripts/cc-block-report.py --since 2026-06-07T10:00:00Z --until 2026-06-07T15:00:00Z --open
```

Outputs:
- `cc-block-report.html` — dark-mode dashboard with summary cards, session breakdown, bucket timeline
- `cc-block-report.csv` — raw bucket-session data (for spreadsheet analysis)
