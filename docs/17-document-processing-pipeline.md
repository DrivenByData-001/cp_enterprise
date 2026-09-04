# 17 — Phase 3B: Document Processing Pipeline

**Status:** implemented — pipeline, schema, CLI, API, tests. The 10-document
pilot ran and was reviewed; that review found the historical-extraction
policy needed refinement (§8a, CP Ent Phase 3B 0.2) before the remaining
corpus is processed — the pilot's own roles were left untouched. The rest of
the 307-document historical corpus has **not** been processed.
**Related:** `docs/14-phase2-postgres-architecture.md` (confirmed schema this
phase extends), `docs/13-ai-task-layer.md` (the `run_json_task` abstraction
this phase's AI call goes through), `docs/16-phase3-capability-engine.md`
(unchanged by this phase — see §9 below)

---

## 1. Objective

`jobber.document` became the immutable intake/source layer, but 307
historical raw job adverts already captured there
(`source_key LIKE 'historical_roles:v1:%'`) had no linked `role_instance`.
This phase builds the pipeline that turns an *existing* raw document into a
structured role, without ever creating a second document for it:

```text
raw source
    -> jobber.document                  (immutable source evidence)
    -> job_posting_extract              (AI task, via app.ai.run_json_task)
    -> validated JobPostingImport       (the analytical contract)
    -> jobber.role_instance             (derived operational representation)
       jobber.role_skill_observation
       legacy_scores / legacy_analysis
    -> jobber.extraction_run            (provenance of the attempt)
    -> jobber.d_embedding               (best-effort, from document.content_text)
```

Requirement extraction / concept linking / embeddings for those requirements
remain a **separate**, later, downstream operation (§8) — this phase stops at
`role_instance` + legacy skill observations + a role embedding.

## 2. The four layers, and why they stay distinct

```text
jobber.document        = immutable source evidence — never overwritten by analysis
JobPostingImport       = an internal validated analytical contract (a Pydantic model, not a table)
jobber.role_instance    = the structured, derived operational representation
jobber.extraction_run   = provenance/history of each processing attempt
```

No new raw-role table was created, and `jobber.document` gained no workflow
columns (`processing_status`, `role_id`, ...) — see §5.

## 3. `jobber.extraction_run` schema changes (`0007_document_processing_lifecycle.sql`)

Additive only — no column dropped or retyped, no existing row touched:

| Change | Detail |
|---|---|
| `result_role_instance_id UUID NULL REFERENCES jobber.role_instance(id)` | What this run *produced* — deliberately separate from the existing `role_instance_id` subject column, which means "this run's *subject* was this role" (true for `requirement_extract`, never true for `job_posting_extract`, whose subject is always a document). See §6. |
| `output_payload JSONB NULL` | The exact validated `JobPostingImport` JSON for a successful/partial run — the durable equivalent of the old analysed-`.json`-file step. No intermediate file on disk is required. |
| `status` CHECK widened to `('running', 'ok', 'partial', 'failed')` | Previously `('ok', 'partial', 'failed')`. `running` means the AI task has started but not completed — see §4. |
| `vocabulary_version_id` made nullable, plus a new CHECK | See §4. |
| `idx_extraction_run_document_task` on `(document_id, task, started_at DESC)` | Serves the duplicate-processing check (§7), state derivation (§5), and batch eligibility (§9) — all filter on `(document_id, task)` and want the most recent attempt(s). |

## 4. Vocabulary version semantics

`vocabulary_version_id` was `NOT NULL` for every task. That remains correct
for the four closed-vocabulary tasks — `requirement_extract`,
`concept_link_adjudicate`, `profile360_claim_map`, `profile360_capability_map`
— which all consume the controlled concept vocabulary and must keep supplying
a real vocabulary version. `job_posting_extract` does not consume that
vocabulary at all (it extracts a `JobPostingImport`, not a concept mapping),
so fabricating a vocabulary dependency for it would be dishonest bookkeeping.

The column is now nullable, but a guarded CHECK keeps the old guarantee for
every other task:

```sql
CHECK (vocabulary_version_id IS NOT NULL OR task = 'job_posting_extract')
```

`test_vocabulary_version_id_nullable_only_for_job_posting_extract`
(`test_migration_compatibility.py`) proves both directions: NULL is accepted
for `job_posting_extract`, still rejected for `requirement_extract`.

## 5. Processing state is derived, never stored on `document`

`jobber.document` gained no `processing_status`/`analysed`/`role_id` column.
State is derived live from `extraction_run` history
(`app.document_processing.document_processing_state`,
`job_posting_processing_counts`):

| State | Definition |
|---|---|
| `raw` | No `job_posting_extract` attempt exists for this document. |
| `running` | The latest attempt's status is `running`. |
| `analysed` | The latest attempt's status is `ok` and it has a `result_role_instance_id`. |
| `partial` | The latest attempt's status is `partial` — a role was still created, but the model flagged the extraction as incomplete/uncertain (its own `metadata.extraction_status`, see §6); surfaced separately so it actually gets human attention rather than blending into a clean `analysed`. |
| `failed` | The latest attempt's status is `failed` (no role was created). |

State is keyed off the **latest** attempt specifically (not "has this
document ever succeeded") so `partial` stays visibly distinct from
`analysed`. This is safe because `process_job_posting_document` never starts
a *new* attempt once any attempt has already produced a role (§7) — so
whenever a role exists, it is always the latest run's own doing; "latest" and
"the run that succeeded" never diverge in practice.

`GET /api/documents/processing-status` (optionally `?source_prefix=...`)
returns exactly this breakdown as counts, for a future dashboard.

## 6. Extraction-run lifecycle

`app.document_processing.process_job_posting_document` implements a real
lifecycle instead of recording a run only after the fact:

1. Load and validate the document (exists, `kind='job_posting'`, non-blank `content_text`).
2. Under a transaction-scoped advisory lock (§7), check for an existing
   successful or in-flight run; if none, **insert `status='running'` and
   commit** — before any network call.
3. Call the AI provider (`app.ai.run_json_task`) — no Postgres transaction is
   held open during this call.
4. Validate the response as `JobPostingImport` (done inside `run_json_task`;
   a failure raises one of the existing `AITaskError` subclasses).
5. Persist the result, or mark the run failed.
6. Finish the run (`finished_at`, terminal `status`).

A provider/validation failure updates the same `running` row to
`status='failed', finished_at=now(), error_type, error_message` in a fresh
transaction (never the one that may have just failed) — it remains in the
table for audit, per §12's atomicity guarantee (below).

A successful, schema-valid response is not automatically `status='ok'`: the
model's own `metadata.extraction_status` (`ok | partial | failed`, already
part of the `JobPostingImport` contract) decides — `extraction_status='ok'`
(or omitted) maps the run to `ok`; anything else maps it to `partial`. This
gives `partial` real meaning: "a role was created, but the model itself
flagged the extraction as incomplete or uncertain" — exactly the case §5's
`PARTIAL / NEEDS ATTENTION` state exists to surface.

As a deterministic extraction-quality guardrail, the pipeline also marks a
response `partial` when its validated `skills` list is empty while any parsed
`job.description`, `job.requirements`, or `job.responsibilities` is non-blank.
It preserves the role and complete output payload for review, does not infer
or fabricate skills, and does not affect the historical-extraction policy or
the existing handling of minimal parsed output.

## 7. Duplicate-processing and concurrency protection

Before starting a new attempt, the pipeline checks (brief §14, literally):

```sql
task = 'job_posting_extract' AND result_role_instance_id IS NOT NULL
```

against *any* prior run for the document (not just the latest) — if one
exists, processing is skipped (`status: "already_analysed"`). A document
whose only attempts failed is eligible for retry; a document with no attempt
at all is processed. **Successful-role reanalysis is intentionally not
implemented** — a document that already produced a role (`ok` or `partial`)
is never reprocessed automatically, even via `--retry-failed`. That is
deferred until role supersession/versioning semantics are designed (brief
§14/§26); this is a deliberate, conservative scope boundary, not an
oversight.

Concurrency (brief §15) is protected with a **transaction-scoped Postgres
advisory lock**, keyed on `(document_id, task)` via
`pg_advisory_xact_lock(hashtext(document_id), hashtext(task))`, held only for
the short "check + insert running row" transaction — never across the AI
call. Two concurrent callers for the same document serialise on that lock;
whichever commits its `running` row first wins, and the second sees that row
(or the finished result, if the first attempt has already completed) once it
acquires the lock, and returns `already_processing`/`already_analysed`
instead of creating a second attempt. This is what actually prevents two
workers from creating two roles from the same document — not the initial
existence check by itself, which alone would be a plain SELECT-then-INSERT
race. `test_concurrent_processing_cannot_create_two_roles` proves this with
two real threads against two real pooled connections.

## 8. Historical-context handling

A deliberate product decision: historical adverts are analysed substantially
in the professional and labour-market context of their **original posting
date**, never silently as if posted today.

`app.document_processing._build_input_text` constructs the model input as:

```text
Known original posting date: 2008-03-27
Known source: 11. Recruit
Original listing title: Economic Modeller ...
Known source URL: ...                          (whichever of these the document actually has)

Historical analysis instruction:
Interpret the advertised role substantially in the professional and labour-market
context of its original posting date. Do not silently apply present-day assumptions.

Original advert text:
<document.content_text>
```

`prompts/extract_job_posting.md` gained an explicit, deterministic
**HISTORICAL CONTEXT** section (not just an ad hoc instruction line) codifying:

- stated salary is copied verbatim from the original advert, never inflated or "modernised";
- no invented historical salary estimate without credible basis — `null` instead;
- `market_demand_score`/`automation_risk_score`/`top_adjacent_roles`/etc. must reflect the *original* era's labour market, not the present one;
- prefer `null` over false precision whenever a historical judgment can't be meaningfully made;
- `metadata.captured_at` is always the real present moment; `job.posting_date` is the historical source date;
- no inflation adjustment or other compensation normalisation here — that is a separate, later step.

`_apply_known_metadata` fills fields the model left blank (`job.posting_date`,
`job.title`, `metadata.source`, `metadata.url`) from the document's own known
metadata — it never overwrites a value the model actually provided, and never
touches the document itself.

### 8a. Historical-extraction policy refinement (CP Ent Phase 3B 0.2)

Review of the persisted 10-document pilot output found that despite §8's
original prompt language, historical extractions were still coming back with
analytical fields that were meant to be deferred (a populated
`market_demand_score`/`automation_risk_score`, non-empty `top_adjacent_roles`,
and/or a `salary_estimate_min`/`max` that simply restated the stated salary).
The pilot's own 10 roles were left exactly as they are — this is a
policy-and-safeguard fix for *future* processing, not a backfill.

Two changes, deliberately layered rather than replacing one with the other:

1. **Prompt strengthening (primary).** The HISTORICAL CONTEXT section now
   splits historical fields into two explicit classes instead of one set of
   "prefer null" guidance: (A) factual/structural extraction — including the
   structural judgments `seniority_score`/`complexity_score`/
   `specialisation_score`/`transferability_score`/`rarity_score`, which
   describe the role's own documented shape, not the labour market, and stay
   fully in scope — and (B) forward-looking market judgments
   (`market_demand_score`, `automation_risk_score`, `top_adjacent_roles`),
   which are now an unconditional "always null for historical", not merely
   preferred. Salary keeps its fact/judgment split, made explicit: a stated
   salary makes restating it as an "estimate" wrong outright, and an
   estimate with no stated salary to draw from should still normally be
   `null` unless the advert text itself grounds one (e.g. a named pay
   band) — never modern salary knowledge.
2. **Deterministic safeguard (backstop).** `document_processing._enforce_historical_extraction_policy`
   runs after `_apply_known_metadata` and before persistence, and
   unconditionally nulls `analysis.market_demand_score`,
   `analysis.automation_risk_score`, and `analysis.top_adjacent_roles`, and
   nulls `analysis.salary_estimate_min`/`max` wherever the corresponding
   `job.salary_min`/`max` fact is present — *whenever the document itself
   carries a known `source_date`* (the same signal that puts "Known original
   posting date" into the model's input in the first place). This guarantees
   the contract even against a non-compliant model response, without
   guessing at "historical-ness" from the model's own output. It never fires
   for a document with no known `source_date` (an ordinary current-posting
   extraction), and it never touches the structural scores, skills,
   requirements, or any other factual field — only the three deferred
   analysis fields and the salary-estimate/salary-fact interaction above.

No schema change was needed: every field this touches was already
`Optional`/nullable on `JobPostingImport` (`backend/app/models.py`) — the fix
is prompt language plus one small, targeted function, not a model or pipeline
redesign.

## 9. An existing document becomes a role — without creating another document

`backend/app/posting_persistence.py::posting_role_columns(payload,
document_id)` is the shared, refactored column builder (previously inlined in
`routes/import_routes.py::posting_columns`): given a validated
`JobPostingImport` and a `document_id`, it returns the flat
`jobber.role_instance` column dict (including `legacy_scores`/
`legacy_analysis` packing) — and does **not** create or require a document.

For raw-document processing, `document_processing._persist_success` calls it
with the **existing** document's id — `role_instance.document_id` is set to
that document, `db.create_document()` is never called. The legacy JSON/native
import paths still create a fresh document first (unchanged behaviour there
— see §10), then call the same shared column builder; no business logic is
duplicated between the two.

`legacy_analysis["raw_json"]` still carries the full validated
`JobPostingImport`, for backward compatibility, even though
`extraction_run.output_payload` is now the authoritative run-specific copy.

## 10. `/api/import/native` moved to raw-first

Previously: extract first, then create a document from the model's
*composed* summary text. Now (`routes/import_routes.py::import_posting_native`):

1. Create the `jobber.document` first, with `content_text` = the **actual
   pasted text, verbatim** (not a composed/derived summary).
2. Call the same `process_job_posting_document` service the historical
   corpus uses.
3. Reshape the result into the pre-existing response contract
   (`id`, `status`, `extraction`, `run.{task,model,prompt_name,prompt_version,status}`)
   so the frontend (`Import.tsx`, which reads `res.id` and `res.run.model`)
   and every HTTP status code existing tests assert
   (503 config / 502 provider / 422 format-or-schema) are unchanged.
   `extraction_run_id` is added to the response (additive, non-breaking).

This was achievable **without breaking compatibility** — the external
contract (response shape, status codes) is preserved; only the internal
construction changed, plus one genuine, deliberate behaviour change:
`jobber.document.content_text` for a natively-imported posting is now the
verbatim pasted text, and a real `extraction_run` row is now written for
native imports (previously a known gap — docs/13 §4 — since native import
predated the capability model's provenance recording).

The legacy JSON import path (`POST /api/import`, `/bulk`) is untouched and
still supported for backward compatibility/debugging, per brief §20 — it was
not removed.

## 11. Service layer, CLI, and API

**Service** — `backend/app/document_processing.py::process_job_posting_document(document_id)`:
verifies the document, checks for prior success, runs the lifecycle above,
persists atomically (§12), attempts an embedding, and returns:

```text
{document_id, extraction_run_id, status, role_instance_id, error, error_type,
 embedding_error, model, prompt_name, prompt_version, output_payload}
```

`status` is one of `already_analysed | already_processing | ok | partial | failed`.
All AI provider calls stay inside `app.ai` (via `run_json_task`) — nothing in
routes or scripts calls OpenAI directly.

**API** (`backend/app/routes/documents.py`):

```text
POST /api/documents/{document_id}/analyse       -> one document, synchronously
GET  /api/documents/{document_id}/status        -> {document_id, state}
GET  /api/documents/processing-status[?source_prefix=] -> {raw, running, analysed, partial, failed, total}
```

A web request never processes more than one document — bulk corpus
processing stays a CLI/batch operation.

**CLI** — `backend/scripts/process_job_documents.py`:

```bash
cd backend
python -m scripts.process_job_documents --source-prefix historical_roles:v1: --limit 10
python -m scripts.process_job_documents --source-prefix historical_roles:v1: --dry-run
python -m scripts.process_job_documents --source-prefix historical_roles:v1: --retry-failed --limit 5
```

Flags: `--limit N`, `--source-prefix PREFIX`, `--date-from`/`--date-to`
(`YYYY-MM-DD`), `--retry-failed`, `--dry-run`. No `--force` — an
already-successful document is always skipped, never reprocessed (§7). Default
behaviour selects eligible documents (raw, plus failed-only when
`--retry-failed`), processes sequentially, continues past isolated failures,
and prints a compact per-document line plus final totals:

```text
[3/10] 2008-06-12 | Capital Modelling Analyst | ok

selected=10 processed=10 succeeded=9 partial=1 failed=0 skipped_existing=0 embedding_pending=0
```

## 12. Atomicity and embeddings

Once a validated `JobPostingImport` exists, `_persist_success` writes
`role_instance` + skill observations + `legacy_scores`/`legacy_analysis` +
`extraction_run.output_payload`/`result_role_instance_id`/`status`/`finished_at`
in **one short transaction**. A failure anywhere in that block rolls the
whole thing back (no dangling role, no run pointing at an incomplete role),
and the run is then marked `failed` in a fresh, subsequent transaction.

Embedding happens **after** that transaction has committed
(`_attempt_role_embedding`), using `document.content_text` as the embedding
source — consistent with `embeddings.rebuild_role_embeddings`'s existing
preference for a linked document's verbatim text over a recomposed summary
(docs/16 §18). A transient embedding failure (model unavailable, network
issue) never rolls back or invalidates the already-persisted role/run; it is
reported as `embedding_error` in the result, and the role stays eligible for
the existing `rebuild_role_embeddings`/`POST /api/space/rebuild-role-embeddings`
backfill.

## 13. Requirement extraction stays a separate, later step

This pipeline stops at `role_instance` + legacy skill observations + a role
embedding. It does **not** run `requirement_extract`/concept linking/concept
proposals as part of the same transaction or call. Batch orchestration for
that remains a future decision, made after evaluating this phase's output —
unchanged from before this phase, and Phase 3's capability-engine semantics
(docs/16) are untouched by this work.

## 14. Tests

`backend/tests/test_document_processing.py` (service layer, `run_json_task`
mocked — never a live OpenAI call) and `backend/tests/test_documents_api.py`
(route layer) cover: the raw document is never modified; an existing document
is reused, never duplicated; exactly one role is created per successful run;
`output_payload` holds the validated `JobPostingImport`; `result_role_instance_id`
linkage; the `running -> ok` lifecycle (proved by a fake provider call that
itself queries the database mid-call, from a separate connection, and
observes a committed `running` row); provider/schema-validation failures
recorded as `failed`; failed documents can be retried; successful documents
are skipped on rerun (and the provider is never called a second time);
concurrent duplicate processing cannot create two roles (two real threads);
`vocabulary_version_id` is null for `job_posting_extract` and still required
for `requirement_extract`; historical `source_date`/`source`/`title` reach
the model input, and a historical date is never silently replaced with
today's; embedding failure doesn't roll back a successful role; state
derivation across raw/running/analysed/partial/failed; no `profile360` writes
occur anywhere in this pipeline; batch eligibility selection (skip
successful, include raw, retry only when asked). `test_migration_compatibility.py`
gained direct schema/constraint assertions for the new columns and CHECKs.

§8a's historical-extraction policy refinement added its own focused
regression coverage in `test_document_processing.py`: a stated historical
salary stays in `salary_min`/`salary_max`/`currency` while
`salary_estimate_min`/`max` are nulled rather than echoing it; a historical
advert with no stated salary leaves both the factual and estimated salary
fields null; `market_demand_score`/`automation_risk_score` are null and
`top_adjacent_roles` is null/empty for historical output regardless of what
the (mocked) model returned; the structural scores
(`seniority_score`/`complexity_score`/`specialisation_score`/
`transferability_score`/`rarity_score`) survive unchanged; skills and
`requirements`/`responsibilities` extraction is unaffected; and a control
document with no known `source_date` proves the safeguard never fires for an
ordinary (non-historical) extraction.

`test_import_native.py` was updated (not just left alone) to match the
refactor: its `run_json_task` mock now patches
`app.document_processing.run_json_task` (the new call site) instead of
`app.routes.import_routes.run_json_task`, and gained tests for the two
genuine behaviour changes (§10): verbatim raw-text storage, and a real
`extraction_run` row now being written. `backend/tests/conftest.py`'s
embedding stub list was extended to cover `app.document_processing.embed_text`
alongside the existing route-module entries.

## 16. Pilot

The full 307-document historical corpus (`source_key LIKE
'historical_roles:v1:%'`) was **not** processed by this implementation work,
per the brief. A controlled pilot runs separately, after review:

```bash
cd backend

# 1. Dry run first — lists what would be selected, changes nothing:
python -m scripts.process_job_documents --source-prefix historical_roles:v1: --dry-run

# 2. An 8-10 document pilot, spanning oldest/newest, short/long, salary
#    stated/not, clean/messy source (selected by ordering oldest-source-date
#    first — --date-from/--date-to narrow to specific eras if a more
#    deliberately stratified sample is wanted):
python -m scripts.process_job_documents --source-prefix historical_roles:v1: --limit 10
```

## 17. Deferred / out of scope for this phase

- Automatic reanalysis or role supersession/versioning for a document that
  already succeeded (§7) — needs its own design.
- Running `requirement_extract`/concept linking automatically after
  `job_posting_extract` (§13).
- Processing the full 307-document historical corpus — a small pilot runs
  separately after review (§16).
- A frontend dashboard for the processing-status counts — the API (§11) is
  built to support one; no frontend work was done here.
