# Phase 6: Career Direction / property-first target discovery

This phase adds the other half of the product: a persistent, property-first
way to answer *"what kind of future career state do I actually want to
pursue?"* — without requiring the user to start from a job title. It is
**decision support, not an AI decision about the user's career**: discovery
proposes grounded, unordered hypotheses for a human to review; nothing in
this build ranks a Career Direction, chooses one automatically, or produces
an opaque fit/utility score.

## Three different things, never merged into one

- **Preferences** (`jobber.preference_dimension`/`preference_observation`,
  migration 0005) — evidence about what the user tends to value or enjoy.
  Unchanged by this phase. No Career Direction write ever lands in
  `preference_observation`, and no preference write is ever triggered by
  saving or discovering a direction.
- **Career Direction** (`jobber.career_direction`/`career_direction_dimension`,
  migration 0030) — a user-owned statement of the future career state being
  explored or chosen: desired properties/trade-offs, practical constraints,
  and, optionally, a link to a concrete Target and/or a market archetype.
- **Target** (`jobber.role_instance`, `instance_type='user_defined_target'`,
  unchanged) — one concrete role hypothesis used for requirements/Pathways
  analysis. Not replaced or duplicated: a Career Direction may exist with no
  Target at all, and links to one via `target_role_instance_id` rather than
  growing a second target-shaped table.

## Schema (migration 0030)

`jobber.career_direction_discovery_run` is created first (no dependency),
then `jobber.career_direction` (which references it via
`source_discovery_run_id`), then `jobber.career_direction_dimension`.

`jobber.career_direction`:

- `state` — `exploring | selected | archived`.
- `origin` — `user | ai_adopted`.
- `target_role_instance_id` — `ON DELETE SET NULL`: deleting a Target narrows
  a direction back to "not yet linked", never destroys the direction.
- `target_archetype_concept_id` — validated app-side to be an active
  `role_archetype` concept (same discipline `archetype_classification.py`
  already applies to `role_instance.archetype_concept_id`); a plain FK to
  `jobber.concept` since a CHECK constraint cannot see another table's
  columns.
- `constraints` — JSONB, typed at the API layer by `CareerDirectionConstraints`
  (app/models.py), never by a Postgres CHECK — same posture as
  `application_artifact.content`.
- `source_discovery_run_id` / `source_candidate_id` — set only for an
  `ai_adopted` direction. `source_candidate_id` is a server-assigned id
  (`career_directions.py::_validate_and_shape`), not a real FK — a candidate
  lives inside the run's own `output` JSONB, not its own table — and Direction
  detail resolves it by reading that run's output. Always `NULL` for a manual
  direction.

**At most one Career Direction may have `state='selected'`**, enforced at the
database level: `idx_career_direction_one_selected` is a partial unique index
on `state` filtered to `state = 'selected'` — every row satisfying that filter
carries the same value, so uniqueness of that column among them means at most
one such row can exist. `select_direction` (`app/career_directions.py`) is the
*only* writer of `state='selected'` anywhere in this build, and it always
performs the swap (existing selected → `exploring`, chosen → `selected`) as
two statements on one caller-supplied cursor — one `db_cursor()` transaction,
so the invariant is never visible as violated mid-transaction. Nothing in the
discovery/adoption path ever calls it.

`jobber.career_direction_dimension` — one row per `(career_direction_id,
dimension_code)`, holding `desired_direction` (`toward | away | neutral`) and
`importance` (`1`-`3`) plus an optional note. These are **explicit user
choices for this direction**, structurally separate from
`preference_observation` — no FK, view, or query anywhere in this build joins
the two, and `career_directions.py`'s own tests assert a direction save never
writes a `preference_observation` row.

## Typed constraints

`CareerDirectionConstraints` (`app/models.py`) is the API-enforced shape of
the `constraints` JSONB column: bounded `locations`/`remote_types`/
`employment_types`/`seniority_levels`/`other` string lists (≤20 entries, ≤200
chars each, blank entries dropped), and an optional `compensation_floor`
reusing `compensation_observation`'s own `pay_period`/`employment_basis`
vocabulary so a floor is always comparable to real evidence on the same
terms. An empty list always means "not specified", never a hard requirement
satisfied by zero options. `other` is user intent/context; it is never
offered to discovery as market evidence. Currency is upper-cased, amount must
be positive, and nothing here ever converts or annualises a figure.

## Manual vs AI-adopted origin

Manual creation (`POST /api/career-directions`, `origin='user'`) never calls
AI and works with zero market/archetype/Profile360 evidence available.
AI-adopted directions (`origin='ai_adopted'`) are created only through
`POST .../discovery-runs/{run_id}/candidates/{candidate_id}/adopt` — never
directly by a discovery call. Both share the same detail/edit/select/archive
workflow; there is no second code path for either.

## Discovery-run audit table

`jobber.career_direction_discovery_run` follows the Concept Dossier /
Application Artifact audit precedent (migrations 0012/0028): kept separate
from `jobber.extraction_run` because that table's `subject_type`/subject-FK
CHECK constraints (migration 0003) are unnamed and cover a fixed set of
subject kinds that does not include "career direction discovery", and
altering them on a table every other AI feature also writes to is a
materially riskier change than this feature warrants.

