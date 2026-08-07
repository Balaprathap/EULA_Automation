#!/usr/bin/env bash
#
# Reproducible Cloud Run deployment for ClauseGuard.
#
#   source env.sh && ./deploy.sh all
#
# Subcommands: apis | repo | build | api | worker | urls | logs | all
#
# No secret value appears in this file, in any image layer, or in git. Secrets
# live in Google Secret Manager and are mounted by reference at runtime.
set -euo pipefail

: "${GCP_PROJECT_ID:?source env.sh first}"
: "${GCP_REGION:=us-central1}"
: "${AR_REPO:=clauseguard}"
: "${API_SERVICE:=clauseguard-api}"
: "${WORKER_NAME:=clauseguard-worker}"

IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${AR_REPO}/backend"
RUNTIME_SA="clauseguard-run@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

# Secrets mounted as environment variables, by reference. SECRET:latest form.
SECRET_REFS="\
SUPABASE_URL=SUPABASE_URL:latest,\
SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,\
SUPABASE_SERVICE_ROLE_KEY=SUPABASE_SERVICE_ROLE_KEY:latest,\
SUPABASE_JWT_SECRET=SUPABASE_JWT_SECRET:latest,\
DATABASE_URL=DATABASE_URL:latest,\
REDIS_URL=REDIS_URL:latest,\
ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,\
EMBEDDING_API_KEY=EMBEDDING_API_KEY:latest"

# Non-secret config. Optional cloud integrations stay OFF for this deployment.
COMMON_ENV="\
ENVIRONMENT=production,\
LOG_LEVEL=INFO,\
SUPABASE_STORAGE_BUCKET=documents,\
SUPABASE_REPORTS_BUCKET=reports,\
EMBEDDING_PROVIDER=openai,\
EMBEDDING_MODEL=${EMBEDDING_MODEL:-text-embedding-3-small},\
EMBEDDING_DIMENSIONS=${EMBEDDING_DIMENSIONS:-1536},\
ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-sonnet-4-5},\
EMAIL_PROVIDER=console,\
AWS_REPORT_STORAGE_ENABLED=false,\
AWS_SES_ENABLED=false,\
VERTEX_SECOND_REVIEW_ENABLED=false,\
VERTEX_AUTOMATIC_REVIEW_ENABLED=false,\
BIGQUERY_ANALYTICS_ENABLED=false"

step() { echo; echo "=== $* ==="; }

cmd_apis() {
  step "Enabling APIs"
  gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    --project="$GCP_PROJECT_ID"
}

cmd_sa() {
  step "Least-privilege runtime service account"
  gcloud iam service-accounts create clauseguard-run \
    --display-name="ClauseGuard Cloud Run runtime" \
    --project="$GCP_PROJECT_ID" 2>/dev/null || echo "  already exists"

  # Only secret access. No storage, no BigQuery, no Vertex - nothing this
  # deployment does not need.
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None >/dev/null
  echo "  granted roles/secretmanager.secretAccessor only"
}

cmd_repo() {
  step "Artifact Registry"
  gcloud artifacts repositories create "$AR_REPO" \
    --repository-format=docker --location="$GCP_REGION" \
    --description="ClauseGuard backend images" \
    --project="$GCP_PROJECT_ID" 2>/dev/null || echo "  already exists"
}

cmd_build() {
  step "Building and pushing the backend image"
  # One image, two entrypoints - identical to docker-compose and render.yaml.
  gcloud builds submit backend \
    --tag "${IMAGE}:latest" \
    --project="$GCP_PROJECT_ID"
}

