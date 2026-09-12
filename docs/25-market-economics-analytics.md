# 25 — Market Economics Analytics: Full Use of Accepted Compensation Evidence

**Status:** implementation prompt
**Sequence:** follows `docs/24-phase4-economics-and-vocabulary-overview.md`
**Scope:** additive Phase 4 refinement; no Economics redesign and no replacement of the existing archetype / Gap Value machinery.

---

## 0. Objective

The current Economics implementation captures substantially more compensation evidence than it exposes analytically.

Today:

- accepted market/survey observations can exist without an archetype assignment;
- those observations are visible under **Economics → Market Data**;
- only observations tied to role archetypes currently feed the derived archetype-compensation layer;
- as a result, useful evidence such as PQE salary curves, practice-area comparisons, total-package figures, recruiter salary ranges, and eventually historical trends is under-used.

Implement a **Market Summary / market analytics layer** that uses **all accepted market compensation evidence**, whether or not an observation is mapped to an archetype.

Do **not** replace or weaken the existing archetype economics model. The intended relationship is:

> accepted market evidence → market analytics
> accepted archetype-linked evidence → archetype benchmarks → Gap Value

Market analytics and archetype economics are two consumers of the same evidence layer.

The user explicitly does **not** want a large UI restructuring at this stage. Add the new analytical capability inside the existing Economics / Market Data experience and keep the existing top-level tab structure intact.

---

## 1. Current baseline and constraints

Start from the latest `main`. Do not assume a historical SHA.

Relevant current code includes, at minimum:

- `backend/app/routes/market_data.py`
- `backend/app/market_data_processing.py`
- `backend/app/economics_engine.py`
- `backend/app/models.py`
- `frontend/src/pages/Economics.tsx`
- `frontend/src/lib/api.ts`
- `prompts/extract_market_survey.md`
- `docs/24-phase4-economics-and-vocabulary-overview.md`

The current durable evidence table is `jobber.compensation_observation`.

It already carries the fields needed for this work, including:

- `market_id`
- `archetype_concept_id`
- `role_instance_id`
- `raw_role_label`
- `component`
- `pay_period`
- `employment_basis`
- `amount_min`
- `amount_mid`
- `amount_max`
- `currency`
- `reported_p25`
- `reported_p50`
- `reported_p75`
- `reported_mean`
- `bonus_pct`
- `reported_sample_size`
- `basis`
- `review_status`
- `document_id`
- `page_reference`
- `table_reference`
- `source_note`
- `source_quality`
- `source_kind`
- `geography_reported`
- `domain_or_practice_area`
- `seniority_band_reported`
- `experience_band`
- `pqe_band`
- `observed_at`
- `period_end`

Market survey documents are `jobber.document(kind='market_survey')`.

Only `review_status='accepted'` observations may influence market analytics.

The existing three initial reports provide a useful acceptance corpus:

- **Acumen Resources — Actuarial Salary & Industry Insights 2025**
  - accepted PQE-based salary evidence;
  - reported medians;
  - salary ranges on most rows;
  - explicit row-level sample sizes on the core qualified-actuary rows.
- **Raretec Recruitment — Raretec Salary Survey 2026**
  - accepted Life / Non-Life / Pensions evidence;
  - PQE and total-experience bands;
  - reported mean, median and range;
  - base and total-package components;
  - row-level sample size not published.
- **Cavehill Consulting Group — UK & Ireland Insurance Markets H1 2026**
  - accepted named-role recruiter benchmark ranges;
  - Life, General Insurance and Reinsurance / London Market context;
  - range-based evidence;
  - no reported median/mean and no row-level sample size.

At the time this prompt was written, production held 39 accepted observations from these 3 reports. Do not hardcode those counts into application logic or automated tests; treat them only as a production smoke-check expectation.

Most importantly: **archetype assignment must not be required for inclusion in market analytics**.

---

## 2. Non-goals

Do not turn this into a broad Economics redesign.

Specifically, do **not**:

1. remove or rename the existing Economics top-level tabs;
2. move Archetypes or Gap Value elsewhere;
3. redesign the whole navigation;
4. auto-create or auto-assign role archetypes;
5. change the existing `d_archetype_comp` reference-selection rules;
6. change Gap Value semantics;
7. pool recruiter ranges, survey medians and posting salary midpoints into one synthetic "market salary";
8. claim statistical validation or market representativeness;
9. infer row-level sample sizes;
10. infer missing medians or percentiles;
11. annualise day rates;
12. convert currencies;
13. silently combine base pay and total package;
14. silently combine different geographies;
15. silently harmonise non-identical PQE or experience bands;
16. manufacture a time trend from incomparable data;
17. run AI automatically when viewing market data;
18. require AI extraction for already curated/accepted observations.

This should be an **additive analytical read layer over accepted evidence**.

---

## 3. Core design principle: preserve evidence types

The central rule for this implementation is:

> Display what the source actually reported. Do not coerce unlike statistics into a common statistic merely to make a chart easier.

Examples:

- an explicit median is a median;
- an explicit mean is a mean;
- a min/max range is a range;
- a derived range midpoint, if used for ordering or visual positioning, must be explicitly labelled `derived_range_midpoint`;
- a Cavehill range must never be labelled a median;
- Raretec mean and median must remain separately available;
- Acumen row-level `n` must remain visible where published;
- an unpublished `n` must remain unknown.

The new market summary is descriptive market evidence, not a consensus engine.

A later phase may build carefully defined multi-provider consensus. This prompt does not.

---

## 4. Add a market-analytics read model

Prefer a new backend module, for example:

`backend/app/market_analytics.py`

Keep analytics/transformation logic out of the FastAPI route where practical so it can be unit-tested as pure/deterministic logic.

Expose a read endpoint under the existing market-data API, for example:

`GET /api/market-data/analytics/summary`

A different path is acceptable if it is consistent with existing routing, but keep market analytics logically under market data rather than creating a new unrelated subsystem.

### 4.1 Server-side filters

The endpoint should accept optional filters for the dimensions that genuinely exist in the evidence:

- `market_id`
- `currency`
- `component`
- `pay_period`
- `practice_group`
- `provider`
- `period_from`
- `period_to`

If useful and clean, also support:

- `source_kind`
- `employment_basis`

Do not make archetype a required filter.

All filtering must happen against accepted evidence only.

### 4.2 Response shape

Return a purpose-built analytical payload rather than forcing the UI to reverse-engineer every concept from raw database rows.

The exact Pydantic/TypeScript shape may vary, but it should contain the following logical sections.

#### `coverage`

At minimum:

- accepted observation count;
- distinct source-document count;
- distinct provider count;
- earliest observation/report period represented;
- latest observation/report period represented;
- count with reported median;
- count with reported mean;
- count with range;
- count with explicit row-level sample size;
- count mapped to an archetype;
- count not mapped to an archetype.

This last pair is informational only. Unmapped evidence remains first-class market evidence.

#### `facets`

Return filter values with stable labels/counts where useful:

- markets/geographies;
- currencies;
- components;
- pay periods;
- practice groups;
- providers;
- source kinds;
- available periods/years.

Do not populate filters from rejected/unreviewed rows.

#### `pqe_series`

Rows suitable for plotting PQE-based progression.

Each item should preserve enough provenance to tell the user what the point means, including:

- provider;
- document/report;
- report/source date;
- market/geography;
- raw reported practice label;
- analytical practice group;
- raw `pqe_band`;
- component;
- pay period;
- currency;
- `reported_p50`;
- `reported_mean`;
- `amount_min`;
- `amount_max`;
- row-level sample size;
- source kind;
- source quality;
- observation/document IDs.

Do not require an archetype ID.

Do not interpolate missing PQE bands.

If a chart needs band ordering, implement deterministic display ordering by the numeric lower bound where parseable while preserving the raw label. Unknown/unparseable bands should remain visible rather than being discarded.

#### `experience_series`

Equivalent to `pqe_series`, keyed by `experience_band`.

