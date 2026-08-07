# Infrastructure and deployment

> **Status: not deployed.** No production environment has been provisioned and
> no deployment URL is claimed anywhere in this repository. Everything below is
> the exact procedure required, with each step that still needs performing
> marked **TODO (requires credentials)**.

---

## Target topology

| Component | Service | Notes |
|---|---|---|
| Frontend | Vercel | Next.js 14, standalone output |
| API | Render or Railway | Docker, `uvicorn app.main:app` |
| Worker | Render or Railway | **Same image**, `python -m app.worker` |
| Postgres + pgvector | Supabase | Also provides Auth and Storage |
| Auth | Supabase Auth | Email/password, optional Google OAuth |
| Object storage | Supabase Storage | Private `documents` bucket |
| Queue, cache, rate limits | Upstash Redis (or any Redis 7) | TLS `rediss://` recommended |
| Error tracking | Sentry (optional) | `SENTRY_DSN` |

The API and worker deliberately run **the same Docker image** with different
start commands, so they cannot drift apart.

---

## 1. Supabase

**TODO (requires credentials).**

1. Create a project. Choose a region near your API.
2. **Settings → API**: copy the project URL, `anon` key, and `service_role` key.
3. **Settings → API → JWT Settings**: copy the JWT secret.
4. **Settings → Database**: copy the connection string (use the pooled
   connection for the API, the direct connection for migrations).
5. **Database → Extensions**: confirm `vector` and `pg_trgm` are available.
   Migration `0001` enables them.
6. **Authentication → URL Configuration**:
   - Site URL: `https://<your-frontend-domain>`
   - Redirect URLs: `https://<your-frontend-domain>/dashboard`
     (add `http://localhost:3000/dashboard` for local development)
7. *(Optional)* **Authentication → Providers → Google**: enable, and set the
   authorized redirect URI to `https://<project>.supabase.co/auth/v1/callback`.
8. Run the migrations (below). They create the storage bucket, its RLS policies,
   and the signup trigger — **no manual dashboard configuration is needed.**

### Migrations

```bash
cd backend
export DATABASE_URL="postgresql://postgres:<password>@db.<project>.supabase.co:5432/postgres"
python -m scripts.migrate
```

Idempotent and safe to re-run. Each migration is recorded in
`schema_migrations` with a checksum; a file edited after being applied is
reported rather than silently skipped.

### Seed

```bash
python -m scripts.seed              # default policy + 12 categories, all orgs
python -m scripts.seed --demo       # additionally: demo org + sample agreement
```

Also idempotent. Demo data is written through the ordinary schema and flagged
with `organizations.is_demo`, so it is always distinguishable from real data.
Production API paths never generate demo content.

---

## 2. Redis

**TODO (requires credentials).**

Upstash, Redis Cloud, or a Render/Railway Redis add-on. Copy the connection
string into `REDIS_URL`. Prefer `rediss://` (TLS).

Required commands: `LPUSH`, `BRPOPLPUSH`, `RPOPLPUSH`, `LREM`, `LLEN`, `SET NX`,
`DEL`, `KEYS`, and the sorted-set commands used by the rate limiter. Any
standard Redis 7 instance is fine. Note that some serverless Redis products do
not support blocking commands — if `BRPOPLPUSH` is unavailable, use a
conventional Redis instance.

---

## 3. API service

**TODO (requires credentials).**

Render (`render.com` → New → Web Service) or Railway.

| Setting | Value |
|---|---|
| Runtime | Docker |
| Root directory | `backend` |
| Dockerfile | `backend/Dockerfile` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |
| Port | `8000` (or `$PORT`) |

Environment variables:

```
ENVIRONMENT=production
LOG_LEVEL=INFO

DATABASE_URL=postgresql://...
REDIS_URL=rediss://...

SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_JWT_SECRET=...
SUPABASE_STORAGE_BUCKET=documents

ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-5

EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=...
EMBEDDING_DIMENSIONS=1536

APP_BASE_URL=https://<frontend-domain>
API_BASE_URL=https://<api-domain>
CORS_ORIGINS=https://<frontend-domain>      # never "*"

MAX_UPLOAD_MB=10
MAX_DOCUMENT_PAGES=150
RATE_LIMIT_ANALYSES_PER_HOUR=20
RATE_LIMIT_REQUESTS_PER_MINUTE=200

SENTRY_DSN=                                  # optional
```

