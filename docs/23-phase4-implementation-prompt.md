# 23 — Phase 4 Implementation Prompt (verbatim)

**Status:** source prompt for the Phase 4 build (market economics / compensation
/ gap value, plus the Accepted Vocabulary overview). Recorded verbatim for
provenance; see `docs/24-phase4-economics-and-vocabulary-overview.md` for the
implementation record, deviations, and known limitations.

---

Work from the current `main` branch of:

DrivenByData-001/cp_enterprise

Current main includes PR #14 / merge commit:

ea38bfbf276c2f2712457cd50fbb0e7f99808b23

Implement one cohesive build with two objectives:

1. Phase 4 — market economics / compensation / gap value.
2. A lightweight Accepted Vocabulary overview to support ongoing curation.

Do not implement Phase 5 transition difficulty, learning-time estimates, potential, prerequisite/adjacency modelling, or other unrelated features.

Do not mutate production data from the implementation environment.

============================================================
0. ARCHITECTURAL PRE-FLIGHT
============================================================

Before writing migrations or code:

- inspect the current main branch;
- inspect all existing migrations through 0012;
- inspect the current capability engine and d_role_fit implementation;
- inspect role_instance, role_archetype_detail, concept, concept_edge,
  concept_edge_rule, requirement_claim, role_skill_observation,
  d_capability_coverage and d_role_fit;
- reconcile the Phase 4 design in docs/11 with the Phase 2/3 architecture
  actually implemented.

The live production shape has already been inspected externally. Use these
facts as expected production context, not as permission to write to production:

- approximately 330 observed postings;
- 56 active canonical concepts;
- 34 active capability concepts;
- 0 active role_archetype concepts;
- 0 role_instance rows currently assigned to an archetype;
- approximately 1,248 mapped capability observations in
  role_skill_observation;
- currently 0 capability requirement_claim rows in production;
- approximately 23 postings contain stated salary data:
    - 14 GBP
    - 7 EUR
    - 2 USD
- compensation evidence is therefore currently sparse;
- jobber.role_archetype_detail already exists;
- role_instance.archetype_concept_id already exists;
- concept_edge_rule already permits:
    role_archetype --demands--> capability.

Do not assume the original docs/11 SQLite-era DDL literally matches production.
Use UUIDs and the established Postgres/Supabase conventions.

============================================================
1. IMPORTANT PHASE 4 DEVIATION: PERSON-SIDE COMPENSATION
============================================================

docs/11 originally proposed jobber.episode_compensation.

DO NOT implement that table.

Phase 2 established that profile360 is authoritative for person-side evidence
and jobber must not create a second home for the user's career history.

The current profile360 schema does not expose structured compensation fields.

Therefore:

- do not store the user's salary history in jobber;
- do not add salary fields to profile360 from this app;
- do not put salary into person_capability_assertion or any other workaround;
- document this as an explicit Phase 4 deviation.

For this build:

"current benchmark" means the best compensation benchmark among roles/archetypes
the current structural model considers reachable.

It does NOT mean the user's actual current salary.

The UI must say this clearly.

A true personal salary-history timeline remains deferred until the authoritative
profile360 model has an appropriate compensation source.

This deviation must not block Q7/Q8:
- which reachable roles/archetypes pay more?
- which capability gaps unlock higher-paid opportunities?

============================================================
2. UNIFY ROLE REQUIREMENT EVIDENCE BEFORE ECONOMICS
============================================================

This is essential.

The current Phase 3 capability engine derives role fit only from
jobber.requirement_claim.

Production's historical/current corpus primarily carries its role-side
requirement evidence in:

jobber.role_skill_observation

with canonical_concept_id populated through vocabulary curation.

Therefore Phase 4 cannot rely solely on requirement_claim or every historical
role will look requirement-free.

Introduce one canonical backend loader for role requirement evidence.

Semantics:

1. If a role has usable, non-superseded, non-rejected requirement_claim rows:
   use those as authoritative.

2. Otherwise fall back to mapped role_skill_observation rows whose
   canonical_concept_id resolves to an active canonical concept.

