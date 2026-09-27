# Phase 5: Interview Preparation + Application Lifecycle

Phase 4 gave the Application workspace a real, grounded generation package
(Positioning, CV, Cover letter, Supporting statement) but left the
application's actual progress — submission, interviews, offers, closure —
entirely unrecorded, and Interview stayed an honest placeholder. Phase 5
completes the **Prepare → Position → CV / Supporting material → Interview →
Outcome** journey: a persisted, user-recorded lifecycle timeline, and a real
Interview stage whose Interview Prep artifact reuses Phase 4's grounded
generation machinery exactly.

The central invariants carried forward from Phase 4, unchanged: generated
material may be persuasive in wording, but it must never invent evidence, and
every generated factual block still carries a validated source reference. The
one invariant Phase 5 adds on top: a lifecycle event is user-recorded
history, never an AI judgment, and never a driver of `jobber.application.status`.

## Application event schema

`jobber.application_event` (`backend/migrations/0029_application_lifecycle_interview.sql`):

```sql
CREATE TABLE jobber.application_event (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id  UUID NOT NULL REFERENCES jobber.application(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL CHECK (event_type IN (...)),
    event_at        TIMESTAMPTZ NOT NULL,
    label           TEXT,
    notes           TEXT,
    details         JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Unlike `jobber.application`'s own restrictive FK to `role_instance` (migration
0027, deliberately not `ON DELETE CASCADE` so role deletion can't silently
destroy application history), `application_event.application_id` **is**
`ON DELETE CASCADE` — an event has no meaning once its Application is gone,
and no Application-deletion route exists in this or any prior phase, so this
FK is never actually exercised as a delete path today. One index,
`(application_id, event_at DESC)`, covers every list/aggregate this phase
needs: the workspace timeline, the "next scheduled interview" lookup, and the
Applications-index aggregate.

### Event types

`submitted | interview_scheduled | interview_completed | offer_received |
offer_accepted | offer_declined | rejected | role_closed | withdrawn |
closed | other` — enforced by the table's own CHECK constraint and mirrored
as `app.models.ApplicationEventType` / `app.application_events.EVENT_TYPES`
(derived from the same Pydantic Literal via `typing.get_args`, so the two can
never drift apart) and the frontend's `ApplicationEventType` union.

### Status vs. event semantics

`jobber.application.status` is unchanged from Phase 3: a user workflow state,
set only through `PATCH /api/applications/{id}`, never derived from an event,
Interview Prep, artifact readiness, or evidence coverage. Recording an event
**never** touches `status`, and vice versa — the two are allowed to disagree
for a while:

- an interview may be scheduled while the user hasn't yet changed status to
  `interviewing`;
- an offer may be recorded while the application remains `interviewing`.

The timeline records **what happened**; status records **how the user
currently categorises the application**. No combined "record X and set
status to Y" mutation was added in this phase — the UI's event actions
(below) and the existing status selector remain two separate, deliberate
user actions.

## Lifecycle Event API

All under the existing `/api/applications` router
(`backend/app/routes/applications.py`), all scoped by both `application_id`
and (for edit/delete) `event_id`:

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/applications/{id}/events` | Newest-first, bounded (`LIMIT 200`). The one request the workspace needs. |
| `POST` | `/api/applications/{id}/events` | Body: `{event_type, event_at, label?, notes?, details?}`. |
| `PATCH` | `/api/applications/{id}/events/{event_id}` | Every field optional — only supplied fields change. |
| `DELETE` | `/api/applications/{id}/events/{event_id}` | Removes a mis-recorded event only — never the Application or any artifact. |

Validation (`app/models.py::ApplicationEventInput`/`ApplicationEventUpdate`):
`event_type` is a closed Pydantic `Literal`, `label`/`notes` are
length-bounded and blank-to-`None` normalised (same convention as
`ApplicationNoteInput`), and `details` is rejected above 5000 serialized
characters — bounded JSON, never an open-ended blob.

### Applications-index lifecycle aggregate

`GET /api/applications` (`list_applications`) now also returns, per item:

```json
{ "latest_event": {"event_type": "...", "event_at": "..."} | null,
  "next_interview": {"event_at": "...", "label": "..." | null} | null }
```

computed with two `LEFT JOIN LATERAL` subqueries inside the **same** bounded
list query — never a second request per application, never an events fetch
per row. `next_interview` is deterministic: the soonest `interview_scheduled`
event with `event_at >= now()`; a past-dated one is ignored.
`application_events.next_scheduled_interview` expresses the identical rule in
Python, for the workspace (which already has the full event list from
`GET .../events` and derives it client-side rather than trusting a second
copy of the same logic to agree).

