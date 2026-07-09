Check the current status of all Propguru deals and the evaluation pipeline.

Run this and summarize in a table:

```bash
API=http://localhost:8000
KEY="dev-secret-key-change-in-prod"

echo "=== Deals ===" && \
curl -s "$API/api/v1/propguru/deals" -H "X-API-Key: $KEY" | \
  python3 -c "
import sys, json
data = json.load(sys.stdin)
deals = data if isinstance(data, list) else data.get('deals', data.get('items', []))
print(f'Total deals: {len(deals)}')
for d in deals:
    prop = d.get('property', {}) or {}
    addr = prop.get('address_line1', prop.get('property_code', '—'))
    city = prop.get('city', '')
    print(f\"  {d.get('deal_code','?'):10} {d.get('stage','?'):25} {addr}, {city}\")
"
```

Report:
1. Total deal count and breakdown by stage (lead / evaluation_pending / evaluation_done / listed / sold)
2. Any deals stuck in `evaluation_pending` (may indicate a failed agent run)
3. Quick next step suggestion if all deals are still in "lead" (suggest triggering evaluation)
