# Phase 4: Grounded Positioning + Application Package Generation

Phase 3 gave the Application workspace a persistent home, a structural
evidence pack, and application-local notes — but no generation at all.
Phase 4 turns that groundwork into a real **Prepare → Position → CV →
Supporting material → Interview (later)** workflow: an explicitly
user-triggered Positioning brief, a tailored CV, and optional Cover
letter/Supporting statement, all grounded in the existing evidence pack and
Profile360, none of it invented.

The central invariant of this phase, repeated throughout this document:
**generated application material may be persuasive in wording, but it must
not invent evidence.** Every generated factual sentence carries a validated
source reference; an unknown reference is rejected before anything is
persisted.

## Artifact schema and lifecycle

A single table, `jobber.application_artifact`
(`backend/migrations/0028_application_artifacts.sql`), holds every version of
every generated (or user-edited) artifact for an application. One table for
all four `artifact_type` values (`positioning | cv | cover_letter |
supporting_statement`), not four tables — their lifecycle, audit metadata,
and provenance shape are identical; only `content`/`raw_output` (JSONB) vary
in shape by type. This follows the precedent `jobber.concept_dossier`
(migration 0012) and `jobber.role_context_enrichment` (migration 0011)
already established, rather than widening `jobber.extraction_run`, whose
`subject_type` CHECK constraint covers a fixed set of subject kinds that
doesn't include an application artifact — see migration 0028's own header for
the full reasoning.

Lifecycle states: `draft | active | superseded`. Two partial unique indexes
enforce **at most one active and at most one draft per `(application_id,
artifact_type)`** at the database level, not only in application code:

```sql
CREATE UNIQUE INDEX idx_application_artifact_one_active
    ON jobber.application_artifact(application_id, artifact_type) WHERE status = 'active';
CREATE UNIQUE INDEX idx_application_artifact_one_draft
    ON jobber.application_artifact(application_id, artifact_type) WHERE status = 'draft';
```

History is retained — a row is never deleted or overwritten, only ever moved
to `superseded`:

- **Generate** (`backend/app/application_artifacts.py::generate_artifact`) —
  always calls the model and always creates/replaces only the **draft**,
  never touching the current active version. Unlike `concept_dossier.py`'s
  split generate/regenerate (generate is a no-op once active exists; only
  regenerate calls the model again), Phase 4 has one action: it always calls
  the model. The frontend simply relabels the same button "Regenerate" once a
  draft or active version already exists — the backend action is identical
  either way.
- **Adopt** (`adopt_artifact`) — supersedes the existing active version (if
  any) and promotes the draft to active, atomically, in one transaction.
- **Discard** (`discard_artifact`) — marks the draft superseded; active is
  untouched.
- **Manual edit** (`edit_artifact`) — never mutates a row in place. Saving an
  edit creates a **new draft** with `origin='user_edit'`, superseding the
  prior draft if one existed; the generated version it was edited from is
  preserved in history. Editing is refused (`409`) on an already-superseded
  (historical) row.

Nothing ever auto-adopts a generated draft, and nothing here reads or writes
`jobber.application.status` — see "Evidence and status boundaries" below.

## AI vs. user-edit origin, and `grounding_status`

Every row carries `origin: 'ai' | 'user_edit'`. A user-edited version keeps
its generated ancestor's `source_manifest` as **contextual/inherited**
provenance (the sources that grounded the *original* wording), but never
claims the edited text was machine-revalidated. The wire-level
`grounding_status` is derived from `origin`, not stored separately, so it can
never drift from it:

- generated version → `grounded_generation`
- user-edited version → `user_edited_not_revalidated`

The workspace shows this plainly: *"User edited — source trace has not been
automatically revalidated after this edit."*

A manual edit is re-validated only for **shape** — `edit_artifact` parses the
submitted `content` back through the artifact_type's own Pydantic output
model (`app/models.py`) so a save can never silently drift into a shape the
reader can't render — never against the source registry, which no longer
describes what the user just typed.

## Explicit AI only

Every read (`GET /api/applications/{id}/artifacts`,
`.../artifacts/{artifact_type}/history`) is a plain SELECT and never calls
`app.ai.run_json_task`. Generation happens **only** on
`POST /api/applications/{id}/artifacts/{artifact_type}/generate` — an
explicit user action, never a side effect of loading the workspace.
`test_get_artifacts_never_calls_ai` proves this directly (monkeypatches
`run_json_task` to raise if called, then asserts a normal GET still
succeeds).