Startup **fails loudly** if any required variable is missing, if `CORS_ORIGINS`
contains `*`, or if `EMBEDDING_PROVIDER=deterministic`. That is intentional — a
misconfigured production deployment should not start.

> **Embedding dimensions are baked into the schema.** `document_chunks.embedding`
> is declared `vector(1536)`. Changing to a model with a different width requires
> a migration that alters the column and re-embeds every chunk.

---

## 4. Worker service

**TODO (requires credentials).**

A second service from the **same repository and Dockerfile**.

| Setting | Value |
|---|---|
| Type | Background Worker (Render) / Service (Railway) |
| Start command | `python -m app.worker` |
| Environment | Identical to the API |

Run at least one. Multiple workers are safe: each job is claimed with an atomic
status-guarded `UPDATE`, so exactly one worker wins any race.

The worker requeues its own orphaned jobs on startup, and a sweep every 60
seconds requeues analyses whose heartbeat is more than 5 minutes stale (up to 3
attempts). A resumed run skips categories already recorded in
`analyses.completed_categories`.

---

## 5. Frontend

**TODO (requires credentials).**

Vercel → Import the repository.

| Setting | Value |
|---|---|
| Framework | Next.js |
| Root directory | `frontend` |
| Build command | `npm run build` |
| Install command | `npm ci` |
| Output | (default) |

Environment variables — **only these three**, all safe for the browser:

```
NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
NEXT_PUBLIC_API_BASE_URL=https://<api-domain>
```

`NEXT_PUBLIC_*` values are inlined at build time. Changing one requires a
rebuild, not just a restart.

After deploying, add the Vercel domain to `CORS_ORIGINS` on the API and to the
Supabase redirect URLs, then redeploy the API.

---

## 6. Smoke tests

**TODO (requires a deployed environment).** Run in order; each must pass before
the next.

```bash
API=https://<api-domain>
APP=https://<frontend-domain>

# 1. Liveness
curl -fsS $API/health
# {"status":"ok","version":"1.0.0","environment":"production"}

# 2. Readiness - both dependencies must report true
curl -fsS $API/health/ready
# {"status":"ready","checks":{"database":true,"redis":true}}

# 3. Unauthenticated access is refused
curl -s -o /dev/null -w '%{http_code}\n' $API/api/v1/documents      # 401

# 4. CORS allows the frontend and nothing else
curl -sI -H "Origin: $APP" -X OPTIONS $API/api/v1/documents | grep -i access-control-allow-origin
curl -sI -H "Origin: https://evil.example" -X OPTIONS $API/api/v1/documents | grep -i access-control-allow-origin
# the second must NOT echo the evil origin

# 5. Security headers
curl -sI $API/health | grep -iE 'content-security-policy|strict-transport|x-frame-options'

# 6. OpenAPI is served
curl -fsS $API/openapi.json | head -c 100
```

Then, in a browser:

7. Register an account. Confirm a workspace is created (the dashboard loads).
8. Sign out, sign back in. Confirm the session restores on refresh.
9. Upload the sample agreement from `backend/tests/fixtures/sample_eula.txt`.
10. Start an analysis. Confirm progress advances through the real stages.
11. Confirm the analysis completes with a score and findings.
12. Open a finding. Confirm the source pane highlights the quoted clause.
13. Accept the finding, add a note, reload. Confirm both persisted.
14. Sign in as a second user in a different organization. Confirm none of the
    first organization's documents are visible.
15. As a non-admin, confirm `/admin` shows the "administrators only" state.

---

## 7. Rollback

