Trigger the Propguru evaluation pipeline for a specific deal.

Arguments: $ARGUMENTS (pass a deal_id UUID or deal_code like DEAL-001)

Steps:
1. If $ARGUMENTS looks like a deal_code (starts with DEAL-), first resolve it to a UUID:
```bash
API=http://localhost:8000
KEY="dev-secret-key-change-in-prod"
curl -s "$API/api/v1/propguru/deals" -H "X-API-Key: $KEY" | \
  python3 -c "
import sys, json
deals = json.load(sys.stdin)
if isinstance(deals, dict): deals = deals.get('deals', deals.get('items', []))
for d in deals:
    if d.get('deal_code') == '$ARGUMENTS' or d.get('id') == '$ARGUMENTS':
        print(d['id'])
        break
"
```

2. Trigger evaluation:
```bash
curl -s -X POST "$API/api/v1/propguru/deals/{DEAL_ID}/evaluate" \
  -H "X-API-Key: $KEY" | python3 -m json.tool
```

3. Poll until stage changes from `evaluation_pending`:
```bash
for i in $(seq 1 20); do
  STAGE=$(curl -s "$API/api/v1/propguru/deals/{DEAL_ID}" -H "X-API-Key: $KEY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('stage','?'))")
  echo "[$i] Stage: $STAGE"
  if [ "$STAGE" = "evaluation_done" ] || [ "$STAGE" = "lead" ]; then break; fi
  sleep 5
done
```

Report the final stage and evaluation report summary (recommended price, score factor, confidence).
Note: The evaluation supervisor agent must be active for this to work. If it fails, activate it first:
`curl -s -X POST "$API/api/v1/agents/propguru-evaluation-supervisor/activate" -H "X-API-Key: $KEY"`
