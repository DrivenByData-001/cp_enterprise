# 13 — Native AI Task Layer

**Status:** implemented — posting extraction (Phase 1) plus four Phase 2
closed-vocabulary tasks: role requirement extraction, concept-candidate
adjudication, profile360 claim mapping, profile360 capability mapping
**Related:** `docs/11-capability-model-design.md` (capability model; `extraction_run`, §4.1),
`docs/14-phase2-postgres-architecture.md` (Postgres schema these tasks write to)

---

## 1. Objective

Make AI-assisted extraction a first-class, in-app operation instead of an
external "paste this prompt into a chat UI, paste the JSON back" workflow.
The first vertical slice: raw job posting text → native AI extraction →
typed validation → the existing posting import path.

The abstraction (`backend/app/ai.py`) is deliberately generic so the same
pattern later covers episode extraction, concept linking, evidence-claim
extraction, capability attribution, target-role construction, compensation
extraction, and profile enrichment — each a call to `run_json_task` with a
different prompt file and output model, not a new integration.

## 2. What was built

### `backend/app/ai.py` — the AI task abstraction

- `run_json_task(task, prompt_name, user_input, output_model, max_tokens=8192) -> AITaskResult[T]`
  loads a prompt from `prompts/`, calls the configured OpenAI model, and
  validates the JSON response into `output_model`.
- Four typed failure modes, so callers never see a raw provider/JSON/Pydantic
  exception:
  - `AIConfigError` — missing `OPENAI_API_KEY`/`CP_AI_MODEL`, or an unknown
    prompt file.
  - `AIProviderError` — the provider was unreachable or returned an error
    (`openai.APIError` and subclasses).
  - `AIResponseFormatError` — the response text wasn't valid JSON.
  - `AISchemaValidationError` — valid JSON that doesn't satisfy `output_model`.
- `AITaskResult` carries both the validated `output` and an `AITaskRun` —
  `task`, `model`, `prompt_name`, `prompt_version`, `started_at`,
  `finished_at`, `status`, `input_chars`, `output_chars`. See §4.
- Provider details (model name, API key) are read from environment/config,
  never hardcoded, and nothing outside `ai.py` imports the `openai`
  package — swapping providers means changing `_client()`/`run_json_task()`
  here only.

### Prompt versioning (§2, architectural principle)

Prompts are versioned by content, not by a hand-maintained version string:
`prompt_version(text)` is the first 12 hex characters of `sha256(text)`.
Editing `prompts/extract_job_posting.md` automatically produces a new
version with no separate bookkeeping to fall out of sync — the same
idempotency-by-hash pattern already used for `document.body_sha256`
(`docs/11-capability-model-design.md` §4.1).

### `POST /api/import/native` — native posting extraction

`backend/app/routes/import_routes.py::import_posting_native`. Takes
`{ text, source_url?, known_posting_date? }`, runs the existing
`prompts/extract_job_posting.md` prompt through `run_json_task` against
`JobPostingImport`, and feeds the validated result through the **same**
`_insert_posting` helper the legacy JSON and bulk-file import paths already
use — no insertion logic is duplicated. Each `AITaskError` subclass maps to
a distinct HTTP status (503 config, 502 provider, 422 format/schema) so the
UI can distinguish "you need to set up an API key" from "the model's output
didn't parse."

### Import UI

`frontend/src/pages/Import.tsx` now leads with "paste text → Extract with AI
& import" as the primary flow, and demotes the old paste-JSON-from-an-
external-chat workflow to a labelled "Legacy JSON import" card for
migration/recovery/debugging. No more "take this prompt to Claude/ChatGPT."

### Configuration

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI API credential. Never committed; read from the environment only. |
| `CP_AI_MODEL` | Model name to call, e.g. `gpt-4o-mini`. No default — an unset value is a configuration error, not a silent fallback. |

See the README for local setup.

## 3. What was deliberately not built

- **No provider framework.** One small module, one provider. Adding a second
  provider later means a second `_client()`-equivalent behind the same
  `run_json_task` signature, not a plugin system.
- **No queue or background worker.** `run_json_task` is a synchronous call
  inside the request; posting extraction is a single call that returns in
  seconds. Revisit only if a task genuinely needs to run longer than an HTTP
  request should block.
- **No redesign of `JobPostingImport` or the posting schema.** The existing
  prompt/contract predates the capability model and stays as-is here — see
  §5.
- **No `document`/`extraction_run` rows written yet** for native posting
  extraction. See §4.

## 4. Relationship to `extraction_run` — done in Phase 2

Everything this section originally deferred is now built. `role_instance` and
per-posting `document` rows exist (`backend/app/db.py::get_or_create_document`,
called from `import_routes.py`/`role_instances.py` before the role is
created), and `extraction_run` is written for real —
`backend/app/extraction.py::_record_extraction_run` — for every AI task,
success or failure.