| Scenario | Action |
|---|---|
| Bad frontend deploy | Vercel → Deployments → promote the previous build. Instant. |
| Bad API deploy | Render/Railway → roll back to the previous image. The worker can stay up; jobs queue safely in Redis meanwhile. |
| Bad worker deploy | Roll back the worker only. In-flight jobs are requeued by the heartbeat sweep and resume from completed categories. |
| Bad migration | Migrations are forward-only. Write a new numbered migration that reverses the change. **Take a Supabase snapshot before migrating.** |
| Queue backlog | Scale worker replicas up. Queue depth and live worker count are on `/api/v1/admin/metrics`. |
| Provider outage | Affected categories become `needs_review`; analyses complete as `partial` rather than failing. Re-run once the provider recovers. |

---

## 8. Operations

**Monitoring.** `/health` for uptime checks, `/health/ready` for dependency
health. `/api/v1/admin/metrics` (admin auth) reports analysis counts, success
and error rates, verification pass rate, per-stage latency, p95 analysis time,
token usage, estimated cost, queue depth, and live workers.

**Logging.** Structured JSON to stdout with a correlation id on every line.
`X-Request-ID` is echoed on every response, and appears in every error envelope.

**Cost control.** Per-organization analysis rate limits; embeddings cached by
content hash so re-analysis of the same agreement re-embeds nothing; only
retrieved chunks reach the model, never the whole document; per-analysis cost
tracked in `analyses.estimated_cost_usd` and visible on the usage page.

**Backups.** Supabase provides automated backups on paid plans. Take a manual
snapshot before every migration.

**Scaling.** The API is stateless — scale horizontally. Workers are safe to run
in parallel. The likely first bottleneck is provider rate limits, not the
application.

---

## Local development stack

```bash
cp .env.example .env
docker compose up --build
docker compose run --rm migrate
docker compose run --rm seed
```

Brings up Postgres with pgvector, Redis, the API, and the worker. Supabase Auth
and Storage remain cloud services; point `SUPABASE_*` at a real project.

The compose `migrate` service runs `--local-shim`, which creates the `auth` and
`storage` schemas Supabase normally supplies. Against your real Supabase
database, run `python -m scripts.migrate` **without** that flag — the script
refuses it on a Supabase host.

`docker compose --profile full up --build` additionally builds the frontend.

**Platform notes.** On Windows PowerShell, chain commands with `;` rather than
`&&`. In Git Bash, prefix commands taking `/`-prefixed arguments with
`MSYS_NO_PATHCONV=1` to stop path rewriting. On both, ensure Docker Desktop has
file sharing enabled for the repository directory.


---

## AWS (optional) — S3 report storage and SES delivery

> **Status: implemented behind feature flags, validated with botocore Stubber.
> Live AWS verification is pending.** Both flags default to `false`; ClauseGuard
> runs entirely on Supabase Storage and the existing email providers until you
> complete the steps below.

Do these **after** the core analysis works end to end.

### A1. Create the report bucket

1. S3 → **Create bucket**. Name it e.g. `clauseguard-reports-<yourname>`.
   Region must match `AWS_REGION`.
2. **Block Public Access — leave all four boxes ticked.** The bucket must never
   be public; downloads use short-lived pre-signed URLs.
3. **Object Ownership → ACLs disabled (Bucket owner enforced).** The application
   never sets an ACL.
4. **Default encryption → SSE-S3 (SSE-KMS optional, see A2).**
5. *(Optional)* Lifecycle rule: expire objects under the report prefix after
   e.g. 365 days.

Object keys are `{org_id}/{analysis_id}/report-v{n}.pdf`. The org id is the
first path segment so a prefix-scoped IAM policy isolates tenants.

### A2. Optional SSE-KMS

Only if you want a customer-managed key:

1. KMS → **Create key** → Symmetric → Encrypt and decrypt.
2. Copy the key ARN into `AWS_KMS_KEY_ID`.
3. Add `kms:GenerateDataKey` and `kms:Decrypt` for that key ARN to the IAM
   policy below.

Leave `AWS_KMS_KEY_ID` empty to use SSE-S3 (AES256). The application requests
encryption either way — it is never optional in code.

### A3. Minimum IAM policy