PQE and total experience are distinct concepts. Never merge them into one axis.

#### `practice_comparisons`

Support comparisons only where the source evidence has a meaningful common context.

For example, Raretec rows can compare Life vs Non-Life vs Pensions when they share:

- one document/provider;
- one component;
- one pay period;
- one currency;
- the same reported PQE or experience band type/value.

Represent these as comparison sets rather than taking a median across providers.

A comparison-set item should identify its common context and then contain the practices and their reported statistics.

Do not treat "All practice areas" as a specific practice.

#### `component_comparisons`

Expose base-vs-total-package evidence only where there are genuinely comparable rows from the same source and same segmentation.

For example, Raretec can support Life / Non-Life / Pensions comparisons between base and total package for the same experience band.

Do not compare day rate to annual salary as though they were components of the same annual figure.

#### `role_ranges`

Expose named-role salary-range evidence, especially recruiter benchmarks such as Cavehill.

Each range row should retain:

- raw role label;
- provider/document/date;
- geography/market;
- reported and analytical practice;
- seniority/experience/PQE context;
- min/max;
- any explicit central statistic if present;
- component/pay period/currency;
- source kind/quality;
- provenance.

A range midpoint may be calculated for sorting/positioning, but if returned it must be named and labelled `derived_range_midpoint`. It must never overwrite `reported_p50` or be described as a median.

#### `trends`

Build the machinery now but be conservative.

A trend series should be emitted only when there are at least **two distinct comparable periods** for the same analytical key.

Comparability must be strict. At minimum the trend key should hold constant:

- provider, unless there is an explicitly designed cross-provider comparison;
- market/geography;
- currency;
- component;
- pay period;
- analytical practice group;
- band type (`pqe` or `experience`);
- exact raw band value;
- statistic type (median vs mean vs range bounds).

Do not silently equate, for example:

- `9–12 PQE` with `11–15 PQE`;
- `Life` with `All practice areas`;
- base with total package;
- Ireland with Dublin;
- mean with median.

With the current corpus many trend series may correctly be unavailable. The UI should say that more comparable history is needed rather than draw a misleading line.

This design should make historical backfill immediately useful when older surveys are added later.

#### `evidence_rows`

Return a compact evidence table for the current filters.

It should contain the provenance/statistic fields necessary to inspect what the charts are based on.

If pagination is needed, add it cleanly. Do not let an unbounded raw-table response become a future scalability problem.

---

## 5. Practice-area analytical grouping

The live source evidence contains semantically related but non-identical labels, including examples such as:

- `Life`
- `Life Insurance`
- `Non-Life`
- `General Insurance`
- `Pensions`
- `Reinsurance & London Market`
- `All practice areas`

We want useful analytical grouping without rewriting the evidence.

Implement a small deterministic **analytical grouping function** in the read layer.

Suggested initial groups:

- `Life`
  - `Life`
  - `Life Insurance`
- `Non-Life / GI`
  - `Non-Life`
  - `General Insurance`
- `Pensions`
  - `Pensions`
- `Reinsurance / London Market`
  - `Reinsurance & London Market`
- `All practice areas`
  - `All practice areas`
- `Unspecified`
  - null/blank

For any other non-empty practice label, preserve it as its own analytical group rather than forcing it into one of the above buckets.

Requirements:

- preserve `domain_or_practice_area` unchanged in storage;
- return both `practice_reported` and `practice_group`;
- centralise the mapping in one tested function;
- do not mutate historical observations;
- do not create a new vocabulary concept solely for this;
- do not treat this grouping as ontology truth.

This is a presentation/analysis normalisation only.

---

## 6. UI: add a Market Summary section, not another top-level tab

Keep the current Economics top-level tabs.

Inside the existing **Market Data** tab, add a new **Market summary** analytical section above the operational upload/review/source-document controls.

The user should be able to get value from the summary without understanding extraction runs or archetype mappings.

Prefer extracting the new UI into a focused component rather than making `Economics.tsx` substantially more monolithic, for example:

`frontend/src/components/economics/MarketSummary.tsx`

or a similarly consistent path.