cmd_api() {
  step "Deploying the API (Cloud Run service)"
  # The Dockerfile CMD already binds 0.0.0.0:${PORT}, which Cloud Run injects.
  gcloud run deploy "$API_SERVICE" \
    --image="${IMAGE}:latest" \
    --region="$GCP_REGION" \
    --platform=managed \
    --allow-unauthenticated \
    --service-account="$RUNTIME_SA" \
    --set-env-vars="$COMMON_ENV" \
    --set-secrets="$SECRET_REFS" \
    --cpu=1 --memory=512Mi \
    --min-instances=0 --max-instances=3 \
    --timeout=300 \
    --project="$GCP_PROJECT_ID"

  local url
  url=$(gcloud run services describe "$API_SERVICE" --region="$GCP_REGION" \
        --format='value(status.url)' --project="$GCP_PROJECT_ID")
  step "Setting API_BASE_URL to its own URL"
  gcloud run services update "$API_SERVICE" --region="$GCP_REGION" \
    --update-env-vars="API_BASE_URL=${url}" --project="$GCP_PROJECT_ID" >/dev/null
  echo "  API_BASE_URL=${url}"

  if [[ -n "${FRONTEND_URL:-}" && "$FRONTEND_URL" != https://your-app.vercel.app ]]; then
    step "Setting CORS to the Vercel origin"
    gcloud run services update "$API_SERVICE" --region="$GCP_REGION" \
      --update-env-vars="APP_BASE_URL=${FRONTEND_URL},CORS_ORIGINS=${FRONTEND_URL}" \
      --project="$GCP_PROJECT_ID" >/dev/null
    echo "  CORS_ORIGINS=${FRONTEND_URL}"
  else
    echo "  SKIPPED CORS - set FRONTEND_URL in env.sh after deploying Vercel, then: ./deploy.sh api"
  fi
}

cmd_worker() {
  step "Deploying the analysis worker"
  # PRIMARY: a Cloud Run worker pool. Purpose-built for pull-based consumers
  # with no HTTP surface, which is exactly what this worker is - it blocks on
  # Redis BRPOPLPUSH and never serves a request.
  if gcloud beta run worker-pools --help >/dev/null 2>&1; then
    echo "  worker pools available - using them"
    gcloud beta run worker-pools deploy "$WORKER_NAME" \
      --image="${IMAGE}:latest" \
      --region="$GCP_REGION" \
      --service-account="$RUNTIME_SA" \
      --command="python" --args="-m,app.worker" \
      --set-env-vars="$COMMON_ENV" \
      --set-secrets="$SECRET_REFS" \
      --cpu=1 --memory=512Mi \
      --min-instances=1 --max-instances=2 \
      --project="$GCP_PROJECT_ID"
  else
    # FALLBACK: a Cloud Run service with CPU always allocated and one warm
    # instance. --no-cpu-throttling is essential: without it Cloud Run throttles
    # CPU to near zero between requests and the consumer loop stalls.
    #
    # NOTE: a Cloud Run *service* must answer the startup probe on $PORT. This
    # worker serves no HTTP, so this path needs the health-listener shim
    # described in docs/INFRASTRUCTURE.md. Do not use it unmodified.
    echo "  worker pools unavailable on this gcloud/project."
    echo "  See docs/INFRASTRUCTURE.md § Worker fallback before continuing."
    return 1
  fi
}

cmd_urls() {
  step "Service URLs"
  gcloud run services describe "$API_SERVICE" --region="$GCP_REGION" \
    --format='value(status.url)' --project="$GCP_PROJECT_ID"
}

cmd_logs() {
  step "Recent API logs"
  gcloud run services logs read "$API_SERVICE" --region="$GCP_REGION" \
    --limit=50 --project="$GCP_PROJECT_ID"
  step "Recent worker logs"
  gcloud beta run worker-pools logs read "$WORKER_NAME" --region="$GCP_REGION" \
    --limit=50 --project="$GCP_PROJECT_ID" 2>/dev/null \
    || echo "  (worker pool not found - check the fallback deployment)"
}

case "${1:-all}" in
  apis)   cmd_apis ;;
  sa)     cmd_sa ;;
  repo)   cmd_repo ;;
  build)  cmd_build ;;
  api)    cmd_api ;;
  worker) cmd_worker ;;
  urls)   cmd_urls ;;
  logs)   cmd_logs ;;
  all)    cmd_apis; cmd_sa; cmd_repo; cmd_build; cmd_api; cmd_worker; cmd_urls ;;
  *)      echo "usage: $0 {apis|sa|repo|build|api|worker|urls|logs|all}"; exit 1 ;;
esac