Attach to the role or user the backend runs as. **No `s3:*`, no wildcards on
resources.** Replace `BUCKET` and `REGION`/`ACCOUNT`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReportObjectAccess",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::BUCKET/*"
    },
    {
      "Sid": "ReportObjectMetadata",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::BUCKET",
      "Condition": { "StringLike": { "s3:prefix": ["*/*/report-v*.pdf"] } }
    },
    {
      "Sid": "SendReportEmail",
      "Effect": "Allow",
      "Action": ["ses:SendEmail"],
      "Resource": "*",
      "Condition": {
        "StringEquals": { "ses:FromAddress": "reports@yourdomain.com" }
      }
    }
  ]
}
```

Notes:
- `HeadObject` is covered by `s3:GetObject`; there is no separate action.
- `DeleteObject` is optional — omit it if you never delete reports.
- The SES condition pins the sender, so a leaked credential cannot send from
  another address.
- If using SSE-KMS, add a fourth statement with `kms:GenerateDataKey` and
  `kms:Decrypt` scoped to the key ARN.

### A4. SES sender verification

1. SES → **Verified identities** → Create identity.
2. Either a **domain** (recommended — enables DKIM and any From address) or a
   **single email address** (fastest for testing).
3. For a domain, add the DKIM CNAME records your DNS provider shows.
4. Put the verified address in `AWS_SES_FROM_EMAIL`.

**SES sandbox — read this before testing.** New accounts are in the sandbox:

- You may only send **to verified addresses.** Verify your own inbox as a second
  identity, or nothing will arrive.
- Sending is capped (typically 200 messages/day, 1/second).
- Bounce and complaint handling is your responsibility.

To leave the sandbox: SES → **Account dashboard** → *Request production access*.
Expect to describe your use case and bounce handling. Approval usually takes
about 24 hours.

*(Optional)* Create a **Configuration Set** for open/bounce tracking and put its
name in `AWS_SES_CONFIGURATION_SET`.

### A5. Credentials

The application uses the **standard boto3 credential chain** and reads no AWS
secret from its own configuration.

| Environment | Method |
|---|---|
| Local verification | `aws configure` (writes `~/.aws/credentials`), or `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the shell |
| Render / Railway | Set the same two variables as platform secrets |
| ECS / EC2 / Lambda | **Prefer an IAM role** — no long-lived key at all |

Never commit `~/.aws/credentials`, never put an AWS key in `.env.example`, and
never expose one to the frontend.

### A6. Application configuration

Non-secret identifiers only:

```
AWS_REGION=us-east-1
AWS_S3_REPORT_BUCKET=clauseguard-reports-yourname
AWS_REPORT_STORAGE_ENABLED=false
AWS_KMS_KEY_ID=
AWS_SES_ENABLED=false
AWS_SES_FROM_EMAIL=reports@yourdomain.com
AWS_SES_CONFIGURATION_SET=
AWS_REPORT_URL_TTL_SECONDS=900
AWS_REPORT_ATTACHMENT_MAX_BYTES=8388608
```

### A7. Live S3 verification

Enable storage only, keeping SES off:

```
AWS_REPORT_STORAGE_ENABLED=true
AWS_SES_ENABLED=false
```

Restart the API and worker, then run an analysis and check:

| Check | How |
|---|---|
| Object key is org-scoped | S3 console → object is at `{org_id}/{analysis_id}/report-v1.pdf` |
| Content type | Object → Properties → `application/pdf` |
| Encryption | Object → Properties → Server-side encryption enabled |
| No public access | Object → Permissions → no public grants |
| Metadata is safe | Object → Metadata → only `analysis-id`, `org-id`, `report-version`, `checksum-sha256`, `generated-at`. **No title, vendor, filename or quote.** |
| Versions not overwritten | Re-generate; a `report-v2.pdf` appears alongside v1 |
| Download works | UI → Download report streams the PDF |
| Cross-tenant blocked | Second org → `GET /api/v1/analyses/{other-id}/report` → 404 |
| Link expiry | Pre-signed URL stops working after `AWS_REPORT_URL_TTL_SECONDS` |
| Failure isolation | Temporarily set a wrong bucket name; the analysis must still reach `complete` |