3. Never merge the two evidence sources for one role unless there is a
   separately justified deduplication model. Prefer one source or the other,
   consistent with the existing Role Detail fallback philosophy.

4. Preserve provenance in the returned requirement items:
   - claim
   - legacy/app role observation

5. Preserve requirement_type/importance where present.

6. Never upgrade a legacy observation to "stated" evidence merely because it
   exists.

Refactor d_role_fit / capability_engine to use this canonical loader.

Existing source-aware requirement_claim behaviour must remain unchanged.

Add regression tests showing:
- requirement_claim wins when present;
- role_skill_observation fallback works when claims are absent;
- mapped capability observations produce real structural fit/gaps;
- unmapped observations are ignored;
- rejected/superseded claims do not re-enter via fallback.

This is not a new Phase 3 scoring model. It is making the already-built model
read both production evidence pipelines correctly.

============================================================
3. ROLE ARCHETYPES
============================================================

Phase 4 needs role archetypes before compensation aggregation is meaningful.

Reuse:

- concept.type_code = 'role_archetype'
- jobber.role_archetype_detail
- role_instance.archetype_concept_id

Do not create a parallel role-family table.

Add an Archetypes workflow, preferably as a tab/section of the new Economics
page rather than another top-level navigation item.

The workflow should show unassigned observed roles grouped by normalised title.

For each group allow the curator to:

- create a new role archetype from the group;
- assign the group to an existing active role archetype;
- assign individual roles differently where the title group is misleading;
- edit archetype metadata:
    - canonical name
    - seniority_band
    - primary_function_concept_id
    - typical_market
    - notes
- see how many roles are assigned.

Creating an archetype must atomically create:

- concept(type_code='role_archetype', status='active')
- role_archetype_detail

and assign the selected roles.

Do not let AI silently create or assign archetypes.

An AI suggestion button may be added only if it maps onto existing accepted
archetypes and always requires curator confirmation. It is not required for
Phase 4 completion.

Title grouping is a curation aid only. The raw posting title remains untouched.

============================================================
4. ARCHETYPE DEMAND
============================================================

Do not treat archetype demand as an intrinsic property manually typed onto a
capability.

Derive it from the assigned roles' canonical requirement evidence.

Do NOT mix this empirical derived state into curator-authored component_of
ontology edges.

Add a recomputable derived table:

jobber.d_archetype_demand

Suggested grain:

(archetype_concept_id, capability_concept_id)

Include enough information to explain the derivation, e.g.:

- roles_in_archetype
- roles_demanding_capability
- demand_rate
- required_count
- preferred_count
- contextual_count
- source_role_ids / trace
- vocabulary_version_id
- engine_version
- computed_at

Only capability-typed concepts participate in d_archetype_demand.

Atomic role requirements remain valid in role fit, but Phase 4's "capability
gap value" is specifically about capability gaps.

Do not invent an arbitrary hard "this archetype requires X" threshold when the
underlying counts can be shown directly.

Where a binary determination is needed for an algorithm, document the exact,
deterministic rule and expose the supporting counts.

============================================================
5. CONTROLLED MARKET DIMENSION
============================================================

Do not use an unconstrained free-text market string.

Add a small first-class controlled market dimension, e.g.:

jobber.market

Market represents the comparison context, not an intrinsic attribute of a
capability.

Use a simple practical shape such as:

- id UUID
- code unique
- label
- country / geography
- optional domain_concept_id -> active domain concept
- status active/deprecated
- notes
- created_at / updated_at

Period is NOT part of the market row. Period belongs on compensation
observations/aggregates.

Do not attempt a global geography ontology in this build.

The UI must always show the selected/named market. There is no invisible
"default market".

Do not compare or aggregate observations across different:

- markets;
- currencies;
- pay periods;
- employment bases where the distinction matters.

============================================================
6. COMPENSATION OBSERVATIONS
============================================================

Create the next additive migration after 0012.

Add:

jobber.compensation_observation

This is the durable source-evidence layer for market compensation facts.

Every observation must have provenance.

Support both:

A. captured posting compensation;
B. recruiter / market salary survey evidence.

Recommended fields, adapting to existing schema conventions after inspection:

Identity / subject:
- id UUID
- source_key unique/idempotent
- role_instance_id nullable
- archetype_concept_id nullable
- raw_role_label nullable
- market_id
- component
- pay_period
- employment_basis where known

Amounts:
- amount_min
- amount_mid nullable
- amount_max
- currency
- reported_p25 nullable
- reported_p50 nullable
- reported_p75 nullable
- bonus_pct nullable where appropriate

Evidence:
- basis:
    posting_stated
    posting_estimated
    survey
    curator_asserted
- review_status:
    unreviewed
    accepted
    rejected
- observed_at / period_start / period_end as appropriate
- document_id
- page_reference
- table_reference
- source_note
- reported_sample_size nullable
- source_quality / provenance quality if justified by existing conventions
- created_at / reviewed_at

Do not force values into fields the source did not provide.

Do not fabricate midpoint, percentile or sample size as if source-stated.

A deterministic range midpoint may be calculated in the derived layer and must
be labelled as such.

Do not convert day rates to annual salaries using an assumed number of working
days.

Keep daily vs annual pay periods separate.

============================================================
7. BACKFILL EXISTING POSTING COMPENSATION
============================================================

Build an idempotent service/script that projects existing role_instance salary
fields into compensation_observation.

Existing fields include:

- salary_min / salary_max
- salary_estimate_min / salary_estimate_max
- currency

Rules:

- salary_min/max -> basis=posting_stated
- salary_estimate_min/max -> basis=posting_estimated
- never combine the two into one observation;
- stated and estimated remain distinguishable forever;
- do not let estimated observations count toward the "5 stated observations"
  monetary-display threshold;
- source_key makes reruns idempotent;
- existing role_instance salary columns remain untouched for compatibility;
- this is a projection into the new evidence layer, not destructive migration.

Use posting_date or document/source date where available.

Market assignment must be explicit and deterministic.

Normalise obvious existing country aliases only through a documented mapping
(e.g. UK -> United Kingdom). Do not guess genuinely ambiguous multi-country rows.

If a market cannot be assigned safely, retain the compensation observation as
unassigned/unusable for aggregation rather than guessing.

============================================================
8. RECRUITER / MARKET SURVEY INGESTION
============================================================

Add a Market Data tab/section.

The user should be able to capture recruiter salary reports as raw market
evidence.

Reuse the existing immutable jobber.document approach.

Supported first version:

- paste raw text; and
- upload selectable-text PDF using the existing pypdf pattern.

No OCR.

Store the report as an immutable document before extracting anything.

Useful source metadata:

- publisher
- report title
- report date
- URL/source
- captured_at
- methodology notes
- page/table references where available

Add explicit on-demand AI extraction through the existing app.ai task layer.

Viewing a report must never call AI.

The AI extraction should produce draft/unreviewed compensation observations,
not accepted economic facts.

Extract, where actually present:

- raw role/practice label
- geography
- domain/practice area
- seniority / qualification / experience band
- permanent vs contract
- salary min/mid/max
- reported percentiles
- bonus
- total comp
- day rate
- currency
- pay period
- reported sample size
- page/table reference
- methodology/source note

Do not infer missing sample sizes or precise percentiles.

Do not create canonical role archetypes automatically.

If an extracted survey row cannot map confidently to an existing active
role_archetype, retain its raw_role_label and require curator assignment before
it enters an archetype aggregate.

Provide review controls:

- accept
- reject
- assign/change role archetype
- assign/change market
- correct extracted numeric fields

Only accepted observations may influence Phase 4 economics.

AI/provider failure must leave the source document and all existing accepted
observations untouched.

============================================================
9. ARCHETYPE COMPENSATION DERIVATION
============================================================

Add recomputable:

jobber.d_archetype_comp

Follow the current Phase 3 convention:
derived tables are disposable/rebuildable, single-profile architecture means
do not resurrect obsolete person_id columns.

Grain must keep economically incomparable observations apart.

At minimum key by:

- archetype_concept_id
- market_id
- period_start
- period_end
- currency
- component
- pay_period

Store transparent sample information such as:

- n_observations
- n_posting_stated
- n_posting_estimated
- n_survey_sources
- effective/reported sample size where defensible
- posting midpoint p25/p50/p75 or equivalent transparent aggregate
- engine_version
- computed_at

CRITICAL:

Do not blindly pool survey percentile tables with individual posting ranges.

They represent different statistical objects.

For posting observations:
- a deterministic advertised-range midpoint may be used for distribution
  summaries;
- label the result as "median advertised range midpoint", not "market median
  salary".

For survey observations:
- preserve the report's own p25/p50/p75/range/sample size;
- display them as survey benchmarks with their publisher/source.

Do not fabricate a pooled "market median" across heterogeneous reports.

============================================================
10. REFERENCE COMPENSATION RULE
============================================================

Phase 4 needs one transparent compensation reference when ranking gaps.

Implement one deterministic selection rule.

For a given archetype + market + period + currency + component/pay-period:

1. Prefer the most recent accepted survey benchmark that:
   - supplies an explicit p50/mid benchmark;
   - reports sample_size >= 5;
   - is in the exact selected market/currency/pay-period;
   - has valid source provenance.

2. If no qualifying survey benchmark exists, use the posting-derived benchmark
   only when there are at least 5 posting_stated observations in the exact
   archetype/market/currency/pay-period bucket.

3. Otherwise:
   - the archetype may still participate structurally;
   - show sample/source information;
   - expose NO monetary benchmark.

Never use posting_estimated observations to satisfy the 5-observation gate.

If multiple qualifying survey reports exist, use the most recent period_end;
break ties deterministically by reported sample size, then stable id.

Always expose which source/basis was selected.

============================================================
11. GAP VALUE DERIVATION
============================================================

Add recomputable:

jobber.d_gap_value

This is the answer to:

"If this capability gap became evidenced, what higher-value opportunities would
become structurally reachable?"

Current architecture is single-profile.

Do not add person_id merely because old docs/11 had one.

d_gap_value MUST be contextual.

Key it at least by:

- capability_concept_id
- market_id
- period_start
- period_end
- currency

Include:

- archetypes_unlocked
- archetypes_improved
- roles_unlocked
- roles_improved
- median/reference_comp_unlocked where qualifying
- comp_delta_vs_best_current_reachable where qualifying
- n_comp_observations / source sample
- evidence_quality
- rank
- trace
- engine_version
- computed_at

Use the current structural model, not embeddings, to determine unlocking.

Canonical simulation rule:

- Current role is structurally reachable when it has no blocking_gaps.
- For capability C:
    - a role is "unlocked by C" when its current blocking_gaps consist only
      of C;
    - a role is "improved by C" when C is one of multiple blocking gaps or
      when closing C otherwise improves its structural requirement status but
      does not make the role fully reachable.
- Aggregate distinct assigned archetypes from those roles.

Do not claim the user has acquired the capability. This is a counterfactual
economic calculation only.

"Best current" means:
the highest valid reference compensation among currently structurally reachable
archetypes in the selected market.

It does NOT mean the user's actual salary.

If no qualifying compensation reference exists:
- retain structural unlocked/improved counts;
- do not manufacture a currency delta.

Ranking must remain useful without money:
structural opportunity may be ordered by unlocked archetypes/roles and clearly
labelled "compensation evidence insufficient".

Do not introduce Phase 5 learning difficulty into the rank.

============================================================
12. CAPABILITY-AGREEMENT READINESS
============================================================

The original design specified capability agreement >= 0.80 before treating
Phase 4 outputs as validated.

Production currently has no eval_run data recording that gate.

Do not pretend it passed.

Add a small Phase 4 readiness indicator in the Economics UI:

- Capability agreement: Not yet evaluated
  OR display the latest real measured result if a compatible evaluation exists.
- Compensation sample sufficiency.
- Number of assigned archetypes.
- Number of accepted compensation observations.

This is informational, not a reason to disable the entire Economics page.

Economic outputs can be used experimentally, but the UI/docs must not call the
capability-fit model "validated" until the gate has actually been run.

Do not manufacture an evaluation result.

============================================================
13. ECONOMICS UI
============================================================

Add one top-level page:

Economics

Keep the page compact and evidence-first.

