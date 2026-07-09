Reset the Propguru simulation data to a clean state: all deals back to "lead" stage, no evaluation reports.

Run these two curl commands in sequence and report the result:

```bash
API=http://localhost:8000
KEY="dev-secret-key-change-in-prod"

echo "=== Resetting Propguru data ===" && \
curl -s -X POST "$API/api/v1/propguru/simulation/reset" \
  -H "X-API-Key: $KEY" | python3 -m json.tool && \
echo "" && \
echo "=== Seeding master data ===" && \
curl -s -X POST "$API/api/v1/propguru/simulation/seed" \
  -H "X-API-Key: $KEY" | python3 -m json.tool
```

After running, confirm how many deals were seeded and that all are in "lead" stage.

Note: This wipes propguru_evaluation_reports, propguru_evaluation_scores, propguru_deals, propguru_market_comps, propguru_properties, propguru_evaluation_criteria, and propguru_channel_partners. It does NOT clear agent_actions or propguru_refine_sessions.
