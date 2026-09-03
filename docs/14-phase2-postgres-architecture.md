# 14 — Phase 2: Postgres/Supabase Architecture, Schema, and Deviations

**Status:** implemented; schema reconciled against a live inspection of the
production database (2026-09-03)
**Related:** `docs/11-capability-model-design.md` (capability model design — Phase 2
"Evidence" scope, superseded on persistence choice, see §1 below),
`docs/13-ai-task-layer.md` (AI task abstraction, extended here),
`docs/15-security-and-rls.md` (RLS/security model)

---

## 0. Schema provenance and the reconciliation pass

This build has never held a credential for the real Supabase project
(`open-brain`) — see §2. The first version of Phase 2 was therefore built
against a **reconstruction** of the already-migrated `jobber` schema, inferred
from `docs/11-capability-model-design.md`'s own design and the row counts the
implementation prompt quoted. That reconstruction assumed `BIGSERIAL`/`BIGINT`
primary keys and a `legacy_role_analysis` side table, among other things.

A subsequent reviewer inspected the live project directly and found the
reconstruction wrong in ways that matter at the type level: **every jobber and
profile360 entity id is `UUID`**, `role_instance` carries its legacy detail as
direct columns (not a side table), several column names differ
(`surface_form`/`canonical_concept_id` rather than `name`/
`resolved_concept_id`, `provenance_quality` rather than `provenance`,
`instance_type`/`target_basis` rather than `kind`), and `profile360` has
eleven RLS-disabled tables, not eight. Every section below states the
**confirmed** shape from that inspection, not the earlier guess — this
document no longer carries the two versions side by side, to avoid a future
reader copying the wrong one. Migrations, `app/db.py`, and every route were
rewritten against these confirmed facts; the 23 already-migrated documents,
23 role instances, and 327 skill observations in production were never
touched, converted, or re-shaped to fit this codebase — the codebase was
corrected to fit them.

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
local Postgres 16 server (with `pgvector`) and network access to PyPI, which this
build used to:

- develop and run every migration against a real, disposable local Postgres,
  bootstrapped with `backend/scripts/local_baseline.sql` (see §7) to the exact
  shape confirmed by the live inspection below;
- run the full backend test suite against that real Postgres (never SQLite,
  never a mock database) — see §7 and `backend/tests/conftest.py`;
- verify pgvector end-to-end (extension install, `vector` columns, `<=>` cosine
  distance operator) with the same driver (`psycopg` 3) the app uses.

The confirmed schema facts in §3–§6 come from a reviewer who inspected the real
`open-brain` project directly and reconciled this codebase against it — this
build's own environment still cannot connect to it. Every migration still uses
`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`, and
`0001_live_schema_preflight.sql` asserts the load-bearing facts below (id
columns are `uuid`) before any later migration runs, raising a clear error
immediately if a target database doesn't match, rather than corrupting data or
failing confusingly several files deep.

## 3. Confirmed shape of the already-migrated `jobber` tables

Live inspection (2026-09-03) confirmed the following for every core `jobber`
table already carrying production data (23 documents, 23 role instances, 327
skill observations, 10 concept types, 21 edge-grammar rows):

- **Every primary key is `UUID`** (`gen_random_uuid()`), not `BIGSERIAL`. Each
  table also keeps its original integer id as a separate
  `legacy_<thing>_id INTEGER UNIQUE` column (e.g.
  `document.legacy_document_id`, `concept.legacy_concept_id`) — these are
  carried over for cross-referencing against anything external that still
  cites the old numbering, but **UUID is identity**; nothing in this codebase
  reads or writes the `legacy_*_id` columns.
- **`jobber.document`** real columns: `id, legacy_document_id, source_key,
  kind, title, source, url, source_date, captured_at, content_text,
  content_sha256, content_kind, provenance_quality, source_payload, notes,
  created_at`. `source_key` (not a content hash) is the real identity column —
  see §4. `content_sha256` is computed but **not unique**: two distinct
  documents in production already share identical reconstructed text, so it
  is an informational duplicate-of signal only, never a dedup key (see
  `app/db.py::create_document` — every call inserts a new row).
