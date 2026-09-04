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
`docs/16-phase3-capability-engine.md`. Since the historical-corpus
consolidation pass, a deterministic vocabulary/capability bootstrap proposes
that catalogue from the captured corpus for curator review, and a corpus
trend-analytics layer (Trends) answers "what has this corpus historically
required, and how has that changed" with sample sizes and a documented,
non-predictive trend classification — see
`docs/18-consolidation-and-analytical-foundation.md`.

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
- **Raw documents are processed, never re-captured.** `jobber.document` is
  immutable source evidence; turning an existing raw posting into a role
  (`job_posting_extract`) always reuses that same document — it never creates
  a second one. Historical postings are analysed in the professional/
  labour-market context of their *original* posting date, not today's.
  Duplicate/concurrent processing of the same document is protected at the
  database level, and every attempt (success or failure) is recorded in
  `jobber.extraction_run`. See `docs/17-document-processing-pipeline.md`.
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
  `docs/16-phase3-capability-engine.md`. **The engine and catalogue tooling
  are implemented and tested; the production catalogue itself is not yet
  populated (0 curated capabilities today) and the ≥0.80 capability-agreement
  evaluation gate has not been run against real hand-labelled data** — see
  that doc's §0.1 before treating any capability-fit output as validated.
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
   filterable by career track, server-side paginated, and temporally
   filtered (defaults to recent/current roles — see "All years"/a specific
   year to browse the full 2008–2025 historical corpus; docs/18 §3).
4. **Space** — a 3D PCA starfield of every captured role, target, and your
   profile, positioned by embedding similarity, with screen-space-sized
   markers (they don't balloon as you zoom in — docs/18 §1) and a temporal
   filter (defaults to all-time, unlike Dashboard — docs/18 §2). If it
   reports too few embedded points despite roles being loaded (typically a
   batch of roles captured before an embedding-model change, or before
   Phase 2 moved embeddings into their own table), it shows exactly how many
   roles are loaded vs. embedded and offers a one-click rebuild — see
   "Maintenance scripts" below for the equivalent command-line path.
4a. **Trends** — descriptive statistics over your captured role corpus:
    what requirements are most common, how they trend over time
    (emerging/increasing/persistent/declining/sparse — a deterministic,
    documented classification, never a forecast), how they compare by
    country/seniority/track, and what co-occurs together. Every number
    carries its own sample size. See docs/18 §7/§8/§9.
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
   suggestion lands unreviewed until you accept or reject it. If the
   canonical vocabulary has no candidates to even consider yet, the app says
   so explicitly rather than implying your evidence is weak (docs/18 §11).
9a. **Capabilities** — curate the capability catalogue: create/edit a
    capability's demonstration standard and depth/autonomy thresholds, and
    manage its core/supporting/contextual component edges to atomic concepts.
    A "Proposed" filter surfaces candidate capabilities/component edges from
    the vocabulary bootstrap (docs/18 §6/§10) for accept/reject/merge review
    — nothing bootstrap-proposed ever affects matching or coverage until
    accepted.
9b. **Vocabulary** — the proposal queue groups lexical duplicates ("Solvency
    II"/"SII") into one review card (docs/18 §6.1); resolving it resolves
    every variant at once. Pending clusters are ranked by a deterministic,
    documented curation-priority score (distinct-role/year/seniority/country
    coverage, never raw mention count) into High/Medium/Low/Sparse bands, with
    server-side filters, search, and pagination so the full queue is never
    loaded into the browser at once; each card carries the evidence behind
    its score plus advisory noise/sparse flags, and batch accept/reject
    requires an explicit selection and a pre-execution confirmation showing
    exactly what will change — see `docs/19-vocabulary-prioritisation-and-
    curation.md`.
9c. **Coverage** — your personal capability coverage, grouped Evidenced /
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

### Temporal filtering — implemented (`docs/18-consolidation-and-analytical-foundation.md`)

The historical corpus (`docs/17-document-processing-pipeline.md`) is
ingested (~307 roles, 2008–2025) and Dashboard/Space both now have temporal
filtering, on the asymmetric defaults this note originally called for:
Dashboard defaults to recent/current roles (with "all years"/a specific year
one click away, docs/18 §3); Space defaults to all-time, since the
historical cloud's shape is itself analytically useful there (docs/18 §2).
An animated year-by-year "time travel" progression through Space is not yet
built — the temporal state/API is deliberately shaped so it can be added
without a redesign (docs/18 §2) — and corpus trend analytics/classification
over the same temporal dimension is now its own page, **Trends** (docs/18
§7/§8/§9).

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

### Maintenance scripts

- `backend/scripts/rebuild_embeddings.py --roles [--force]` — backfills/
  rebuilds `role_instance` embeddings against `DATABASE_URL`. Safe against
  production: it only ever writes `jobber.d_embedding` rows with
  `owner_kind='role_instance'`, never touches `role_instance`/`document`
  rows, and defaults to computing only roles missing a *current-model*
  embedding. `POST /api/space/rebuild-role-embeddings` does the same thing
  over HTTP (also surfaced as a button on the Space page when it detects a
  mismatch). See `docs/16-phase3-capability-engine.md` §18.
- `backend/scripts/seed_phase3_eval_sample.py` — **local demo/illustration
  only, never run against production or a database whose capability
  catalogue matters.** Seeds 5 hand-authored capabilities to prove the
  Phase 3 evaluation machinery works end to end. See its own docstring and
  `docs/16-phase3-capability-engine.md` §13.
- `backend/scripts/bootstrap_vocabulary.py [--dry-run]` — the vocabulary/
  capability bootstrap (docs/18 §6): proposes canonical-concept clusters and
  candidate capabilities/component edges from the captured corpus. Safe
  against production in the sense that everything it writes is
  `status='proposed'`, invisible to matching/coverage until a curator
  reviews it in Vocabulary/Capabilities — but it is a real write, so run
  `--dry-run` first and only run it for real against production with
  explicit sign-off. Never invoked automatically or from any API route.

```bash
cd backend
pytest
```
