# Phase 9: Career Cockpit + Learning Loop

## Objective

Close the loop the product has been building toward since Phase 6:

**Direction → Target → Opportunities → Applications → Outcomes → Learning → Evidence → updated Target progress**

Phase 9 adds no new subsystem. It composes the eight phases already built —
Career Direction, Target/Pathways, capability engine, Opportunity alignment,
Applications, Profile360 promotion, Market Coverage — into one coherent
landing page (`/`, the **Career Cockpit**), adds one small longitudinal
progress/checkpoint model, and adds an explicit review path from
application-local notes/interview reflections into Profile360's existing
manual review queue. It introduces no AI feature, no overall score, and no
automatic mutation of Profile360 evidence.

## The end-to-end loop

1. The user selects a **Career Direction** (Phase 6) and, optionally, a
   linked **Target** (`jobber.role_instance`, unchanged).
2. The Cockpit shows the Target's **current derived evidence state**
   (`app/career_progress.py`, reusing the exact canonical comparison Target/
   Pathways/Comparison already use — never a second derivation).
3. The user explicitly records a **progress checkpoint** — a historical
   snapshot of that derived state, never evidence itself.
4. The Cockpit shows recent **Opportunities** (Phase 7 relationship states,
   batched) and **Applications in motion** (Phase 3-5).
5. Application-local notes/reflections are **Learning** candidates. The user
   may explicitly **send one to Profile360 for review**
   (`app/application_learning.py`) — this only ever writes to
   `profile360.manual_import_queue`, the same table `profile360_promotion.py`
   already writes for capability assertions.
6. Profile360 processes the queue on its own terms. Processing is not
   acceptance: nothing in this app treats "processed" as "evidenced".
7. Only when Profile360 later produces a genuine accepted claim + mapping
   does the capability engine's verdict for that concept change.
8. The next time the user opens the Cockpit (or records a new checkpoint),
   the current Target state — and the checkpoint diff — reflect that change.

Step 5-7 is the loop's central discipline, and is covered end-to-end by
`test_career_cockpit.py::test_end_to_end_evidence_loop_boundary`.

## Cockpit sections (`GET /api/cockpit`, `app/career_cockpit.py`)

One composed, read-only response with seven top-level keys: `direction`,
`target_progress`, `opportunities`, `applications`, `learning`,
`market_context`, `next_actions`. Every section independently carries its
own `state` (see **Failure isolation** below); a caller must branch on
`state` before reading a section's other fields.

- **direction** — selected Direction summary, top desired-property
  dimensions, linked Target summary. States: `no_direction`, `no_target`,
  `with_target`.
- **target_progress** — delegates entirely to `career_progress.read_progress`
  (see below).
- **opportunities** — up to `OPPORTUNITIES_LIMIT` (5) recently captured
  postings, sorted by capture/posting recency only (never desirability),
  each with a Phase 7 relationship label when a Target exists.
- **applications** — active count, counts by status, up to
  `ACTIVE_APPLICATIONS_LIMIT` (5) active Application summaries, the soonest
  upcoming interview across every Application, and up to
  `RECENT_OUTCOMES_LIMIT` (5) recent outcome events. No success-rate,
  conversion-rate, or offer-probability statistic — a personal sample this
  small does not justify one.
- **learning** — up to `LEARNING_ITEMS_LIMIT` (8) recent application notes/
  event reflections across every Application, each with its Profile360
  queue status and, where the note names a concept, whether that concept is
  a current Target requirement and its current evidence status.
- **market_context** — the Target's (or Direction's) archetype, via
  `market_coverage.archetype_coverage_detail` — the exact same Phase 8
  service Opportunity alignment and Pathways use, never a second
  definition.
- **next_actions** — see **Next-action rule precedence** below.

## Checkpoint schema (migration `0031_career_progress_checkpoints.sql`)

