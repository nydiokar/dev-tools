# dev-tools

Miscellaneous developer utilities.

## Tools

### cc-block-report

Parses Claude Code JSONL logs and generates a visual HTML report for the current billing block — shows which sessions burned tokens, active work vs one-off tests, per-bucket timeline.

```bash
# Auto-detect active block and open HTML report
pnpm block:report

# Just generate CSV + HTML (no browser open)
pnpm block:report:csv

# Or run directly with custom window
python scripts/cc-block-report.py --since 2026-06-07T10:00:00Z --until 2026-06-07T15:00:00Z --open
```

Outputs: `cc-block-report.html` (dark-mode dashboard) and `cc-block-report.csv` (raw data).