Unlike Concept Dossier/Application Artifact (which fold audit metadata onto
the single versioned content row), this run table is kept separate from
`jobber.career_direction` for a different reason: one discovery run proposes
*several* candidate hypotheses, only some (or none) of which are ever
adopted — the run is a many-candidates-to-zero-or-more-directions proposal
record, not a version in a single row's own lifecycle. `status` is
`ok | failed`; `output` holds the validated `CareerDirectionDiscoveryResult`
(candidates with their server-assigned ids); `raw_output` is audit/debug only
and is **never** returned by a normal read endpoint
(`GET .../discovery-runs/{id}` and the bounded `GET .../discovery-runs`
history list both strip it).

## Deterministic preference summary

`career_direction_discovery.summarize_preferences` (build §9) returns one
entry per `preference_dimension`: a bounded recent-observations list, the
strongest basis present (ranked per migration 0005's hierarchy —
`observed_behavior > user_stated > repeated_episode_evidence >
validated_psychometric > typology_hypothesis`), an `agreement` verdict
(`no_evidence | agree | conflict`), and a convenience `suggested_direction`/
`basis` pair populated *only* when the recent observations agree (both
`toward` and `away` present is a real, unresolved conflict — `neutral`
alongside either side is not). Nothing here is persisted as a new
`preference_observation` row; the property-first builder's "use my recorded
preferences as a starting point" action reads this summary once and only
ever fills its own unsaved draft state.

## Discovery context

`career_direction_discovery.build_discovery_context` (called once per
`POST /api/career-directions/discover`, inside a single short-lived
`db_cursor()` block) assembles, in a handful of bulk queries — never one per
archetype/posting/capability:

1. **Direction input** — the builder's own dimensions/constraints/guidance.
   The single most authoritative source in the whole context; nothing below
   ever overrides it.
2. **Preference evidence** — every observation surfaced by the summary above.
3. **Existing Targets** — a bounded, most-recent slice of
   `user_defined_target` rows, so discovery never presents an
   already-explored Target as a brand-new idea. Explicitly labelled
   hypotheses, never market truth.
4. **Person-side context** — the current Profile360 snapshot and a bounded
   slice of career episodes, read-only via `profile360_reader`. Unavailable
   Profile360 degrades to a caveat, never a hard failure (§28).
5. **Market/archetype evidence** — the active archetype catalogue (bounded to
   25), each with its assigned-posting count, up to 3 representative
   postings, reviewed-requirement coverage, top derived capability demand
   (`d_archetype_demand`), derived compensation benchmarks
   (`d_archetype_comp`, offered only when `reference_comp` is populated), and
   existing archetype-context enrichment grounded points.
6. **Corpus disclosure** — deterministic counts (total observed postings,
   how many carry an archetype assignment, how many have reviewed
   requirements, how many archetypes have any compensation evidence) plus
   the current `economics_freshness` verdict. Returned to the caller
   alongside the discovery result and rendered next to the candidates, not
   only fed into the prompt — so a thin corpus is visible to the human, not
   only "known" by the model.

## Source registry and grounding

Every piece of evidence above becomes a `SourceEntry` with a stable `ref`
(`direction_input:<code>`, `direction_constraint:<name>`,
`direction_guidance`, `preference_observation:<id>`, `target:<id>`,
`profile_snapshot:<id>`, `profile_episode:<id>`, `archetype:<id>`,
`posting:<id>`, `archetype_demand:<archetype_id>:<capability_id>`,
`archetype_comp:<archetype_id>:<market_id>:<currency>:<component>:<pay_period>`,
`archetype_context:<id>`) and an epistemic `category` (`direction_input |
preference_evidence | person_evidence | market_evidence | target_context`).
`DiscoveryContext.known_source_refs()`/`known_archetype_ids()`/
`category_for_ref()` are what `career_directions.py::_validate_and_shape`
checks a model response against, exactly mirroring the discipline
`application_artifacts.py` already established for Phase 4/5:

- Any `source_refs` entry, or archetype id, the model did not actually
  receive **rejects the entire response** (`CareerDirectionValidationError`,
  422) — never silently filtered out.
- Every factual block must cite at least one source.
- A `priority_alignment` entry must cite `direction_input` and/or
  `preference_evidence` — a market-only citation is rejected.
- A `market_basis` entry must cite `market_evidence`.
- A `person_basis` entry — a claim that the user already has/demonstrates
  something — must cite `person_evidence` (Profile360). Market/archetype
  evidence can never itself prove a capability.
- `compensation_context`, when present, must cite `market_evidence`; because
  an `archetype_comp:` ref only exists in the registry when real derived
  compensation evidence exists, the model has no ref to cite when none does
  — the honest answer is to omit the block, which the UI renders as "No
  comparable compensation evidence available for this hypothesis." No
  currency conversion or day-rate/annual blending happens anywhere in this
  path; the discovery context hands the model each compensation bucket with
  its own currency/pay-period/component intact.