### A8. Live SES verification

```
AWS_SES_ENABLED=true
```

| Check | Expected |
|---|---|
| Subject | `ClauseGuard analysis complete - {document_title}` |
| Body | Status, risk score, band, severity counts, not-legal-advice notice |
| Attachment or link | PDF attached under the size limit, otherwise a short-lived link |
| Message id recorded | `report_deliveries.provider_message_id` populated |
| Recipient fixed | The resend endpoint accepts **no** recipient parameter |
| Duplicate protection | Resend twice quickly — one send, one suppressed |
| Failure isolation | Break the sender temporarily; analysis status must not change |

**In sandbox, the recipient must also be a verified identity** or SES returns
`MessageRejected` — that is expected, not a bug.

### A9. Rollback

Set both flags back to `false` and restart. The application returns to Supabase
Storage and the console/SMTP provider immediately; no data migration is needed,
and reports already in S3 stay there.


---

# Production deployment runbook (Vercel + Render + Supabase + Upstash)

> **Status: not yet deployed.** No live URL is claimed anywhere in this
> repository. Every step below still needs to be performed.

**Prerequisite: the repository must be committed and pushed to GitHub.**
Vercel and Render both deploy *from GitHub* — they cannot see a local folder.

## D1. Supabase (production project)

1. Create a **new** project — do not reuse a development one.
2. **Database → Extensions**: confirm `vector` is available. `pg_trgm` is
   optional (the trigram index is skipped if absent).
3. Apply migrations from your machine, pointed at production:

   ```powershell
   cd backend
   $env:DATABASE_URL = "<supabase connection string>"
   python -m scripts.migrate      # 12 migrations, no --local-shim for Supabase
   python -m scripts.seed         # default policy + 12 categories
   ```

4. Verify in the SQL editor:

   ```sql
   SELECT count(*) FROM schema_migrations;                       -- expect 12
   SELECT count(*) FROM policy_rules;                            -- expect 12
   SELECT count(*) FROM pg_policies WHERE schemaname = 'public'; -- expect 25+
   SELECT id, public FROM storage.buckets;                       -- documents, reports; both public = false
   ```

   Migrations create both private buckets and their RLS policies — no dashboard
   work is needed. **If either bucket shows `public = true`, stop and fix it.**

5. **Authentication → URL Configuration** — leave until step D5, when the Vercel
   URL exists.

## D2. Upstash Redis

1. Create a database in a region near your Render service.
2. Copy the **TLS** connection string — it must start with `rediss://`.
3. Use it as `REDIS_URL` for **both** the API and the worker. They must share one
   instance: the API pushes to the queue and the worker blocks on it.

> The worker uses blocking `BRPOPLPUSH`. If Upstash rejects blocking commands on
> your plan, the worker will never claim a job — use a standard Redis instance.

No Redis credential ever reaches the frontend.

## D3. Render API (Web Service)

Easiest path — **Blueprint**: Render → New → Blueprint → select this repo. It
reads [`render.yaml`](../render.yaml), which defines the API and worker together.

Manual equivalent:

| Setting | Value |
|---|---|
| Type | Web Service |
| Runtime | Docker |
| Root directory | `backend` |
| Dockerfile | `./Dockerfile` |
| Branch | `main` |
| Health check path | `/health` |
| Start command | *(leave blank — the Dockerfile `CMD` binds `$PORT`)* |

Set every variable marked `sync: false` in `render.yaml` via the dashboard.
`CORS_ORIGINS` and `APP_BASE_URL` need the Vercel URL, so set them in step D5.

Verify after deploy:

```bash
curl https://<api>.onrender.com/health         # {"status":"ok",...}
curl https://<api>.onrender.com/health/ready   # database:true, redis:true
```

If `/health/ready` reports `database: false`, `DATABASE_URL` is wrong or the
migrations were not applied.

## D4. Render worker (Background Worker)

Same repo, same `backend` root, same Dockerfile, **same environment variables**.
Command: `python -m app.worker`. A background worker exposes no HTTP endpoint and
takes no health check path.

