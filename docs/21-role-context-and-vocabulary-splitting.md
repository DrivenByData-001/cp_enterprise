# 21 — Vocabulary Cluster Splitting, 2026 Role Detail Fix, and Day-in-the-Life Enrichment

**Status:** implemented — Split cluster (with a curation-override structure
that survives the bootstrap), the 2026 Role Detail rendering fix, and a
persisted, on-demand Day-in-the-Life / Role Context enrichment.
**Not attempted in this pass, on purpose:** Phase 4 economics, production
vocabulary curation, bulk role-context generation, historical-corpus
reprocessing, the `profile360` RLS decision — see §7.
**Related:** `docs/18-consolidation-and-analytical-foundation.md` (the
lexical clustering this pass adds an override on top of, unchanged),
`docs/19-vocabulary-prioritisation-and-curation.md` (the curation queue this
pass adds a fourth action to), `docs/13-ai-task-layer.md` (the AI task layer
Day-in-the-Life generation is built on), `docs/17-document-processing-
pipeline.md` (the `job_posting_extract` pipeline the 2026 regression turned
out not to be the only role-authoring path).

---

## 0. Environment constraint, carried forward unchanged

Same constraint as every prior pass (docs/18 §0, docs/19 §0): **this build
environment has no credential for the real Supabase project.** The 2026 Role
Detail bug was diagnosed and fixed without production access — see §1.3 for
exactly how, and why the resulting fix is nonetheless load-bearing rather
than a guess. Every schema/behaviour change in this pass was developed and
tested against a disposable local Postgres 16 + pgvector instance
(`backend/scripts/local_baseline.sql` + `backend/migrations/*.sql`, exactly
as `backend/tests/conftest.py` already does), and against a separate
disposable Postgres seeded for a live browser smoke test (§5). No production
row was read, written, or reprocessed.

---

## 1. The 2026 Role Detail rendering regression

### 1.1 Symptom, as reported

Dashboard renders and a 2026 date filter shows entries; opening one of those
roles into Role Detail renders almost nothing — no advert text, no skills —
despite the role clearly having real captured content (the brief's own
MetLife Europe / Dublin Senior Actuary example describes Solvency II, ORSA,
IFRS 17, Board reporting, CBI interactions, and a stated HoAF progression
path — all real, capturable evidence).

### 1.2 Root cause

This codebase has **two independent role-authoring pipelines**, and `GET
/api/roles/{role_id}` was only ever built to read one of them:

1. **`JobPostingImport`-shaped** (`/api/import`, `/api/import/native`, the
   historical-corpus migration, `app/document_processing.py`): populates
   `role_instance.description/requirements/responsibilities/summary` and
   `career_track` directly, plus `jobber.role_skill_observation` rows for
   skills. Every historical role and every role captured through these
   endpoints has this shape.
2. **Source-aware ingest** (`POST /api/role-instances/ingest` →
   `POST /api/role-instances/{id}/extract-requirements`,
   `app/routes/role_instances.py` + `app/extraction.py`): creates a *bare*
   `role_instance` (title/organisation/location/posting_date only — see
   `_ingest_raw`) linked to an immutable `jobber.document` holding the
   verbatim pasted text, then extracts requirements into
   `jobber.requirement_claim` (concept-linked), never touching
   `role_instance.description`/`role_skill_observation` at all.

A role captured the second way lists fine on the Dashboard — title,
organisation, location, posting date, and similarity all come from plain
`role_instance` columns the list endpoint already reads — but `GET
/api/roles/{role_id}` had no idea `requirement_claim` or a linked document's
`content_text` existed: it read `role_skill_observation` (empty for this
pipeline → an empty `skills` array) and the flat `description`/
`requirements`/`responsibilities` columns (all `NULL`) and returned exactly
that — a syntactically valid, HTTP 200 response carrying essentially no
content. Nothing crashes; the page "fails to render" in the sense that
matters to the user (there is nothing there), not in the sense of a thrown
exception.