Recommended sections/tabs:

1. Gap Value
2. Archetypes
3. Market Data
4. Benchmarks / Readiness

Gap Value:

- market selector
- period selector
- currency selector where relevant
- ranked capability gaps
- current coverage status
- roles unlocked
- archetypes unlocked
- roles/archetypes improved
- qualifying compensation reference if one exists
- sample size/source basis
- delta vs best currently reachable benchmark if qualified
- clear "insufficient compensation evidence" state otherwise

Do not show false precision.

Round salary figures sensibly.

Never show a monetary delta without the sample/basis beside it or one click
away.

A row/card should expand into its trace:

Capability gap
→ affected roles
→ assigned archetypes
→ compensation benchmark
→ underlying posting/report sources

Archetypes:

- curation/assignment workflow described above
- sample counts by archetype
- compensation readiness per market

Market Data:

- source documents
- extraction/review workflow
- accepted/unreviewed/rejected observations

Benchmarks/Readiness:

- archetype compensation summaries
- survey evidence
- posting evidence
- Phase 4 readiness indicators

============================================================
14. ACCEPTED VOCABULARY OVERVIEW
============================================================

Add this to the existing Vocabulary page.

Keep it lightweight.

It should appear when viewing Accepted vocabulary and should not clutter the
Pending queue.

Add a compact "Accepted Vocabulary" overview panel showing:

- total active concepts;
- count by concept type;
- concepts missing a definition;
- concepts with no active Concept Dossier;
- concepts whose active dossier has no related concepts;
- aliases count;
- recently accepted/reviewed concepts (e.g. latest 10).

Make the counts useful:

- clicking a type count filters accepted vocabulary to that type if practical;
- clicking "missing definition" or "no dossier" should narrow/search the
  accepted list if this can be done without large UI work.

No AI call is required.

Do NOT build a large ontology dashboard.

Do NOT add speculative semantic-overlap scoring in this build.

The Details drawer remains the maintenance surface for each individual concept.

============================================================
15. DERIVED DATA / REBUILD DISCIPLINE
============================================================

Follow the Phase 3 engine pattern.

Facts:
- compensation_observation
- market
- archetype assignments
- accepted source evidence

Derived/disposable:
- d_archetype_demand
- d_archetype_comp
- d_gap_value

Every d_* output must carry:
- engine_version
- computed_at
- vocabulary_version_id where appropriate.

Provide explicit rebuild functions/endpoints/scripts.

GET endpoints must not silently rebuild derived tables.

A rebuild should be deterministic and safe to repeat.

Changing:
- archetype assignment;
- accepted compensation observation;
- relevant vocabulary/role requirement mapping

should make stale derived data detectable or be followed by an explicit rebuild
path, rather than silently presenting known-stale results as current.

============================================================
16. PROVENANCE / HONESTY RULES
============================================================

These are hard requirements.

- Every economic fact has a source.
- Market is always named.
- Currency is never silently converted.
- No FX conversion in this phase.
- Annual and daily compensation never mix.
- Estimated posting salary never becomes stated salary.
- Survey rows never masquerade as individual salary observations.
- Missing sample size remains missing.
- No monetary ranking below the evidence threshold.
- "No evidence found" remains the capability wording; never "you lack X".
- d_gap_value is a counterfactual, not evidence about the person.
- profile360 remains authoritative person-side.
- AI can extract/suggest market facts but cannot silently accept them.
- Raw report documents remain immutable.

============================================================
17. MIGRATIONS / SECURITY
============================================================

Use the next numbered additive migration(s) after 0012.

Prefer one coherent Phase 4 migration unless a genuine dependency makes two
files clearer.

Do not modify prior migration files.

Update the disposable local baseline/test setup consistently.

All new application routes must remain behind the existing authentication
boundary.

Do not add public endpoints.

Do not weaken RLS/security posture.

Do not apply the manual profile360 RLS migration.

============================================================
18. TESTING
============================================================

Add comprehensive backend tests.

At minimum cover:

ROLE REQUIREMENT FALLBACK
- requirement_claim authoritative when present
- role_skill_observation fallback
- no double counting
- unmapped observations ignored

