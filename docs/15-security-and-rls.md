# 15 — Security Model: Backend Access, RLS, and profile360

**Status:** implemented (jobber) / manual step provided, not auto-applied (profile360)
**Related:** `docs/14-phase2-postgres-architecture.md`

## 1. How the backend connects

The FastAPI backend is the only thing that ever holds a database credential.
It connects to Postgres directly (via `psycopg`, see `backend/app/db.py`) using
a connection string in `DATABASE_URL`, which should authenticate as a
**privileged, server-side Postgres role** — on Supabase, that means the
`service_role` connection string (or an equivalent custom role granted
`BYPASSRLS`), never the `anon` key.

The React frontend never receives a Supabase URL, an anon key, or any database
credential. It talks exclusively to the FastAPI backend over `/api/...`, exactly
as it did before Phase 2 — this required no change, because the frontend never
had direct Supabase access to begin with (`frontend/src/lib/api.ts` only calls
relative `/api` paths). This satisfies the brief's §15 requirement structurally,
not by new code.

If direct browser-to-Supabase access is ever introduced later, it must ship
with its own least-privilege RLS policies *before* it ships — nothing in this
build assumes that will happen, and nothing here prepares for it speculatively.

## 2. `jobber` — RLS already enabled, left alone

Per the brief, `jobber`'s tables already have RLS enabled. This build does not
touch that: no migration in `backend/migrations/` disables, alters, or adds
policies to `jobber`'s RLS. The backend's `BYPASSRLS` role can read/write
regardless of what policies exist, so this build has nothing to add here and
nothing to verify beyond "the backend's role has BYPASSRLS" — see §1.

## 3. `profile360` — disabled today, a reviewed manual migration to close it

Most `profile360` tables have RLS disabled today — the brief names this as a
known issue. Fixing it is provided as
`backend/migrations/manual/9001_enable_profile360_rls.sql`, **not** run
automatically (not by `app.db.run_migrations()`, not by the test suite, not on
backend startup). The brief is explicit that enabling RLS blind can break
legitimate access, and this build has no way to observe what currently reads
profile360 in production (a separate `open-brain-mcp` Edge Function predates
this work entirely — `docs/10-career-nav-scoping.md`). That is a judgment call
for whoever operates the real project, informed but not made by this build.

**The threat model, concretely:**

- **Today:** RLS disabled on most profile360 tables means anyone holding the
  public Supabase `anon` key can read (and potentially write, depending on
  grants) the user's personal career evidence directly via PostgREST, with no
  application-layer control in between.
- **After the manual migration:** RLS is enabled and forced, with **zero**
  policies added for `anon`/`authenticated`. A role with `BYPASSRLS`
  (Supabase's `service_role`, or a custom role granted it) is unaffected by
  RLS regardless of policies — so if the FastAPI backend and the
  `open-brain-mcp` Edge Function both authenticate that way (the standard,
  recommended pattern for trusted server-side integrations), nothing breaks
  and the anon-access gap is closed completely.
- **The one thing to verify before applying it in production:** that nothing
  legitimate currently authenticates against profile360 as `anon` or
  `authenticated` rather than `service_role`. Check Supabase project logs or
  the Edge Function's own source for which key it uses. If something does,
  add an explicit least-privilege policy for it in the same sitting as
  applying the migration — the migration file's header repeats this.

## 4. What this build reads from profile360, and how

`backend/app/profile360_reader.py` is read-only: no `INSERT`/`UPDATE`/`DELETE`
statement in this codebase targets the `profile360` schema, and its
allowlisted table set is fixed to the eight tables the brief names
(`documents, episodes, claims, evidence, capabilities, contradictions,
open_questions, snapshots`) — there is no code path that can query an
arbitrary profile360 table from a request parameter. The only jobber-side
tables that reference profile360 rows at all are the two mapping tables
(`backend/migrations/0003_profile360_mapping.sql`), which store a foreign id
and mapping metadata, never a copy of the underlying claim/evidence text.

## 5. Secrets and environment configuration

`DATABASE_URL`, `OPENAI_API_KEY` and `CP_AI_MODEL` are read from the process
environment only (`backend/app/config.py`) — never committed, never logged.
`.env.example` at the repo root documents every variable the backend reads;
`.env` itself is gitignored (already was, for `backend/data/*.db`; extended to
cover `.env`/`.env.*` in this build — see `.gitignore`).
