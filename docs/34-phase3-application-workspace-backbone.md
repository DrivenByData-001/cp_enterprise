# Phase 3: Persistent Application Workspace Backbone

This phase turns "I want to apply" on an observed opportunity into a
persistent, reopenable **Application** workspace at `/applications/:id`,
backed by two new tables (`jobber.application`, `jobber.application_note`)
and a focused API (`backend/app/routes/applications.py`). It is deliberately
a *backbone*: persistence, idempotent create/reopen, a real Applications
index, a structural preparation view, and an evidence pack grounded in the
existing comparison/Profile360 machinery. It does **not** generate CVs,
cover letters, positioning briefs or interview preparation — see "Phase 4
handoff" below.

## Application lifecycle / status semantics

`jobber.application.status` is a **user workflow state**, never an AI
judgment and never derived from structural readiness:

| status | meaning |
| --- | --- |
| `preparing` | default on creation — the user is getting ready |
| `ready` | the user considers themselves ready to submit |
| `submitted` | the user has applied |
| `interviewing` | in an interview process |
| `closed` | the attempt is over (rejected, offer declined, role filled elsewhere, …) |
| `withdrawn` | the user withdrew |

`preparing`/`ready`/`submitted`/`interviewing` are **active**;
`closed`/`withdrawn` are **historical**. The workspace's preparation checks
(structural evidence/requirement-review counts) never set or gate this
field — `PATCH /api/applications/{id}` is the only way it changes, and nothing
else in this codebase calls it. Phase 5 is expected to add richer lifecycle
fields/outcomes on top of this.

## One-active-application-per-role rule

An observed posting may have **at most one active application** at a time.
Closed/withdrawn applications are historical attempts and don't count. This
is enforced by a **partial unique index**, not application code:

```sql
CREATE UNIQUE INDEX idx_application_one_active_per_role
    ON jobber.application(role_instance_id)
    WHERE status IN ('preparing', 'ready', 'submitted', 'interviewing');
```

A role can accumulate several closed/withdrawn historical attempts plus at
most one live one. `PATCH /api/applications/{id}` (status changes) can, in
principle, hit this same index if a user manually reactivates a
closed/withdrawn application while another active one already exists for
the same role — the route catches the resulting `UniqueViolation` and
returns a clean `409` rather than a raw database error.

## Create/reopen idempotency

`POST /api/applications` (`role_instance_id`) is the only way an application
comes into existence, and it is idempotent:

1. Validates the role exists and is an **observed posting**
   (`instance_type = 'observed_posting'`) — a target is rejected with `400`
   and never gets an application row.
2. `INSERT ... ON CONFLICT (role_instance_id) WHERE status IN (<active set>)
   DO NOTHING RETURNING *` — targets the partial unique index directly as its
   conflict inference target, so the insert either creates the row or
   silently no-ops.
3. If nothing was inserted (a conflict occurred), it re-selects the existing
   active row and returns that instead.

This is **safe under concurrent requests**: two simultaneous POSTs for the
same role can only ever result in one active row. Postgres resolves the race
at the `INSERT ... ON CONFLICT` statement itself — the loser's statement
blocks briefly on the winner's uncommitted row and then correctly no-ops
once it commits, rather than raising a duplicate-key error. This is proven
directly (not just asserted) by
`test_database_constraint_prevents_concurrent_duplicate_active_applications`
in `backend/tests/test_applications.py`, which fires two real concurrent
inserts from separate threads/connections and asserts exactly one succeeds.

The response carries `created: true|false` so a caller can tell "opened a new
application" from "reopened the existing one" apart, though both cases
behave identically from the frontend's point of view (navigate to
`/applications/{id}`).

## Evidence-pack composition

`GET /api/applications/{id}/evidence` is the stable, structural
readiness/evidence input Phase 4's grounded generation is expected to read
from. It is a **live derived view**, computed on every request from current
evidence plus this application's own notes — nothing about it is stored or
cached.

It does **not** duplicate the comparison/capability engine. The comparison
assembly that used to live entirely inside `routes/comparison.py::compare_role`
was extracted, verbatim, into `backend/app/comparison_service.py::build_role_comparison(cur, role_instance_id)`.
Both `GET /api/comparison/role/{id}` and `GET /api/applications/{id}/evidence`
call this one function — the Comparison API's existing response contract is
unchanged (it just now returns exactly what the extracted function returns).

The evidence pack response is the shared comparison result plus:

- `application_id` / `role_instance_id`;
- application-specific notes, both as a flat list (`notes`) and grouped onto
  the requirement they're attached to (`items[].notes`, keyed by
  `concept_id`) — loaded in one bulk query, never per-requirement;
- `items[].role_requirement_reviewed` (see next section).

## Reviewed vs. legacy role requirements

The comparison engine has always been able to fall back to
`role_skill_observation` for a role with no usable (current, accepted)
`requirement_claim` rows — see `role_requirements.py`. That fallback is real
evidence, but it was never human-reviewed, and the application workspace
must never present it as equivalent grounding to an accepted claim.

Every evidence-pack item now carries an explicit, machine-readable
`role_requirement_reviewed: true | false`:

- `true` when `role_side.requirement_source == "claim"` — a current,
  human-accepted `requirement_claim`;
- `false` when `role_side.requirement_source == "role_skill_observation"` —
  legacy extraction, never reviewed.

The Application workspace's "Evidence to use" section labels the `false`
case explicitly as **"Legacy role extraction — not human-reviewed"**, never
as a reviewed requirement. This flag does not change the comparison engine's
fallback rule itself (untouched in this phase) — it only makes the
distinction visible to consumers, which is the groundwork Phase 4 needs to
ground generated material only in reviewed requirements where that
distinction matters.

## Application-note boundary

`jobber.application_note` (`note_type`: `general` | `evidence_example`,
optional `concept_id`) is **application-local, human-authored scratch
space**. It is never, by any code path in this phase:

- a Profile360 claim or mapping;
- a `jobber.person_capability_assertion`;
- requirement or capability evidence read by `capability_engine.py`.

Concretely: nothing in `routes/applications.py` writes to `profile360.*`,
`jobber.person_capability_assertion`, or `jobber.requirement_claim`,
and `capability_engine.derive_role_fit`/`derive_capability_coverage` never
read `jobber.application_note`. A note against a `not_found` requirement
does not change that requirement's comparison status — the workspace shows
both facts side by side ("No accepted evidence found" **and**
"Application-only example added"), never merged into one.
`test_notes_do_not_alter_comparison_or_profile360_evidence` in
`backend/tests/test_applications.py` asserts this directly: creating a note
against a concept, then re-fetching `/api/comparison/role/{id}`, shows an
unchanged status.

A later phase may add an explicit promotion/review route from an
application note into Profile360's own review pipeline (the same pattern
`profile360_promotion.py` already uses for capability assertions) — nothing
in this phase does that automatically.

## Profile360 boundary

Unchanged from every earlier phase, and re-confirmed by this one:
Profile360 remains the sole authority for canonical person-side evidence.
Every read in this phase (comparison, evidence pack) is a `SELECT` against
`profile360.*`; nothing in `routes/applications.py` writes to it. Person
assertions (`jobber.person_capability_assertion`) remain distinct from
Profile360 evidence, exactly as `capability_engine.py` has always treated
them (`user_asserted` is its own status, below `evidenced`/`partial`).
Compensation stays entirely out of this phase — an application workspace
never resolves or shows a compensation figure of its own; that's still
`RoleEconomicsSection` on the underlying opportunity page.

## Role-deletion behavior

`jobber.application.role_instance_id` is a **restrictive** foreign key
(default `NO ACTION`, deliberately not `ON DELETE CASCADE`) to
`jobber.role_instance(id)` — see migration `0027_application_workspace.sql`.
Deleting a role that has *any* application — active or
closed/withdrawn — is rejected outright: `DELETE /api/roles/{id}` catches
the resulting `psycopg.errors.ForeignKeyViolation` from
`db.delete_role_instance` and returns a clear `409`, rather than letting the
role (and the application history attached to it) silently disappear. There
is no cascading, soft-delete, or "delete the application first" affordance
in this phase — application deletion is out of scope entirely
(`No destructive application deletion in this phase` — brief §1). A role
with no application still deletes exactly as it always has.

## Structural preparation, not a score

The workspace's "Preparation checks" section is plain states and counts —
requirement review completeness, reviewed-vs-legacy requirement counts,
evidenced/partial/user-asserted/not-found counts, blocking vs. unverified
required gaps, application-only example count. There is no 0–100 score, no
"fit percentage", no traffic-light verdict, and no hiring-probability
inference anywhere in this phase, on the backend or the frontend. Gaps
produce **focused, non-blocking actions** ("Add an example for this
application", "Review my evidence") — nothing about a gap prevents the user
from changing status or continuing to use the workspace.

## Phase 4 handoff

Genuine remaining work for grounded Positioning + CV/application-package
generation:

- **Generation itself.** Nothing in this phase calls an LLM. Positioning
  briefs, tailored CVs, cover letters, and interview-prep material are all
  still to be built, and should be grounded in the evidence pack this phase
  now provides (`GET /api/applications/{id}/evidence`), respecting
  `role_requirement_reviewed` (never presenting legacy, unreviewed role
  extraction as if it were an accepted requirement) and never treating an
  `application_note` as accepted Profile360 evidence in generated prose
  without the user's own review.
- **Note promotion.** An explicit, reviewable path for turning a useful
  `application_note` into a Profile360-bound claim (rather than the
  automatic promotion this phase deliberately never does) would let strong
  application-only examples become durable profile evidence going forward.
- **Status-aware generation gating**, if wanted — e.g. offering CV
  generation once `status` reaches `ready`. This phase keeps status and
  readiness strictly separate on purpose; Phase 4 can choose to read status
  as an input without that violating this phase's invariant, since it would
  be generation deciding to consult status, not status being derived from
  generation or readiness.
- **Phase 5** is expected to extend `jobber.application` with richer
  lifecycle fields/outcomes (e.g. offer details, rejection reasons) — this
  phase's schema and status enum deliberately leave room for that as an
  additive migration.
