# 20 — Render Deployment

**Status:** implemented
**Related:** `render.yaml`, `backend/app/auth.py`, `docs/15-security-and-rls.md`

This is a step-by-step guide for an operator who has never used Render
before. It assumes no prior Render knowledge. If you get stuck, the
**Troubleshooting / known caveats** section at the end covers the issues
most likely to come up.

## 1. What you're deploying

**One** Render **Web Service**. FastAPI serves both the `/api/*` backend
and the built React frontend from the same URL — there is no separate
frontend service, no CORS to configure, and the app's existing relative
`/api/...` calls work completely unchanged.

```text
Browser (phone / tablet / laptop)
   │ HTTPS
   ▼
Render Web Service (this repo)
   ├── /api/*        → FastAPI
   └── everything else → built React app (SPA)
   │
   ▼
Existing Supabase Postgres (unchanged — this deploy never touches it
structurally; see §5 below)
```

**Why one service instead of two:** the frontend already calls relative
`/api/...` paths and expects Vite's dev proxy to stand in for "same
origin" locally. Keeping that true in production (one URL) means no CORS
setup, no cross-origin cookie configuration, and no `VITE_API_BASE_URL` —
the simplest deployment that is still fully secure for a single operator.
A two-service split (separate Static Site + Web Service) is documented as
a fallback in §13 if you ever need it, but isn't necessary here.

This deploy does **not** move your data anywhere. The backend keeps
connecting directly to your existing Supabase Postgres project, exactly as
it does today from your laptop — see §5.

## 2. One-time: generate your two secrets

You need two values before creating the Render service. Run these on your
own machine (or in a Render Shell after the first deploy) wherever you
have the backend's Python virtualenv active — see `backend/scripts/`.

**Your login password hash** (`APP_AUTH_PASSWORD_HASH`):

```bash
cd backend
python scripts/hash_password.py
```

It prompts for a password (twice, hidden input) and prints a line like:

```text
APP_AUTH_PASSWORD_HASH=$2b$12$....................................
```

Copy the whole value after the `=`. This is the *only* password this app
has — there are no separate accounts. Pick something you'll remember; it
is never stored anywhere except as this one-way hash.

**Your session signing secret** (`APP_SESSION_SECRET`):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the printed hex string. This is what signs the login session cookie —
it is not a password you need to remember, just a long random value.

Keep both values somewhere safe (a password manager). You'll paste them
into Render in §4. **Never commit either value to git.**

## 3. Create the Render service