### 6.1 Filter bar

Provide a compact filter row based on returned facets.

At minimum:

- market/geography;
- currency;
- component;
- practice group;
- provider;
- period/year.

Use "All" defaults where safe.

Filters should affect all summary cards/charts/table consistently.

Avoid creating separate navigation tabs for every dimension.

### 6.2 Headline coverage

Show a restrained summary such as:

- accepted observations;
- source reports/providers;
- date range;
- median / mean / range coverage;
- explicit sample-size coverage.

Also show, less prominently:

- archetype-linked rows;
- non-archetype rows.

The purpose is to make it obvious that unarchetyped evidence is still contributing to market analytics.

### 6.3 Visualisations

Implement the following first-pass views.

#### A. Compensation by PQE

Use rows with `pqe_band`.

Primary display should favour **reported median** where available.

Allow reported mean to remain visible distinctly, for example via:

- a toggle;
- a second clearly labelled series;
- or a detail/table treatment.

Do not substitute range midpoints for missing medians.

Where multiple providers are shown, make provider/report identity clear.

Do not smooth/interpolate between bands.

#### B. Compensation by total experience

Equivalent to PQE using `experience_band`.

Keep it a separate chart/section because PQE and total experience are not interchangeable.

#### C. Practice comparison

Present valid same-context practice comparison sets, e.g. Life vs Non-Life / GI vs Pensions within one report/band/component.

Do not average unlike reports into one bar.

The current filter/context should make clear which provider/report/band the comparison represents.

#### D. Base vs total package

Where the source offers comparable base and total-package rows, show both distinctly.

Do not derive total compensation where it was not reported.

Do not compare total package to day rate.

#### E. Named-role salary ranges

Show range-based recruiter evidence such as Cavehill as a range view or range-oriented table/chart.

The source range should be visually primary.

Do not present a derived midpoint as a reported median.

#### F. Trends over time

Include a trend section that works automatically once at least two comparable periods exist.

If there is insufficient comparable history, render an informative empty state such as:

> No like-for-like historical series yet. Trends will appear as comparable historical reports are backfilled.

Do not force a trend from the current sparse corpus.

### 6.4 Evidence table

Include a table beneath or near the charts showing the accepted observations behind the current filters.

Useful columns include:

- provider;
- report;
- report/period date;
- market/geography;
- practice reported;
- analytical practice group;
- role label;
- PQE/experience;
- component;
- median;
- mean;
- range;
- sample `n`;
- source kind;
- archetype mapping status.

Make it possible to understand a plotted point/range by finding the underlying evidence.

---

## 7. Chart implementation

The frontend currently has no conventional 2D charting library.

It is acceptable to add a focused, lightweight React chart dependency such as **Recharts** if this materially reduces custom SVG/chart code.

If adding a dependency:

- keep it limited to the market summary;
- update `package.json` / lockfile normally;
- do not use the existing Three.js stack for ordinary 2D salary charts;
- ensure `npm run lint` and `npm run build` remain clean.

Charts must:

- be responsive;
- have readable axis/tooltip labels;
- preserve currency/component/statistic context;
- not imply precision the source did not provide;
- expose provider/report provenance in tooltips or adjacent text;
- have a tabular evidence fallback nearby.

Avoid decorative chart complexity. The goal is analytical clarity.

---

## 8. Important statistical guardrails

These are acceptance requirements, not optional commentary.

### 8.1 No synthetic cross-provider market median

Do not compute something like:

> median(Acumen median, Raretec median, Cavehill midpoint)

and call it the market median.

Providers differ in methodology, segmentation and population.

Keep provider/source evidence separate unless a later explicit consensus design defines how to combine them.

### 8.2 Do not manufacture distributions

A source that reports min, max and median does not give us the underlying salary distribution.

Do not create:

- histograms;
- violin plots;
- density curves;
- inferred standard deviations;
- fake quartiles.

Only plot distributional statistics that were actually reported.

### 8.3 Sample size remains evidence, not weighting by default

Display explicit row-level `n` where available.