## Lifecycle UI

A new **Lifecycle** section sits near the top of `/applications/:id`,
visually distinct from preparation/evidence: current status (read, not a
second control — the existing selector stays the single place status is
changed), an upcoming-interview banner when one is recorded, the timeline
(newest first, with notes shown inline), and explicit one-click actions —
*Record submission*, *Schedule interview*, *Record completed interview*,
*Record offer*, *Record rejection / role closed*, *Add another event* — each
opening the same small composer (event type, date/time, optional
label/notes) pre-selected to that type. Every event can be edited or deleted
inline. A lifecycle-fetch failure shows its own inline error and never blanks
Application detail, evidence, or the package/Interview sections (its state is
independent — see `ApplicationWorkspace.tsx`'s four parallel `load*` effects).

## Interview Prep artifact type

`jobber.application_artifact.artifact_type` widens (migration 0029) to
accept `interview_prep` as a **fifth** value, reusing
`jobber.application_artifact` and every invariant Phase 4 built for it
exactly: `generate` → `draft`, explicit `adopt` → `active`, `discard`,
`edit` (creates a new `user_edit` draft), history retained, and the same
partial-unique-index-enforced at-most-one-active/at-most-one-draft-per-type
guarantee. No new table, no parallel document-generation subsystem — adding
the type to `app/application_artifacts.py`'s `ARTIFACT_TYPES`/
`_PROMPT_NAMES`/`_OUTPUT_MODELS` dicts is what makes every existing
generate/adopt/discard/edit/history route and function handle it, with zero
new branches in that lifecycle code.

### Typed output

`app/models.py::ApplicationInterviewPrepGeneration`:

```json
{
  "focus_areas": [{"title": "...", "why_it_matters": "...", "source_refs": [...]}],
  "questions": [{
    "question": "...", "question_type": "experience", "source_refs": [...],
    "answer_plan": {
      "approach": "...",
      "evidence_points": [{"text": "...", "source_refs": [...]}],
      "cautions": [{"text": "...", "source_refs": [...]}]
    }
  }],
  "questions_to_ask": [{"text": "...", "source_refs": [...]}],
  "closing_points": [{"text": "...", "source_refs": [...]}],
  "prep_checklist": ["..."]
}
```

`question_type` is one of `experience | technical | leadership | stakeholder
| motivation | role_specific | case | other` — a classification, never a
score. No numeric likelihood/confidence/readiness field exists anywhere in
this shape (build §18) — see "No hidden career scoring" below.

## Interview Prep generation context

`app/application_generation.py::build_application_generation_context` is
still the **one** context builder every artifact type reads through —
Interview Prep adds inputs, not a second builder:

- **Role-side**: unchanged — captured posting text, reviewed
  (`role_requirement:<concept_id>`) and legacy
  (`role_requirement_legacy:<concept_id>`) role requirements.
- **Person-side evidence**: unchanged — accepted/partial Profile360
  evidence, user assertions, application notes, the full episode list and
  current snapshot.
- **Current Application package** (new): the active `cv`, `cover_letter`,
  and `supporting_statement` artifacts (positioning was already offered to
  every downstream type as `strategy`) — offered under a new,
  **non-evidence** category, `application_material`
  (`gen.CATEGORY_APPLICATION_MATERIAL`), refs `application_material:<artifact_id>`.
- **Lifecycle context** (new): the bounded event list, offered as
  `user_supplied_context` refs `application_event:<id>`, plus two plain
  header lines in the rendered prompt (current status, upcoming scheduled
  interview) that are context, not citable claims.

All three "new" inputs are gathered **only** when `artifact_type ==
"interview_prep"` — `build_source_registry`, `render_prompt_text`, and
`compute_fingerprint_for` each gate their Interview-Prep-only additions on
that check, so a plain positioning/cv/cover_letter/supporting_statement call
never sees an `application_material` or `application_event` source at all
(`test_application_material_source_category_is_its_own_never_strategy_or_evidence`).

### The application-material-as-context-not-evidence rule

A generated CV or Positioning brief is not new proof the applicant did
anything — it's a record of what the applicant has already chosen to
*present*. `CATEGORY_APPLICATION_MATERIAL` is therefore excluded from
`_PERSON_SIDE_CATEGORIES` in `application_artifacts.py`, exactly like
`CATEGORY_STRATEGY`/`CATEGORY_ROLE_SIDE` already are: it may sit alongside a
person-side ref on an applicant-facing claim, but can never be that claim's
*only* source (`test_application_material_alone_is_not_person_side_evidence`).

### The lifecycle-event/user-note evidence boundary

An `application_event` source is `user_supplied_context` — the same
epistemic tier as an `application_note` — never `canonical_evidence`. A
recorded interview note ("felt strong on the technical questions") is the
applicant's own recollection, usable as targeting context for a later
prep round, but it satisfies the *person-side* grounding requirement for an
answer-plan evidence point exactly as an application note would (it's real,
first-person, user-supplied context) — it simply can never be treated as
Profile360-verified fact (`test_event_source_is_user_supplied_never_canonical_evidence`,
`test_lifecycle_event_source_satisfies_person_side_grounding`).

## Grounding validation

Extends `application_artifacts.py::_validate_and_shape`, never weakens it:

- **`focus_areas` / `questions` / `answer_plan.cautions` /
  `questions_to_ask`** may be grounded in role-side context alone — a focus
  area or practice question describes what the role *may test*, and a
  caution describes an absence of evidence, so role-side-only grounding is
  legitimate here exactly as it already is for Positioning's
  `gaps_and_cautions`. Every one of these still needs at least one
  non-empty `source_refs` entry — an empty list is rejected like an invented
  ref, same as every other Phase 4 type.
- **`answer_plan.evidence_points` and `closing_points`** make a claim about
  the applicant, so each needs at least one *person-side* source —
  `canonical_evidence`, `partial_evidence`, or `user_supplied_context`
  (which now includes `application_event` refs). A role requirement or
  application-material ref may supplement such a claim but can never be its
  only source.
- Every `source_refs` entry must be one of the call's own registry refs —
  the pre-existing unknown-ref rejection (`_collect_by_key` walking the
  whole response) applies to Interview Prep with no special-casing at all,
  since it already walks arbitrary nested structures.
- A legacy, unreviewed role requirement may still motivate a
  clearly-labelled (`role_requirement_legacy:<id>`) practice area or
  caution, exactly as Positioning's hardening rules already allow — it is
  never treated as an accepted, reviewed requirement
  (`test_legacy_requirement_may_inform_practice_area_but_is_not_reviewed`).

Interview Prep introduces no `concept_id`/`episode_id` field of its own, so
it needs no extra id-validation branch beyond the generic `source_refs`
check every artifact type already goes through.

## Interview Prep staleness

`compute_fingerprint_for` gains an `interview_prep`-only branch (docs/35's
existing fingerprint is otherwise untouched): when `artifact_type ==
"interview_prep"`, the hash additionally folds in the Application's current
`status`, a content hash of every lifecycle event (id + full row hash, so an
*edited* event is caught, not just an added/removed one), and the active
`cv`/`cover_letter`/`supporting_statement` artifacts' id/version alongside
the active `positioning` every downstream type already reads. Every other
artifact_type's fingerprint computation is completely unreachable from this
branch — the check is `if artifact_type == "interview_prep":`, not a shared
code path — so:

- scheduling an interview, recording an offer, or editing an event makes an
  **existing Interview Prep** artifact stale, but leaves an adopted
  CV/Positioning's `stale` flag exactly as it was
  (`test_recording_an_offer_does_not_stale_the_adopted_cv/positioning`);
- adopting a new CV or Positioning makes an existing **Interview Prep**
  artifact stale (it reads them as application-material/strategy context),
  but adopting a new CV never stales Positioning, and vice versa — that
  cross-type isolation predates this phase and is untouched;
- a plain interview note with no status/CV/positioning change still counts
  as an "events changed" input, so it does make Interview Prep stale (it's
  new context Interview Prep could usefully draw on) — it never silently
  triggers a regeneration by itself; staleness is only ever surfaced, never
  acted on automatically.

`GET /api/applications/{id}/artifacts` computes this from **one** shared
evidence gather plus one extra bounded lookup
(`get_active_application_materials` — three indexed active-artifact reads),
computed once per request and reused across every artifact type's summary,
never once per type.

## Interview Prep generation

`POST /api/applications/{id}/artifacts/interview_prep/generate` — the
existing Phase 4 endpoint shape, unchanged (`{guidance?, target_words?}`).
Explicit generation only: no GET anywhere in this phase calls
`run_json_task` (`test_get_artifacts_never_calls_ai_for_interview_prep`,
`test_creating_lifecycle_event_never_calls_ai`). The prompt file,
`prompts/application_interview_prep.md`, states plainly that every source
block is data, not instructions; that generated questions are preparation
hypotheses ("a likely area to prepare", "a practice question based on this
role's requirements"), never known employer questions; that the model must
not invent company facts or candidate evidence; that adopted Application
material is strategy/consistency context, not evidence; and that an
unsupported requirement belongs in a caution, never a fabricated answer. It
reuses `ai.run_json_task` — no direct provider call.

Generation sequence (unchanged from Phase 4, docs/35 §3): the grounded
context is read and its `db_cursor()` block closed *before* the model call;
`run_json_task` runs with no transaction open; the validated response is
persisted in a fresh, short transaction only after success. A provider
failure or a post-validation rejection leaves the current active **and**
draft Interview Prep rows completely untouched
(`test_interview_prep_ai_failure_leaves_existing_artifacts_untouched`), and
never touches CV/Positioning, lifecycle events, or `application.status`.

## Interview workspace stage

Replaces the Phase 4 placeholder in `ApplicationWorkspace.tsx` with three
pieces, in order: an **Interview context** card (current status, next
scheduled interview/stage, the latest event note, all deterministic reads —
no AI); a **What this prep will use** card, the same
"deterministic-summary-before-you-generate" pattern Phase 4 established for
the Application package section, extended with which application materials
are currently adopted and whether an upcoming interview is recorded
(`interview_generation_context`, additive to the existing
`generation_context` in the same `GET .../artifacts` response — no extra
request); and the Interview Prep artifact card, which is the **same**
`ArtifactStageCard` component every other artifact type already uses —
Generate/Regenerate, Review draft, Adopt, Edit, Discard, History, Copy,
Download, the stale banner, and the Sources provenance affordance all come
for free from that shared component once `interview_prep` is a recognised
`ArtifactType`. The editor extends `ArtifactEditor`'s existing "text fields
and lists only" discipline to Interview Prep's shape (focus-area text,
question text, answer-plan approach/evidence-point/caution wording,
questions-to-ask, closing points, checklist items) — no general rich-text
editor was added. Export reuses the same `renderArtifactMarkdown` /
copy-to-clipboard / download-.md machinery, extended with an Interview Prep
branch that never exports the provenance JSON into the document body (the
in-app Sources affordance remains the place for that).

## No hidden career scoring

Unchanged commitment, explicitly upheld by Interview Prep's own schema and
prompt: no interview-readiness score, question-probability, hiring/offer
probability, fit percentage, overall application score, or traffic-light
verdict anywhere in this phase. The system shows structural facts (what's
adopted, what's recorded, what's grounded) and user-entered lifecycle state;
the user decides what to do with them.

## Performance

- Application index: still **one** bounded request
  (`test_list_applications_lifecycle_aggregate_has_no_per_row_queries`
  asserts `<= 3` queries regardless of row count — the same ceiling
  `test_applications.py`'s own role-metadata-join test already asserts).
- Application workspace initial load: **four** parallel requests — the
  existing detail/evidence/artifacts requests plus exactly one new
  `GET .../events` — never a waterfall, never AI on load.
- Interview Prep generation: read context → close transaction → call model
  → validate → persist in a fresh short transaction. No DB transaction is
  ever held across the model call.
- No event-per-row and no source-per-question requests anywhere in this
  phase.

## Regression

Every Phase 3/4 invariant this phase touches remains covered by its
original tests, all still green: the Application status/note boundary, the
grounded-generation lifecycle for the original four artifact types, the
reviewed-vs-legacy split, the CV chronology safeguard, the hardening-note
grounding-sufficiency rules, and role-deletion protection (an Application
with lifecycle events, exactly like one with notes or artifacts, still
blocks role deletion with the existing `409` — nothing about that FK path
changed).

## Phase 6 handoff

Genuine remaining work, deliberately out of scope here:

- **Career Direction / Target Discovery** — not started; this phase is
  entirely about the application-execution journey.
- **Note/event promotion** — a strong interview note or application example
  still has no reviewable path into durable Profile360 evidence (Phase 3
  already flagged this for notes; the same gap now applies to lifecycle
  event notes).
- **Structured offer/compensation economics** — `offer_received` is a plain
  event with free-text notes; no structured compensation-offer schema was
  added.
- **Any hiring/interview-readiness scoring** — deliberately never built, in
  this phase or any prior one; if ever requested, it should stay explicitly
  out of the grounded-generation/evidence system entirely.
