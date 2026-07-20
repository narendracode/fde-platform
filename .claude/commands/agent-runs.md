# Agent Run Inspector

Inspect recent agent runs to debug failures, check costs, and identify stuck pipelines.

**Arguments**: `$ARGUMENTS` — optional filter: `failed`, `running`, `completed`, or an agent name substring (e.g. `propguru`, `sandhar`)

## Steps

### 1. Fetch recent runs

```bash
API=http://localhost:8000
KEY="dev-secret-key-change-in-prod"
FILTER="$ARGUMENTS"

# Determine status filter (if argument is a known status word)
if echo "$FILTER" | grep -qE "^(failed|running|completed|pending)$"; then
  STATUS_PARAM="?status=$FILTER&limit=20"
  NAME_FILTER=""
else
  STATUS_PARAM="?limit=30"
  NAME_FILTER="$FILTER"
fi

curl -s "$API/api/v1/runs$STATUS_PARAM" -H "X-API-Key: $KEY"
```

### 2. Parse and display a summary table

Use python to format the output:

```python
import sys, json, datetime

runs = json.load(sys.stdin)
name_filter = "$NAME_FILTER".strip().lower()

if name_filter:
    runs = [r for r in runs if name_filter in r.get("agent_id", "").lower()
            or name_filter in str(r.get("input", "")).lower()]

print(f"{'#':<3} {'Status':<12} {'Agent':<40} {'Cost $':<8} {'Tokens':<10} {'Duration':<10} {'Started'}")
print("-" * 110)

for i, r in enumerate(runs[:20], 1):
    status = r.get("status", "?")
    agent = str(r.get("agent_id", "?"))[-36:]  # show UUID suffix
    cost = f"{r.get('cost_usd', 0):.4f}"
    tokens = str(r.get("input_tokens", 0) + r.get("output_tokens", 0))
    started = r.get("started_at") or r.get("created_at") or ""
    if started:
        try:
            dt = datetime.datetime.fromisoformat(started.replace("Z", "+00:00"))
            started = dt.strftime("%m-%d %H:%M")
        except:
            started = started[:16]
    secs = ""
    if r.get("started_at") and r.get("completed_at"):
        try:
            s = datetime.datetime.fromisoformat(r["started_at"].replace("Z","+00:00"))
            e = datetime.datetime.fromisoformat(r["completed_at"].replace("Z","+00:00"))
            secs = f"{(e-s).seconds}s"
        except:
            pass
    symbol = {"completed":"✅","failed":"❌","running":"🔄","pending":"⏳"}.get(status, "?")
    print(f"{i:<3} {symbol} {status:<10} {agent:<40} {cost:<8} {tokens:<10} {secs:<10} {started}")

# Show failed run errors
failed = [r for r in runs if r.get("status") == "failed"]
if failed:
    print(f"\n{'='*40} FAILED RUNS {'='*40}")
    for r in failed[:5]:
        print(f"\nRun: {r.get('id')}")
        print(f"Error: {r.get('error', 'no error message')[:300]}")
```

### 3. Fetch error detail for any failed run

If there are failed runs, for each one fetch the full detail and show the error:

```bash
RUN_ID="<run_id_from_above>"
curl -s "$API/api/v1/runs/$RUN_ID" -H "X-API-Key: $KEY" | \
  python3 -c "
import sys, json
r = json.load(sys.stdin)
print('Status:', r.get('status'))
print('Error:', r.get('error', 'none'))
inp = r.get('input', {})
ctx = inp.get('extra_context', {}) if isinstance(inp, dict) else {}
print('Context:', json.dumps(ctx, indent=2))
"
```

### 4. Report summary

Provide a final summary covering:
- Total runs shown, breakdown by status (✅ completed / ❌ failed / 🔄 running / ⏳ pending)
- Total cost across all shown runs (sum of `cost_usd`)
- Any stuck `running` runs — if a run has been in `running` status for >10 min, flag it as likely stuck
- For failed runs: the error message and the `deal_id` or entity from the context, plus the remediation (e.g. "reset via `/simulation/reset` + `/simulation/seed`" for propguru eval failures)
- Suggested next action if anything needs attention
