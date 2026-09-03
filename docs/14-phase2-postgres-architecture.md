# 14 — Phase 2: Postgres/Supabase Architecture, Schema, and Deviations

**Status:** implemented (this document describes what Phase 2 actually built)
**Related:** `docs/11-capability-model-design.md` (capability model design — Phase 2
"Evidence" scope, superseded on persistence choice, see §1 below),
`docs/13-ai-task-layer.md` (AI task abstraction, extended here)

This document exists because the Phase 2 implementation prompt asserted a specific
already-migrated production state (a Supabase project `open-brain`, schema `jobber`
with 23 documents/23 role instances/327 skill observations/10 concept types/21 edge
rules, and a schema `profile360` with 7 documents/9 episodes/93 claims/89 evidence
records/45 capabilities) that this build environment has **no credentials to
inspect or connect to**. Per the prompt's own §18 instruction — "document
significant deviations rather than silently choosing one interpretation" — this
doc records exactly what was assumed, why, and what must be verified before this
code is pointed at the real database.

---

## 1. Deviation from docs/11 §3.5: Postgres, not SQLite

`docs/11-capability-model-design.md` §3.5 explicitly says "Stay on SQLite through
Phase 3" and gives a review trigger of >~50k concepts or multi-person use. Neither
trigger has been hit. **The Phase 2 prompt overrides this directly and explicitly**
("Do not continue building new functionality on SQLite"), citing an external fact
(the migration to Supabase) that doc 11 did not anticipate. This build follows the
Phase 2 prompt: Postgres is now the operational persistence layer. Doc 11's §3.5
reasoning about query complexity was correct and remains true — nothing in the
Postgres port changes the data model's logical shape, only where it lives.

## 2. No live database access in this build environment

This container has no `SUPABASE_URL`, `DATABASE_URL`, or any Postgres credential
for the `open-brain` project, and no MCP tool exposing one. It **does** have a
local Postgres 16 server (with `pgvector` installable via
`apt install postgresql-16-pgvector`) and network access to PyPI, which this build
used to:

- develop and run every migration against a real, disposable local Postgres;
- run the full backend test suite against that real Postgres (never SQLite, never
  a mock database) — see `docs/14` §7 and `backend/tests/conftest.py`;
- verify pgvector end-to-end (extension install, `vector` columns, `<=>` cosine
  distance operator) with the same driver (`psycopg` 3) the app uses.

It has **not** verified anything about the actual `jobber`/`profile360` schemas in
the real `open-brain` Supabase project. Every table this build treats as
"already migrated" is a **reconstruction**, not an observation. See §3.

**Before deploying this against the real project:** run
`backend/scripts/inspect_schema.py` (added by this build) with real credentials
and diff its output against `backend/migrations/`. Every migration in this build
uses `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` specifically so
that running them against a real schema that already matches is a no-op, and
running them against a real schema that differs fails loudly (a `CREATE TABLE`
that hits an existing table with incompatible constraints raises rather than
silently diverging) instead of corrupting data either way.

## 3. Reconstructed shape of the already-migrated `jobber` tables

The prompt names these as already migrated: `document` (23 rows), `role_instance`
(23), `role_skill_observation` (327), `concept_type` (10), `concept_edge_rule`
(21), `concept`, `concept_alias`, `migration_audit`.

This repository's own `docs/11-capability-model-design.md` §4.1–4.4 is the design
document these were almost certainly migrated *from* — the seeded counts match
exactly (10 concept types, 21 edge-grammar rows — this build's pre-Phase-2 SQLite
seed in `app/db.py` produces exactly those two numbers), which is strong internal
evidence the migration used this repo's existing schema/seed as its source, not an
independently-designed schema. On that basis:

| jobber table | Reconstructed from | Column-for-column source |
|---|---|---|
| `document` | doc 11 §4.1 | as specified, + new `provenance` column (§4 below) |
| `role_instance` | doc 11 §4.3 | as specified (this table was *designed* but never built pre-Phase-2 — doc 11's own Phase 0 and Phase 1 build notes flag it "unscoped" twice) |
| `role_skill_observation` | this repo's `job_role_skills` (`app/db.py`) | same columns, renamed table/FK to match the prompt's stated jobber name |
| `concept_type`, `concept`, `concept_alias`, `concept_edge`, `concept_edge_rule`, `concept_proposal` | doc 11 §4.2, this repo's existing seed | unchanged |
| `migration_audit` | prompt only, no shape given | not touched by this build at all — no code reads or writes it, so an incorrect guess here cannot break anything. If it exists, it is orthogonal to Phase 2. |

All of the above are declared as `CREATE TABLE IF NOT EXISTS` in
`backend/migrations/0001_jobber_foundation.sql`. **If the real tables differ**,
this statement is a no-op against them (Postgres does not alter an existing
table to match a `CREATE TABLE IF NOT EXISTS` body) and the application code's
queries — which are schema-qualified and column-named against the reconstruction
above — are what would need reconciling. That reconciliation is expected to be
small (renames/type tweaks) precisely because the migration's own numbers already
corroborate the reconstruction.

Local development / this build's tests never depend on the guess being *exactly*
right — they run the migration against a fresh, empty database, so "IF NOT
EXISTS" is exercised as a plain create, and behaviour is verified against that.

## 4. `document.provenance` — new column, not previously specified anywhere

The prompt requires that legacy-reconstructed documents ("their text was
reconstructed from legacy extracted posting records rather than guaranteed
immutable copies of the original adverts") remain distinguishable from genuine
original-source captures, permanently, because §5 makes claim requirements
conditional on it. Neither doc 11 nor the prompt names a column for this, so
this build adds one:

```sql
provenance TEXT NOT NULL  -- 'original_capture' | 'legacy_extracted' | 'user_paste' | 'unspecified'
```

Added via `ADD COLUMN ... DEFAULT 'legacy_extracted' NOT NULL` (backfills every
pre-existing row — i.e. everything already in `jobber.document` at the moment
this migration runs, which, per the prompt, is exactly the 23 legacy rows and
nothing else) and then `ALTER COLUMN ... DROP DEFAULT`, so every future insert
must state its provenance explicitly rather than silently inheriting a default —
see `backend/migrations/0001_jobber_foundation.sql`. This is the mechanism behind
§5's "stated/implied claims require document provenance": extraction refuses to
produce a `stated`/`implied` requirement claim against a document whose
provenance is `legacy_extracted` (see `backend/app/extraction.py`).

## 5. `profile360` — read-only, schema unknown beyond table names

The prompt names `profile360.documents/episodes/claims/evidence/capabilities/
contradictions/open_questions/snapshots` and describes `claims.basis` values
(`stated/implied/inferred/user_asserted/mixed`) and capability depth
(`exposed/applied/owned/set_standard`) — the same vocabulary doc 11 independently
designed for this repo. Column names beyond `id` are not given anywhere and are
not guessed. `backend/app/profile360_reader.py` therefore never hard-codes a
profile360 column name: it introspects `information_schema.columns` for an
allowlisted table on first use (cached), and exposes rows as plain dicts, with a
best-effort display-field picker (looks for common names like `text`,
`claim_text`, `statement`, `description`, `title`, `name` for human-facing
summaries; falls back to showing the raw row) rather than assuming a specific
field is present. This degrades gracefully regardless of the real column names.

**`profile360_claim_id`/`profile360_capability_id` are assumed `uuid`** (the
modern Supabase convention) in the jobber-side mapping tables
(`backend/migrations/0004_profile360_mapping.sql`). The real foreign-key
constraint to `profile360.claims(id)` / `profile360.capabilities(id)` is added in
a `DO $$ ... EXCEPTION WHEN others THEN RAISE NOTICE ... END $$` block that
degrades to an **unconstrained but still-typed column** (with a visible NOTICE at
migration time) if the live table/column/type does not match — never a hard
migration failure, and never silent. Application code validates the referenced
profile360 row exists (a live read) before writing a mapping row either way, so
referential integrity holds even where the DB-level FK could not be established.

## 6. `jobber` does not get a person-side claims table

Per the prompt's §1 architectural boundary, `profile360` is authoritative for
person-side evidence and must not be duplicated into `jobber`. Doc 11's
`evidence_claim` (person-side, subject = episode) is therefore **not** built in
`jobber` — only `requirement_claim` (role-side, subject = `role_instance`) is.
This repo's own pre-existing `person`/`episode`/`episode_document` tables (Phase
0, hand-entered, used by the History page) are carried over into `jobber`
unchanged for backward compatibility with that page, but they are not extended
with a claims layer — that is now `profile360`'s role. See
`backend/app/routes/comparison.py` for the one place a purely local,
evidence-free "the user asserts this" flag exists
(`jobber.person_capability_assertion`) — deliberately not a claim table (no
spans, no episodes, no modifiers): it is the minimal, honest expression of doc
11 §5.1's `user_asserted` basis ("no document supports it"), scoped to the
comparison UI's "I have done this" action, and it is not profile360's concern
because there is no evidence in it to own.

## 7. Local development and tests

Local dev connects to Postgres over `DATABASE_URL` — there is no local-Postgres
requirement for normal development, since the operational database is already a
hosted Supabase instance. This is what makes Windows setup simple: no native
Postgres install, no Docker Desktop, no compiler — `psycopg[binary]` ships
prebuilt wheels. See the README.

Tests are different: they must never touch the production database (prompt §16).
`backend/tests/conftest.py` requires a **separate** Postgres reachable via
`TEST_DATABASE_URL` (falls back to `postgresql://postgres:postgres@localhost:5432/postgres`
for local/CI convenience), creates a throwaway database per test session, runs
every migration against it, and wraps each test in a transaction that is rolled
back at teardown — real Postgres semantics, zero shared state between tests, and
categorically incapable of touching Supabase because it never receives that
connection string. If `TEST_DATABASE_URL`/local Postgres is unreachable, the
Postgres-backed tests skip with an explicit message rather than failing or
silently passing.
