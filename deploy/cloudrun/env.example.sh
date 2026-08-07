#!/usr/bin/env bash
# Copy to env.sh, fill in, and `source env.sh` before running deploy.sh.
# env.sh is gitignored. NO SECRET VALUES BELONG IN THIS FILE — secrets live in
# Google Secret Manager and are referenced by name only.

export GCP_PROJECT_ID="your-project-id"
export GCP_REGION="us-central1"
export AR_REPO="clauseguard"
export API_SERVICE="clauseguard-api"
export WORKER_NAME="clauseguard-worker"

# Set after the Vercel deployment exists. Until then the API allows nothing.
export FRONTEND_URL="https://your-app.vercel.app"

# Non-secret application config.
export ANTHROPIC_MODEL="claude-sonnet-4-5"
export EMBEDDING_MODEL="text-embedding-3-small"
export EMBEDDING_DIMENSIONS="1536"