1. Push this repository to GitHub (if it isn't already).
2. In the Render dashboard: **New +** → **Blueprint**.
3. Connect the GitHub repository. Render will detect `render.yaml` at the
   repo root and propose one service named `cp-enterprise`.
4. Click through to create it. Render will NOT start a working deploy yet
   — it's still missing the secret environment variables (next step).

If you prefer not to use the Blueprint flow, you can create a Web Service
manually with these settings (this is exactly what `render.yaml`
specifies):

| Setting | Value |
|---|---|
| Repository / root directory | repo root (not `backend/` or `frontend/`) |
| Runtime | Python 3 |
| Build command | `cd frontend && npm ci && npm run build && cd ../backend && pip install -r requirements.txt` |
| Start command | `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers` |
| Health check path | `/api/health` |
| Plan | Starter (see §14 — Free tier is not recommended) |

## 4. Set environment variables

In the Render service → **Environment**, set:

| Variable | Required? | Value |
|---|---|---|
| `DATABASE_URL` | **Required** | Your Supabase project's Postgres connection string (see §5) |
| `APP_AUTH_PASSWORD_HASH` | **Required** | From §2 |
| `APP_SESSION_SECRET` | **Required** | From §2 |
| `APP_ENV` | Required | `production` (already set by `render.yaml`) |
| `OPENAI_API_KEY` | Optional | Only needed for AI-assisted extraction. Leave unset to run without AI features. |
| `CP_AI_MODEL` | Optional | Defaults to `gpt-4o-mini` (already set by `render.yaml`) |
| `APP_CORS_ORIGINS` | Not needed | Only for the two-service variant, §13 |

The app **fails to start** (deploy shows as failed, with a clear error in
the logs) if `DATABASE_URL`, `APP_AUTH_PASSWORD_HASH`, or
`APP_SESSION_SECRET` is missing — this is deliberate (§8 "fail-closed"),
not a bug: it's safer for a misconfigured deploy to refuse to run than to
silently serve the app without real authentication.

## 5. Set `DATABASE_URL`

Use the same Supabase connection string this app already uses from your
laptop today — copy it out of your local `.env` file, or from the Supabase
dashboard (**Project Settings → Database → Connection string**), and paste
it into Render's `DATABASE_URL`. It must be the **privileged/service-role**
connection string (never the `anon` key) — see `docs/15-security-and-rls.md`
§1, which this deploy does not change.

**Connection pooling:** a Render Web Service is a long-running server, the
same connection pattern your laptop already uses (`backend/app/db.py` opens
a small persistent `psycopg_pool`, 1–10 connections). This is exactly the
workload Supabase's **direct/session** connection string is meant for. If
Supabase's dashboard offers both a direct connection string and a
"Transaction pooler" (pgbouncer, typically port 6543) string, prefer the
**direct/session** one (port 5432) for this app — the same one you already
use locally — since the app holds a real connection pool rather than
opening one connection per request. Only reach for the transaction pooler
if Supabase's dashboard is warning you about a connection-count limit; if
you do, verify pgvector queries and the migration runner still work (some
poolers restrict session-level features), and note the manual RLS
migration below is unaffected either way.

**Not changed by this deploy:** `backend/migrations/manual/9001_enable_profile360_rls.sql`
is still not applied automatically by anything — not the app, not this
deploy. Enabling RLS on `profile360` remains the separate, deliberate
decision `docs/15-security-and-rls.md` §3 describes. Deploying to Render
does not change that judgment call or make it more urgent by itself.

## 6. Deploy

With the Blueprint flow, Render deploys automatically once the required
env vars are set (or click **Manual Deploy → Deploy latest commit**).
Watch the **Logs** tab.

**What a successful deploy looks like:**

```text
==> Running build command 'cd frontend && npm ci && npm run build && ...'
... npm output, then vite build output ...
... pip install output ...
==> Build successful
==> Running 'cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers'
INFO:     Started server process [...]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:10000
==> Your service is live 🎉
```

Render then polls `/api/health` and marks the service **Live** (green)
once it responds `200 {"status": "ok"}`.

If instead the deploy shows **Failed** and the logs end with something
like `app.config.ConfigError: APP_SESSION_SECRET is not set` — an
environment variable from §4 is missing or misspelled. Fix it and Render
will redeploy automatically.

## 7. First login

Open the service's `https://<your-service>.onrender.com` URL. You should
see a minimal "Career Navigator — sign in to continue" screen (never a
raw error page). Enter the password you hashed in §2. You should land on
the normal Dashboard. A **Log out** button is in the top-right of the nav
bar.

If the page instead shows a blank screen or a browser network error,
check the Render logs — see §14.

## 8. Authentication design (for reference)

- One shared password for one operator — no separate accounts, no OAuth.
- The password is never stored — only a bcrypt hash
  (`APP_AUTH_PASSWORD_HASH`), checked by `bcrypt.checkpw` in
  `backend/app/auth.py`.
- A successful login sets one signed, `HttpOnly`, `SameSite=Strict` cookie
  (`itsdangerous`-signed via Starlette's `SessionMiddleware`) containing
  nothing but `{"authenticated": true}` — no password, no token that could
  be replayed elsewhere. In production (`APP_ENV=production`) it is also
  marked `Secure` (HTTPS-only).
- The signature embeds a timestamp and is re-checked server-side on every
  request — a session simply stops working 7 days after login
  (`SESSION_MAX_AGE_SECONDS` in `app/auth.py`) even if the cookie is
  replayed, no server-side session table required.
- **Fail-closed:** `backend/app/main.py` calls `config.session_secret()`
  and `config.auth_password_hash()` at import time — if either environment
  variable is missing, the process never starts. There is no environment
  or hostname-based bypass anywhere in the app.
- **Brute-force protection:** an in-process limiter blocks an IP after 5
  failed logins within 60 seconds (`backend/app/auth.py`). It resets on
  deploy/restart — an accepted tradeoff for a single-instance, single-user
  deployment (see §14).
- **CSRF:** `SameSite=Strict` means the browser never attaches the session
  cookie to a request originating from another site, which is the primary
  CSRF defense here — appropriate for a private single-user tool without
  adding a separate CSRF-token system.

## 9. Protected endpoint policy

Centralized in one place — `backend/app/main.py`'s router registration —
not sprinkled across route files. The complete public allowlist:

```text
GET  /api/health
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/status
```

Every other `/api/*` route (all 16 routers — roles, vocabulary,
capabilities, profile360, import, etc.) requires a valid session via a
router-level `Depends(require_auth)`. `backend/tests/test_auth.py::test_api_auth_cannot_be_bypassed_by_calling_endpoints_directly`
walks the app's actual OpenAPI schema and asserts this holds for every
registered route — a future router added without that dependency fails
this test rather than shipping unprotected.

The built frontend's static files (HTML/JS/CSS) are served without auth —
they contain no data, only UI code, and every real data request they make
is one of the protected calls above.

## 10. SPA routing

`backend/app/main.py` serves the built frontend for any path that isn't
`/api/*` and isn't a real static file, falling back to `index.html` so
client-side routes resolve correctly. This means:

- Refreshing `/vocabulary`, `/roles/<uuid>`, `/comparison/<uuid>`, etc.
  directly returns the app shell (which then renders the right page
  client-side), not a 404.
- A request to a genuinely missing `/api/...` route still 404s normally —
  the SPA fallback explicitly refuses any path starting with `api/`, so a
  typo'd or removed API route is never silently served the HTML shell.

Verified for every route listed in the brief by
`backend/tests/test_auth.py` (`test_deep_frontend_route_refresh_serves_index_not_404`
and friends) and by an end-to-end browser smoke test (§11 of the delivery
report) driving direct navigation to each one.

## 11. Rollback

Render keeps every previous deploy. To roll back:

1. Service → **Events** (or **Deploys**) tab.
2. Find the last known-good deploy.
3. Click **Rollback to this deploy** (or **Redeploy**).

This only changes which build is running — it does not touch Supabase.
Since this app has no Render-side database, there is no data migration to
reverse; the only thing to check after a rollback is whether the rolled-
back code is compatible with whatever migrations have already run against
Supabase (migrations are additive/idempotent — see §14 below and
`docs/14-phase2-postgres-architecture.md`).

## 12. Rotating the password or session secret

**Change the login password:** run `python backend/scripts/hash_password.py`
again with the new password, then update `APP_AUTH_PASSWORD_HASH` in
Render's environment variables and save — Render redeploys automatically.
Existing sessions keep working until they naturally expire (7 days) since
the session cookie doesn't contain the password.

**Rotate the session secret** (e.g. if you suspect the value leaked):
generate a new one (§2) and update `APP_SESSION_SECRET` in Render. This
immediately invalidates every existing session (everyone, including you,
is signed out and must log in again) — expected and harmless for a
single-user tool.

## 13. Alternative: two-service deployment

Not needed for this app today, but if you ever want the frontend on its
own Render Static Site (e.g. to front it with a CDN/custom domain
independently of the API):

1. Create a second Render service: **Static Site**, root `frontend/`,
   build command `npm ci && npm run build`, publish directory `dist`.
2. Keep the backend as its own Web Service (same build/start commands as
   above, minus the `cd frontend && npm ci && npm run build &&` prefix).
3. Set `APP_CORS_ORIGINS` on the backend to the Static Site's exact
   `https://...onrender.com` origin.
4. The frontend would then need `VITE_API_BASE_URL` pointed at the
   backend's own URL, and every `fetch` in `frontend/src/lib/api.ts` would
   need `credentials: 'include'` added (same-origin requests send cookies
   automatically; cross-origin ones don't) — not implemented in this build
   since the same-origin design in §1 avoids needing it.

## 14. Troubleshooting / known caveats

- **Build fails with `npm: command not found`.** This blueprint assumes
  Render's native Python build image includes Node.js (the common,
  documented pattern for a Python-backend-plus-built-SPA-frontend service
  on Render, and the one this doc is written around) — this could not be
  verified by an actual Render deploy from this build's sandboxed
  environment. If your Render build environment doesn't have Node
  available, the fix is to switch the service to a Docker runtime with a
  small multi-stage Dockerfile (Node stage builds `frontend/dist`, Python
  stage runs the backend and copies it in) — not included here to avoid
  shipping an untested Dockerfile, but straightforward if you hit this.
- **Free plan not recommended.** Render's free tier spins the service down
  after inactivity — the next request pays a cold start (including
  re-downloading the embedding model, §15) of tens of seconds, which is a
  poor experience for a tool meant to be opened casually from a phone. The
  free tier's RAM (512 MB) is also tight once FastAPI + onnxruntime +
  scikit-learn are all loaded (see the delivery report's resource
  estimate). Starter is the recommended minimum.
- **Cold start after a deploy or restart.** The first request that needs
  an embedding (import, vocabulary bootstrap, rebuild) after a fresh
  deploy downloads the `BAAI/bge-small-en-v1.5` model (~100–150 MB) into
  `/tmp/fastembed_cache`. Every request until that finishes will be slow;
  once cached, subsequent calls on that same running instance are fast.
  This is expected and, at this app's usage scale, not worth paying for a
  persistent disk to avoid — see §17 of the delivery report.
- **Login rate limiting is per-instance, in-memory.** It resets on every
  deploy/restart and (per §8) is keyed by the request's client IP —
  correct behaviour, but only if `--proxy-headers` (already in the start
  command above) is actually honoring Render's `X-Forwarded-For`. This is
  a single-instance deployment (no autoscaling configured), so there is
  only ever one limiter to reset.
- **Migrations run on every startup, safely.** `backend/app/db.py::run_migrations()`
  records applied filenames in `jobber.migration_history` and only runs
  new ones — a redeploy or restart re-running it is a no-op. A genuinely
  failing migration raises and crashes startup (visible in Render's logs
  as a failed deploy), rather than being silently swallowed. See the
  delivery report §16 for the full analysis of why this is safe under
  Render's redeploy behaviour.
