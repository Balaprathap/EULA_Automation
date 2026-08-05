# Setup checklist — from zero to one real analysis

Everything below is required before ClauseGuard can process a real agreement.
Nothing here asks you to paste a credential into a chat or commit one: all
secrets live in `.env`, which is gitignored.

Status legend: ☐ not done · ☑ done

---

## 1. Supabase project

☐ Create a project at [supabase.com](https://supabase.com) (free tier is fine).

Copy these four values into `backend/.env` (or the repo-root `.env`):

| Where in the dashboard | Variable |
|---|---|
| Settings → API → Project URL | `SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_URL` |
| Settings → API → `anon` public key | `SUPABASE_ANON_KEY` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` |
| Settings → API → `service_role` key | `SUPABASE_SERVICE_ROLE_KEY` — **server only, never `NEXT_PUBLIC_`** |
| Settings → API → JWT Settings → JWT Secret | `SUPABASE_JWT_SECRET` |
| Settings → Database → Connection string (URI) | `DATABASE_URL` |

☐ **Authentication → URL Configuration**
   - Site URL: `http://localhost:3000`
   - Redirect URLs: add `http://localhost:3000/dashboard`

☐ *(Optional)* **Authentication → Providers → Google** — enables the
  "Continue with Google" button. Skip for the first run.

> Supabase now issues **ES256** access tokens. The backend fetches the public
> keys from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, so the API needs
> outbound HTTPS to `*.supabase.co`. Legacy HS256 projects still work.

## 2. Database migrations and seed

```powershell
cd C:\Users\balap\Downloads\EULA\backend
python -m scripts.migrate      # 12 migrations, from zero
python -m scripts.seed --demo  # default policy + 12 categories + sample document
```

Both are idempotent. Against a **local/docker** Postgres instead of Supabase, add
`--local-shim` to `migrate` (it creates the `auth`/`storage` schemas Supabase
normally provides). The flag is refused against a `*.supabase.co` host.

This also creates the private `documents` and `reports` storage buckets and
their RLS policies — **no manual dashboard work is needed.**

## 3. Redis

☐ Local (simplest): `docker compose up postgres redis`
☐ Or Upstash: copy the `rediss://` URL

| Variable | Example |
|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` |

> Blocking `BRPOPLPUSH` is required. Some serverless Redis products do not
> support blocking commands — use a standard instance if the worker cannot
> reserve jobs.

## 4. AI credentials

☐ **Anthropic** — [console.anthropic.com](https://console.anthropic.com). Requires
  billing credit. Roughly $0.15–0.30 per analysis.

☐ **Embeddings** — an OpenAI key from
  [platform.openai.com](https://platform.openai.com). Costs pennies
  (~$0.02 per million tokens).

| Variable | Notes |
|---|---|
| `ANTHROPIC_API_KEY` | Required |
| `ANTHROPIC_MODEL` | Defaults to a value in `.env.example`; verify against current Anthropic docs |
| `EMBEDDING_PROVIDER` | `openai` |
| `EMBEDDING_API_KEY` | Required |
| `EMBEDDING_MODEL` | `text-embedding-3-small` |
| `EMBEDDING_DIMENSIONS` | `1536` — **must match the `vector(1536)` column** |

> `EMBEDDING_DIMENSIONS` is baked into the schema. Changing to a model with a
> different width needs a migration and a re-embed of every chunk.

## 5. Backend configuration

| Variable | Local value |
|---|---|
| `ENVIRONMENT` | `development` |
| `APP_BASE_URL` | `http://localhost:3000` |
| `API_BASE_URL` | `http://localhost:8000` |
| `CORS_ORIGINS` | `http://localhost:3000` |
| `LOG_LEVEL` | `INFO` |
| `EMAIL_PROVIDER` | `console` — logs a redacted line, sends nothing |

Startup fails loudly and names any missing variable. In production it also
refuses a `*` CORS wildcard and the test-only embedding provider.

## 6. Frontend configuration

`frontend/.env.local` — **only these three**, all safe for the browser:

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Never put `SUPABASE_SERVICE_ROLE_KEY`, `ANTHROPIC_API_KEY`, or any AWS
credential here. `NEXT_PUBLIC_*` values are inlined into the browser bundle at
build time.

## 7. AWS — leave off for now

```
AWS_REPORT_STORAGE_ENABLED=false
AWS_SES_ENABLED=false
```

Reports go to Supabase Storage and email to the console provider until you
complete `docs/INFRASTRUCTURE.md` § AWS. Do not enable these before the core
analysis works end to end.

---

## Run it

```powershell
# Terminal 1 — infrastructure
docker compose up postgres redis

# Terminal 2 — API
cd backend
uvicorn app.main:app --reload --port 8000

# Terminal 3 — worker (at least one required)
cd backend
python -m app.worker

# Terminal 4 — frontend
cd frontend
npm install
npm run dev
```

## Verify before uploading anything

```powershell
curl http://localhost:8000/health/ready
```

Must return `{"status":"ready","checks":{"database":true,"redis":true}}`.
If either is false, stop and fix that first — the UI will render but every page
will show its error state.

## First analysis

1. Open <http://localhost:3000> and register.
2. Upload `backend\tests\fixtures\sample_eula.txt`.
3. Watch `python -m app.worker` output while it runs.
4. Expect stages: parsing → chunking → retrieving → extracting → verifying → scoring.

## Common failures

| Symptom | Cause |
|---|---|
| `Missing required environment variables: …` | Named variable absent from `.env` |
| `/health/ready` → `database: false` | `DATABASE_URL` wrong, or migrations not run |
| `/health/ready` → `redis: false` | Redis not running |
| Analysis stays `queued` | No worker running |
| Analysis fails at embedding | `EMBEDDING_API_KEY` empty or invalid |
| 401 on every API call | `SUPABASE_JWT_SECRET` wrong, or no outbound HTTPS to `*.supabase.co` for JWKS |
| `this database has no auth schema` | Migrating a plain Postgres — add `--local-shim` |