Two changes from what this section originally sketched, both recorded in full
in `docs/11-capability-model-design.md`'s Phase 2 build notes (§11):

- **`extraction_run.document_id` became nullable**, alongside three sibling
  subject columns discriminated by a new `subject_type` — a profile360 claim/
  capability mapping task has no jobber document to point at, and the
  original `NOT NULL` had no way to express that honestly.
- **`import_posting_native` itself still does not write an `extraction_run`
  row** — it predates the capability model's `JobPostingImport` shape (§5
  below) and stays a `legacy_role_analysis`-only path, matching §14 of the
  Phase 2 brief ("old flat fields... are not authoritative Phase 2
  outputs"). The four tasks that *do* write `extraction_run` are the new
  closed-vocabulary ones — see §7 below — reached via the separate
  "source-aware ingest" path (`POST /api/role-instances/ingest`) plus
  `POST /api/role-instances/{id}/extract-requirements`, not via
  `/api/import/native`.

`AITaskRun` (§4.1 originally) is unchanged and still the thing every task
returns on success; `extraction_run` is now what persists it (or, on
failure, an `error_type`/`error_message` in its place — `run_json_task`
itself still never touches storage, `extraction.py` does that on top of it).

## 5. Prompt vs. architecture conflicts found

`prompts/extract_job_posting.md` predates the capability model entirely —
it asks for a flat `skills[]` array with free-text `category`/`importance`,
plus a grab-bag of speculative "analysis" scores
(`seniority_score`, `rarity_score`, `automation_risk_score`, ...). These are
exactly the `legacy_role_analysis` fields `docs/11-capability-model-design.md`
§10.2 says have "no provenance and no way to recompute them" and plans to
retire. Per the task brief this integration does not attempt to fix that —
`JobPostingImport` is used unchanged so the native path and the legacy JSON
path stay interchangeable. It reproduces the same non-canonical vocabulary
Phase 1 already treats as raw `concept_proposal` input (§10.1), which is the
correct place for it to be resolved, not here.

## 6. Local setup

```bash
cd backend
pip install -r requirements.txt
export DATABASE_URL=postgresql://...       # see README / docs/14
export OPENAI_API_KEY=sk-...
export CP_AI_MODEL=gpt-4o-mini
python3 -m uvicorn app.main:app --reload --port 8000
```

Without `OPENAI_API_KEY`/`CP_AI_MODEL` set, every AI task (native posting
import, requirement extraction, profile360 mapping) fails with a clear
`AIConfigError` — mapped to `503` for the request that triggered it, and (for
the four Phase 2 tasks, §7 below) persisted as a `failed` `extraction_run`
row — while the rest of the app (embeddings, legacy JSON import, everything
else) is unaffected.

## 7. Phase 2 closed-vocabulary tasks

Four new tasks, all in `backend/app/extraction.py`, all reached through
`run_json_task` like the Phase 1 posting extraction above — no direct
provider calls, no new failure-handling pattern:

| Task | Prompt | Output schema | Written by |
|---|---|---|---|
| `requirement_extract` | `prompts/extract_role_requirements.md` | `RequirementExtractionResult` | `extraction.extract_role_requirements` |
| `concept_link_adjudicate` | `prompts/adjudicate_concept_candidates.md` | `ConceptAdjudicationResult` | same function, Phase B of the same call |
| `profile360_claim_map` | `prompts/map_profile360_claim.md` | `ClaimMappingResult` | `extraction.map_profile360_claim` |
| `profile360_capability_map` | `prompts/map_profile360_capability.md` | `ClaimMappingResult` | `extraction.map_profile360_capability` |

`requirement_extract` is deliberately **open**-vocabulary at the prompt level
(it asks for verbatim requirement spans, not a match against a supplied
concept list) — the closed-vocabulary guarantee comes from what happens
*after* the model responds: `docs/11-capability-model-design.md` §7.3's
cascade (exact match → embedding retrieval → `concept_link_adjudicate`
adjudication → `concept_proposal`) runs in Python, and the model is never
shown the full vocabulary at once. An earlier draft of this prompt tried to
hand the model the whole candidate list in one call instead — abandoned
because it scales badly past a few hundred concepts and doesn't use the
embedding-retrieval infrastructure §7.3 already specifies.

Every one of the four validates its own hard rule before persisting
anything: `requirement_extract`'s spans are checked with
`span_validation.validate_span` (never trusted on the model's say-so, and
downgraded to `basis='inferred'` with no span at all when the source
document's provenance isn't `original_capture`); the two `profile360_*_map`
tasks may only choose from the candidate list they were shown, verified by
looking the chosen name up in that same list before writing a mapping row —
a hallucinated name that doesn't match any candidate is treated as a
decline, not an error.