This is a genuine compatibility gap between two pipelines that have coexisted
in the schema since Phase 2/3B, not a defect introduced by the recent
deployment/auth work (`ca47584` touched only `auth.py`/`config.py`/
`main.py`/deployment docs — nothing in `routes/roles.py`,
`db.py::flatten_role_instance`, or `RoleDetail.tsx`). It is best read as
"the newer capture pipeline outpaced the UI that reads its output," exactly
the "regression/compatibility problem, not a request to re-extract" framing
the brief uses.

### 1.3 How this was actually diagnosed (not guessed)

With no production credential available (§0), the method was: build the
*real* production data shape locally and run the *real* code path against
it, rather than reason about the frontend by eye. Concretely:

1. Read every function `GET /api/roles/{id}` touches beyond what `GET
   /api/roles` touches (`role_skill_observation`, `get_embedding` vs.
   `get_embeddings`, `role_extraction_quality` vs. its `_bulk` sibling,
   `_path_to_target`) and confirmed each pair is behaviourally equivalent —
   ruling out a bug in code both endpoints share (any such bug would also
   break the Dashboard list, contradicting "Dashboard renders").
2. That narrowed the search to code paths `GET /api/roles` never executes at
   all — the skills query and the flat text fields — and to
   `routes/role_instances.py`, the one role-creation path that never routes
   through `posting_persistence.py`.
3. Reproduced a MetLife-Dublin-shaped posting through that exact pipeline
   against a disposable local Postgres (`app.extraction.run_json_task`
   mocked, never a live OpenAI call — see
   `backend/tests/test_role_detail_2026_regression.py`) and called the real
   `GET /api/roles/{id}` route through `TestClient`. Before the fix, this
   reproduced the symptom exactly: HTTP 200, `skills: []`,
   `description/requirements/responsibilities: null`, despite four
   `requirement_claim` rows and the original advert text sitting in the
   database the whole time.

### 1.4 The fix

Additive, read-path-only — no data touched, no re-extraction, nothing
hidden:

- **`app/db.py::role_skills_with_fallback`**: returns
  `role_skill_observation` evidence when present (unchanged for every
  existing role); only when that is completely empty, falls back to
  `requirement_claim` evidence (joined to `concept.canonical_name`/
  `type_code`), excluding rejected claims. Never merges the two sources.
- **`app/db.py::build_role_view`**: the single place that now assembles the
  full role projection (used by both `GET /api/roles/{id}` and Day-in-the-
  Life generation, §3.7) — flattens the row, calls the fallback above for
  skills, and adds `source_document_text`: the linked document's own
  verbatim `content_text`, populated **only** when
  `description`/`requirements`/`responsibilities` are *all* empty. A role
  with real flat-field content is completely unaffected.
- **`routes/roles.py::get_role`** is now a thin wrapper around
  `build_role_view` plus the embedding/path/extraction-quality logic that
  was already there, unchanged.
- **Frontend** (`RoleDetail.tsx`): a new "Captured source text" card renders
  `source_document_text` verbatim, shown only when the normal "Raw posting"
  card has nothing to show. The existing Skills card needed no change — it
  already renders whatever `role.skills` contains.

### 1.5 Tests (`backend/tests/test_role_detail_2026_regression.py`)

- `test_2026_style_role_appears_in_list_endpoint` — confirms the list side
  of the symptom (unaffected, before and after).
- `test_2026_style_role_renders_with_its_real_evidence_via_detail_endpoint`
  — the regression test itself: seeds the exact source-aware-ingest +
  `extract_role_requirements` shape (MetLife Dublin content, `posting_date`
  2026-05-27), and asserts the detail endpoint now returns the four
  `requirement_claim`-derived skills and the verbatim source text. **Fails
  before the fix** (`skills == []`, both text fields `None`) and passes
  after — verified by running it against the pre-fix code first.
