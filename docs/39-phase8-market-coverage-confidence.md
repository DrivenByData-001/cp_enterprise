# Phase 8: Market Coverage & Confidence

## Objective

Make the evidence base behind every market-derived conclusion in this app
explicit, so a thin or skewed corpus is never presented as more than it is.
Phase 8 is disclosure, not a new statistical model: it introduces one
canonical, read-only, deterministic service — `backend/app/market_coverage.py`
— that every feature relying on the captured role/compensation corpus
composes, so "coverage", "evidence depth" and "representativeness" can never
be defined two different ways in two different places.

This is not a market-size estimation, survey-validation, or sampling-weight
project. The app has no known labour-market sampling frame, and nothing in
this phase claims otherwise.

## Three concepts, never collapsed into one

- **Coverage** — measurable facts about the corpus this app actually holds:
  how many roles, how complete their metadata, how much requirement review
  has happened, how much compensation evidence exists. Always a count over a
  count.
- **Evidence depth** — a transparent, named, rule-based sufficiency state for
  a *specific* market-derived claim (one archetype, one compensation
  context, one trend): `insufficient` / `thin` / `supported` /
  `broader_support`. Never a numeric score, and never compared across
  evidence types as if they were statistically equivalent.
- **Representativeness** — whether the corpus reflects the external labour
  market. Always `{"known": false, "reason": "..."}` on a whole-corpus
  response. Internal completeness, corpus size, source diversity and broad
  metadata coverage are explicitly named as facts that must never flip this
  to `known: true`.

These three are read together but never merged into one verdict. An
archetype can have `broader_support` evidence depth while representativeness
is still unknown; a corpus can be 100% metadata-complete and still say so.

## The service: `backend/app/market_coverage.py`

Deterministic, read-only, bounded, no AI, no automatic rebuild. Structured
like `market_analytics.py`: a handful of impure `fetch_*` functions (the
only functions that touch the database) feed pure Python transform
functions, so almost all of the analytical logic is unit-testable without a
database (`backend/tests/test_market_coverage.py`).

Public surface:

- `CoverageScope` — the role-corpus scope dataclass (`year_from`, `year_to`,
  `country`, `seniority_level`, `archetype_id`).
- `build_coverage_summary(cur, scope)` — the full Market Coverage response.
- `archetype_coverage_detail(cur, archetype_concept_id, scope=None, *, freshness=None)`
  — one archetype's bounded support. `scope=None` (the default) means the
  whole corpus — this is what Opportunity alignment, Pathways and Career
  Direction detail use, since they want "what backs this archetype",
  independent of whatever filter another page happens to have active.
  Market Coverage's own drill-down passes its active `scope` (with
  `archetype_id` overridden) so the detail matches the table it was opened
  from.
- `archetype_support_bulk(cur, archetype_ids, *, freshness=None)` — the same
  aggregation for several archetypes in one bulk round trip (Pathways'
  intermediate-archetype nodes), keyed by archetype id.
- `discovery_corpus_disclosure(cur)` — the compact, bounded subset Career
  Direction discovery hands to the model and the frontend.
- `structural_evidence_depth(...)`, `compensation_evidence_depth(...)`,
  `trend_evidence_depth(classification)` — the three evidence-depth rule
  families, each with its own named, centrally-documented thresholds (see
  below).
- `REPRESENTATIVENESS_UNKNOWN`, `REQUIREMENT_STATE_PRECEDENCE`,
  `classify_requirement_review_state(...)`.

No migration. No new durable table. Everything is derived live from
`jobber.role_instance`, `jobber.document`, `jobber.requirement_claim`,
`jobber.role_skill_observation`, `jobber.concept_proposal(_occurrence)`,
`jobber.concept`/`role_archetype_detail`, `jobber.d_archetype_demand`,
`jobber.d_archetype_comp`, `jobber.compensation_observation`, and
`jobber.economics_rebuild_state` (via `economics_freshness`).

## Scope semantics

`CoverageScope` supports `year_from`/`year_to`/`country`/`seniority_level`/
`archetype_id`. Every response echoes the applied scope (`scope.dated_scope_active`
says whether a date filter is active). Year filters apply to `posting_date`
only, never `document.captured_at`.

