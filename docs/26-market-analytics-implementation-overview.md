# 26 — Market Analytics Implementation Overview

**Status:** implemented, tested, documented.
**Analytical readiness gate (capability-agreement ≥ 0.80) — still not attempted, still not passed** (unchanged from docs/24 §12 — this build adds no capability-model dependency and does not touch that gate either way).
**Related:** `docs/25-market-economics-analytics.md` (the verbatim brief this document implements), `docs/24-phase4-economics-and-vocabulary-overview.md` (the Phase 4 economics/Gap Value build this is additive to, unchanged in its own status), `docs/23-phase4-implementation-prompt.md`.

---

## 0. What this adds

> Every accepted market/survey observation is now first-class market evidence, whether or not a curator has mapped it to a role archetype.

Before this build, an accepted `compensation_observation` row only became analytically useful once assigned to a role archetype (feeding `d_archetype_comp`/`d_gap_value`). This left PQE salary curves, practice comparisons, base-vs-total-package evidence, and named-role recruiter ranges effectively invisible for any row still awaiting archetype curation — which, per docs/24 §14, is most of the current corpus.

This build adds a second, independent read layer over the same evidence:

> accepted market evidence → market analytics (new, this build)
> accepted archetype-linked evidence → archetype benchmarks → Gap Value (unchanged, docs/24)

Nothing about the archetype-benchmark/Gap Value path changed. `economics_engine.py`, `d_archetype_comp`, `d_gap_value`, and the reference-compensation rule (docs/24 §9) are untouched.

---

## 1. Backend: `app/market_analytics.py`

A new module, deliberately structured as pure functions over an already-fetched row list (only `fetch_accepted_evidence_rows` touches the database), so every analytical rule is unit-testable without Postgres — mirroring `economics_engine.py`'s "pure function of accepted evidence" convention.

- `fetch_accepted_evidence_rows(cur)` — one query joining `compensation_observation` (`review_status = 'accepted'` only, no archetype filter) against `market` and `document`, capped at a defensive `LIMIT 20000`. Numeric columns are cast to `float`/left `None`; UUID columns stringified; `document_title` falls back to the document's own `source_payload.report_title` when `document.title` is null (same fallback the Market Data UI already used). Each row carries a derived `practice_group` and `period_key` (`period_end` or `observed_at`).
- `apply_filters(rows, MarketAnalyticsFilters)` — `market_id`, `currency`, `component`, `pay_period`, `practice_group`, `provider`, `source_kind`, `employment_basis`, `period_from`/`period_to`. No archetype filter exists, by design (docs/25 §4.1: "Do not make archetype a required filter" — there is no path to filter unmapped evidence *out*).
- `build_facets(rows)` — computed from **every** accepted row, never the currently-filtered subset, so the filter bar's own options stay stable as the user narrows their view (docs/25 §4.2 "stable labels/counts").
- `build_coverage(rows)` — computed from the filtered subset: accepted count, distinct document/provider counts, earliest/latest period, median/mean/range/sample-size coverage counts, and the archetype-linked/not-linked split.
- `build_pqe_series` / `build_experience_series` — one point per row carrying that band (never aggregated, never interpolated), sorted by `_band_sort_key` (numeric lower bound where parseable, unparseable bands sorted after but never dropped).
- `build_practice_comparisons` — groups by `(document_id, component, pay_period, currency, band_type, band)`; a comparison set is only emitted when ≥2 distinct practice groups remain after excluding `"All practice areas"`/`"Unspecified"`.
- `build_component_comparisons` — groups by `(document_id, practice_group, pay_period, currency, band_type, band)` restricted to `component ∈ {base, total_package}`; emitted only when both are present. `day_rate` never enters this section.
- `build_role_ranges` — any row with `amount_min`/`amount_max`; computes `derived_range_midpoint` for sorting only, always kept separate from `reported_p50`.
- `build_trends` — groups by the full strict key (`provider, market_id, geography_reported, currency, component, pay_period, practice_group, band_type, band`), and emits up to three independent series per key (`median`, `mean`, `range`), each only when ≥2 distinct periods have that statistic. Median and mean are never merged into one series.
- `build_evidence_rows(rows, limit, offset)` — paginated (`limit` capped at 500), preserving the same most-recent-period-first ordering as the base fetch.
- `build_market_analytics_summary(cur, filters, evidence_limit, evidence_offset)` — the orchestrator wired to the route.

### Practice-area grouping (docs/25 §5)

`group_practice_area(raw)` is the one centralised, tested mapping: `Life`/`Life Insurance` → `Life`; `Non-Life`/`General Insurance` → `Non-Life / GI`; `Pensions` → `Pensions`; `Reinsurance & London Market` → `Reinsurance / London Market`; `All practice areas` stays its own group; null/blank → `Unspecified`; any other label is preserved verbatim as its own group. `domain_or_practice_area` is never mutated in storage — every response carries both `practice_reported` (raw) and `practice_group` (derived) side by side.

