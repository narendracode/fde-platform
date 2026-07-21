#!/bin/bash
# Stop hook — runs when Claude finishes its response.
# If Python files in src/ were changed this session (marker left by post-edit-lint.sh),
# restarts the Celery worker so it picks up the new code.
# The API container is NOT restarted — uvicorn --reload handles that automatically.

PROJECT_DIR="/Users/narendra/Projects/AI/langflow-poc"
MARKER="/tmp/claude-langflow-py-changed"

if [ ! -f "$MARKER" ]; then
  exit 0
fi

rm -f "$MARKER"

echo ""
echo "━━━ [restart] Python files changed — restarting Celery worker"

# Check Docker is running
if ! docker info >/dev/null 2>&1; then
  echo "    ⚠️  Docker not running — skipping restart"
  exit 0
fi

cd "$PROJECT_DIR"

# Restart worker — picks up new tool/task code from the mounted src/ volume
docker compose restart worker 2>&1 | sed 's/^/    /'

# Brief pause then confirm it came back up
sleep 2
STATUS=$(docker inspect --format '{{.State.Status}}' langflow-poc-worker-1 2>/dev/null || echo "unknown")
echo "    ✓ worker status: $STATUS"
echo ""