**A role with no posting date never silently leaves a dated scope.**
`fetch_scope_candidate_roles` fetches every role matching the *non-date*
filters (undated roles included); `_apply_date_scope` — a pure function — is
what actually applies the year filter, splitting the fetched rows into
`(in_scope, excluded_as_undated)`. `roles.excluded_undated_count` in the
response is exactly `len(excluded_as_undated)`, and when it is nonzero the
top-level `limitations` list says so explicitly.

Compensation evidence is a **separate scope** (`market_analytics.MarketAnalyticsFilters`
— market/currency/component/pay-period/employment-basis/period). The
`compensation` block in a coverage response is corpus-wide, deliberately
**not** narrowed by the role-corpus scope's year/country/seniority/archetype
filters — role-corpus dates/geography and compensation evidence
periods/markets are not the same dimension, and conflating them would
silently and incorrectly narrow "the market's" evidence by a filter that has
no equivalent meaning on survey-sourced rows. This is stated in the
response itself (`compensation.scope_note`).

## Role-corpus completeness (`roles`)

For every role in scope: linked/unlinked source document, known/unknown
posting date, country, seniority, employment type, remote type. Every field
uses the one shared proportion shape:

```json
{"count": 42, "total": 60, "proportion": 0.7, "meaning": "42 of 60 captured postings have a known posting date"}
```

`meaning` is always phrased "N of M captured postings...", never a claim
about the external market — the numerator/denominator/meaning triple is
present on every proportion in this module's response, with no exceptions,
so nothing is ever an unexplained percentage.

## Posting date vs. capture date (`time`)

**Capture recency is not posting recency**, enforced throughout this module:
every time-coverage figure is built from `posting_date` alone, except the
two fields explicitly named for `document.captured_at`:

- `freshly_captured_but_posting_date_unknown_count` — roles with no posting
  date whose linked document was captured in the current calendar year.
  Labelled exactly that; never folded into "current" counts.
- `document_capture_date_range` — the earliest/latest `captured_at` among
  linked documents in scope.

`by_year`/`current_calendar_year_count`/`previous_calendar_year_count`/
`older_count` are all posting-date buckets. `captured_at` is never
substituted for a missing `posting_date` anywhere in this module.

## Geography (`geography`)

Countries exactly as captured on `role_instance.country` — no geocoding, no
fuzzy normalisation. `country_distribution` is bounded
(`COUNTRY_DISTRIBUTION_LIMIT = 50`) but `distinct_known_country_count` is the
true count regardless of truncation. `region_distribution` reuses
`trends.region_for_country` (the same small, explicit, best-effort lookup
Trends already uses) — a country missing from that table contributes to
`unknown_country`/is simply absent from the region bucket, never guessed.

## Source / provenance (`sources`)

`document.source` is called **capture source** throughout this module — this
app's ingestion captures a posting from wherever the user found it, and
nothing in the ingestion path confirms it is an independent external
publisher/provider in the market-research sense. Capture-source diversity is
never called "independent market sources". Bounded distributions for
capture source, `provenance_quality`, `document.kind`, and URL presence.

## Requirement-review coverage (`requirements`)

Built on `role_requirements.load_requirement_review_summary_bulk` — the same
canonical review-completeness definition Requirement Review, Role Detail and
Comparison already use. Nothing here re-derives or changes that logic.

Nine independent, non-exclusive tallies (`review_complete`,
`accepted_present`, `unreviewed_present`, `unresolved_vocabulary_present`,
`needs_reextraction_present`, `extraction_attempted_incomplete`,
`extraction_never_attempted`, `legacy_only`, `no_usable_evidence`) plus one
**mutually exclusive** high-level state per role, `classify_requirement_review_state`,
first-match-wins:

1. `needs_reextraction` — `summary["needs_reextraction"] > 0`
2. `unresolved_vocabulary` — `summary["unresolved_proposals"] > 0`
3. `review_pending` — `summary["unreviewed"] > 0`
4. `reviewed` — `summary["accepted"] > 0` (reaching here implies the review
   gate is clean — none of 1–3 held)
5. `legacy_only` — no accepted claim, but at least one `role_skill_observation`
   resolves to an active concept
6. `not_extracted` — none of the above; no usable signal of any kind