- **`jobber.role_instance`** carries its full detail as *direct columns* —
  there is no separate `legacy_role_analysis` table, contrary to this
  codebase's original guess. Real columns include `instance_type`
  (`observed_posting | user_defined_target | synthetic_reference` — not
  `kind`; `target_basis` — `real_role | imagined` — distinguishes real from
  imagined targets within `user_defined_target`), plus `title, organisation,
  location, country, remote_type, employment_type, seniority_level,
  posting_date, salary_min/max, salary_estimate_min/max, currency,
  description, requirements, responsibilities, summary, career_track,
  legacy_scores, legacy_analysis, extraction_status, extraction_notes,
  status`. `legacy_scores`/`legacy_analysis` are JSONB — production packs the
  pre-capability-model scores and derived analysis (seniority_score,
  top_adjacent_roles, etc.) into these two columns rather than one column
  per score. `app/db.py::flatten_role_instance` unpacks them back to
  top-level keys on every API response, so the frontend's `Role` type never
  had to change shape even though storage did.
- **`jobber.role_skill_observation`** real columns: `surface_form` (not
  `name`), `canonical_concept_id` (not `resolved_concept_id`), plus
  `category, importance, requirement_type, observation_basis`.
  `observation_basis = 'legacy_extraction'` is preserved on the original 327
  migrated rows; this codebase's own writes use `'app_capture'` — see
  `app/db.py::upsert_role_instance`.
- **`jobber.concept` / `concept_alias` / `concept_type` / `concept_edge_rule`**
  match this codebase's original design (doc 11 §4.2) column-for-column,
  except for the UUID-id/`legacy_*_id` pattern above.

All of the above are asserted, not created, by
`backend/migrations/0001_live_schema_preflight.sql` — see that file's header
for exactly why (creating them would risk reshaping production's real,
already-populated tables). Every later migration (`0002`–`0005`) only adds new
tables/columns on top of this baseline.

## 4. `document.provenance_quality`

The real column (not a new invention, and not named `provenance`) is:

```sql
provenance_quality TEXT NOT NULL
    CHECK (provenance_quality IN ('original', 'legacy_extracted', 'reconstructed', 'unknown'))
```

`'original'` means a genuine, verbatim capture of source text — only documents
with this value can back a `stated`/`implied` requirement claim with a
verbatim evidence span (`app/extraction.py`: `can_trust_spans = document
["provenance_quality"] == "original"`). `'legacy_extracted'`,
`'reconstructed'`, and `'unknown'` all downgrade any claim against that
document to `inferred` with no stored span, regardless of how convincing the
model's proposed quote looks — this is the mechanism behind doc 11 §5.2
invariant 1 and brief §5's "stated/implied claims require document
provenance." Every caller of `app/db.py::create_document` must state this
value explicitly; there is no default relied on in code.

## 5. `profile360` — read-only, confirmed columns for claims/capabilities/evidence

Live inspection confirmed real column shapes for the tables this codebase
actually queries:

- **`profile360.claims`**: `id (uuid), claim_key, episode_id, claim_text,
  evidence_class, depth, recency, confidence, uncertainty, created_at,
  updated_at`. The epistemic-basis column is **`evidence_class`** (not
  `basis`), with values `stated | implied | inferred | user_asserted | mixed`.
- **`profile360.capabilities`**: `id (uuid), capability_key, name,
  description, synthesis_status, current_assessment, uncertainty, created_at,
  updated_at`.
- **`profile360.evidence`**: `id (uuid), claim_id, document_id, evidence_type,
  passage, locator, approximate_date, notes`.
- **`profile360.episodes`** / **`profile360.snapshots`**: confirmed `id`
  is `uuid`; no other column beyond that was confirmed by the inspection.
  `app/profile360_reader.py` never hard-codes a column name for these two —
  it introspects `information_schema.columns` on first use (cached) and
  exposes rows as plain dicts with a best-effort display-field picker
  (`claim_text`, `narrative_text`, `text`, `statement`, `description`,
  `summary`, `title`, `name`, `label` — first match wins), falling back to a
  raw key:value rendering rather than assuming a specific field is present.
  Derived timeline/duration math (doc 11 §5.4 — union-of-spans years of
  experience) is deliberately not rebuilt against these two until their real
  date-field names are confirmed.
- Eleven tables total have RLS disabled today: `documents, episodes, concepts,
  claims, evidence, claim_concepts, capabilities, capability_claims,
  contradictions, open_questions, snapshots` — see `docs/15` §3 for the
  manual migration that closes this, covering all eleven (an earlier version
  of that migration only covered eight, missing `concepts`, `claim_concepts`,
  and `capability_claims`).