Do not automatically weight one provider's median by `n` against another provider unless a future method explicitly defines that model.

Unknown `n` remains unknown.

Known small samples retain their existing quality semantics.

### 8.4 Preserve component/pay-period boundaries

Never silently combine:

- base salary;
- total package;
- day rate;
- bonus percentage.

Never annualise daily rates in this phase.

### 8.5 Preserve geography/currency boundaries

Never silently combine:

- Ireland-wide and Dublin-specific evidence;
- EUR with another currency.

No FX conversion in this phase.

### 8.6 Preserve reported vs derived values

Any downstream range midpoint must remain transparently derived and must not overwrite reported fields.

---

## 9. Adjacent UX fix: AI extraction button state

The existing initial reports were manually curated into accepted observations and therefore can contain useful accepted data without an AI extraction run.

The current document detail logic uses `extraction_runs.length` to choose between:

- `Run AI extraction`
- `Re-run extraction`

This makes curated reports look unfinished.

Fix this state logic.

Suggested behaviour:

### Document has accepted/curated observations but no AI extraction run

Show a clear status such as:

> 15 accepted observations loaded

Make the action secondary and label it:

> Run AI extraction (optional)

The user should not infer that extraction is required before the report contributes to analytics.

### Document has no observations and no extraction run

`Run AI extraction` can remain the primary next action.

### Document has an extraction run

Use `Re-run AI extraction` or equivalent, with the existing run status visible.

Do not delete or modify existing accepted observations merely because AI extraction is run or re-run.

---

## 10. API / TypeScript integration

Add explicit TypeScript types for the analytics response rather than using `unknown` blobs.

Add corresponding API methods in `frontend/src/lib/api.ts`.

Keep UUIDs as strings, consistent with the rest of the application.

Prefer meaningful types such as:

- `MarketAnalyticsCoverage`
- `MarketAnalyticsFacets`
- `MarketAnalyticsPoint`
- `MarketPracticeComparison`
- `MarketComponentComparison`
- `MarketRoleRange`
- `MarketTrendSeries`
- `MarketAnalyticsSummary`

Names may differ, but keep the contract explicit.

---

## 11. Testing requirements

### 11.1 Backend unit/API tests

Add focused tests for the new analytics behaviour.

At minimum cover:

1. **Accepted-only rule**
   - accepted survey rows included;
   - unreviewed/rejected rows excluded.

2. **No archetype requirement**
   - an accepted observation with `archetype_concept_id = NULL` appears in analytics.

3. **Practice grouping**
   - `Life` and `Life Insurance` analyse as `Life`;
   - `Non-Life` and `General Insurance` analyse as `Non-Life / GI`;
   - raw labels remain intact;
   - `All practice areas` remains distinct.

4. **Statistics remain distinct**
   - reported median stays median;
   - reported mean stays mean;
   - a range-only row does not acquire a reported median.

5. **Range-only recruiter evidence**
   - appears in `role_ranges`;
   - does not contaminate median series.

6. **PQE vs experience separation**
   - PQE rows do not silently appear as experience rows and vice versa.

7. **Component separation**
   - base and total package remain separate;
   - day rate remains separate.

8. **Geography/currency filtering**
   - filters do not mix markets/currencies.

9. **Comparison-set integrity**
   - practice comparisons only combine rows sharing a genuinely common source/context.

10. **Trend strictness**
    - one period → no trend series;
    - two exact like-for-like periods → trend series;
    - mismatched band/component/geography/statistic → no false trend.

11. **Source counts**
    - count distinct documents/providers correctly rather than counting observation rows as independent reports.

12. **Extraction-button support data**
    - document detail still exposes observations and extraction runs correctly for curated-without-AI-run documents.

Use test fixtures; do not make tests depend on the live production row count.

### 11.2 Existing regression suite

Run the complete backend suite.

The implementation must not change the semantics of existing:

- market-data ingestion/review;
- archetype compensation derivation;
- posting compensation backfill;
- Gap Value;
- capability-demand derivation.

### 11.3 Frontend verification

Run:

- `npm run lint`
- `npm run build`

If a frontend test framework already exists by implementation time, add focused tests. Do not introduce a large new test framework solely for this change unless clearly justified.

---

## 12. Production smoke checks

If the coding environment has safe read access to the production Supabase data after deployment, verify the following without hardcoding them into application logic:

1. the market summary includes accepted evidence from all three existing reports;
2. non-archetyped Acumen and Raretec evidence appears in market analytics;
3. Raretec exposes:
   - median and mean distinctly;
   - PQE and experience views;
   - base and total-package comparisons;
4. Cavehill appears as named-role range evidence without being relabelled as median evidence;
5. Acumen exposes PQE median/range/sample-size context;
6. existing archetype compensation summaries remain unchanged by the analytics work;
7. no AI extraction is required for already accepted curated observations to appear in the summary.

At the time of writing, the existing derived Dublin archetype references were:

- Life Actuarial Manager — EUR 110,000;
- Life Senior Manager / Lead Actuary — EUR 144,500;
- Reinsurance / London Market Pricing Actuary — EUR 131,500.

These are useful regression smoke checks only. Do not encode them as permanent application constants.

---

## 13. Persistence / migration expectation

This feature should be implementable as a read/analytics layer over existing durable evidence.

**No new database migration is expected.**

Do not create persisted derived-market-summary tables merely for chart rendering.

If a migration turns out to be genuinely necessary, stop and justify why the existing `compensation_observation` + `document` + `market` structure cannot support the requirement. Any migration must be additive.

Do not rewrite prior migrations.

---

## 14. Performance / future growth

The current corpus is small, but design the read path so historical backfill does not immediately break it.

Requirements:

- filter in SQL where sensible;
- avoid N+1 document/market lookups;
- do not return an accidentally unbounded evidence table forever;
- if raw evidence rows are returned, support a sensible cap/pagination;
- derive chart series deterministically from the filtered evidence set;
- keep analytical grouping code isolated and testable.

Do not prematurely build a warehouse, cube or general BI engine.

---

## 15. UX wording

Use wording that distinguishes:

- **evidence** from **benchmark**;
- **reported** from **derived**;
- **market analytics** from **archetype economics**;
- **coverage** from **statistical validity**.

Good examples:

- `Reported median`
- `Reported mean`
- `Reported range`
- `Sample n not published`
- `Recruiter benchmark`
- `Respondent survey`
- `Derived range midpoint`
- `No like-for-like historical series yet`

Avoid:

- `Market median` unless the source actually reports that statistic for that market slice;
- `validated`;
- `representative`;
- `consensus`;
- `average market salary` when the displayed number is not actually an average over a defined market sample.

---

## 16. Definition of done

This work is complete when:

1. **all accepted survey/market observations can contribute to market analytics regardless of archetype mapping;**
2. the Market Data tab has a useful Market Summary section;
3. filters work across the summary;
4. PQE and experience progression can be inspected;
5. same-context practice comparisons are visible;
6. base-vs-total-package evidence is visible where reported;
7. named-role range evidence is visible;
8. trend infrastructure exists but refuses to draw misleading trends from insufficient/incomparable history;
9. an evidence table makes chart provenance inspectable;
10. raw reported labels/statistics remain preserved;
11. no synthetic cross-provider consensus is introduced;
12. the AI extraction button no longer makes already-curated reports look incomplete;
13. existing archetype benchmarks and Gap Value behaviour remain unchanged;
14. no unnecessary schema migration is introduced;
15. full backend tests pass;
16. frontend lint/build pass;
17. implementation documentation is updated to record the new market-analytics capability and its statistical limitations.

---

## 17. Implementation posture

Be conservative with inference and ambitious with visibility.

The database already contains useful market evidence. This task is primarily about allowing the application to **show and interrogate that evidence honestly**, rather than forcing every observation through an archetype before it becomes useful.

Do not solve future questions that the current data cannot yet support.

Build the analytical surface so that richer conclusions — especially provider-specific historical trends — appear naturally as more accepted historical market reports are backfilled.