`REQUIREMENT_STATE_PRECEDENCE` exports this order; `requirements.state_distribution`
in the response sums to the scope's role count exactly once per role. The
independent tallies can disagree with the state bucket by design — a role
with `needs_reextraction > 0` and no accepted claim or legacy fallback counts
in *both* `needs_reextraction_present` and `no_usable_evidence`, while its
single state is `needs_reextraction` (rung 1 wins).

`legacy_only`/fallback-availability is computed by `_fetch_legacy_fallback_counts`,
a simplified corpus-processing proxy: `role_skill_observation.canonical_concept_id`
resolves to an *active* concept. It deliberately does not replay
`role_requirements.py`'s curator-veto logic (a rejected/corrected claim
suppressing a specific concept's observation) — Phase 8 discloses corpus
*processing state*, not requirement-review authority, and replaying the veto
would need a second per-role veto set for a distinction this module has no
use for.

## Vocabulary coverage (`vocabulary`)

How much of the corpus rests on curated/resolved concepts versus raw legacy
terms: roles with an accepted canonical requirement, roles relying on legacy
fallback, resolved vs. unresolved `role_skill_observation` rows, and
unresolved vocabulary-proposal occurrence count (scoped to roles in scope,
via `concept_proposal_occurrence`). This is corpus-*processing* coverage —
never proof of external market completeness — and nothing here resolves a
term automatically.

## Archetype coverage (`archetypes`)

`active_archetype_count` is a whole-vocabulary fact (`active_archetype_catalogue`),
never scoped. Everything else — assigned/unassigned, supported/unsupported —
is scoped to the same role filter as the rest of the response. A posting
assigned to a *deprecated* archetype counts toward `assigned_deprecated`,
never toward `assigned_active`, and never appears in the bounded `support`
table (which only ever lists active archetypes with at least one supporting
posting in scope).

Per-archetype `support` rows (one per active archetype with ≥1 supporting
posting): assigned posting count, reviewed-requirement posting count,
known/unknown posting-date counts, latest known posting date, distinct
country count (+ up to 5 country names), demand-derivation availability
(`d_archetype_demand`), compensation-benchmark availability
(`d_archetype_comp.reference_comp IS NOT NULL`), `economics_freshness`, and
`evidence_depth` (via `structural_evidence_depth` — see below). An archetype
with exactly one supporting posting always lands in the `thin` evidence-depth
state, which is the built-in "thin-evidence caveat" the build brief asks for.

This exact per-archetype shape (`archetype_concept_id`, `canonical_name`,
`status`, `seniority_band`, `typical_market`, `assigned_posting_count`,
`reviewed_requirement_posting_count`, `known_posting_date_count`,
`unknown_posting_date_count`, `latest_known_posting_date`,
`distinct_country_count`, `countries`, `demand_derivation_available`,
`compensation_benchmark_available`, `economics_freshness`, `evidence_depth`)
is produced by exactly one pure function, `_aggregate_archetype_rows`, and
is identical across `build_archetypes_block`'s scoped table,
`archetype_coverage_detail`'s single-archetype lookup, and
`archetype_support_bulk`'s multi-archetype lookup — the frontend's
`ArchetypeEvidence` type and `ArchetypeEvidencePanel` component (build §25)
rely on this being one shape everywhere.

`archetypes.filter_options` is a second, deliberately different list: the
whole active catalogue's `{archetype_concept_id, canonical_name}`, always
computed from `catalogue` (unscoped), never from `support`. This is for a
filter control's own options, which must never shrink as the user narrows
the scope — the same principle `market_analytics.build_facets` already
applies to Market Summary's filter bar (computed from every row, never the
currently-filtered subset). `support`, by contrast, only ever lists
archetypes with at least one supporting posting in the *current* scope, so
using it for filter options would make the archetype dropdown's own choices
disappear as soon as a filter excluded their only supporting posting.

No role is ever auto-classified by any Phase 8 GET (`test_no_archetype_auto_classification`).

## Compensation evidence (`compensation`)

Composed directly from `market_analytics.fetch_accepted_evidence_rows` +
`market_analytics.build_coverage`/`build_facets` — never duplicated. Adds, on
top of the unmodified `market_analytics` coverage shape: a `basis`
distribution (posting_stated / posting_estimated / survey / curator_asserted,
kept statistically separate, never pooled), top providers, and
`role_linked_coverage` — the one part of this block that *is* scoped to the
role-corpus filter, since "does this posting in scope have accepted
compensation evidence" is a role-level fact. Missing role-linked compensation
is never treated as evidence of low compensation.