Verify in the logs: `worker starting`, then `redis connected`. An idle queue is
silent by design — no output does **not** mean it is broken.

## D5. Vercel frontend

| Setting | Value |
|---|---|
| Framework | Next.js (auto-detected) |
| Root directory | `frontend` |
| Install / Build | `npm ci` / `npm run build` |

Environment variables — **exactly these three**:

```
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_ANON_KEY
NEXT_PUBLIC_API_BASE_URL      = https://<api>.onrender.com
```

Never put the service-role key, `ANTHROPIC_API_KEY`, `REDIS_URL`, `DATABASE_URL`
or any AWS credential here. `NEXT_PUBLIC_*` values are inlined into the browser
bundle at build time and are readable by anyone.

## D6. Wire the origins together

Once Vercel gives you a URL:

1. **Render** (API *and* worker): set
   `APP_BASE_URL = https://<app>.vercel.app` and
   `CORS_ORIGINS = https://<app>.vercel.app`, then redeploy.
   Production startup **fails** if `CORS_ORIGINS` is `*`.
2. **Supabase → Authentication → URL Configuration**:
   - Site URL: `https://<app>.vercel.app`
   - Redirect URLs: `https://<app>.vercel.app/**`
3. Confirm the API allows only that origin:

   ```bash
   curl -sI -H "Origin: https://<app>.vercel.app" -X OPTIONS \
     https://<api>.onrender.com/api/v1/documents | grep -i access-control-allow-origin
   curl -sI -H "Origin: https://evil.example" -X OPTIONS \
     https://<api>.onrender.com/api/v1/documents | grep -i access-control-allow-origin
   ```

   The second must **not** echo the evil origin.

## D7. Production smoke test

| # | Step | Expected |
|---|---|---|
| 1 | Open the Vercel URL | Landing page renders |
| 2 | Register, then sign out and back in | Session restores on refresh |
| 3 | Visit `/dashboard` signed out | Redirects to `/login` |
| 4 | Upload `backend/tests/fixtures/sample_eula.txt` | Document reaches `ready` |
| 5 | Start analysis | API returns `202`; status `queued` |
| 6 | Watch Render worker logs | Job claimed; stages advance |
| 7 | Watch the UI | parsing → chunking → retrieving → extracting → verifying → scoring |
| 8 | Analysis finishes | Status `complete` or `partial` |
| 9 | Open a finding | Source pane highlights the exact clause |
| 10 | Check quarantined findings | Hidden by default, excluded from the score |
| 11 | Accept a finding, reload | Review persisted |
| 12 | Download the PDF report | Streams `application/pdf` |
| 13 | Second org, other org's analysis id | `404` |
| 14 | Grep API + worker logs | No contract text, no quotes, no keys, no emails |
| 15 | Grep logs for AWS/GCP calls | None — both flags are `false` |

## D8. Rollback

| Scenario | Action |
|---|---|
| Bad frontend deploy | Vercel → Deployments → promote the previous build (instant) |
| Bad API deploy | Render → Events → roll back. Jobs queue safely in Redis meanwhile |
| Bad worker deploy | Roll back the worker only; in-flight analyses resume from `completed_categories` |
| Bad migration | Forward-only. Write a reversing migration. **Snapshot Supabase before migrating** |
| Runaway cost | Lower `RATE_LIMIT_ANALYSES_PER_HOUR`, or scale the worker to zero — the API stays up and jobs queue |


---

# Google Cloud Run deployment (primary backend target)

> **Status: not deployed.** Scripts are written and lint/test-verified; no GCP
> resource has been created. `render.yaml` is retained as an alternative.

Frontend stays on Vercel. Supabase and Upstash are unchanged.

## C1. Project and APIs

```bash
gcloud auth login
gcloud projects create clauseguard-prod --name="ClauseGuard"   # or use an existing one
gcloud config set project clauseguard-prod

cd deploy/cloudrun
cp env.example.sh env.sh          # env.sh is gitignored
# edit env.sh: GCP_PROJECT_ID, GCP_REGION
source env.sh

./deploy.sh apis                  # run, cloudbuild, artifactregistry, secretmanager
```