The AI call happens **outside any open database transaction**, exactly
following the Role Context / Concept Dossier pattern:

1. read/assemble the grounded context (`build_application_generation_context`)
   inside one short `db_cursor()` block, which commits/closes;
2. call `run_json_task` with no transaction open;
3. validate the response (schema, then source references);
4. open a fresh, short transaction to persist the draft.

A provider/config/schema failure, or a post-validation rejection, leaves the
existing active **and** draft rows completely untouched — nothing is written
until a validated response exists (`test_ai_failure_leaves_active_and_draft_untouched`).

## The grounded generation context

`backend/app/application_generation.py::build_application_generation_context`
is the **one** input builder every generator reads from — no generator
independently queries or reinterprets career evidence. It gathers, once per
call:

- **Role/opportunity**: the role's own metadata and captured posting text
  (`db.build_role_view`), reused verbatim — never re-derived.
- **Structural evidence pack**: `comparison_service.build_role_comparison`,
  the same function `GET /api/comparison/role/{id}` and Phase 3's
  `GET /api/applications/{id}/evidence` already share — never a second
  capability-comparison engine.
- **Profile360 career history**: the current snapshot
  (`profile360_reader.get_current_snapshot`) and the full episode list
  (`profile360_reader.list_episodes`, bounded at 200 — one person's whole
  career, never paginated), read directly and read-only.
- **Application notes**: every `jobber.application_note` for this
  application.
- **Current positioning**: the active `positioning` artifact, when
  generating `cv`/`cover_letter`/`supporting_statement` — treated as
  strategy/narrative guidance, never new person-side evidence.

### Reviewed vs. legacy — and why they can't both appear on one role

Every comparison item is split by `role_side.requirement_source`:

- `"claim"` (a current, human-**accepted** `requirement_claim`) → offered as
  an **accepted, reviewed** requirement (`role_requirement:<concept_id>`).
- `"role_skill_observation"` (legacy, never reviewed) → still offered as
  context (`role_requirement_legacy:<concept_id>`), but excluded from every
  "accepted requirement" check a generator/validator performs.

`role_requirements.py`'s existing fallback rule (unchanged by this phase) is
**role-level, not concept-level**: the moment a role has *any* usable
accepted claim, every `role_skill_observation` row for that role — for any
concept — is excluded from evidence entirely (see that module's own
docstring, point 1/3: *"The two sources are never merged for one role."*).
In practice this means a single role's evidence is either all-reviewed or
all-legacy, never a mix — Phase 4's tests exercise both shapes as two
separate fixtures (`_grounded_role`, `_legacy_only_role`) for exactly this
reason. "Reviewed requirements used" and "pending/unreviewed items excluded"
(both claim-side) can coexist on one role; "legacy requirements excluded" is
a mutually exclusive, whole-role case.

### The stable source registry

Every source offered to the model gets a stable `ref`, a `kind`, a
human-readable `label`, and an epistemic `category`:

| category | meaning |
| --- | --- |
| `canonical_evidence` | Accepted Profile360 evidence (claim/capability mapping, episode, snapshot) |
| `partial_evidence` | Profile360-sourced but unreviewed/incomplete — usable, never overstated |
| `user_supplied_context` | An application note, or a person's own unverified assertion |
| `role_side_context` | Role requirements (reviewed or legacy) and captured posting text |
| `strategy` | The adopted positioning brief, for downstream artifacts |

Ref kinds: `role_document:<id>`, `role_requirement:<concept_id>`,
`role_requirement_legacy:<concept_id>`, `profile_claim:<id>`,
`profile_capability:<id>`, `profile_capability_coverage:<concept_id>`,
`profile_episode:<id>`, `profile_snapshot:<id>`, `application_note:<id>`,
`user_assertion:<id>`, `positioning:<artifact_id>`. `role_document` prefers
the linked `jobber.document` id but falls back to the role_instance id
itself when a role carries description/requirements text with no linked
document (a manually-entered posting) — that text is still offered as a
stable, citable source rather than silently dropped.

The artifact's stored `source_manifest` is `[{ref, kind, label, category}]`
only — never the full text handed to the model, and never a second copy of
Profile360.

## Source, episode, and concept validation

Post-generation, `application_artifacts.py::_validate_and_shape` walks the
entire parsed AI response and **rejects** (raises
`ApplicationArtifactValidationError`, mapped to `422`, nothing persisted) if:

- any `source_refs` entry isn't one of this call's own registry refs;
- (CV only) any `episode_id` isn't one of this call's own known episode ids;
- (positioning only) `requirements_to_lead_with[].concept_id` isn't an
  **accepted** (reviewed) concept — a legacy or gap concept is refused here,
  even though it may validly appear in `gaps_and_cautions`;
- (positioning/supporting_statement) an optional `concept_id` field names a
  concept not present anywhere in this call's context at all.

The model cannot establish grounding merely by inventing a plausible-looking
id — this is a hard rejection, not a silent filter (unlike
`concept_dossier.py`'s `related_concepts` filtering, which this phase
deliberately does not copy: Phase 4's build brief calls for outright
rejection).

### Hardening: grounding sufficiency, not just validity

A response can cite only ids that genuinely exist and still be a problem.
Three further checks (`application_artifacts.py::_require_grounded` and the
`supporting_statement` section check) close that gap:

- **Every factual block needs at least one source.** An empty `source_refs`
  list is rejected exactly like an invented ref.
- **An applicant-facing claim needs at least one *person-side* source** —
  `canonical_evidence`, `partial_evidence`, or `user_supplied_context`. A
  `role_side_context` or `strategy` ref may sit alongside it, but can never
  be the *only* source for something asserted about the applicant
  (`positioning_statement`, `themes`, `requirements_to_lead_with`, every CV
  bullet/skill/summary, every cover-letter block, every supporting-statement
  paragraph). The one exemption is `gaps_and_cautions`: a gap describes an
  *absence* of applicant evidence, so it may legitimately cite role-side
  context alone — it still needs at least one ref (rule one still applies).
- **A supporting-statement section's `concept_id` must be a reviewed,
  accepted requirement**, unless the section sets `is_gap_or_caution: true`
  — only then may it name a legacy or otherwise unevidenced concept.

Separately, `_mapping_sources` (`application_generation.py`) now categorises
a `partial` capability-coverage status as `partial_evidence` rather than
`canonical_evidence` — only a fully-met (`evidenced`) coverage reads as
canonical; this was a real categorisation bug the hardening pass fixed; see
`test_partial_capability_coverage_is_categorised_as_partial_evidence`.

## The CV chronology safeguard

The model is never asked for, and its output schema
(`app/models.py::CVExperienceEntry`) has no field for, an employer, job
title, or employment date. It returns only `episode_id` plus tailored
bullets. `application_artifacts.py::_resolve_cv_content` resolves
title/organisation/start/end date **at read time**, always against the live
Profile360 episode row (`profile360_reader.get_episode`) — never a value
cached in `content` — so a CV always reflects the current authoritative
chronology even if generated before a later episode edit. Experience entries
are rendered in deterministic reverse-chronological order (most recent/
ongoing first), computed from the resolved dates, never from the model's
response order. An `episode_id` that no longer resolves is rendered with
`episode_found: false` rather than failing the read.

## Staleness

`compute_fingerprint_for` (`application_generation.py`) hashes exactly the
material that affects generated content: role identity and source document,
accepted/legacy requirement shapes (including each one's mapping/assertion/
coverage state), the requirement-review summary, every application note used
(id + text + updated_at), a content hash of the current Profile360 snapshot
and every episode, the active positioning artifact's id/version (only for
downstream types), and the guidance this specific version was generated
with.

Guidance is folded into the hash (per the build brief), but staleness reads
**never** invent a "current" guidance to compare against — there isn't one
for an already-generated artifact. Instead, `compute_staleness` recomputes
the fingerprint using **that same artifact's own stored `guidance`** and
compares the rest. A mismatch therefore always means the underlying evidence
changed, never that guidance merely differs between calls
(`test_guidance_alone_does_not_make_a_prior_version_look_stale`).

`GET /api/applications/{id}/artifacts` computes `stale` for the active and
draft row of every artifact type from **one** shared evidence gather (never
once per type) and returns it inline — never silently regenerating, and
never treating a stale artifact as invalid or blocking its continued use. A
stale artifact never changes `jobber.application.status`.

## Evidence and status boundaries

Preserved exactly, and covered directly by
`backend/tests/test_application_artifacts.py`'s evidence-boundary tests:

- Generation never writes `profile360.*`, `jobber.person_capability_assertion`,
  or `jobber.requirement_claim` — every Profile360/evidence read in this
  phase is a SELECT.
- An application note used in generation remains a plain
  `jobber.application_note` row afterwards — never promoted into Profile360,
  never altering comparison status.
