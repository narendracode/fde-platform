#!/usr/bin/env bash
# ci_deploy.sh — Mock CI/CD pipeline for the Fundly Agent Platform
#
# Simulates what would happen in a real CI/CD pipeline (GitHub Actions, etc.)
# when a YAML agent config is merged to main:
#   1. Check services are healthy
#   2. Run DB migrations
#   3. Register agents in the platform DB
#   4. Sync flows to LangFlow
#   5. Smoke test the platform API
#   6. Print a deployment summary
#
# Usage:
#   bash scripts/ci_deploy.sh                    # deploy all agents
#   bash scripts/ci_deploy.sh --agent react-agent  # deploy one agent
#   bash scripts/ci_deploy.sh --skip-smoke       # skip the smoke test
#   bash scripts/ci_deploy.sh --dry-run          # print what would change, no mutations
#
# From Makefile: make ci-deploy

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}▶ $*${RESET}"; }
success() { echo -e "${GREEN}✓ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $*${RESET}"; }
error()   { echo -e "${RED}✗ $*${RESET}"; exit 1; }
section() { echo -e "\n${BOLD}━━━ $* ━━━${RESET}"; }

# ── Args ───────────────────────────────────────────────────────────────────────
AGENT_FLAG=""
SKIP_SMOKE=false
DRY_RUN=false

for arg in "$@"; do
  case $arg in
    --agent=*) AGENT_FLAG="--agent ${arg#*=}" ;;
    --agent)   shift; AGENT_FLAG="--agent $1" ;;
    --skip-smoke) SKIP_SMOKE=true ;;
    --dry-run)    DRY_RUN=true ;;
  esac
done

# ── Config ─────────────────────────────────────────────────────────────────────
API_URL="${API_URL:-http://localhost:8000}"
LANGFLOW_URL="${LANGFLOW_URL:-http://localhost:7860}"
API_KEY="${API_KEY:-dev-secret-key-change-in-prod}"

echo -e "\n${BOLD}Fundly Agent Platform — CI/CD Deploy${RESET}"
echo   "  Platform API : ${API_URL}"
echo   "  LangFlow     : ${LANGFLOW_URL}"
if [[ "$DRY_RUN" == "true" ]]; then
  warn "DRY RUN mode — no changes will be made"
fi
echo

# ══════════════════════════════════════════════════════════════════════════════
section "STEP 1 — Health checks"
# ══════════════════════════════════════════════════════════════════════════════

info "Checking platform API..."
if curl -sf "${API_URL}/health" >/dev/null 2>&1; then
  success "Platform API is up"
else
  error "Platform API not reachable at ${API_URL}. Run: make up"
fi

info "Checking LangFlow..."
if curl -sf "${LANGFLOW_URL}/health" >/dev/null 2>&1 || \
   curl -sf "${LANGFLOW_URL}/api/v1/version" >/dev/null 2>&1; then
  success "LangFlow is up"
else
  warn "LangFlow not reachable at ${LANGFLOW_URL} — flow sync will be skipped"
  SKIP_LANGFLOW=true
fi

info "Checking database (via API health endpoint)..."
DB_STATUS=$(curl -sf "${API_URL}/health/db" 2>/dev/null || echo '{"status":"error"}')
if echo "$DB_STATUS" | grep -q '"status":"ok"'; then
  success "Database is connected"
else
  error "Database not reachable. Check postgres container: docker compose logs postgres"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "STEP 2 — Database migrations"
# ══════════════════════════════════════════════════════════════════════════════

if [[ "$DRY_RUN" == "true" ]]; then
  info "[dry-run] Would run: docker compose exec api uv run alembic upgrade head"
else
  info "Running Alembic migrations..."
  docker compose exec -T api uv run alembic upgrade head
  success "Migrations applied"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "STEP 3 — Register agents in platform DB"
# ══════════════════════════════════════════════════════════════════════════════

if [[ "$DRY_RUN" == "true" ]]; then
  info "[dry-run] Would seed agents from agents/configs/*.yaml into DB"
else
  info "Seeding agents..."
  docker compose exec -T api uv run python scripts/seed_data.py
  success "Agents registered in DB"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "STEP 4 — Sync flows to LangFlow"
# ══════════════════════════════════════════════════════════════════════════════

if [[ "${SKIP_LANGFLOW:-false}" == "true" ]]; then
  warn "LangFlow unreachable — skipping flow sync"
else
  SYNC_FLAGS="$AGENT_FLAG"
  if [[ "$DRY_RUN" == "true" ]]; then
    SYNC_FLAGS="$SYNC_FLAGS --dry-run"
  fi

  info "Syncing YAML configs to LangFlow flows..."
  LANGFLOW_URL="$LANGFLOW_URL" \
    uv run python scripts/sync_langflow_flows.py $SYNC_FLAGS
fi

# ══════════════════════════════════════════════════════════════════════════════
section "STEP 5 — Smoke test"
# ══════════════════════════════════════════════════════════════════════════════

if [[ "$SKIP_SMOKE" == "true" ]] || [[ "$DRY_RUN" == "true" ]]; then
  warn "Smoke test skipped"
else
  info "Testing /health endpoint..."
  HEALTH=$(curl -sf "${API_URL}/health")
  success "Health: $HEALTH"

  info "Testing /api/v1/agents/configs (lists YAML configs)..."
  CONFIG_COUNT=$(curl -sf "${API_URL}/api/v1/agents/configs" \
    -H "X-API-Key: ${API_KEY}" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
  success "Found ${CONFIG_COUNT} agent config(s)"

  info "Testing /api/v1/agents/tools (lists tool registry)..."
  TOOL_COUNT=$(curl -sf "${API_URL}/api/v1/agents/tools" \
    -H "X-API-Key: ${API_KEY}" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['tools']))")
  success "Found ${TOOL_COUNT} tool(s) in registry"

  info "Running agent smoke test (react-agent, tool-only query)..."
  SMOKE_RESP=$(curl -sf -X POST "${API_URL}/api/v1/agents/react-agent/run" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${API_KEY}" \
    -d '{"message": "What is the square root of 144?"}' 2>/dev/null || echo '{}')

  if echo "$SMOKE_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d.get('output') else 1)" 2>/dev/null; then
    OUTPUT=$(echo "$SMOKE_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['output'][:120])")
    success "Agent responded: ${OUTPUT}..."
  else
    warn "Smoke test response was empty or agent returned an error (LLM API key may not be set)"
    info "Set ANTHROPIC_API_KEY in .env and restart: make up"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
section "DEPLOYMENT SUMMARY"
# ══════════════════════════════════════════════════════════════════════════════

echo
echo -e "  ${GREEN}Platform API${RESET}   →  ${API_URL}/docs"
echo -e "  ${GREEN}LangFlow UI${RESET}    →  ${LANGFLOW_URL}"
echo
echo -e "  ${BOLD}Quick commands:${RESET}"
echo -e "  Invoke agent (sync):   curl -X POST ${API_URL}/api/v1/agents/react-agent/run \\"
echo -e "                           -H 'X-API-Key: ${API_KEY}' \\"
echo -e "                           -d '{\"message\": \"your question\"}'"
echo
echo -e "  View runs:             curl ${API_URL}/api/v1/runs -H 'X-API-Key: ${API_KEY}'"
echo
if [[ "$DRY_RUN" == "true" ]]; then
  warn "DRY RUN complete — no changes were made"
else
  success "Deploy complete"
fi
echo