`CareerDirectionCandidate`/`CareerDirectionDiscoveryResult`
(`app/models.py`) additionally set `model_config = {"extra": "forbid"}` — a
schema-level backstop against `fit_score`/`rank`/`probability`/anything
equivalent: such a field fails Pydantic validation
(`AISchemaValidationError`) before grounding is ever checked, rather than
silently passing through as an unused extra key.

## Unordered hypotheses

`prompts/career_direction_discovery.md` instructs the model to return 3-5
distinct, unordered hypotheses where evidence supports them, and never to
use ranking/verdict language ("best", "top", "recommended", "#1", "most
likely", "realistic"/"unrealistic", a probability of success). If the model
returns zero candidates without setting `insufficient_evidence`,
`_validate_and_shape` forces it to `true` with a default reason — an empty
list is never presented as "nothing good exists" with no explanation. List
position never carries meaning anywhere in the backend or frontend; the
builder renders candidates in a plain grid, and adoption looks candidates up
by their server-assigned id, never by index-as-rank.

## Explicit selection

Nothing in `discover_candidates`/`adopt_candidate` ever selects a direction.
Selection is always a separate, explicit `POST /api/career-directions/{id}/select`
call a human triggers — from Direction detail, never automatically after
saving or adopting. Archiving the selected direction leaves *no* selected
direction rather than auto-picking a replacement; reopening only applies to
an archived direction (409 otherwise); selecting an archived direction is
rejected (409) until it is reopened first.

## Target linking and materialisation

`/targets/new?direction_id=<id>` (`AddTarget.tsx`) loads the direction and
sensibly prefills the Target title (the direction's name), description and
supporting-material text (the direction's summary, dimensions and
constraints, rendered as plain text) and defaults `is_imagined = true` — a
Career Direction never anchors to a specific real role, so there is nothing
truthful to default to instead. Every field stays editable, and the existing
Generate-draft/Continue-manually workflow is completely unchanged. After the
existing Save action succeeds, the page issues one follow-up
`PATCH /api/career-directions/{id}` linking `target_role_instance_id` to the
new Target; a failure on that link never blocks navigation to the newly
saved Target (which already exists and is valid on its own) — the direction
can still be linked later from its own detail page's "Link an existing
Target" picker, which reuses the same `PATCH` endpoint. Linking an observed
posting is rejected (400) everywhere this is validated (manual create,
update, and this flow) — a Career Direction's Target is always a
user-defined hypothesis, never market data.

## Performance

- `/future`: two bounded, independent requests (`GET .../selected`,
  `GET .../career-directions`), each with its own loading/error state —
  a Career Direction API failure never blanks the supporting-exploration
  links below it.
- The builder (`/future/build`): two bounded requests on load
  (`listPreferenceDimensions`, `getCareerDirectionPreferenceSummary|`); one
  more (`POST .../discover`) only on explicit Generate.
- Direction detail: one request for the direction itself (plus, for an
  archetype-linked direction, three small aggregate counts folded into the
  same response); a discovery run is fetched only for an `ai_adopted`
  direction, and only to render "why this direction exists".
- `discover_candidates`: context assembly is one short `db_cursor()` block
  (a handful of bulk queries, bounded to 25 archetypes / 3 representative
  postings each / 40 episodes / 15 targets), closed *before* the model call;
  persistence is a second, separate short transaction after a validated
  response. No N+1 per archetype, posting, or capability anywhere in this
  path.
- No AI call on any `GET` in this feature — asserted directly in
  `backend/tests/test_career_directions.py::test_get_endpoints_never_call_ai`.

## Freshness

Discovery never rebuilds `d_archetype_demand`/`d_archetype_comp`/archetype
context as a side effect. It reads `economics_freshness()` once and surfaces
the verdict verbatim in `corpus_disclosure` — a stale/never-rebuilt state is
shown as a caveat, never silently presented as current.

## Product integration

- `/future` is the Career Direction workspace home: current direction (or an
  honest "not selected yet" state), a bounded list of non-archived
  directions, a Discover CTA, and the pre-existing supporting-exploration
  links (Preferences/Targets/Pathways/Trends/Economics/Career space), kept
  exactly as before.
- Home's Career Direction card and the Opportunity Decision Workspace's
  `CareerDirectionTile` (`DecisionSummary.tsx`) both read the same
  `GET /api/career-directions/selected` and name the direction (and its
  linked Target, if any) with **no** alignment/fit score — the Opportunity
  tile explicitly says "Opportunity-to-direction alignment is handled in the
  next phase." Both degrade to their pre-Phase-6 honest-empty-state copy on
  fetch failure, never blocking the rest of the page.

## Phase 7 handoff

Deliberately out of scope here, left for `You → Opportunity → Target`:
Opportunity-to-direction alignment/matching, any ranking or scoring across
directions or candidates, richer route optimisation beyond existing
Pathways, and the fuller Market Coverage & Confidence product (Phase 8).
