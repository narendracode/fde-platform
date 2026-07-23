#!/bin/bash
# PostToolUse hook — runs after every Edit or Write tool call.
# If the changed file is a Python file inside src/, runs ruff format + ruff check --fix
# and leaves a marker so the Stop hook knows to restart the worker.
# Note: runs on the host (not inside Docker) because src/ is mounted :ro in containers.

set -euo pipefail

PROJECT_DIR="/Users/narendra/Projects/AI/langflow-poc"
MARKER="/tmp/claude-langflow-py-changed"

# Read hook JSON from stdin and extract file_path
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('file_path', ''))
" 2>/dev/null || true)

# Only act on Python files inside the project's src/ directory
if [[ "$FILE_PATH" != "$PROJECT_DIR/src/"*.py ]]; then
  exit 0
fi

RELATIVE="${FILE_PATH#$PROJECT_DIR/}"
echo ""
echo "━━━ [lint] $RELATIVE"

# ruff format (black-compatible formatter)
echo "    → ruff format"
cd "$PROJECT_DIR" && uv run ruff format "$RELATIVE" 2>&1 | sed 's/^/       /'

# ruff check with auto-fix (import sorting, unused imports, style)
echo "    → ruff check --fix"
cd "$PROJECT_DIR" && uv run ruff check --fix "$RELATIVE" 2>&1 | sed 's/^/       /'

echo "    ✓ done"

# Leave marker so Stop hook restarts the worker
touch "$MARKER"
