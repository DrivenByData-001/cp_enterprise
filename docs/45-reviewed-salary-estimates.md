# Salary estimates in role-details review

The role-details suggestion action now extracts metadata, extracts stated
compensation using the existing source-validation service, then estimates pay
only when neither the proposal nor accepted observations contain usable headline
pay. A failed extraction is reported as a failure, never treated as absent pay.
Factual metadata can still be saved if the salary task fails.

The estimator uses the shared `CP_AI_MODEL` setting (default `gpt-5.4-mini`). It
receives the source responsibilities and role metadata plus up to 60 accepted
posting/survey salary observations ranked by country, title overlap and recency.
Evidence retains currency, period, component, source title and dates. Model
citations must refer to supplied IDs. General-knowledge estimates are allowed
when suitable evidence is absent, but are explicitly low confidence. No live web
research is implied. Ambiguous scope/location may return no estimate with a
reason rather than an invented range.

The existing metadata proposal endpoint returns an additional `compensation`
section. Its review screen shows employer-stated figures separately from the AI
range, with editable amounts, currency, pay basis, rationale and assumptions.
Compensation may be excluded while saving the factual details.

`PATCH /api/role-instances/{id}/metadata` accepts an optional
`compensation_review`. Metadata and compensation save in the same transaction.
The salary review is bound to a recorded run and role; source changes invalidate
it. Stated amounts reuse `accept_posting_compensation` and its quotation/value
validation. Estimates are stored in `jobber.role_salary_estimate`, outside
`compensation_observation`, so they cannot inflate salary benchmarks or become
input evidence for further estimates. The original proposal/evidence remains in
the extraction run; reviewed values, model and evidence snapshot are retained.
Resaving one run updates its estimate instead of creating duplicates.

The shared compensation resolver uses stated pay first, then a reviewed
role-specific AI estimate, then the existing market/legacy fallbacks. A changed
role timestamp withholds the prior estimate until reviewed again. Explicit
market-filtered lookups do not reuse an estimate for an unspecified market;
currency-filtered lookups require a matching currency. The UI labels estimates
as **AI-estimated salary**, including in personal comparisons and role economics.

Deployment requires migration `20261007161743_reviewed_salary_estimates.sql`.
It creates a server-only RLS-enabled table with cascading role deletion and a
nullable extraction-run reference. No production records are seeded or changed.
