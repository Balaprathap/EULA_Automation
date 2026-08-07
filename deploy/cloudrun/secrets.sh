#!/usr/bin/env bash
# Create the seven backend secrets in Google Secret Manager.
#
# Run this ONCE, interactively. Values are read from your terminal and piped
# straight to gcloud — they are never written to disk, never echoed, and never
# stored in git. Re-running adds a new version rather than overwriting.
set -euo pipefail
: "${GCP_PROJECT_ID:?source env.sh first}"

SECRETS=(
  SUPABASE_URL
  SUPABASE_ANON_KEY
  SUPABASE_SERVICE_ROLE_KEY
  SUPABASE_JWT_SECRET
  DATABASE_URL
  REDIS_URL
  ANTHROPIC_API_KEY
  EMBEDDING_API_KEY
)

for name in "${SECRETS[@]}"; do
  if ! gcloud secrets describe "$name" --project="$GCP_PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$name" --replication-policy=automatic --project="$GCP_PROJECT_ID"
  fi
  # -s suppresses echo so the value never appears on screen or in shell history.
  read -r -s -p "Value for $name: " value; echo
  printf '%s' "$value" | gcloud secrets versions add "$name" \
    --data-file=- --project="$GCP_PROJECT_ID"
  unset value
  echo "  stored $name"
done

echo
echo "Done. Verify with:  gcloud secrets list --project=$GCP_PROJECT_ID"