```sql
jobber.career_progress_checkpoint (
    id                       UUID PRIMARY KEY
    career_direction_id      UUID NOT NULL REFERENCES jobber.career_direction(id) ON DELETE CASCADE
    target_role_instance_id  UUID REFERENCES jobber.role_instance(id) ON DELETE SET NULL
    checkpoint_type          TEXT NOT NULL CHECK (IN ('baseline', 'checkpoint'))
    label                    TEXT
    state_schema_version     SMALLINT NOT NULL DEFAULT 1
    state                    JSONB NOT NULL
    source_revision          JSONB NOT NULL DEFAULT '{}'
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

Append-only by omission: no PUT/DELETE route exists anywhere in this phase.
`target_role_instance_id` is `ON DELETE SET NULL` (not CASCADE) so deleting
an old Target narrows a checkpoint back to "no longer comparable to
anything current" rather than destroying its history — verified directly by
`test_target_deletion_sets_null_and_preserves_checkpoint_history`.

## Derived-state snapshot semantics

`career_progress.build_current_target_state(cur, direction)` calls
`comparison_service.build_role_comparison` (the same function Target
detail, Pathways and Comparison already call) and reshapes its output into
the bounded, storable snapshot:

```json
{
  "direction": {"id": "...", "name": "..."},
  "target": {"id": "...", "title": "..."},
  "review": {"target_review_complete": true, "target_mapping_complete": true},
  "counts": {
    "requirements_total": 12, "required_total": 8,
    "evidenced": 6, "partial": 2, "user_asserted": 1, "not_found": 3,
    "blocking_required": 2, "unverified_required": 2
  },
  "requirements": [
    {"concept_id": "...", "canonical_name": "Capital modelling", "requirement_type": "required", "status": "partial"}
  ],
  "development_actions": {"open": 2, "done": 1}
}
```

`evidenced`/`partial`/`user_asserted`/`not_found` are preserved distinctly
throughout — never collapsed into "met". No copied Profile360 claim text,
application content, full evidence traces, or market source text is ever
stored — only the bounded structural facts above. `source_revision` reuses
`target_cache`'s existing evidence-revision fingerprint (migration 0019
triggers + a profile360 content hash) rather than inventing a second
revision system; it does not depend on embeddings, since a Target's
evidence snapshot has no need for similarity ranking.

A `development_action`'s `status` (`open`/`done`) is recorded for context
only. Completing one changes no requirement's evidence status — see
`test_development_actions_are_counted_but_never_evidence`.

## Comparable-checkpoint rule

Two checkpoints are comparable only when they share
`(career_direction_id, target_role_instance_id, state_schema_version)`.
Changing the selected Direction, changing its linked Target, or deleting
the old Target (which sets `target_role_instance_id` to `NULL`) each fall
out of that scope automatically — no separate detection logic is needed.
When no comparable checkpoint exists, `GET /api/cockpit/progress` reports
`state: "no_checkpoint"` rather than comparing against an unrelated Target's
history.

## Evidence strengthened / assertion added / evidence weakened

`career_progress.diff_checkpoint` compares two snapshots by `concept_id`,
using the ordinal rank `not_found(0) < user_asserted(1) < partial(2) <
evidenced(3)`:

- **`not_found → user_asserted`** is reported as **`assertion_added`** —
  structurally apart from `evidence_strengthened`, even though its rank
  moved up. An assertion is never accepted evidence (build §7/§34), and the
  diff must never describe it as strengthened evidence.
- Every other rank-increasing move (`not_found → partial`,
  `not_found → evidenced`, `user_asserted → partial`,
  `user_asserted → evidenced`, `partial → evidenced`) is
  **`evidence_strengthened`**.
- Every rank-decreasing move is **`evidence_weakened`** — regressions are
  never hidden.
- A concept whose `requirement_type` changed is reported once, under
  `target_definition_changed.requirement_type_changed`, and excluded from
  the evidence-transition groups — a redefinition and an evidence change
  are different facts, and conflating them would misrepresent which one
  happened.
- `requirements_added`/`requirements_removed` cover concepts present in only
  one snapshot.
- Unchanged requirements are counted (`unchanged_count`) but never
  itemized.

No field anywhere in the diff is a score or a percentage — see
`test_diff_never_computes_a_numeric_score` and
`test_cockpit_no_score_anywhere_in_response`.

## Application note/event promotion (`app/application_learning.py`)

The same promotion pattern `profile360_promotion.py` already established
for `person_capability_assertion`: the only write is an upsert into
`profile360.manual_import_queue`, keyed by a deterministic source key
derived from the source row's own id:

- `cp_enterprise_application_note:<note_uuid>`
- `cp_enterprise_application_event:<event_uuid>`

An event may be queued only when it carries nonblank `notes` — the event
itself is never turned into evidence, only the user's own written
reflection. A note or event's queued payload preserves provenance
(application/role id and title, note/event type, verbatim text, linked
concept metadata where one exists, source timestamps, and a
`content_sha256` hash of the fields that determine staleness).

Promoting a note/event never mutates the source row, never changes
Application status or artifact state, and never changes any capability or
Target-gap status — verified by
`test_promoting_never_mutates_the_source_note_or_event` and
`test_queueing_never_changes_capability_status`.

## Profile360 manual queue boundary

Nothing in this phase writes to `profile360.claims`, `profile360.capabilities`,
a profile360 episode, or any jobber↔Profile360 mapping table. The only
write anywhere in Phase 9's learning-loop surface is the upsert into
`profile360.manual_import_queue` described above — verified directly by
`test_promotion_writes_only_to_manual_import_queue`.

## Queue status vs. evidence status

These are two independent axes, and the UI/API keep them visually and
structurally distinct:

| Queue status (`application_learning.py`) | Capability/evidence status (`capability_engine.py`) |
|---|---|
| `not_queued` | `evidenced` |
| `queued_pending` | `partial` |
| `processed_by_profile360` | `user_asserted` |
| `source_changed_since_queue` | `not_found` |

`processed_by_profile360` means only that Profile360's own tool processed
the queued item — never that the capability engine independently reports
`evidenced`. A GET never writes: `application_learning.status_for` compares
the queue row's stored `content_sha256` against the *current* source row's
hash purely for reading; an edited-but-not-requeued note reads as
`source_changed_since_queue` until an explicit requeue (the same POST
endpoint, called again) upserts the new payload and resets `processed` to
`false` — matching `profile360_promotion.py`'s existing repeat-promote
convention.

## No AI in Phase 9

Every read in this phase's surface is a deterministic composition of
already-derived structures; the only writes are the explicit checkpoint
POST and the two explicit promotion POSTs. `test_no_ai_call_anywhere_in_
cockpit` patches the AI task runner to raise if called and asserts the
Cockpit still renders successfully.

## Next-action rule precedence (`career_cockpit.build_next_actions`)

Deterministic, rule-based, capped at `NEXT_ACTIONS_LIMIT` (4). Evaluated in
this fixed order; every rule whose condition holds contributes one action:

1. an interview is scheduled within `INTERVIEW_URGENCY_DAYS` (7)
2. no Career Direction is selected
3. the selected Direction has no linked Target
4. Target requirement review/mapping is incomplete
5. no progress baseline has been recorded yet
6. current evidence changed since the last checkpoint
7. unqueued application learning exists
8. open development actions are due/overdue
9. recent opportunities have been captured
10. otherwise: review the Direction or Market Coverage

Rules 2-6 are mutually exclusive stages of the same Direction/Target setup
funnel (`elif`, not independent checks) — only the earliest unmet one of
them fires for a given state. Ordering is workflow/urgency precedence, not
a career-quality ranking; the heading is "Useful next actions", never "best
next move".

## Performance / query-count boundedness

Each Cockpit section opens its own `db_cursor()` (see **Failure isolation**
below) and issues a small, fixed number of bounded queries — none scaled by
corpus size. The Opportunities section's Phase 7 relationship summary
reuses `opportunity_alignment.bulk_target_relationship`, a batched
counterpart to `assess_specific_candidate` that bulk-loads requirements/
review for the target plus every candidate in two queries and evaluates
each distinct concept's evidence status at most once — never the
single-role alignment path called once per opportunity, and never
`assess_all_candidates`'s whole-corpus scan.
`test_cockpit_query_count_does_not_grow_with_corpus_size` grows postings,
Applications, notes and events well past every section's own preview bound
and asserts the query count measured by `tests/query_counter.py` is
unchanged.

## Failure isolation

Postgres aborts a transaction on its first error, so `career_cockpit.py`
gives each of its six composed sections its own connection/transaction
(`_safe(lambda: ...)` wrapping its own `db_cursor()` block) rather than
sharing one cursor across the whole composition. A failure in, say, Market
Coverage genuinely cannot poison Applications or Opportunities — the failing
section alone reports `{"state": "unavailable", "reason": "..."}`, and the
rest of the page renders normally. The one read that is not isolated this
way is the selected-Direction lookup itself, a single cheap indexed SELECT
most other sections need context from; a genuine failure there is allowed
to propagate as a 500 rather than silently faking "no Direction selected".

## Product architecture after all nine phases

- **Direction** (Phase 6) is the user's chosen destination statement.
- **Target** (`role_instance`, Phase 2 onward) is the concrete structural
  hypothesis a Direction may link to.
- **Pathways** (`docs/30`, extended Phase 7) shows direct/intermediate
  routes and gap value toward a Target.
- **Opportunities** (Phase 7) relate one real observed posting to the
  selected Direction/Target with a named, rule-based relationship state.
- **Applications** (Phase 3-5) are the persistent, user-controlled workflow
  once the user decides to pursue an opportunity, with a lifecycle timeline
  and a generated, grounded package.
- **Profile360** (external, read mostly) remains the sole authoritative
  person-side evidence store; `manual_import_queue` is its one write
  surface from this app, now with two independent writers
  (`profile360_promotion.py`, `application_learning.py`) sharing one
  established pattern.
- **Market Coverage** (Phase 8) is the shared evidence-depth/
  representativeness service every market-derived claim in the app composes.
- **Career Cockpit** (Phase 9) is the composition layer tying all of the
  above into one coherent landing page, plus the one new longitudinal
  primitive — the progress checkpoint — that lets "has anything changed"
  be answered by inspection rather than memory.

No component gained a numeric score, an AI verdict, or an automatic write
to another component's authoritative state as part of this phase.