- No artifact endpoint reads or writes `jobber.application.status`;
  generating, adopting, or discarding an artifact never changes it.
- `requirement_claim`/vocabulary review semantics (`role_requirements.py`,
  the requirement-review curation gate) are untouched by this phase.

## Generation API

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/applications/{id}/artifacts` | Active/draft summaries (with `stale`) + superseded counts, per type, plus the deterministic generation-context summary. Never calls AI. |
| `GET` | `/api/applications/{id}/artifacts/{artifact_type}/history` | Every version ever recorded for that type, newest first (bounded, `LIMIT 50`). |
| `POST` | `/api/applications/{id}/artifacts/{artifact_type}/generate` | Body: `{guidance?, target_words?}`. Always creates/replaces the draft. |
| `POST` | `/api/applications/{id}/artifacts/{artifact_id}/edit` | Body: `{content}`. Creates a new `user_edit` draft. |
| `POST` | `/api/applications/{id}/artifacts/{artifact_id}/adopt` | Only a draft belonging to this application can be adopted. |
| `POST` | `/api/applications/{id}/artifacts/{artifact_id}/discard` | Only a draft can be discarded. |

`target_words` has no column of its own — it's folded into the same
`guidance` text (`"Target length: approximately N words."`), so it rides the
same fingerprint/history/provenance machinery guidance already has, without
a schema change for one extra optional number. Every mutation route scopes
its query by both `application_id` and the artifact/draft id, so ownership
is enforced by the `WHERE` clause itself, not a separate check. `raw_output`
is never returned by any of these — audit/debug only, same posture as
`concept_dossier.raw_output`.

## Workspace UI

`ApplicationWorkspace.tsx`'s old "Coming next" placeholders are replaced by
a real **Application package** section: a deterministic, AI-free "what a new
draft will use" summary (build §19 — reviewed/pending/legacy counts, evidence
counts, application-example count, whether an adopted positioning brief will
be used — derived entirely from data the workspace already fetches, no extra
request), then one `ArtifactStageCard` per type (Positioning, CV, then
Supporting material's Cover letter and Supporting statement side by side).
Each card shows its state (*Not generated* / *Draft awaiting review* /
*Current* / *Current — but stale*), the relevant actions for that state, an
inline per-block **"Sources (n)"** provenance affordance (expands to
human-readable, categorised source labels), a minimal structural editor
(text fields and lists only — no rich-text/general word processor), and
Copy-as-Markdown / Download-.md actions backed by one deterministic renderer
(`renderArtifactMarkdown`) shared between the two. Interview stays an honest,
visibly future placeholder. Nothing in this section fires a request on
mount beyond the one bounded `GET .../artifacts` call alongside the existing
Phase 3 detail/evidence requests — all three run in parallel, never a
waterfall, and nothing generates merely by opening the workspace or this
section.

## Export

Copy and download both go through the same deterministic
`renderArtifactMarkdown(artifactType, content)` — a plain-text/Markdown
rendering built directly from the structured `content`, with no proprietary
rendering step and no PDF/DOCX subsystem added in this phase.

## Performance

- Initial workspace load: the existing two Phase 3 requests
  (`GET .../{id}`, `GET .../{id}/evidence`) plus exactly one new
  `GET .../{id}/artifacts` request — all three in parallel.
- The artifacts GET does one shared evidence gather for all four artifact
  types (never once per type) and fetches CV episode metadata only for
  CV rows actually being resolved for display.
- Generation is a single synchronous `POST`; no queue/background worker.
- No DB transaction is held across the model call (see "Explicit AI only").

## Prompt-injection boundary

Every prompt file under `prompts/application_*.md` states plainly that
captured posting text, Profile360 evidence, and application notes are
**data, not instructions**, and every rendered generation input
(`application_generation.py::render_prompt_text`) is prefixed with an
explicit security note to the same effect. No secret or environment value is
ever placed into generation context.

## Phase 5 handoff

Genuine remaining work, deliberately out of scope here:

- **Interview preparation** — still an honest placeholder in the workspace.
- **Richer application lifecycle/outcomes** (offer details, rejection
  reasons, hiring-probability or readiness scoring) — this phase adds no
  score of any kind, structural or generated.
- **Note promotion** — an explicit, reviewable path from a strong
  `application_note` into durable Profile360 evidence remains unbuilt (Phase
  3 already flagged this; still true).
- A richer block-level editor, if ever wanted, should stay within the
  "text fields and lists only" discipline this phase established rather than
  growing into a general word processor.