## Derived-economics freshness (`derived_economics`)

`economics_freshness()` reused verbatim, exactly once per response (computed
early in `build_coverage_summary` and threaded through, so
`build_archetypes_block`'s per-row `economics_freshness` field and the
top-level `derived_economics` block never disagree within one response). No
second freshness calculation exists anywhere in this phase, and no Phase 8
GET ever rebuilds anything.

## Evidence depth: rules and thresholds

Three independent families, each a small set of named, centrally-defined
constants — never one universal threshold pretending posting counts,
compensation observations and dated trend periods are statistically
equivalent, and never tuned to make current production data look stronger.

**Structural / archetype support** (`structural_evidence_depth`):

| state | condition |
|---|---|
| `insufficient` | archetype not active, or `posting_count < STRUCTURAL_THIN_MIN_POSTINGS` (1) |
| `thin` | `posting_count < STRUCTURAL_SUPPORTED_MIN_POSTINGS` (3) |
| `broader_support` | `posting_count >= STRUCTURAL_BROADER_MIN_POSTINGS` (8) **and** `distinct_countries >= STRUCTURAL_BROADER_MIN_COUNTRIES` (2) |
| `supported` | otherwise |

**Compensation support** (`compensation_evidence_depth`):

| state | condition |
|---|---|
| `insufficient` | `observation_count < COMPENSATION_THIN_MIN_OBSERVATIONS` (1) |
| `thin` | `observation_count < COMPENSATION_SUPPORTED_MIN_OBSERVATIONS` (3) or `distinct_document_count < COMPENSATION_SUPPORTED_MIN_DOCUMENTS` (2) |
| `broader_support` | `observation_count >= COMPENSATION_BROADER_MIN_OBSERVATIONS` (8) **and** `distinct_provider_count >= COMPENSATION_BROADER_MIN_PROVIDERS` (2) **and** `economics_fresh` |
| `supported` | otherwise (including the `broader_support`-eligible case when economics are stale — **stale derived economics can never produce the strongest compensation-support state**) |

**Trend support** (`trend_evidence_depth`) reuses `trends.classify_trend`'s
own `usable_periods`/`SPARSE_MIN_SAMPLE` rather than inventing a second
small-sample rule:

| state | condition |
|---|---|
| `insufficient` | `classification.label == "sparse_insufficient_evidence"` |
| `thin` | `usable_periods < TREND_SUPPORTED_MIN_USABLE_PERIODS` (3) |
| `broader_support` | `usable_periods >= TREND_BROADER_MIN_USABLE_PERIODS` (5) |
| `supported` | otherwise |

Every result carries `state`, a plain-English `reason`, and the facts it was
computed from (e.g. `posting_count`, `observation_count`,
`usable_periods`) — never a bare score. `EVIDENCE_DEPTH_*` constants name the
four states; there is no fifth "confidence" numeric field anywhere.

## Representativeness

`REPRESENTATIVENESS_UNKNOWN` is a constant, not a computation:

```json
{"known": false, "reason": "This is a user-collected/captured corpus with no known probability sampling frame. Internal completeness, corpus size, capture-source diversity and metadata coverage cannot establish representativeness of the external labour market."}
```

Every whole-corpus Market Coverage response, every Pathways response, and
every `discovery_corpus_disclosure` carries this verbatim. Nothing in this
module ever computes `known: true`.

`concentration` (top capture sources / countries / organisations /
compensation providers) is reported as **observed concentration**, never
"bias" — `concentration.note` and any auto-generated `limitations` entry
(triggered when one bucket holds ≥ `CONCENTRATION_NOTE_THRESHOLD` = 50% of
the scope) say "the captured corpus is concentrated in X", never "biased
toward X", since no external target distribution exists to measure bias
against.

## API

`GET /api/market-coverage/summary` — the whole scoped response (`year_from`,
`year_to`, `country`, `seniority_level`, `archetype_id` query params).

`GET /api/market-coverage/archetypes/{archetype_concept_id}` — bounded
single-archetype detail, scoped by the same filters (minus `archetype_id`,
supplied by the path). 404 for an unknown archetype id.

