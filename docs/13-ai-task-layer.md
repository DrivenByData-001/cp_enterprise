# 13 — Native AI Task Layer

**Status:** implemented (posting extraction only)
**Related:** `docs/11-capability-model-design.md` (capability model; `extraction_run`, §4.1)

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
  loads a prompt from `prompts/`, calls the configured Anthropic model, and
  validates the JSON response into `output_model`.
- Four typed failure modes, so callers never see a raw provider/JSON/Pydantic
  exception:
  - `AIConfigError` — missing `ANTHROPIC_API_KEY`/`CP_AI_MODEL`, or an unknown
    prompt file.
  - `AIProviderError` — the provider was unreachable or returned an error
    (`anthropic.APIError` and subclasses).
  - `AIResponseFormatError` — the response text wasn't valid JSON.
  - `AISchemaValidationError` — valid JSON that doesn't satisfy `output_model`.
- `AITaskResult` carries both the validated `output` and an `AITaskRun` —
  `task`, `model`, `prompt_name`, `prompt_version`, `started_at`,
  `finished_at`, `status`, `input_chars`, `output_chars`. See §4.
- Provider details (model name, API key) are read from environment/config,
  never hardcoded, and nothing outside `ai.py` imports the `anthropic`
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
| `ANTHROPIC_API_KEY` | Anthropic API credential. Never committed; read from the environment only. |
| `CP_AI_MODEL` | Model name to call, e.g. `claude-sonnet-5`. No default — an unset value is a configuration error, not a silent fallback. |

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

## 4. Relationship to `extraction_run` (Phase 2 integration)

`vocabulary_version` and `extraction_run` already exist in the schema
(built, empty, in Phase 0 — `docs/11-capability-model-design.md` §11 Phase 0
build notes) but nothing in the app writes to them yet, including this
layer. That's a deliberate choice, not an oversight:

- `extraction_run.document_id` is `NOT NULL REFERENCES document(id)`, and
  postings don't have a `document` row today — `role_instance` (the table
  that would let a posting own one) is explicitly unscoped, flagged in both
  the Phase 0 and Phase 1 build notes as "whoever scopes the next phase must
  build it." Writing an `extraction_run` row here would mean inventing a
  document row for it to point at, ahead of that decision — exactly the
  "prematurely force legacy import into the claim/extraction model" the
  task brief warned against.
- `extraction_run.vocabulary_version_id` is likewise `NOT NULL`, and no code
  path has ever inserted a `vocabulary_version` row — posting extraction
  doesn't touch the concept vocabulary at all, so forcing a reference here
  would be a fiction, not a fact.
- `extraction_run.task` is documented as one of
  `episode_extract | concept_link | capability_attribute | requirement_extract`
  (§4.1) — none of which is "extract a whole posting." Native posting
  extraction is closer to the *legacy* Pass D/whole-document shape than to
  any of the four canonical passes.

**What was built instead:** `AITaskRun` carries exactly the fields
`extraction_run` needs — `task`, `model`, `prompt_version`, `started_at`,
`finished_at`, `status` — as a plain dataclass returned to the caller (and
included in the `/api/import/native` response as `run`), so nothing is lost.

**Phase 2 integration plan**, once `role_instance`/`document` exist for
postings: have `import_posting_native` create a `document` row (`kind =
'job_posting'`, `body` = the pasted text, `source = 'user_paste'` or the
given `source_url`) before calling `run_json_task`, then insert one
`extraction_run` row from the returned `AITaskRun` plus that `document_id`
(and a real `vocabulary_version_id` once one exists). At that point
`task='job_posting_extract'` should be added to the documented enum in
`docs/11-capability-model-design.md` §4.1 alongside the other four passes,
or the whole-posting extraction should be reframed as a bulk Pass B/D run —
worth a short design decision at that time, not decided here.

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
export ANTHROPIC_API_KEY=sk-...
export CP_AI_MODEL=claude-sonnet-5
python3 -m uvicorn app.main:app --reload --port 8000
```

Without `ANTHROPIC_API_KEY`/`CP_AI_MODEL` set, `POST /api/import/native`
returns `503` with a message naming the missing variable — the rest of the
app (embeddings, legacy JSON import, everything else) is unaffected, since
no other code path imports `app.ai`.