- `test_older_role_skill_observation_shaped_posting_still_renders` — the
  brief's explicit "older historical role still works" guard: a plain
  `role_skill_observation`-shaped posting is untouched by either fallback.
- `test_target_role_still_renders` — a `user_defined_target` role (no
  document, no skills) still returns 200 with the right `node_type`.

Full backend suite (403 tests, see §5) passed both before write (as a
baseline) and after, twice.

---

## 2. Vocabulary "Split cluster"

### 2.1 Problem

The bootstrap's lexical clustering (`vocabulary_bootstrap.cluster_key_for`,
docs/18 §6.1) is a deliberately small, explicit synonym-seed list — safe for
the vast majority of cases, but it does contain
`("stakeholder management", "stakeholder engagement")` as one group. The
brief's own curation finding is that these are *related but distinct*
concepts that should never have been merged. Rejecting the merge via
"Reject" would discard both surface forms' evidence entirely, which is
wrong — the fix has to be to **undo the grouping**, not the evidence.

### 2.2 Architecture decision: `cluster_key_locked`, not a second table

`cluster_key` is a plain column on `jobber.concept_proposal`
(migration 0009), and `vocabulary_bootstrap.compute_cluster_keys` **fully
recomputes and overwrites it on every real bootstrap run** for every pending
proposal. Simply reassigning `cluster_key` after a split would therefore be
silently undone the next time the bootstrap ran — exactly the failure mode
the brief calls out by name.

The chosen design (migration `0010_vocabulary_cluster_split.sql`) is the
minimal one that closes this gap without a second table:

```sql
ALTER TABLE jobber.concept_proposal
    ADD COLUMN cluster_key_locked   BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN cluster_split_from   TEXT,          -- audit only
    ADD COLUMN cluster_split_at     TIMESTAMPTZ;   -- audit only
```

`cluster_key_locked = TRUE` is set **only** by a curator split
(`app/vocabulary_curation.py::split_cluster`) and is never cleared by
anything automatic. Both bootstrap code paths now honour it:

- `vocabulary_bootstrap.compute_cluster_keys` (the real, writing bootstrap)
  skips recomputing `cluster_key` entirely for any locked proposal.
- `vocabulary_bootstrap.analyze_cluster_keys_dryrun` (`--dry-run`, which
  works purely from `role_skill_observation` and never touches
  `concept_proposal` at all) now looks up locked overrides via
  `_locked_cluster_keys` (a `surface_form → cluster_key` map — the join key
  that works for both code paths, since `concept_proposal.surface_form` is
  always already `normalize_name`'d, matching what the dry-run computes
  independently) and prefers a locked key over a freshly computed
  `cluster_key_for` result.

