# Phase 7: Opportunity alignment — `You → Opportunity → Target`

Phase 6 gave the product a persistent, user-owned Career Direction and let
Home and the Opportunity Decision Workspace *name* the selected direction
and its linked Target, with no alignment analysis — that analysis was
explicitly deferred ("Opportunity-to-direction alignment is handled in the
next phase," docs/37). Phase 7 builds that analysis: for one real observed
opportunity, how does it relate to where the user is now, and to the Target
their selected Career Direction points at?

This is **decision support, not a recommendation engine**. Nothing in this
phase tells the user whether to apply, and there is no composite opportunity
score, fit percentage, hiring probability, offer probability, learning-time
estimate, or ranking anywhere in the product. Every fact returned is a named,
rule-based structural state with a plain-language reason attached.

## One shared service, one endpoint

`backend/app/opportunity_alignment.py` is the only place this logic lives.
It composes three already-existing engines rather than inventing a fourth:

- **`You → Opportunity`** — `comparison_service.build_role_comparison`, the
  same engine Comparison and the Application evidence pack already use.
- **`Opportunity → Target`** — a new `stepping_stones.assess_specific_candidate`
  (see below), built from the exact same `assess_candidate` state machine
  `assess_all_candidates` (Pathways) already uses.
- **Compensation** — `compensation_resolver` + `personal_earnings`, unchanged.

`GET /api/roles/{role_id}/career-alignment` (`backend/app/routes/roles.py`)
is the one read endpoint. It is used, unmodified, by all three surfaces:

- Role Detail (`?` no query param — the selected Career Direction decides
  the Target, or that none exists);
- the Application workspace (same, keyed by the application's role);
- Pathways' opportunity overlay (`?target_id=`, an explicit override — see
  "Two ways to reach a Target" below).

No new database table and no new cache. Every read is a bounded composition
over already-derived structures (`jobber.d_target_evidence`,
`jobber.d_archetype_comp`, profile360) for exactly two roles — the
opportunity and the Target — never a corpus scan.

## Two ways to reach a Target

`build_opportunity_alignment(cur, opportunity_id, *, target_id_override=None)`:

- **Direction-driven** (no override — Role Detail, Applications): the Target
  comes from the selected Career Direction. The top-level `state` machine
  (below) reflects Career Direction/Target state honestly.
- **Explicit-target** (`target_id_override` — Pathways' `?opportunity_id=`
  overlay): the Target is whatever the user is already looking at in
  Pathways, which need not be the selected Direction's Target at all. Only
  `insufficient_target_evidence`/`target_available` are reachable this way —
  a target is already known, so `no_selected_direction`/
  `direction_without_target` never apply. `direction`/`direction_constraints`/
  `direction_dimensions` are still populated when a Direction happens to be
  selected (context only), so Pathways and Role Detail share exactly one
  service and one endpoint rather than two independently maintained ones.

## Selected-direction states

| `state` | Meaning | What's returned |
|---|---|---|
| `no_selected_direction` | No Career Direction is selected. | `opportunity`, `method`, a UI message. |
| `direction_without_target` | A direction is selected but has no linked Target. | + `direction`, `direction_constraints`, `direction_dimensions`, `archetype_relationship` (opportunity vs. direction archetype only). No Target is invented. |
| `insufficient_target_evidence` | A Target is known but has no usable requirements at all (canonical requirement loader returns nothing). | + `direction`, `target`, `you_to_opportunity`, `economics` (opportunity/target/personal) — every independent fact that doesn't depend on the Target's requirement set. |
| `target_available` | A Target is known and has at least one usable requirement. | The full `You → Opportunity → Target` payload (wire shape below). |

Review/mapping *incompleteness* on an otherwise-usable Target (an unreviewed
claim, an unresolved vocabulary mapping) does **not** force
`insufficient_target_evidence` — it surfaces as `relationship_unclear`
within `target_available`, alongside every other independently-safe fact
(`you_to_opportunity`, `direction_constraints`, `economics`). Only a Target
with zero usable requirements at all — nothing to structurally compare
against — is `insufficient_target_evidence`.

Archived/exploring directions are never treated as selected —
`career_directions.get_selected_direction_summary` only ever reads
`state = 'selected'` rows, the same function `/future` and Home already use.

## Refactor: `assess_specific_candidate`

`stepping_stones.assess_all_candidates` scans every observed posting
(`WHERE instance_type = 'observed_posting'`) because Pathways/Target
analysis genuinely needs a corpus-wide ranked candidate set. Role Detail,
Applications, and Pathways' overlay need exactly **one** candidate's
structural relationship to one Target — paying for that scan on every such
request would make a single-opportunity read scale with corpus size for no
reason.

`stepping_stones.assess_specific_candidate(cur, target_id, candidate_id,
target_vec, profile_vec, evidence_revision)` is the extracted per-candidate
body: the same bulk requirement/review loaders (called with exactly two ids
instead of the whole corpus), the same `_evaluate_missing_concepts` helper
(also extracted, and now shared by both functions — a distinct concept is
evaluated and cached in `jobber.d_target_evidence` identically either way),
and the same `assess_candidate` call. `assess_all_candidates` now calls
`_evaluate_missing_concepts` too, so there is exactly one concept-evaluation
code path, not two independently-maintained copies.

`backend/tests/test_opportunity_alignment.py::
test_bulk_and_specific_candidate_assessment_agree` asserts the same
(target, candidate) pair produces byte-identical `assess_candidate` output
whichever function computed it, and
`test_specific_candidate_assessment_does_not_scan_the_corpus` asserts the
specific-candidate path's query count does not grow with unrelated postings
in the corpus.

## `You → Opportunity`

Reuses `comparison_service.build_role_comparison` verbatim — no second
person-fit algorithm. The alignment response's `you_to_opportunity` is a
slimmed-down structural summary (counts by status, blocking/unverified
required gaps, legacy-requirement count, review completeness/blockers,
embedding similarity) that deliberately **omits** the comparison's own
`fit_score` — Phase 7 never exposes it as the alignment decision signal.

## `Opportunity → Target` and gap movement

`opportunity_to_target` (from `assess_specific_candidate`'s output) answers
three questions about the *outstanding* Target requirements (those not
already `evidenced`):

- **`target_gaps_involved`** — outstanding Target requirements that are also
  requirements of the opportunity. Each item carries the concept name, each
  side's requirement type, the person's current evidence status, and each
  side's source (`claim` vs. legacy `role_skill_observation`).
- **`target_gaps_not_touched`** — outstanding Target requirements absent
  from the opportunity's requirement set.
- **`additional_opportunity_demands`** — the opportunity's own *required*
  concepts that are not Target requirements at all (friction the Target
  route doesn't have).

Wording is deliberate: the product always says a role **involves** a
capability, never that it will **teach** or **give** it — a job requirement
is exposure, not guaranteed capability acquisition (`method.
involvement_not_acquisition`).

## Relationship classification

A small, named, rule-based vocabulary (`opportunity_alignment.
_classify_relationship`) — never a score. First match wins:

1. **`relationship_unclear`** — the stepping-stone engine could not reach a
   verdict: candidate review incomplete, Target review incomplete, or
   Target mapping incomplete (`step["assessment"]` is `insufficient_evidence`
   or `incomplete_target_mapping`). Legacy/review incompleteness always wins
   over a confident-looking label.
2. **`target_already_evidenced`** — the user's accepted evidence already
   covers every mapped Target requirement (`assess_candidate`'s own
   `target_evidenced` state) — the stepping-stone question stops being
   meaningful.
3. **`same_destination_family`** — the opportunity carries the *same
   reviewed archetype* as the Target, or as the selected Direction's own
   archetype anchor (`archetype_relationship.same_as_target_archetype` /
   `same_as_direction_archetype`). Checked before the stepping-stone verdict
   on purpose: a reviewed structural anchor is a stronger, more concrete
   fact than a gap-coverage judgment, but it never suppresses the other
   sections of the response — `opportunity_to_target`/`direction_constraints`/
   `economics` are always returned alongside it, so the user still sees the
   opportunity's own evidence gaps even when it's in the same family as the
   Target.
4. **`potential_step`** / **`no_identified_target_progress`** /
   **`not_more_reachable_than_target`** — taken directly from
   `assess_candidate`'s own state machine (`potential_step`,
   `no_target_progress`, and `needs_evidence`/`not_an_intermediate_step`
   respectively), which already encodes "structurally easier/more evidenced
   than the Target" (see `backend/app/stepping_stones.py`).

Same-archetype-family requires a **reviewed and still-active** archetype
assignment on both sides (`role_instance.archetype_concept_id`, the only
field `archetype_classification.assign_archetype` ever writes) — never
inferred from a title string or an embedding threshold. A concept can be
deprecated after a role was assigned to it (the assignment is never
cascade-cleared), so `_archetype_relationship` requires the *shared*
concept's `status` to still be `active` before `same_as_target_archetype`/
`same_as_direction_archetype` can be true — a deprecated shared archetype is
stale vocabulary, not a reviewed structural anchor, and falls through to
whatever the stepping-stone verdict alone would produce. The archetype facts
themselves are still displayed regardless of status (transparency); only the
same-family classification is gated. An opportunity with no reviewed
archetype always reports `archetype_relationship.opportunity_archetype =
null` and a `"No reviewed archetype is assigned to this posting."` note, and
is never auto-classified as a side effect of alignment.

Semantic similarity (`step["similarity_to_target"]`/`similarity_to_profile`,
surfaced separately as `semantic_similarity`) never participates in this
classification — `_classify_relationship` doesn't even take it as a
parameter. It is secondary context only, and if shown at all, it is labelled
"Semantic similarity" and kept out of the main relationship result.

None of the six states is a verdict: `same_destination_family` is not a
recommendation to apply, `potential_step` is not a prediction the user will
acquire the missing capabilities, and `no_identified_target_progress` is not
a statement that the role has no value outside the selected Target
(`method.relationship_caveat` restates this on every response).

## Direction constraints and qualitative dimensions

Direct constraints (`direction_constraints`) are evaluated only where
structured role data genuinely supports it: `locations` (`_location_matches`
— exact, case/whitespace-insensitive equality against the role's `location`
**or** `country` column, never containment: `location`/`country` are plain
free-text columns with no canonical geography table behind them, so
containment would false-positive "Ireland" against "Northern Ireland" or
"York" against "New York" — exactly the fuzzy "two differently-named places
are the same" guess this build forbids), `remote_types`, `employment_types`,
`seniority_levels` (`_text_matches` — equality or containment is safe for
these short, effectively-controlled vocabularies), and `compensation_floor`.
Each reports `matches` / `conflicts` / `unknown` / `not_specified` with the
observed and desired values and a plain-language reason. An empty desired
list is always `not_specified`, never a hard requirement satisfied by zero
options.

The compensation floor comparison reuses the same currency/component-family/
employment-basis compatibility rules `compensation_resolver.
compare_to_personal_earnings` already applies: different currencies never
convert (`unknown`, named explicitly), a day-rate opportunity against an
annual floor is `unknown` with "No annualisation is assumed" rather than a
silently bridged figure, and an incompatible employment basis is `unknown`
rather than treated as equivalent.

Qualitative dimensions (`direction_dimensions` — intellectual complexity,
technical engagement, people leadership, autonomy, bureaucracy tolerance,
etc.) are **never** fabricated from a title or keyword guess. Every one
reports `assessment: "not_structurally_assessed"` with the Direction's own
desired property alongside it — false precision is worse than an honest
"not assessed," and there is no AI call to fill the gap on a GET.

## Archetype relationship

When available, `archetype_relationship` exposes the opportunity's,
Target's, and Direction's reviewed archetypes side by side with two boolean
facts (`same_as_target_archetype`, `same_as_direction_archetype`) — plain
data the frontend renders, never a hidden weighting.

## Economics

`economics.opportunity` and `economics.target` each come straight from
`compensation_resolver.resolve_role_compensation`, sharing one
`economics_freshness()` read per request so a stale-derived-economics
verdict is consistent between them. They are shown **side by side**, each
with its own basis (`advert_stated` / `market_estimate` / `legacy_estimate`
/ `insufficient_evidence`) — there is no synthetic "which pays more"
verdict computed from the two, and a higher-paying opportunity is never
implied to be a better career move. `economics.vs_personal_earnings` reuses
`compare_to_personal_earnings` exactly as Pathways/Role Detail already do.
When derived economics are stale or never built, the resolver already
withholds the market-estimate tier and says so in `reason` — GET never
rebuilds anything.

## Review and legacy transparency

`review.opportunity`/`review.target` expose completeness, blockers, and
(for the Target) mapping completeness/unresolved count directly from the
canonical requirement-review summary
(`role_requirements.load_requirement_review_summary[_bulk]`) and
`target_mapping.target_mapping_summary` — the same functions every other
review-gated surface in the product already reads. A legacy
(`role_skill_observation`-sourced) requirement contributing to a shared gap
is flagged per item (`target_requirement_source`/`opportunity_requirement_source`)
and counted (`legacy_requirements_involved`,
`you_to_opportunity.legacy_requirement_count`) — never silently presented as
human-reviewed.

## Wire shape (`target_available`)

```json
{
  "state": "target_available",
  "direction": { "...": "CareerDirection, or null under target_id_override with none selected" },
  "target": { "id": "...", "title": "...", "organisation": "...", "archetype_concept_id": "..." },
  "opportunity": { "id": "...", "title": "...", "organisation": "...", "archetype_concept_id": "..." },
  "relationship": { "state": "potential_step", "label": "Potential stepping stone", "reason": "..." },
  "you_to_opportunity": { "counts": {...}, "blocking_gaps": [...], "unverified_required": [...], "...": "..." },
  "opportunity_to_target": {
    "target_gaps_involved": [...],
    "target_gaps_not_touched": [...],
    "additional_opportunity_demands": [...],
    "...": "..."
  },
  "direction_constraints": [{ "constraint": "locations", "status": "matches", "...": "..." }],
  "direction_dimensions": [{ "dimension_code": "autonomy", "assessment": "not_structurally_assessed", "...": "..." }],
  "archetype_relationship": { "...": "..." },
  "economics": { "opportunity": {...}, "target": {...}, "vs_personal_earnings": {...} },
  "review": { "opportunity": {...}, "target": {...} },
  "semantic_similarity": { "to_target": 0.7, "to_profile": 0.6 },
  "method": { "...": "prose disclaimers, no numeric score" }
}
```

## Frontend

`frontend/src/components/opportunity/AlignmentSection.tsx` is the one place
that renders an `OpportunityAlignment` response — no page re-derives what a
relationship state or a constraint status means:

- **`AlignmentDecisionTile`** — the compact state, replacing Phase 6's
  `CareerDirectionTile` (which only ever named the direction). Used by Role
  Detail's Decision Summary and the Application workspace's compact
  summary. Every one of the four backend states is a normal, honest render
  here, never an error — only a fetch failure renders as an error, with its
  own retry and without blocking the rest of the page.
- **`AlignmentFullSection`** — the fuller "You → this opportunity → Target"
  section (You → Opportunity, Opportunity → Target, Direction constraints,
  Economics, Why this relationship), each its own card so a missing piece
  never blanks the pieces that are available. Used by Role Detail below the
  Decision Summary, and by Pathways' opportunity overlay.
- **`AlignmentOpportunityHeader`** / **`RelationshipBadge`** — small pieces
  Pathways' overlay needs (it's showing a *different* role than the page
  it's embedded in) that Role Detail doesn't (the page's own header already
  says the title).

### Role Detail

`RoleDetail.tsx` fetches alignment once, alongside compensation/comparison,
exactly like Phase 6's direction fetch did (same `currentId` stale-response
guard — navigating `/roles/A → /roles/B` without unmounting can never let a
slow A alignment response render as B's). A `target` in the response adds a
"View this opportunity in Pathways" link to `/pathways/{target_id}?opportunity_id={role_id}`.

### Application workspace

`ApplicationWorkspace.tsx` shows the compact tile only (never the fuller
section — that stays on Role Detail/Pathways). The alignment fetch is keyed
by the *role* id, which is only known once the application detail loads, so
it runs as its own effect gated on `detail?.role.id` rather than joining the
four requests keyed directly on the application id. Never mutates
application status or artifacts; a failed fetch never blanks preparation
checks, evidence, or the application package.

### Pathways

`/pathways/{target_id}?opportunity_id={role_id}` shows the overlay above
the existing Direct/Intermediate sections, calling the same endpoint with
`target_id` set to the route's own target — independent of `result`
(the existing Pathways payload), so neither blanks the other on failure.
The opportunity is never inserted into an archetype route card. Route depth
and the existing route cards are completely unchanged.

**Target-selector fix** (folding in the Phase 6 smoke-test issue,
build §19): `Pathways.tsx` had no stale-response guard at all on its main
`load()` call, and the `<select>`'s value could show the placeholder rather
than the URL's target on first paint (the matching `<option>` doesn't exist
until `listTargets()` resolves). Both are fixed: `load`/the new alignment
fetch now guard on a `currentId` ref exactly like Role Detail/Applications,
and a `targetNotFound` check (once the target list has loaded and the
route's id isn't in it) renders an explicit "This target could not be
found" notice rather than leaving the dropdown ambiguously on the
placeholder with no explanation. `Pathways.test.tsx` adds the regression:
the selector visibly reflects the URL target once options load, updates on
same-tree navigation (the browser-back/forward case), and the not-found
state never silently shows another real target as selected.

## No AI, no new score, no N-hop optimiser

No prompt file, no model call, and no AI-generated narrative anywhere in
this phase — plain deterministic prose templates (`method`, `relationship.reason`,
each constraint's `reason`) are sufficient, and
`test_opportunity_alignment.py::test_get_alignment_never_calls_ai` asserts
it directly. `stepping_stones.assess_candidate`'s internal `ranking_score`
is never returned by the alignment service and never rendered. Pathways'
route depth remains exactly direct + one intermediate archetype — Phase 7
adds a specific-opportunity overlay on top of that structure, never a
second hop, never a chained archetype sequence, never a route score.

## Performance

- No corpus-wide scan for a single-opportunity request (`assess_specific_candidate`,
  above) — the specific-candidate path's query count is asserted not to
  grow with corpus size.
- One alignment request from Role Detail, one from the Application
  workspace (fired once `detail.role.id` is known), and Pathways issues
  exactly one additional alignment request when — and only when —
  `?opportunity_id=` is present; without it, Pathways' request count is
  completely unchanged from Phase 6.
- `You → Opportunity` and `Opportunity → Target` each evaluate their own
  concepts once within their own engine (`comparison_service`'s live
  `capability_engine` calls, and `assess_specific_candidate`'s
  `d_target_evidence`-cached calls) — the two engines are independently
  bounded (never corpus-wide) but not globally deduplicated against each
  other, since merging them would mean inventing a third evidence engine
  rather than composing the two that already exist and are already tested.

## Phase 8 handoff

Deliberately out of scope here, left for **Market Coverage & Confidence**:
corpus freshness/representativeness as a first-class product surface,
source/geography/time coverage disclosure beyond what `economics_freshness`
and each resolved figure's own `evidence`/`evidence_quality` already state,
and any richer confidence framing across the whole corpus rather than one
opportunity/Target pair at a time.