Billing must be enabled on the project, even when covered by credits.

## C2. Least-privilege runtime identity

```bash
./deploy.sh sa
```

Creates `clauseguard-run@…` with **only** `roles/secretmanager.secretAccessor`.
No storage, BigQuery or Vertex role is granted — this deployment needs none.

## C3. Secrets

```bash
./secrets.sh
```

Prompts for eight values and pipes each straight into Secret Manager. Values are
never echoed, never written to disk, never committed. Cloud Run mounts them by
reference (`NAME:latest`) at runtime, so no secret ever enters an image layer.

## C4. Redis preflight — run this before deploying

```bash
cd ../../backend
REDIS_URL="<your upstash rediss:// url>" python -m scripts.preflight_redis
```

The worker reserves jobs with a blocking `BRPOPLPUSH`. Some serverless Redis
tiers reject blocking commands, and the failure is silent: the worker starts,
looks healthy, and never claims a job. This script exits non-zero if that would
happen. **Do not skip it.**

## C5. Build and deploy

```bash
cd ../deploy/cloudrun && source env.sh
./deploy.sh repo      # Artifact Registry
./deploy.sh build     # Cloud Build -> pushes the backend image
./deploy.sh api       # Cloud Run service; prints the API URL
./deploy.sh worker    # Cloud Run worker pool
```

Or `./deploy.sh all`.

The API needs no start-command override: the Dockerfile `CMD` already binds
`0.0.0.0:${PORT}`, and Cloud Run injects `PORT`.

## C6. Why a worker pool

The analysis worker is a **pull-based consumer with no HTTP surface** — it
blocks on Redis and never serves a request. That conflicts with a Cloud Run
*service* in two ways:

1. A service must answer a startup probe on `$PORT`. The worker binds nothing,
   so the revision would fail to start.
2. Cloud Run throttles CPU to near zero outside request handling by default, so
   the consumer loop would stall.

**Cloud Run worker pools** are built for exactly this shape: no HTTP, no
throttling, one warm instance. `deploy.sh worker` detects availability with
`gcloud beta run worker-pools --help` and uses them when present.

### Worker fallback (if worker pools are unavailable)

If the detection fails, `deploy.sh worker` stops rather than deploying something
broken. Two options:

- **Cloud Run service with `--no-cpu-throttling --min-instances=1`.** Requires
  adding a small HTTP health listener to `app/worker.py` so the startup probe
  passes. That is a code change, so it is deliberately *not* applied here.
- **Compute Engine `e2-micro`.** No code change; often free tier. Run the same
  image with `python -m app.worker`.

## C7. CORS, after Vercel exists

```bash
# set FRONTEND_URL in env.sh, then:
source env.sh && ./deploy.sh api
```

Production startup refuses `CORS_ORIGINS="*"`, so this must be the real origin.
Also add the Vercel URL to Supabase → Authentication → Site URL and Redirect URLs.

## C8. Verify

```bash
API=$(./deploy.sh urls | tail -1)
curl -fsS "$API/health"          # {"status":"ok",...}
curl -fsS "$API/health/ready"    # database:true, redis:true
./deploy.sh logs                 # API + worker logs
```

Worker logs should show `worker starting` then `redis connected`. **An idle
queue is silent by design** — no output does not mean it is broken.

## C9. Estimated cost

| Resource | Config | Approx/month |
|---|---|---|
| Cloud Run API | 1 vCPU / 512Mi, min 0 | $0–5 (scales to zero) |
| Cloud Run worker | 1 vCPU / 512Mi, min 1, always-on CPU | $8–15 |
| Artifact Registry | one image | <$1 |
| Secret Manager | 8 secrets | <$1 |
| **Total** | | **~$10–20**, covered by credits |

The warm worker instance dominates — unavoidable for a continuous consumer.

## C10. Rollback

```bash
gcloud run services update-traffic clauseguard-api --to-revisions=PREVIOUS=100 \
  --region="$GCP_REGION"
```

Roll the worker back the same way. Jobs queue safely in Redis meanwhile, and an
interrupted analysis resumes from `completed_categories`.