**This is the "human curation decisions must outrank automated lexical
clustering" rule, enforced at the one place both the dry-run and the real
bootstrap actually read from**, not by convention. `cluster_split_from`/
`cluster_split_at` are pure audit trail (brief's "maintain auditability"),
never read by any query.

A second table (e.g. an explicit "override" table Split cluster writes into,
looked up by every query) was considered and rejected as unnecessarily
complex for what is fundamentally one boolean fact ("this proposal's cluster
assignment is a human decision, not a computed one") attached to a row that
already carries the field it protects.

### 2.3 New deterministic cluster keys: collision-safe by construction

Each resulting group gets a new key of the form `manual:<label>`, where
`<label>` is `suggested_canonical_label(group)` (the existing longest-
surface-form-wins heuristic, normalized) — e.g. `manual:stakeholder
engagement`. Before assigning, `_deterministic_split_keys` checks the
candidate against every `cluster_key` currently in use (excluding the
cluster being split) and appends a numeric suffix (`-2`, `-3`, …) until it is
unique. This is deterministic given unchanged DB state, human-readable, and
**collision-safe by construction** rather than by assumption — even in the
vanishingly unlikely case a raw surface form itself already produced a
`manual:`-prefixed automated key.

### 2.4 What a split does and doesn't do (`vocabulary_curation.split_cluster`)

- Validates the request first (§2.5), before any write.
- For each resulting group, one `UPDATE jobber.concept_proposal SET
  cluster_key = <new key>, cluster_key_locked = TRUE, cluster_split_from =
  COALESCE(cluster_split_from, <old key>), cluster_split_at = now() WHERE
  status = 'pending' AND surface_form = ANY(<group>) AND
  COALESCE(cluster_key, surface_form) = <old key>`.
- **Never** deletes a `concept_proposal` row, never touches
  `role_skill_observation` (observations aren't keyed by cluster at all —
  only proposals are; a split is purely a re-keying operation), never
  creates a `jobber.concept`.
- **Atomicity**: same convention as `execute_batch` (docs/19 §7) — plain
  `UPDATE`s, no per-group `SAVEPOINT`, so the first exception propagates out
  of the caller's `with db_cursor() as cur:` block and rolls back the whole
  operation. Proven directly by
  `test_split_rolls_back_entirely_on_mid_operation_failure`, which forces a
  failure on the *second* group's update and asserts the *first* group's
  (already-executed) update was rolled back too.
- **Idempotency**: retrying an identical split call after it already
  succeeded 404s (the original `cluster_key` no longer has any member — see
  `vocabulary_curation.split_cluster`'s docstring) rather than silently
  double-splitting or corrupting anything — a safe, if not byte-identical,
  outcome for a retried call.

### 2.5 Validation (`vocabulary_curation._validate_split`)

In order: cluster must exist (else 404); if it exists but has zero pending
members, its resolution state decides 404 (never existed / already fully
resolved differently — n/a here) vs. 409 (already resolved — a resolved
cluster cannot be split); a single-surface-form cluster cannot be split
(400); a split must produce ≥2 groups (400, also enforced client-side by the
request model); every group must be non-empty (400); no surface form may
appear in more than one group (400); the groups must **exactly** partition
the cluster's current pending surface forms — nothing left out, nothing
extra (400).

### 2.6 Split UX (`frontend/src/pages/Vocabulary.tsx`)

A fourth action, **Split cluster**, appears on a pending `ClusterCard` only
when it has more than one surface form (brief's own gating rule). Clicking
it opens `SplitClusterEditor`: every surface form defaults to its own group
(a `<select>` per form, "Group 1"/"Group 2"/…) — the curator regroups true
synonyms by picking the same group number for more than one form, never the
reverse (nothing is pre-merged for them). **Preview split** calls
`POST /api/vocabulary/clusters/split/preview` (read-only) and shows each
resulting cluster's suggested label, surface forms, and live role/
observation counts before anything writes; **Confirm split** then calls
`POST /api/vocabulary/clusters/split` for real. After a split, both
resulting clusters appear as ordinary independent pending cards — Accept /
Merge into… / Reject / Split again all work exactly as they do for any other
pending cluster, with no special-casing, since `build_pending_cluster_index`
groups purely by whatever `cluster_key` is currently persisted.

### 2.7 Priority recomputation

Nothing bespoke was needed: `list_clusters`/`build_pending_cluster_index`
already compute role/observation counts and the priority score live from
`role_skill_observation` joined against the current `cluster_key`, on every
request (docs/19 §1/§2). Once a split changes that column, each resulting
cluster is automatically scored from only its own evidence on the very next
read — proven by `test_priority_metrics_recomputed_per_resulting_cluster`
(3 roles vs. 1 role split apart → visibly different role counts and priority
scores per resulting cluster).

### 2.8 API

```
POST /api/vocabulary/clusters/split/preview   { cluster_key, groups: [{surface_forms}, ...] }
POST /api/vocabulary/clusters/split           { cluster_key, groups: [{surface_forms}, ...] }
```

Both protected by the existing central auth policy (mounted on the same
`vocabulary.router`, already under `dependencies=[Depends(require_auth)]` in
`app/main.py` — verified by the existing route-scanning auth test, which
would fail if a router were added without it).

### 2.9 Tests (`backend/tests/test_vocabulary_split.py`, 14 tests)

Split into two pending clusters; all `concept_proposal` rows and
`role_skill_observation` rows preserved (counts unchanged, statuses still
`pending`); priority recomputed per resulting cluster; Accept/Merge/Reject
work independently after a split (one accepted, the other merged into a
*different* pre-existing concept, neither touching the other); reject after
split only affects its own cluster; splitting again on a resulting cluster
that still has multiple forms; four invalid-split cases (missing a surface
form, duplicate surface form across groups — rejected by the request
model's own validator, a single group, a single-surface-form cluster);
resolved cluster cannot be split (409); unknown cluster 404; transaction
rollback on a forced mid-operation failure; and the brief's own worked
example end-to-end — **"stakeholder engagement"/"stakeholder management"
survives both `analyze_cluster_keys_dryrun` (`--dry-run`) and a later real
`compute_cluster_keys` run, including after a brand-new posting using one of
the split surface forms is captured and the bootstrap is re-run** — proving
the lock, not luck, is what keeps them apart.

---

## 3. Day-in-the-Life / Role Context enrichment

### 3.1 Purpose and boundary

A role-side market/context enrichment — what doing this job would actually
feel like — persisted per role, generated on demand, never for all roles at
once, and never merely by viewing a role. Lives entirely in `jobber`
(`jobber.role_context_enrichment`); **never reads or writes `profile360`** —
this describes the occupation, not fit to the user (verified directly:
`test_generation_input_never_includes_profile360_evidence`).

### 3.2 Grounded vs. inferred

Every individual claim — not just the response as a whole — carries a
`basis` (`advert_grounded` | `inferred`) and a `confidence`
(`high`/`medium`/`low`), enforced as a Pydantic `Literal` on the AI output
schema (`app/models.py::RoleContextGeneration` and its nested models) so a
model response using any other label fails schema validation rather than
silently passing through unlabelled. `advert_grounded` means the role
material actually said it (a named relationship, a named regime, a stated
progression path); everything else — team size, meeting cadence, the
"feel" of a manager relationship — is `inferred`, which is expected and
fine, but must say so.

### 3.3 Persisted schema (migration `0011_role_context_enrichment.sql`)

```sql
CREATE TABLE jobber.role_context_enrichment (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_instance_id      UUID NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded')),
    generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    generator_version     TEXT NOT NULL,
    model                 TEXT NOT NULL,
    source_document_id    UUID REFERENCES jobber.document(id),
    source_content_sha256 TEXT,
    day_in_life           JSONB NOT NULL DEFAULT '[]',
    typical_week          JSONB NOT NULL DEFAULT '[]',
    team_context          JSONB NOT NULL DEFAULT '{}',
    manager_context       JSONB NOT NULL DEFAULT '{}',
    stakeholder_context   JSONB NOT NULL DEFAULT '{}',
    career_progression    JSONB NOT NULL DEFAULT '[]',
    grounding_summary     JSONB NOT NULL DEFAULT '{}',
    caveats               TEXT,
    raw_output            JSONB,                          -- audit/debug only, never returned by the API
    extraction_run_id     UUID REFERENCES jobber.extraction_run(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Version history is retained** (rows are never deleted, consistent with
this schema's general evidence/audit posture), but exactly one row per role
may be `status = 'active'` — enforced by a **partial unique index**
(`idx_role_context_enrichment_one_active ... WHERE status = 'active'`), not
only by application care. `GET .../context` only ever reads the active row.

`extraction_run.subject_type = 'role_instance'` already covers this task —
no schema change was needed there. The one necessary loosening: `task =
'role_context_generate'` had to join `job_posting_extract` as an exception to
"`vocabulary_version_id` is required" (0007's rule), since this task does no
concept-linking and has no vocabulary dependency to report — additive, same
pattern 0007 itself used.

### 3.4 Structured content

Explicit typed sections rather than one blob (`app/models.py`):
`DayInLifeItem`, `TypicalWeekItem`, `TeamContext` (with a `TeamSizeEstimate`
range, never a single suspicious number), `ManagerContext`, `GroundedNote`
(used for team-work/stakeholder items), `CareerStep`, `GroundingSummary`.
Every leaf carries its own `basis`/`confidence` — provenance is available at
the granularity of each individual claim, not once for the whole response.

### 3.5 Generation lifecycle (`app/role_context.py`)

- **`GET /api/roles/{id}/context`** — pure read, never calls the AI
  provider. Returns `{role_instance_id, enrichment: null}` when nothing has
  been generated yet (never a 404 for that — the role itself might be
  perfectly real) — verified directly:
  `test_get_never_triggers_generation` monkeypatches `run_json_task` to
  raise `AssertionError` if it's ever called, then asserts a plain `GET`
  never trips it.
- **`POST /api/roles/{id}/context/generate`** — first-time generation. A
  no-op if one is already active (`created: false`, the existing row
  returned unchanged, **no AI call** —
  `test_generate_again_without_force_is_a_noop_no_ai_call`) — repeated or
  accidental clicks never waste a model call or spawn a spurious version.
- **`POST /api/roles/{id}/context/regenerate`** — always calls the model and
  creates a new active version.
- The model is always called **outside** any open DB transaction: evidence
  is read and the `extraction_run` row is inserted as `'running'` and
  committed *before* the network call (same lifecycle discipline as
  `document_processing.process_job_posting_document`); persistence happens
  in a fresh, separate transaction afterward.
- **Persistence is an unconditional supersede-then-insert in one
  transaction** (`_persist`): mark any existing active row `'superseded'`,
  then insert the new one as `'active'`, in the same `with db_cursor()`
  block. This is what keeps the partial unique index from ever being
  violated, with no extra locking needed — there is never a moment where two
  `'active'` rows for the same role could both commit.
- **A failed model call never reaches `_persist` at all** — the
  `extraction_run` row is marked `'failed'` and the exception is re-raised
  as `RoleContextGenerationError`; the prior active enrichment (if any) is
  provably untouched
  (`test_model_failure_leaves_prior_enrichment_intact`).
- `AIConfigError`/`AIProviderError`/`AIResponseFormatError`/
  `AISchemaValidationError` map to 503/502/422/422 respectively — the same
  convention `routes/import_routes.py` already established for
  `/api/import/native`. With no `OPENAI_API_KEY` set, the rest of the app
  (including every *existing* stored enrichment, via `GET`) is completely
  unaffected — verified directly
  (`test_missing_api_key_returns_a_clear_operational_error`).

### 3.6 Source inputs (`role_context._build_input_text`)

Title, organisation, location, country, seniority, career track, employment
type, then description/requirements/responsibilities/summary — falling back
to the linked document's own verbatim text when those are empty (the same
fallback §1.4 added, reused here so a source-aware-ingested role generates
sensible Day-in-the-Life output too — proven directly by
`test_generation_falls_back_to_source_aware_ingest_evidence`), then known
skills (via the same `role_skills_with_fallback` §1.4 introduced), then, for
a target role only, existing `typical_tasks`. **Never** profile360.
`source_content_sha256` is the SHA-256 of exactly this constructed text —
persisted for a future staleness check (not built in this pass — see §7),
not currently compared against anything.

### 3.7 AI prompt (`prompts/role_context_enrichment.md`)

Mirrors the existing prompt-file convention (`extract_role_requirements.md`
et al.): a rules preamble, then an OUTPUT SCHEMA block, then an INPUT
section. States plainly, as the single most important rule, the
`advert_grounded`/`inferred` distinction and what each means; forbids
inventing named people/teams/reporting lines; requires a range (not a false-
precision single number) for team size and similar quantities; requires
distinguishing "common for this kind of role" from "this posting states";
forbids claiming a universal career ladder (progression must be specific to
field/seniority/organisation type); and instructs that a posting's own
stated progression path (e.g. "this role is a pathway toward X") be marked
`advert_grounded` and treated as the strongest available signal, with
anything beyond it still `inferred`.

### 3.8 Coexistence with target `typical_tasks`

No target-schema refactor. `typical_tasks` (already on
`role_instance.legacy_analysis` for a `user_defined_target`) is read as one
more *input* to Day-in-the-Life generation for a target role
(§3.6) — Day-in-the-Life is additive context on top of it, not a
replacement; nothing about the existing Targets page, skill decomposition,
or technical-subjects rendering changed.

### 3.9 API

```
GET  /api/roles/{role_id}/context
POST /api/roles/{role_id}/context/generate
POST /api/roles/{role_id}/context/regenerate
```

All three under the same central auth policy as every other router — a
dedicated `routes/role_context.py` router (same "one small router per sub-
feature" precedent as `routes/role_instances.py`'s requirements sub-
resource), registered with `dependencies=_protected` in `app/main.py`.
`test_context_endpoints_reject_unauthenticated_calls` and the pre-existing
whole-app auth-scan test both cover this.

### 3.10 Role Detail UI

A new "Day in the Life" card, placed after the advert/skills content and
before the footer actions (per the brief's placement). Shows `[Generate]`
when no enrichment exists; once one exists, shows generated date +
`generator_version`, every section (Typical day, Typical week, Team, Reports
to / management dynamic, Key stakeholders, Career progression) with a
compact "From advert" (green) / "Inferred" (grey) badge on every individual
claim, a one-line methodology note (no methodology essay), and a
`[Regenerate]` button. Regeneration never happens merely by viewing the
role — only the explicit button click calls `generate`/`regenerate`.

### 3.11 Tests (`backend/tests/test_role_context.py`, 14 tests)

GET with no enrichment (`enrichment: null`, not an error); GET on an unknown
role (404); GET never triggers generation; generate creates and persists
(fingerprint, `generator_version`, grounded/inferred labels all present and
correct); a persisted repeat GET causes no AI call; generate again without
`force` is a no-op with no AI call; regenerate creates a new active version
and supersedes the old one (version history retained — both rows still
exist, `superseded` then `active`); model failure leaves the prior
enrichment fully intact; an AI schema-validation failure is rejected, not
persisted; a missing API key returns a clear 503, not a crash; generate on
an unknown role is 404; unauthenticated calls rejected on all three
endpoints; generation input never includes profile360 evidence; and
generation correctly falls back to source-aware-ingest evidence (the same
fallback §1 introduced) for a role with no flat description/skills at all.

---

## 4. Migrations

```
backend/migrations/0010_vocabulary_cluster_split.sql   -- cluster_key_locked, split audit columns
backend/migrations/0011_role_context_enrichment.sql    -- role_context_enrichment table + extraction_run CHECK loosening
```

Both purely additive (`ADD COLUMN`/`CREATE TABLE IF NOT EXISTS`/`CREATE
INDEX IF NOT EXISTS`, one `CHECK` constraint dropped-and-widened, never
narrowed) — no existing column dropped or retyped, no existing row touched,
no production data backfill required. The app starts normally with zero
`role_context_enrichment` rows and zero locked proposals, exactly the state
a fresh deploy will actually be in. Both apply cleanly through the existing
`run_migrations()` file-based runner (idempotent — recorded in
`jobber.migration_history`, a re-run/redeploy is a no-op) — proved
indirectly by every test in §1/§2/§3 running against a database that applies
every migration, `0001` through `0011`, from scratch each session, and
directly by `test_migration_compatibility.py` (pre-existing, unchanged,
still green).

---

## 5. Testing

Focused suites: `test_role_detail_2026_regression.py` (4), `test_vocabulary_
split.py` (14), `test_role_context.py` (14) — 32 new tests, all passing.

Full backend suite (`pytest -q`, disposable local Postgres 16 + pgvector,
installed via `apt-get install postgresql-16-pgvector` for this sandbox):
**403 passed, 0 failed, 0 skipped — run twice**, confirming determinism (no
test-ordering or shared-state flakiness introduced).

Frontend: `tsc -b` clean; `oxlint` clean (exit 0); `npm run build` succeeds
(one pre-existing "chunk >500kB" advisory warning, the Space page's
three.js/react-three-fiber dependency — unrelated to this pass, noted
already in docs/19 §14).

Browser smoke test (Playwright against this sandbox's pre-installed
Chromium, a local backend on a disposable seeded Postgres with
`app.role_context.run_json_task`/`app.extraction.run_json_task` monkeypatched
to deterministic fakes — no live OpenAI call — plus the real Vite dev
server): login → Dashboard → filter to 2026 → open the MetLife-Dublin-shaped
role → Role Detail renders its skills (via the `requirement_claim` fallback)
and its captured source text (via the document-text fallback) → Day in the
Life shows `[Generate]` → generate → both "From advert" and "Inferred"
labels visible → refresh → the generated context persists with no second AI
call → Vocabulary → the seeded "stakeholder engagement"/"stakeholder
management" cluster → Split cluster → preview → confirm → both resulting
pending clusters visible independently → log out → back at the login screen.
**Zero console errors, zero uncaught page errors**, start to finish. No
production curation action was taken.

---

## 6. Production safety

No production `DATABASE_URL` was available to this build (§0), so this is
by construction rather than merely by discipline: every write in this pass
— migrations, split-cluster operations, role-context generation, every test
— ran against a disposable local database created and dropped within this
session. Nothing here writes to production automatically: migrations 0010/
0011 apply only when this branch is deployed and the app starts, Split
cluster and Day-in-the-Life generation are both explicit, human-triggered
HTTP calls with no scheduled job or startup hook anywhere in either code
path, and the vocabulary bootstrap CLI (`backend/scripts/bootstrap_
vocabulary.py`) is unchanged in this pass beyond honouring locked proposals
— it still writes nothing on `--dry-run` and remains a deliberate,
explicitly-invoked CLI, never reachable from the frontend.

---

## 7. Scope exclusions / known limitations

- **No staleness detection.** `source_content_sha256` is persisted but
  nothing yet compares it against the role's *current* evidence to flag "this
  enrichment may be out of date" — the brief didn't ask for this in this
  pass, and it would need a defined policy (does an edited posting
  auto-invalidate its enrichment, or just get flagged?) this build didn't
  attempt to guess.
- **No merge-time UI for Split cluster's resulting groups.** The split
  editor lets a curator regroup surface forms before confirming; it does not
  offer a live "preview what the *bootstrap* would have done" comparison —
  the brief's dry-run/rerun survival guarantee is proven by tests (§2.9),
  not surfaced as a UI diff.
- **Day-in-the-Life has no batch/background generation**, deliberately —
  the brief explicitly excludes bulk-enriching the corpus, and nothing in
  this pass adds a code path that could do so even accidentally (no cron,
  no "generate for all roles" button, no queue).
- **The 2026 regression fix does not unify the two role-authoring
  pipelines.** `role_instance.description` etc. remain `NULL` for a
  source-aware-ingested role; `GET /api/roles/{id}` now *reads around* that
  gap rather than backfilling it. Unifying the two pipelines properly (e.g.
  having source-aware ingest populate the flat fields too) was judged out of
  scope for a "deliberately narrow consolidation build."
- Every §14 exclusion from the brief that predates this pass (Phase 4
  economics, capability proposal writes, `profile360` RLS, new scraping
  infrastructure, multi-user auth, historical-corpus reprocessing) remains
  exactly as excluded as before — nothing in this pass touches any of them.