### Route

`GET /api/market-data/analytics/summary` (`routes/market_data.py`), behind the same `require_auth` dependency as every other route. Query params map 1:1 onto `MarketAnalyticsFilters`; `period_from`/`period_to` are validated as genuine ISO 8601 dates (422 on a malformed value, same posture as `models._validate_iso_date`) rather than surfacing an opaque error. No new router — this lives under the existing `market_data` router, per docs/25 §4's "keep market analytics logically under market data."

---

## 2. Frontend

- `frontend/src/components/economics/MarketSummary.tsx` — the new component, mounted at the top of the existing **Market Data** tab in `Economics.tsx`, above the unchanged operational upload/review/source-document controls. The four existing Economics top-level tabs (Gap Value / Archetypes / Market Data / Benchmarks-Readiness) are untouched.
  - A compact filter bar (market, currency, component, practice, provider, year) built from `facets`, all defaulting to "All".
  - Headline coverage cards, including the archetype-linked/not-linked split so it's visible that unmapped evidence is contributing.
  - **Compensation by PQE** / **Compensation by total experience** — grouped bar charts (Recharts), one bar series per provider, with a median/mean toggle. A section falls back to its evidence table alone (no chart) when the filtered rows span more than one currency or pay period, rather than drawing an axis that can't honestly represent both.
  - **Practice comparison** / **Base vs total package** — one card per comparison set from the backend, each showing a small chart when every item in the set has a reported median, otherwise a plain table (never a fabricated bar from a missing statistic).
  - **Named-role salary ranges** — a table with a lightweight CSS range bar (position/width only) per row; hovering shows the `derived_range_midpoint`, which is never shown as a median.
  - **Trends over time** — one small line chart per returned series, or the exact empty-state copy from docs/25 §6.3.F when none qualify.
  - An **evidence table** at the bottom (paginated, 25/page) covering every row behind the current filters.
- `frontend/src/lib/money.ts` — `formatMoney` extracted out of `Economics.tsx` (previously page-local) so both the page and the new component share it without an oxlint `react/only-export-components` fast-refresh warning.
- `frontend/src/lib/api.ts` — `MarketAnalyticsSummary` and its constituent types (`MarketAnalyticsCoverage`, `MarketAnalyticsFacets`, `MarketAnalyticsPoint`, `MarketPracticeComparison`, `MarketComponentComparison`, `MarketRoleRange`, `MarketTrendSeries`, `MarketAnalyticsEvidencePage`) plus `api.getMarketAnalyticsSummary(filters)`. No `unknown` blobs.
- **Recharts** (`^3.10.1`) added as the frontend's first conventional 2D charting dependency, scoped to the market summary only — the existing Three.js stack (Space page) is untouched. `npm run lint` (oxlint) and `npm run build` (`tsc -b && vite build`) both remain clean; the build's pre-existing chunk-size advisory (carried since Phase 3, docs/24 §16) is unchanged in kind, just a larger number.

### AI extraction button state (docs/25 §9)

`MarketDataDocumentDetailView` in `Economics.tsx` no longer keys its button purely off `extraction_runs.length`:

- accepted observations exist, no extraction run yet → status line ("N accepted observation(s) loaded — this report already contributes to market analytics.") plus a **secondary**-styled `Run AI extraction (optional)` button;
- no observations, no run → `Run AI extraction` stays the primary action, unchanged;
- a run exists → `Re-run AI extraction`, with the existing last-run status line, unchanged.

Extraction itself is untouched — running or re-running it never deletes or modifies existing accepted observations.

---

## 3. Statistical guardrails honoured (docs/25 §8)

- **No synthetic cross-provider median** — `build_*` never computes a statistic across more than one document/provider; every comparison and trend key includes `document_id` or the full provider/context tuple.
- **No manufactured distributions** — only `reported_p25/p50/p75/mean`, `amount_min/mid/max`, and `reported_sample_size` are ever surfaced; no histogram, inferred stdev, or fake quartile is computed anywhere.
- **Sample size stays evidence, never a weight** — `reported_sample_size` is displayed verbatim (`"n not published"` when null); nothing in this module weights one row against another by it.
- **Component/pay-period boundaries preserved** — `component_comparisons` only ever compares `base` vs `total_package`; `day_rate` is excluded from that section entirely, and no day rate is annualised anywhere in this build.
- **Geography/currency boundaries preserved** — every filter, comparison key, and trend key holds `market_id`/`geography_reported`/`currency` constant; there is no FX conversion.
- **Reported vs derived preserved** — the only computed figure anywhere in this module is `derived_range_midpoint` (role ranges), always a separate field, never written over `reported_p50`.

---

## 4. Testing

