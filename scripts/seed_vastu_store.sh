#!/bin/bash
# Seed the vastu-shastra memory store and upload knowledge PDFs.
# Run after `make upd` + `make migrate`.

set -e

API="${API_BASE_URL:-http://localhost:8000}"
KEY="${API_KEY:-dev-secret-key-change-in-prod}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/../data/vastu"

echo "=== Seeding Vastu Shastra store ==="

# 1. Create the store (idempotent — 409 if already exists is fine)
echo "[1/4] Creating store..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/stores" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "vastu-shastra",
    "name": "Vastu Shastra Knowledge Base",
    "description": "Rules, principles, and directional guidelines for Vastu Shastra property compliance evaluation.",
    "company": "propguru",
    "memory_type": "semantic",
    "embedding_model": "text-embedding-3-small",
    "chunk_size": 512,
    "chunk_overlap": 64
  }')
if [ "$STATUS" = "201" ]; then
  echo "  Store created (201)"
elif [ "$STATUS" = "409" ]; then
  echo "  Store already exists (409) — OK"
else
  echo "  Unexpected status $STATUS"
  exit 1
fi

# 2. Upload PDFs and collect doc IDs
upload_and_approve() {
  local file="$1"
  local title="$2"
  local filename
  filename=$(basename "$file")

  echo ""
  echo "[Upload] $title"

  DOC_RESP=$(curl -s -X POST "$API/api/v1/stores/vastu-shastra/documents" \
    -H "X-API-Key: $KEY" \
    -F "title=$title" \
    -F "uploaded_by=seed-script" \
    -F "file=@$file;filename=$filename")
  DOC_ID=$(echo "$DOC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)

  if [ -z "$DOC_ID" ]; then
    echo "  Upload failed: $DOC_RESP"
    return 1
  fi
  echo "  Uploaded — doc id: $DOC_ID"

  # Approve to trigger embedding
  APPROVE_RESP=$(curl -s -X PATCH \
    "$API/api/v1/stores/vastu-shastra/documents/$DOC_ID/approve?approved_by=seed-script" \
    -H "X-API-Key: $KEY")
  EMBED_STATUS=$(echo "$APPROVE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('embed_status','?'))" 2>/dev/null)
  echo "  Approved — embed task: $EMBED_STATUS"
}

echo ""
echo "[2/4] Uploading PDFs..."
upload_and_approve "$DATA_DIR/complete-vastu-guide.pdf"    "Complete Vastu Guide (Jain University)"
upload_and_approve "$DATA_DIR/vastu-shastra-principles.pdf" "Vastu Shastra Principles"

echo ""
echo "[3/4] Verifying store..."
curl -s "$API/api/v1/stores/vastu-shastra" \
  -H "X-API-Key: $KEY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Docs: {d[\"doc_count\"]} | Chunks: {d[\"chunk_count\"]}')
print(f'  Embedding model: {d[\"embedding_model\"]}')
"

echo ""
echo "[4/4] Done."
echo ""
echo "  Note: embedding runs as a background Celery task."
echo "  Wait ~30s then check chunk count:"
echo "  curl -s $API/api/v1/stores/vastu-shastra -H 'X-API-Key: $KEY' | python3 -m json.tool"
echo ""
echo "  Then re-activate the supervisor to pick up the vastu-scorer worker:"
echo "  curl -s -X POST $API/api/v1/agents/propguru-evaluation-supervisor/activate \\"
echo "    -H 'X-API-Key: $KEY' | python3 -m json.tool"