Neither route calls AI, writes anything, or rebuilds anything.

## Market Coverage page (`/market/coverage`)

`frontend/src/pages/MarketCoverage.tsx`. Sections: Corpus in scope, Time
coverage, Geography, Source/provenance, Requirement readiness, Archetype
coverage (with a bounded, clickable per-archetype table that opens the
shared `ArchetypeEvidenceCard`), Compensation evidence, and "What this does
not tell you" (the `limitations` list, always including the
representativeness-unknown statement). Filters (`year_from`/`year_to`/
`country`/`seniority_level`/`archetype_id`) live in the URL query string; the
archetype filter's own options come from `archetypes.filter_options` in the
same summary response (never a second `listArchetypes` request), and
clicking a table row opens `ArchetypeEvidenceCard` directly from that row's
already-fetched `ArchetypeEvidence` data (never a second
`GET /api/market-coverage/archetypes/{id}` request) — a cold page load is
exactly one API request. Counts, numerator/denominator bars and bounded
tables only — no gauge, speedometer, confidence ring, or red/amber/green
verdict anywhere on the page or in this phase.

The Time coverage year-by-year chart is the one place a bar's *visual
width* and its *displayed statistic* are deliberately different numbers:
width scales against the tallest year bucket (pure chart legibility, via a
dedicated `ScaledBar` component, never `FractionBar`), while the displayed
count/denominator/percentage is always each year's share of every captured
posting in the current scope (`roles.total` — the same convention Trends'
own "by year" chart already uses). Conflating the two — showing a
percentage computed against the chart-scaling maximum — would misrepresent
a bar as a share of the corpus when it is actually only a share of the
tallest bucket.

Linked from `/future` ("How much evidence do I actually have?", under
Supporting exploration), `/trends` (coverage strip), and Market Summary
(coverage card).

## Reusable archetype evidence panel

`frontend/src/components/market/ArchetypeEvidencePanel.tsx` exports:

- `EvidenceDepthBadge` — the small named-state badge (never a numeric
  gauge), reused everywhere an evidence-depth state is shown (Trends,
  Market Summary, Market Coverage, Opportunity alignment, Pathways).
- `ArchetypeEvidenceInline` — a compact one-line-plus-badge form for
  embedding in another card (Opportunity alignment's `market_evidence_context`,
  Pathways' Fit tab).
- `ArchetypeEvidenceCard` — the full card form (Market Coverage's
  drill-down).

All three read the `ArchetypeEvidence` shape produced by
`_aggregate_archetype_rows` on the backend, so the same archetype's support
never differs depending on which page is looking at it.

## Integration contracts

### Trends (`/trends`)

A `CoverageStrip` card (roles in scope, dated/undated counts, countries
represented, predominant requirement-evidence mode, a link to Market
Coverage with the same filters) fetches `GET /api/market-coverage/summary`
once, independent of the page's existing requests; a failure there leaves
the rest of Trends fully functional (`coverage: null` just hides the strip).
`GET /api/trends/requirement-trend` gains an `evidence_depth` field, composed
at the route layer (`routes/trends.py`) from `trends.classify_trend`'s
already-computed `classification` via `market_coverage.trend_evidence_depth`
— `trends.py` itself, and its classification rules, are untouched (avoiding
a `market_coverage` ↔ `trends` import cycle, since `market_coverage.py`
already imports from `trends.py`).

### Market Summary (`GET /api/market-data/analytics/summary`)

`routes/market_data.py` composes `evidence_depth` (via
`market_coverage.compensation_evidence_depth`, fed by the exact coverage
numbers `market_analytics.build_coverage` already returns) and
`representativeness` onto the existing, unmodified response.
`market_analytics.py` itself is untouched — `test_market_analytics.py`
passes unmodified. `MarketSummary.tsx`'s coverage card gained the evidence-
depth badge and a representativeness/link line; every existing chart, table
and filter is unchanged.

### Career Direction discovery (Phase 6)

`career_direction_discovery.py`'s `_corpus_disclosure` (a hand-rolled set of
six counters) is deleted; `build_discovery_context` now calls
`market_coverage.discovery_corpus_disclosure(cur)` directly. The disclosure
handed to the model and to the frontend's post-generation panel is the exact
same deterministic facts (now a richer, still-bounded set: posting-date
range and undated count, country concentration, compensation-evidence
availability, representativeness) — the model never sees or generates a
coverage/confidence label of its own; `CareerDirectionCandidate`'s schema has
no such field, unchanged from Phase 6.

