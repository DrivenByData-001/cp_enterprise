# Career Navigator

A personal career navigation tool. Treats the job market as a multidimensional
space: your career narrative and captured job postings are embedded into the
same vector space, so "closest roles to me" and "gap to a target role" become
similarity computations, not guesswork — and, since Phase 2, an evidence-backed
capability model connects specific requirement claims on a role to specific
evidence about you, traceable in both directions. Since Phase 3, a
deterministic capability-coverage/role-fit engine turns curated capabilities
(economically meaningful units of "what you can do", composed from atomic
concepts) into four honest evidence states — see
`docs/16-phase3-capability-engine.md`.

**Persistence is Postgres** (a Supabase project in production; any Postgres —
including a disposable local one — for development and tests). SQLite is no
longer used by the running app; see `docs/14-phase2-postgres-architecture.md`
for the full architecture and the one-time SQLite→Postgres migration scripts
kept for reproducibility.

## How it fits together

- **Ingestion is source-aware.** The Import page's "Source-aware ingest" card
  captures raw pasted text or a selectable-text PDF as an immutable document
  first, then lets you run closed-vocabulary requirement extraction against
  it — separately, reviewable, never auto-accepted. The older
  "AI extraction (legacy flat fields)" flow still works unchanged for the
  original flat posting shape (title/org/salary/skills[]), and "Legacy JSON
  import" remains for migration/recovery. See `docs/13-ai-task-layer.md`.