ARCHETYPES
- create archetype
- assign title group
- reassign role
- invalid non-role_archetype assignment rejected
- deprecation behaviour

COMPENSATION
- posting stated backfill
- posting estimated backfill
- idempotent rerun
- no currency mixing
- no market mixing
- no pay-period mixing
- ambiguous market stays unassigned
- survey extraction creates unreviewed observations
- rejected survey rows excluded
- accepted survey rows included
- sample size remains null if source omitted it

BENCHMARKS
- <5 stated postings => no posting monetary reference
- >=5 stated postings => posting reference available
- qualifying survey p50/sample>=5 takes precedence
- survey sample<5 does not qualify
- estimated salary cannot satisfy threshold
- survey/posting evidence never falsely pooled

GAP VALUE
- one blocking capability unlocks role
- one of multiple gaps only improves role
- distinct archetype aggregation
- unassigned roles handled safely
- best-current-reachable benchmark rule
- no monetary delta without qualified evidence
- markets/currencies isolated
- counterfactual rebuild deterministic

VOCAB OVERVIEW
- counts by type
- missing definition
- no dossier
- no related concepts
- recent concepts
- only active/accepted concepts counted as intended

REGRESSION
- complete existing backend test suite remains green.

Run:

backend:
- full pytest suite

frontend:
- tsc -b
- oxlint
- npm run build

Also run a real browser smoke flow if the existing environment supports it.

============================================================
19. SMOKE FLOW
============================================================

Use a disposable Postgres database.

Suggested browser flow:

1. login;
2. Vocabulary -> Accepted;
3. verify Accepted Vocabulary overview renders;
4. open Economics;
5. create a role archetype from a test title group;
6. assign roles;
7. verify compensation observations/backfill;
8. verify a bucket below threshold shows no monetary figure;
9. seed enough test observations to cross threshold;
10. rebuild economics;
11. verify benchmark appears with sample count;
12. verify a gap-value row shows its trace;
13. ingest a synthetic market-report text source;
14. run mocked/stubbed extraction if live AI unavailable;
15. review/accept one survey observation;
16. rebuild and verify survey benchmark selection;
17. logout.

No real production rows should be modified by this smoke flow.

============================================================
20. DOCUMENTATION
============================================================

Add a Phase 4 implementation document.

Record clearly:

- actual schema built;
- deviations from docs/11;
- why jobber.episode_compensation was NOT built;
- role_skill_observation fallback needed for the historical corpus;
- market dimension design;
- posting vs survey statistical treatment;
- exact benchmark-selection rule;
- exact 5-observation gate;
- d_gap_value algorithm;
- capability-agreement gate status;
- rebuild semantics;
- known limitations.

Update README so "Deliberately out of scope" moves Phase 4 to implemented and
leaves Phase 5 deferred.

Do not claim actual salary-history tracking is implemented.

============================================================
21. COMPLETION STANDARD
============================================================

Do not call the build complete merely because the schema exists.

Phase 4 implementation is complete when:

- role archetype curation/assignment works;
- legacy/current role requirement evidence feeds structural fit correctly;
- posting compensation can be projected idempotently;
- market-report evidence can be captured/reviewed;
- d_archetype_demand rebuilds;
- d_archetype_comp rebuilds;
- d_gap_value rebuilds;
- evidence thresholds suppress unsupported monetary figures;
- Economics UI exposes the trace and sample basis;
- Accepted Vocabulary overview works;
- full backend suite passes;
- tsc/oxlint/build pass;
- browser smoke passes or any environment limitation is explicitly reported.

The initial production Economics page is expected to be sparse because production
currently has zero curated archetypes and only ~23 stated-salary postings.

That is not a bug.

Do not seed or auto-accept production archetypes merely to make the page look
populated.

The first post-deploy operator task will be curator assignment of a useful set
of archetypes and ingestion of representative salary reports.

At completion report:

- branch name;
- commit SHA;
- migration filename(s);
- files changed;
- exact backend test result;
- frontend typecheck/lint/build result;
- browser smoke result;
- resulting API/pages;
- documented design deviations;
- any remaining limitations.

Do not merge automatically.
