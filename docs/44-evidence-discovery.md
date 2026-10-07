# Evidence discovery and reviewed mappings

## User journey

Applications → Continue → Evidence → Find supporting evidence. The role must
have accepted requirements. The search scans career claims and their evidence
and episode context in recent-first batches, alongside the job specification.
Each claim can support multiple requirement concepts. Results persist across
refreshes and are separate from canonical evidence until reviewed.

Each finding shows the original claim, provenance, exact requirement passage,
limitations and a suggested explanation. The user can edit/save a draft, reject,
or explicitly approve. Clarifications are optional and become user-asserted
provenance on approval, never relabelled documentary evidence. The existing
“Add reviewed career claim” / “Correct career claim” workflow remains available
within each requirement for new or corrected career facts, including credentials.
The user's qualification year of 2020 remains in the saved findings note until
accepted into that workflow; no personal facts are hardcoded into migrations.

Approval reuses the existing claim, creates/accepts its canonical concept mapping,
and stores reviewed depth/autonomy against that particular mapping. Episode
narratives and claim text are preserved. The existing matcher reads current
assessments; any changed claim, episode or supporting evidence invalidates those
modifier values. Unknown values remain unknown. An approved mapping can benefit
other applications, but application evidence selection and coverage judgments
remain explicit and distinct.

## Persistence and failure behaviour

Migration `20261007051224_evidence_discovery.sql` adds server-only RLS-enabled
run, finding, review-audit and assessment tables. Existing authenticated routes
protect all discovery operations; no public API grants or security-definer
functions are introduced. The application already applies migrations at startup.

Discovery uses a FastAPI background task, with each completed batch committed
separately and no transaction held across an AI call. Run state, progress and
errors persist. It is not a durable external job queue: interrupted runs become
retryable failures after 15 minutes without progress. A replaced/expired worker
cannot later publish results. Completed proposals remain available on failure.
Retries deduplicate by application, claim, concept and source/requirement revision.
Rejections for unchanged input are retained. A changed source can produce a new
proposal; existing explicit mapping rejections are respected.

Approvals lock the application, finding and source claim, check optimistic finding
revision and source/requirement fingerprints, then commit mapping, clarification,
assessment and audit together. Repeated approval receives a conflict rather than
duplicating evidence. The UI preserves the draft after an API failure. Saving edits
persists it for return later. No generated SQL is executed.

## Model

All native AI tasks use `gpt-5.4-mini` by default. `CP_AI_MODEL` remains an optional
explicit override; render.yaml and .env.example specify the same default. No
separate discovery model is introduced. Discovery requests strict JSON Schema,
validates IDs against the supplied batch and accepted requirements, and rejects
incomplete/refused output. Existing task schemas retain their existing JSON-mode
behaviour with added incomplete/refusal checks.

## Verification and deployment limits

- Backend unit coverage: unknown IDs, duplicate versus multiple capability mappings,
  batch ordering/no truncation, invalid review levels, app-wide default, strict schema,
  refusal and incomplete output.
- Disposable PGlite integration exercised the migration and actual service SQL:
  discovery, edit without mutation, approval, provenance/audit, modifier use,
  preservation of episode narrative, source invalidation, stale requirements,
  conflict handling and provider failure persistence. This is not a live Supabase test.
- PostgreSQL integration tests are included in `tests/test_evidence_discovery.py`;
  local native PostgreSQL was unavailable, so those tests were skipped locally.
- Frontend unit suite and application browser flows include discovery review on
  desktop/mobile, edited clarification, explicit approval, history and failure states.
- Live OpenAI output quality and production deployment remain unverified: this
  development environment has no OPENAI_API_KEY. Render's configured key was not read.

Before release, run the native PostgreSQL integration suite and a live discovery
smoke test with the deployment's configured OpenAI account. Check the saved career
evidence findings as an evaluation set, especially treaty drafting versus technical
documentation, external client ownership, commercial pricing, ALM and PQE limits.
The model must explain partial support rather than upgrade a broad label into full
role-specific coverage. Production personal records were not modified during build.