- **Requirement extraction is closed-vocabulary.** Every requirement is either
  mapped onto an existing canonical concept (with a verbatim quote from the
  source, when the source document's provenance supports one) or filed as an
  unresolved `concept_proposal` for human review — extraction never invents a
  concept. See `docs/11-capability-model-design.md` §7.3 and
  `backend/app/extraction.py`.
- **profile360 is read-only, and never duplicated.** The user's deep career
  evidence (claims, capabilities, episodes) lives in a separate `profile360`
  schema owned by another tool. This app reads it and maps it onto the same
  canonical vocabulary jobber uses for roles — see the "profile360" nav page —
  without ever copying the underlying evidence. See
  `docs/14-phase2-postgres-architecture.md` §5/§6.
- **Comparison is evidence-backed, not scored.** Every requirement on a role
  shows one of four states — Evidenced / Partial / User-asserted / No evidence
  found — each traceable to its source on both sides. "No evidence found"
  never means "you lack this."
- **Capabilities are curated, coverage is derived.** A capability (e.g. "Lead
  a reserving process") is a deliberately curated concept with a
  demonstration standard and core/supporting/contextual atomic components.
  Whether *you* can evidence it is always computed by a deterministic engine
  from accepted claims — never extracted, never an AI judgment call, and
  compositional evidence (you've used the parts) alone can never become
  "evidenced" without something directly stating the whole capability. See
  `docs/16-phase3-capability-engine.md`.
- **Preferences are structurally separate from capability.** Would-you-enjoy-it
  is tracked on its own dimensions, with its own source/basis/confidence.
  Personality/psychometric material can only ever enter as a low-authority
  hypothesis — it has no path into a capability or comparison table.
- **Embedding is automatic and local.** No API key needed — the backend embeds
  text with a small local model (`BAAI/bge-small-en-v1.5` via `fastembed`,
  downloaded once on first use, runs on CPU) and stores vectors in Postgres via
  `pgvector`.
- **Target-role decomposition is still manual**, on purpose (see
  `docs/13-ai-task-layer.md` §1): paste `prompts/decompose_target_role.md`
  into Claude/ChatGPT, paste the resulting JSON into the Add Target page.
- **Everything else is mechanical**: rank by similarity to your profile, see
  it on a 2D/3D map.

## Stack

- **Backend**: Python (FastAPI) + Postgres (via `psycopg`), pgvector for embeddings
- **Frontend**: React + Vite
- **Embeddings**: local, via `fastembed` (no external API)
- **AI extraction**: OpenAI, via the native task layer (`backend/app/ai.py`)

## Running it

```bash
# backend — set DATABASE_URL first, see "First-time setup" below
cd backend
python -m uvicorn app.main:app --reload --port 8000

# frontend (separate terminal)
cd frontend
npm run dev
```

Open http://localhost:5173. The first import will download the embedding
model (~130MB, one-time, needs internet access to huggingface.co). The backend
applies any pending database migrations automatically on startup
(`backend/migrations/*.sql` — additive/idempotent, safe to re-run).

`OPENAI_API_KEY`/`CP_AI_MODEL` are only needed for AI-assisted extraction
(posting extraction, requirement extraction, profile360 mapping); without them
the rest of the app runs normally, and every AI attempt — including a failed
one from a missing key — is recorded, never silently swallowed.

## Using it

1. **Profile** — a read-only view of your current profile360 snapshot (the
   narrative itself is authored in profile360's own tool, not here) and its
   history. It gets embedded on demand for every similarity computation
   elsewhere in this app (Dashboard/Space/Targets).
2. **Import** — two independent paths, both fine to use:
   - *Source-aware ingest* — paste raw text (or upload a selectable-text PDF)
     to capture it as an immutable document + `role_instance`, then extract
     reviewable requirement claims against the canonical vocabulary from the
     role's Requirements page.
   - *AI extraction (legacy flat fields)* — the original one-shot flow into
     the flat posting shape (title/org/salary/skills[]/scores). Still fully
     supported; not yet wired onto the claim model.
   - *Legacy JSON import* remains for migration/recovery, or drop multiple
     `.json` files at once to bulk-import a folder you've already captured.
3. **Dashboard** — roles ranked by similarity to your current profile,
   filterable by career track.
4. **Space** — a 3D PCA starfield of every captured role, target, and your
   profile, positioned by embedding similarity.
5. **Targets** — a role you're navigating towards, real or imagined. Give
   `prompts/decompose_target_role.md` (plus supporting material) to
   Claude/ChatGPT, paste the resulting JSON into the Add Target page.
6. **Editing** — any posting or target can be edited from its detail page (a
   full overwrite of the legacy flat fields, not a merge — re-embeds it).
7. **Requirements** (a role's detail page → "Requirements") — run closed-
   vocabulary AI extraction against that role's source document, then accept
   or reject each proposed requirement claim.
8. **Compare** (a role's detail page → "Compare") — an evidence-backed,
   four-state comparison against everything mapped from profile360, with a
   one-click "I have done this" for anything you can personally assert with
   no document behind it.
9. **profile360** — browse read-only person-side claims/capabilities and
   request an AI-suggested mapping onto the canonical vocabulary — including,
   for claims, a capability-level attribution attempt ("Pass C") — every
   suggestion lands unreviewed until you accept or reject it.
9a. **Capabilities** — curate the capability catalogue: create/edit a
    capability's demonstration standard and depth/autonomy thresholds, and
    manage its core/supporting/contextual component edges to atomic concepts.
9b. **Coverage** — your personal capability coverage, grouped Evidenced /
    Partial / User asserted / No evidence found, each expandable into its
    full evidence trace back to profile360.
10. **Preferences** — record what you'd enjoy, separately from what you can
    do, with its own evidence basis (observed behaviour ranks above a
    psychometric/typology hypothesis, never the reverse).
11. **History** — a read-only browse of your career episodes from profile360,
    the authoritative person-side evidence store. There is no more hand-entry
    or timeline chart here; authoring episodes is profile360's own tool's job.

## Schema

See `docs/14-phase2-postgres-architecture.md` for the full `jobber` schema
(role_instance, document, concept vocabulary, requirement_claim,
extraction_run, profile360 mapping tables, preferences), confirmed by direct
live inspection of the production Supabase project (this build itself still
has no credential for it — read that doc's §0/§2 before pointing this at it).
`docs/11-capability-model-design.md` is the original design document the
schema implements; `docs/15-security-and-rls.md` covers the backend/RLS
security model.

## Deliberately out of scope for now

- Compensation/economics: gap-value ranking, archetype compensation,
  `d_gap_value`, monetary gap ranking, learning-time/transition-effort
  estimates (doc 11 Phase 4/5) — Phase 3 stops at the structural evidence
  picture (four states, blocking gaps), deliberately before anything
  monetary. See `docs/16-phase3-capability-engine.md` §17.
- `salary_benchmarks` / compensation modelling (doc 11 Phase 4).
- Prerequisite/adjacent/substitutable capability graphs and any transition-
  effort judgment layer (doc 11 Phase 5).
- Actuarial exam tracking.
- OCR for scanned/image-only PDFs — selectable-text PDFs only for now.
- A general-purpose web scraper for discovering candidate postings (brief
  §12) — ingestion is paste/upload only.
- Any cloud sync beyond the shared Postgres database itself.

### Note for future devs: time-evolution view (deferred)

`role_instance.posting_date` and `.captured_at` are tracked and shown
everywhere, and historic postings (paste-from-file, no URL) are a first-class
capture path. What's *not* built yet is doing anything with that time
dimension on the **Space** view — a date-range filter under the scatter in
`frontend/src/pages/Space.tsx`, defaulting to all-time, that re-requests
`GET /api/space` scoped to that window. Backend side, `backend/app/routes/space.py`
would need optional `from`/`to` query params filtering the roles selected
before the PCA fit. Held off until there's enough historic data loaded in to
actually make the view meaningful.

## First-time setup

Install the backend and frontend dependencies once:

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cd ../frontend
npm install
```

Then set `DATABASE_URL` in the shell where you start the backend (copy
`.env.example` to `.env`, or export it directly). This works identically on
Windows/macOS/Linux — `psycopg[binary]` ships prebuilt wheels, so there is
nothing to compile, and no local Postgres server is required: point
`DATABASE_URL` at your Supabase project's Postgres connection string and the
backend applies its own schema migrations on startup. If you'd rather point it
at a from-scratch local Postgres instead (no existing `jobber`/`profile360`
data), run `psql "$DATABASE_URL" -f backend/scripts/local_baseline.sql` once
first — the migrations assert a pre-existing baseline rather than creating it,
since creating it would be the wrong thing to do against a real database that
already has it (see `docs/14` §7).

```powershell
# PowerShell
$env:DATABASE_URL = "postgresql://postgres:<password>@<project-ref>.supabase.co:5432/postgres"
$env:OPENAI_API_KEY = "your-api-key"       # optional — only needed for AI extraction
$env:CP_AI_MODEL = "gpt-4o-mini"           # optional — only needed for AI extraction
```

Never commit real credentials — `.env`/`.env.*` are gitignored; `.env.example`
documents every variable.

### Running the tests

Tests need a *separate*, disposable Postgres — never your `DATABASE_URL` —
reachable via `TEST_DATABASE_URL` (defaults to a local
`postgresql://postgres:postgres@localhost:5432/postgres` if unset). Each test
session creates and drops its own throwaway database; nothing here can touch
production data. See `docs/14-phase2-postgres-architecture.md` §7.

```bash
cd backend
pytest
```