- `backend/tests/test_market_analytics.py` — 18 tests, all against synthetic fixtures (never the live/production row count), covering every scenario in docs/25 §11.1: accepted-only filtering, no-archetype-requirement, practice-grouping (synonyms + raw-label preservation), median/mean/range statistical distinctness, range-only recruiter evidence not contaminating a median series, PQE/experience separation, component separation (base/total_package/day_rate), geography/currency filter isolation, practice-comparison context integrity (including the "All practice areas"/"Unspecified" exclusion), trend strictness (single period → none; two like-for-like periods → a series; any mismatched dimension → none), distinct-document/provider source counting, and the extraction-button support data (a curated, run-less document still exposes its accepted observations via the existing `GET .../documents/{id}`). Two additional end-to-end tests exercise the actual HTTP route and confirm facet stability under an active filter.
- Full backend suite: **591 passed, 0 failed** (573 pre-existing + 18 new), run against a real disposable Postgres 16 + pgvector — no regression in market-data ingestion/review, archetype compensation, posting backfill, Gap Value, or capability-demand derivation.
- Frontend: `npm run lint` (oxlint) and `npm run build` (`tsc -b && vite build`) both clean.
- Manual smoke test: local backend + Vite dev server, a synthetic corpus shaped like the three real reports (Acumen-style PQE bands with medians/ranges/sample sizes, Raretec-style Life/Non-Life/Pensions with mean+median+base/total-package, Cavehill-style named-role recruiter ranges with no median/n), driven with a headless Chromium via Playwright: logged in, opened Economics → Market Data, confirmed the Market Summary section renders with correct coverage counts (13 accepted / 3 documents / 3 providers / 0 archetype-linked), correct PQE/experience charts with a working median↔mean toggle, correct practice and base/total-package comparison cards, a working named-role range table, the exact "no like-for-like historical series yet" trends empty state (this synthetic corpus has only one period per provider), a working evidence table with pagination, filtering by practice group correctly narrowing every section (and correctly reporting no valid practice comparison once only one practice remains selected), and the AI-extraction button correctly reading "4 accepted observations loaded — this report already contributes to market analytics" with a secondary "Run AI extraction (optional)" button on a curated, run-less document. No browser console/page errors. No production or shared database was touched — a disposable local Postgres database, created and dropped for this check alone.

---

## 5. Non-goals confirmed unchanged

Every item in docs/25 §2 holds: the four Economics tabs are unchanged; Archetypes/Gap Value were not moved; no auto-created or auto-assigned archetypes; `economics_engine.select_reference_compensation` and `d_archetype_comp` are untouched; Gap Value semantics are untouched; no synthetic cross-provider "market salary" is computed; nothing claims statistical validation or representativeness; no sample size, median, or percentile is ever inferred; no day-rate annualisation; no currency conversion; base pay and total package are never silently combined; geographies are never silently combined; PQE/experience bands are never silently harmonised; no trend is manufactured from incomparable data; AI extraction is never triggered automatically; AI extraction is never required for already-accepted observations to appear in market analytics.

---

## 6. Persistence

**No migration was added or needed**, confirming docs/25 §13's expectation — every field this build reads (`geography_reported`, `domain_or_practice_area`, `experience_band`, `pqe_band`, `reported_mean`, `source_kind`, `source_quality`, …) already exists on `jobber.compensation_observation` from migrations `0013`/`0014` (docs/24 §1). No new table was created; the market-analytics response is computed live on every request from `compensation_observation` + `document` + `market`, never persisted.

---

## 7. Known limitations

- **Chart currency/pay-period gating.** `Compensation by PQE`/`Compensation by total experience` fall back to a table-only view whenever the current filter selection spans more than one currency or pay period — this is a deliberate refusal to draw a mixed-unit axis, not a bug; picking a currency filter resolves it.
- **`role_ranges` is a table + inline CSS bar, not a Recharts floating-bar chart.** docs/25 §6.3.E explicitly allows "a range view or range-oriented table/chart"; a table with a proportional range bar was chosen over a custom floating-bar Recharts series to keep the implementation simple and robust within this pass.
- **Trends will be sparse for some time.** With today's corpus (one report per provider), almost every trend key has exactly one period and therefore correctly renders no series — by design (docs/25 §6.3.F), not a defect. The machinery activates automatically as comparable historical reports are backfilled.
- **Facets are always global, never filter-of-filters.** `build_facets` always reflects the full accepted corpus rather than options remaining valid under the *other* currently-applied filters — chosen for simplicity and because docs/25 §4.2 asks for "stable" facet labels/counts; a user can therefore select a combination that yields zero rows in some section.
- Every limitation already recorded in docs/24 §14 (no personal salary timeline, one rolling "to-date" period for archetype comp, no day-rate detection on backfilled postings, no FX conversion, `d_*` staleness not flagged beyond the existing "rebuild economics" action, the capability-agreement gate not yet run) is unchanged and inherited as-is; this build does not touch any of them.