**`profile360_claim_id`/`profile360_capability_id`/`profile360_episode_id` are
real, direct foreign keys** to `profile360.claims(id)` /
`profile360.capabilities(id)` / `profile360.episodes(id)` in every jobber-side
table that references them (`backend/migrations/0004_profile360_mapping.sql`,
`0005_preferences.sql`) — not a defensive best-effort attempt that degrades to
an unconstrained column, now that the referenced columns' types are confirmed.

## 6. `jobber` does not get a person-side claims or episode table

Per the brief's §1 architectural boundary, `profile360` is authoritative for
person-side evidence and must not be duplicated into `jobber`. Doc 11's
`evidence_claim` (person-side, subject = episode) is therefore **not** built in
`jobber` — only `requirement_claim` (role-side, subject = `role_instance`) is.

Live inspection also confirmed that `jobber.person`, `jobber.episode`,
`jobber.episode_document`, and `jobber.profile_snapshots` — this codebase's
original pre-Phase-2 hand-entered History/Profile tables — **do not exist in
production** and must not be created: profile360's own `episodes` and
`snapshots` are the one authoritative source for that data now.
`backend/app/routes/episodes.py` and `backend/app/routes/profile.py` are
therefore **read-only**, browsing `profile360.episodes`/`profile360.snapshots`
via `profile360_reader.py` — there is no more episode CRUD, no `/timeline`
endpoint, and no `POST /api/profile`; authoring that data is profile360's own
tool's job. The frontend's History and Profile pages were rewritten to match
(read-only browsers, no forms).

The one purely local, evidence-free "the user asserts this" flag
(`jobber.person_capability_assertion`, used by the comparison UI's "I have
done this" action) still has no home in profile360 — it is deliberately not a
claim (no span, no episode, no modifiers) and must never outrank a real
profile360 mapping in the comparison view's epistemic ordering (`evidenced >
partial > user_asserted > not_found`, see `app/routes/comparison.py`). It
carries a `promoted_to_profile360_at` column and a
`POST /api/comparison/assert/{concept_id}/promote` endpoint
(`app/profile360_promotion.py`) that makes a best-effort attempt to queue the
assertion into `profile360.manual_import_queue` for that tool's own review —
introspecting that table's columns defensively, since its shape has not been
confirmed, and returning a clear "unsupported" error rather than guessing at
it. Until a person promotes (or profile360 grows a first-class import path for
this), the assertion is explicitly a **temporary navigation override**, not
evidence.

## 7. Local development and tests

Local dev connects to Postgres over `DATABASE_URL`. Pointing it at the real
Supabase project needs nothing extra — the confirmed baseline in §3/§5 already
exists there. Standing up a from-scratch local Postgres instead (no existing
`jobber`/`profile360` data) needs one extra step first, because
`0001_live_schema_preflight.sql` only *asserts* that baseline and deliberately
never creates it (creating it would be the wrong thing to do against a real
production database that already has it):

```bash
psql "$DATABASE_URL" -f backend/scripts/local_baseline.sql
```

`local_baseline.sql` is a struct-only stand-in built to the exact shapes
confirmed in §3/§5 (it seeds `concept_type`/`concept_edge_rule` with the same
rows production has, but no document/role_instance/skill-observation rows —
there is no way to fabricate the real captured corpus locally). It also stubs
a minimal `profile360` schema, since migrations now declare real foreign keys
into it (§5) and even a jobber-only local setup needs *something* at those
tables to satisfy them.

Tests are different: they must never touch the production database (prompt
§16). `backend/tests/conftest.py` requires a **separate** Postgres reachable
via `TEST_DATABASE_URL` (falls back to
`postgresql://postgres:postgres@localhost:5432/postgres` for local/CI
convenience), creates a throwaway database per test session, applies
`local_baseline.sql` to it, then runs every migration — so every test run is
itself a live proof that the Phase 2 migrations apply cleanly on top of the
confirmed production baseline shape (`tests/test_migration_compatibility.py`
makes this explicit, and also proves migrations refuse to run at all against
a database that never got that baseline). Isolation between tests is by
truncating every non-seed jobber/profile360 table before each test, not a
per-test transaction rollback — this app's `db_cursor()` commits per call
(by design), so a wrapping transaction would not actually contain a
multi-call test. If `TEST_DATABASE_URL`/local Postgres is unreachable, the
Postgres-backed tests skip with an explicit message rather than failing or
silently passing.