### Opportunity alignment (Phase 7)

`opportunity_alignment.py` gains `market_evidence_context` on every state
that already computes an `opportunity_role` (`direction_without_target`,
`insufficient_target_evidence`, `target_available`) — the opportunity's own
archetype support, via `archetype_coverage_detail(cur, archetype_id, freshness=freshness)`
(`freshness` reused from the same call already made in
`build_opportunity_alignment`, no extra query). Composed strictly after
`relationship` is classified; `_classify_relationship` never reads it.
`same_destination_family`/`potential_step`/`no_identified_target_progress`/
every other Phase 7 relationship state is provably unaffected — the
frontend's `market_evidence_context` card renders independently, and
`test_market_evidence_context_present_when_archetype_assigned` asserts the
relationship state alongside it.

### Pathways

`pathways.py`'s `_direct_route` gets `market_evidence` for the target's own
archetype (a single `archetype_support_bulk` call for one id); `_group_by_archetype`
gets one bulk `archetype_support_bulk` call across every intermediate
archetype on the page (bounded by the structural engine's own stepping-stone
output, never role-count-scaled — `test_cold_pathways_does_not_scale_query_count_with_candidate_count`
still holds). The top-level result gains `representativeness`. Route depth,
ordering and every existing state machine are unchanged
(`test_market_evidence_never_changes_route_depth_or_state`,
`test_only_direct_and_one_intermediate_hop_are_produced`). No route-
confidence score is introduced.

## Performance / query counts

- Market Coverage page, cold load: **exactly 1 request**
  (`GET /api/market-coverage/summary`, ~13 bounded backend queries — one
  role+document fetch; 4 for the bulk requirement-review summary; 1 for
  legacy-fallback counts; 1 archetype catalogue; 2 archetype demand/comp
  availability; 1 compensation evidence fetch; 1 role-linked-compensation
  set; 2 for `economics_freshness` — none scale with role count beyond the
  one initial fetch, and none loop per role or per archetype;
  `test_summary_query_count_does_not_grow_with_role_count`). The archetype
  filter dropdown's options and the per-archetype drill-down card are both
  populated straight from this one response (`archetypes.filter_options`,
  `archetypes.support`) — the page calls neither `listArchetypes` nor the
  dedicated archetype-detail endpoint.
- `GET /api/market-coverage/archetypes/{id}` (~10 bounded queries) exists and
  is exercised directly by its own backend tests, for a consumer that needs
  one archetype's detail without the whole summary — the Market Coverage
  page itself has no such need, since every archetype it can drill into is
  already a full `ArchetypeEvidence` row in `archetypes.support`.
- Trends: existing requests **+ 1** coverage request.
- Career Direction discovery: server-side only, **no extra browser request**.
- Opportunity alignment: composed server-side into the existing single
  alignment response — **0 extra requests**.
- Pathways: composed server-side into the existing single pathways response
  — **0 extra requests**; the intermediate-archetype bulk call is one query
  set regardless of how many postings support those archetypes.

## Safety / semantics carried through every surface

No AI call in any Phase 8 code path. No automatic posting-date inference,
country inference, archetype assignment, requirement extraction/review,
vocabulary resolution, or economics rebuild. No new durable table — every
fact is derived live. No single market-confidence score, gauge or
red/amber/green verdict anywhere. No currency conversion, no cross-provider
compensation pooling (all composed from `market_analytics.py`, which already
enforces this).

## Phase 9 handoff

Deliberately out of scope here, left for the **Career Cockpit & Learning
Loop**: combining a selected Career Direction, its Opportunities,
Applications, outcomes and this phase's evidence-depth/coverage disclosures
into one coherent home/feedback surface — e.g. surfacing *which* evidence
gaps a user's own activity (accepted requirements, recorded applications,
outcomes) is closing over time. Phase 8 supplies the deterministic facts;
Phase 9 is the first surface that would read them longitudinally rather than
as a point-in-time snapshot.
