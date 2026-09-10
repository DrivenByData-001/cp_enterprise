# 24 — Phase 4: Market Economics / Compensation / Gap Value, and the Accepted Vocabulary Overview

**Status:** engineering merge gate — implemented, tested, documented.
**Analytical readiness gate (capability-agreement ≥ 0.80) — not attempted, not passed.**
These are two different questions with two different answers, exactly as
docs/16 §0.1 already established for Phase 3. See §12 below before citing
any Phase 4 output as validated.
**Related:** `docs/11-capability-model-design.md` (original design, superseded
where noted below), `docs/12-architectural-notes-future.md` §2 (the market-
dependence amendment this build applies to `d_gap_value` from day one),
`docs/16-phase3-capability-engine.md` (the engine Phase 4 builds on top of,
unchanged in its own status), `docs/23-phase4-implementation-prompt.md` (the
verbatim brief this document implements).

---

## 0. What Phase 4 answers

> Which reachable roles/archetypes pay more? Which capability gaps unlock
> higher-paid opportunities?

It does **not** answer what the user's actual salary is or has been — that
remains out of scope until profile360 exposes a compensation source (§2) —
and it does **not** introduce Phase 5 learning-time/transition-difficulty
judgment into the ranking (§11).

---

## 1. Actual schema built (migration `0013_phase4_economics.sql`)

Role archetypes needed **no new schema at all**: `concept(type_code=
'role_archetype')`, `jobber.role_archetype_detail`, and
`role_instance.archetype_concept_id` already existed since migration `0002`
and were simply unused in production (0 active role_archetype concepts, 0
assigned roles). Phase 4 is the first thing that reads or writes any of the
three.

What migration `0013` actually adds, in the established Postgres/Supabase
conventions (UUID `gen_random_uuid()` PKs, `TEXT` + `CHECK` for enums,
`JSONB` for trace/structured fields, guarded idempotent DDL):

- **`jobber.market`** — the controlled comparison-context dimension (prompt
  §5): `id, code (unique), label, country, geography, domain_concept_id,
  status, notes, created_at, updated_at`. Deliberately no `period` column —
  period lives on compensation observations/aggregates, never on the market
  itself.
- **`jobber.compensation_observation`** — the durable source-evidence layer
  (prompt §6): identity/subject (`role_instance_id`, `archetype_concept_id`,
  `raw_role_label` — at least one required), `market_id`, `component`
  (`base | bonus_pct | total_package | day_rate`), `pay_period` (`annual |
  daily`), `employment_basis`; amounts (`amount_min/mid/max`, `currency`,
  `reported_p25/p50/p75`, `bonus_pct`); evidence (`basis` — `posting_stated
  | posting_estimated | survey | curator_asserted`, `review_status` —
  `unreviewed | accepted | rejected`, `document_id`, `page_reference`,
  `table_reference`, `source_note`, `reported_sample_size`,
  `extraction_run_id`).
- **`jobber.d_archetype_demand`** — derived, keyed `(archetype_concept_id,
  capability_concept_id)` (prompt §4).
- **`jobber.d_archetype_comp`** — derived, keyed `(archetype_concept_id,
  market_id, period_start, period_end, currency, component, pay_period)`
  (prompt §9).
- **`jobber.d_gap_value`** — derived, keyed `(capability_concept_id,
  market_id, period_start, period_end, currency)` (prompt §11) — market is
  part of the key **from the first migration**, applying doc 12 §2.3's
  advance amendment rather than retrofitting it later.
- One additive widening of `extraction_run_vocabulary_required_check` to
  add `compensation_extract` alongside the existing `job_posting_extract`/
  `role_context_generate` exemptions (that task has no controlled-
  vocabulary dependency either).

No prior migration file was modified. `backend/scripts/local_baseline.sql`
needed **no change** — it only defines the pre-existing production baseline
Phase 2 preflight-asserts (`document`, `role_instance`,
`role_skill_observation`, `concept`, …); Phase 4's five new tables are all
genuinely new and created by `0013` alone, which `conftest.py`'s
`run_migrations()` picks up automatically. `backend/tests/conftest.py`'s
`_RESETTABLE_JOBBER_TABLES` was extended with the five new tables so test
isolation covers them.

---

## 2. Deviation: `jobber.episode_compensation` was **not** built

Doc 11 §4.5 originally proposed a person-side `episode_compensation` table
alongside `compensation_observation`. **Deliberately not built.** Phase 2
established `profile360` as the sole authoritative store for the user's own
career evidence (docs/14 §6) — a second, jobber-local home for the user's
salary history would be exactly the kind of duplicated person-side truth
that boundary exists to prevent. The current profile360 schema exposes no
structured compensation fields for this app to read, and this app must not
add any (prompt §1: "do not add salary fields to profile360 from this
app").

Consequently:

- There is no "actual salary" figure anywhere in this build.
- **"Current benchmark" always means** the best compensation reference
  among currently structurally reachable archetypes in the selected market
  (`economics_engine.discover_gap_value_buckets`/`derive_gap_value_for_bucket`'s
  `best_current_reachable`) — **never** the user's real salary. The
  Economics UI states this explicitly (`Economics.tsx`'s page-level
  subtitle).
- A true personal salary-history timeline remains deferred until
  profile360 exposes an appropriate compensation source. This does not
  block Q7/Q8 (which reachable roles pay more; which gaps unlock higher
  pay) — both are answered entirely from role/archetype-side evidence.

---

## 3. Unifying role requirement evidence (`role_requirements.py`)

Before Phase 4 archetype demand or role fit could be trusted for the
historical corpus, the Phase 3 capability engine had to stop reading only
`jobber.requirement_claim` (currently 0 capability rows in production)
and also read `jobber.role_skill_observation` (~1,248 mapped capability
observations in production).

`role_requirements.load_role_requirements(cur, role_instance_id)` is the
one canonical loader, shared by `capability_engine.derive_role_fit` and
`economics_engine.derive_archetype_demand`:

1. If the role has **usable** `requirement_claim` rows — non-superseded
   (`superseded_by IS NULL`) **and** non-rejected (`review_status !=
   'rejected'`) — those are authoritative.
2. Otherwise, mapped `role_skill_observation` rows whose
   `canonical_concept_id` resolves to an **active** concept.
3. The two sources are **never merged** for one role.
4. A `role_skill_observation`-sourced item always carries `basis =
   review_status = evidence_span = None` — never upgraded to "stated"
   evidence merely by existing.
5. `requirement_type`/`importance` are preserved where present on either
   source; a legacy observation with no stored `requirement_type` carries
   `None` (never fabricated as `required`), so it contributes to neither
   `blocking_gaps` nor `unverified_required`.

This mirrors, in spirit, `db.role_skills_with_fallback`'s existing "prefer
one source, fall back to the other, never merge" pattern for Role Detail
display — with the opposite precedence, because Phase 4's derivations need
`requirement_claim`'s review/basis/span provenance to win whenever it
exists.

One deliberate behaviour refinement, not just a passthrough: previously
`capability_engine.derive_role_fit`'s inline query did **not** filter out
`review_status = 'rejected'` requirement_claim rows (only
`superseded_by IS NULL`). The canonical loader now excludes rejected claims
from both the "does this role have usable claims" check and the returned
item list — consistent with the existing `db.py` convention for the same
table, and covered by
`test_role_requirements.py::test_rejected_claims_do_not_block_fallback`/
`test_rejected_or_superseded_claims_never_reappear_even_with_no_fallback`.
Production currently has 0 capability requirement_claim rows, so this
changes no production behaviour today.

`capability_engine.derive_role_fit` and `routes/comparison.py::compare_role`
were updated to carry the new provenance fields (`requirement_source`,
`role_skill_observation_id`) through unchanged otherwise — every existing
Phase 2/3 field (`basis`, `review_status`, `evidence_span`,
`blocking_gaps`, `unverified_required`, `fit_score`,
`embedding_similarity`) keeps its exact prior meaning and shape.

---

## 4. Role archetypes (`routes/archetypes.py`)

Reuses `concept(type_code='role_archetype')` + `role_archetype_detail` +
`role_instance.archetype_concept_id` exactly, with no parallel table.

- `GET /api/archetypes/title-groups` — unassigned **observed** roles,
  grouped by a live-normalised title (lowercase, punctuation stripped,
  whitespace collapsed) — a curation aid only; the raw `role_instance.title`
  is never touched.
- `POST /api/archetypes` — atomically creates `concept` +
  `role_archetype_detail`, optionally assigning `role_instance_ids` in the
  same transaction.
- `POST /api/archetypes/{id}/assign` — assign/reassign roles to an existing
  **active** archetype; rejects (404) a target that isn't a
  `role_archetype`-typed concept, and rejects (400) assignment onto a
  deprecated one.
- `PUT /api/archetypes/{id}` — metadata edit (`canonical_name`,
  `seniority_band`, `primary_function_concept_id`, `typical_market`,
  `notes`, `status`). Deprecation is reversible (`active ⇄ deprecated`,
  never a hard delete) and excludes the archetype from the default
  `GET /api/archetypes` listing and from `economics_engine`'s derivation
  scans (`_active_archetypes`), while its historical role assignments are
  left untouched.

No AI-driven creation or assignment path exists anywhere in this router —
every mutating endpoint is a direct curator action.

---

## 5. Archetype demand (`economics_engine.derive_archetype_demand`)

Empirically derived from the archetype's currently-assigned roles' canonical
requirement evidence (§3's loader), never a curator-typed intrinsic
property, and never written into `concept_edge`'s curated ontology.

Grain: `(archetype_concept_id, capability_concept_id)`. Only
capability-typed requirement items participate — atomic requirements stay
valid in role fit but are out of `d_archetype_demand`'s scope by design
(prompt §4). Every row carries `roles_in_archetype`,
`roles_demanding_capability`, `demand_rate` (a plain ratio, not a
threshold), `required_count`/`preferred_count`/`contextual_count`,
`source_role_ids`, and a `trace`. No arbitrary "this archetype requires X"
boolean is invented anywhere — the UI shows the counts directly.

`rebuild_archetype_demand` recomputes every active archetype's rows fresh,
then deletes anything not just recomputed for that archetype (a
capability no longer demanded) and anything for an archetype no longer
active — never truncate-first.

---

## 6. Market dimension and posting/survey statistical treatment

`jobber.market` is intentionally small: no global geography ontology
(prompt §5). `market.py`'s `normalize_country`/`get_or_create_market_by_country`
implements the one normalisation prompt §7 asks for — a short, explicit
alias table for genuinely unambiguous abbreviations (`UK → United Kingdom`,
`USA → United States`, …) plus a blocklist of values that name no single
country (`Remote`, `Europe`, `EU`, `Global`, …, which stay unassigned). Any
other non-empty country string is assumed already specific and gets its own
market row directly. A market this can't safely resolve is left
`market_id = NULL` on the observation — retained, never guessed.

**Posting vs. survey are kept statistically separate everywhere** (prompt
§9): `d_archetype_comp.posting_p25/p50/p75` are computed **only** from
`posting_stated` rows' per-row midpoints (`(amount_min + amount_max) / 2`,
or whichever bound is present alone) and are always the "median
advertised-range midpoint" — never labelled "market median salary".
`survey_benchmarks` is a separate JSON array preserving each accepted
survey row's own reported `p25/p50/p75`/sample size/source note verbatim.
The two are never pooled into one figure.

---

## 7. Backfill (`compensation_backfill.py`, `scripts/backfill_compensation.py`)

Idempotent projection of `role_instance.salary_min/max` and
`salary_estimate_min/max` into `compensation_observation`:

- `salary_min/max` → `basis='posting_stated'`; `salary_estimate_min/max` →
  `basis='posting_estimated'` — always two separate observations when both
  are present, never combined.
- `source_key = f"posting_stated:{role_id}"` / `f"posting_estimated:{role_id}"`,
  `ON CONFLICT (source_key) DO NOTHING` — a rerun creates zero new rows;
  it never overwrites a row a curator may since have corrected.
- Backfilled rows are born `review_status='accepted'` — a mechanical
  projection of already-captured structured fields, not a new AI claim
  needing review (unlike survey extraction, §8).
- `role_instance`'s own salary columns are never modified.
- Every backfilled row is treated as `component='base', pay_period='annual'`.
  **Stated limitation:** this schema carries no stored pay-period marker on
  postings, and every extraction prompt in this codebase
  (`prompts/extract_job_posting.md`) asks for a plain salary range with no
  unit distinction — the overwhelming convention for a salaried posting. A
  genuinely day-rate historical posting, if one exists in the corpus, is
  not detected here; it would need correcting via the
  compensation-observation review endpoints.
- A currency-less role is skipped and counted (`skipped_no_currency`),
  never guessed.

Exposed as both `POST /api/economics/compensation-observations/backfill`
and `python -m scripts.backfill_compensation`, matching the dual
script+endpoint precedent `scripts/rebuild_embeddings.py` already set
(docs/16 §18).

---

## 8. Market survey ingestion (`market_data_processing.py`, `routes/market_data.py`)

Reuses the existing immutable `jobber.document` approach exactly
(`kind='market_survey'`) and the existing `app.ai` task layer
(`prompts/extract_market_survey.md`, task `compensation_extract`),
mirroring `document_processing.py`'s transaction-safe lifecycle: a
`running` `extraction_run` row is inserted and committed *before* the AI
provider is called; a provider/validation failure marks that row `failed`
in a fresh transaction and leaves the document and every existing accepted
observation untouched; a successful extraction persists every recognised
item plus the run's own status in one short transaction.

- **Paste text** and **upload selectable-text PDF** (no OCR — the same
  pattern `routes/role_instances.py::ingest_pdf` already uses) both create
  the immutable document first, before any AI call.
- **Viewing a report never calls AI** — `GET /api/market-data/documents/{id}`
  is a pure read (`test_market_data.py::test_viewing_document_never_calls_ai`
  proves this by making a reached AI call raise `AssertionError`).
- Every extracted item becomes a `review_status='unreviewed'` draft — never
  an accepted fact. Neither `archetype_concept_id` nor `market_id` is ever
  set by extraction itself — both require an explicit curator action
  (`PATCH /api/market-data/compensation-observations/{id}`) — "do not
  create canonical role archetypes automatically" applies symmetrically to
  market assignment here.
- An item whose `component`/`pay_period`/`currency` cannot be recognised
  against the DB's controlled vocabulary is **dropped**, not guessed, and
  counted in the run's `items_skipped_incomplete`.
- Missing sample size/percentiles are never inferred — the extraction
  prompt says so explicitly and nothing downstream fills them in.
- Re-running extraction on a document that already has a completed
  (`ok`/`partial`) run is blocked (`already_processed`) rather than
  accumulating repeat AI passes over the same report — review or reject the
  existing drafts first.

---

## 9. Archetype compensation + the reference-compensation rule (prompt §10)

`economics_engine.select_reference_compensation`, the **one** deterministic
selection rule, applied per `(archetype, market, currency, component,
pay_period)` bucket:

1. **Survey first**: the most recent (`period_end` desc, tie-broken by
   `reported_sample_size` desc then a stable id) accepted `survey`
   observation that (a) supplies an explicit `reported_p50` or `amount_mid`,
   (b) has `reported_sample_size >= 5` (a `NULL` sample size never
   qualifies), (c) matches the exact market/currency/component/pay_period,
   and (d) has a linked source `document_id`.
2. **Else posting**: only when there are **≥ 5** `posting_stated`
   observations in the exact bucket — `posting_estimated` never counts
   toward this gate, at any count. The reference is the median of each
   posting's own advertised-range midpoint, explicitly labelled as such.
3. **Else**: `reference_comp = reference_source = None`. The archetype still
   participates structurally; sample/source information is still shown
   (`n_observations`, `n_posting_stated`, `n_survey_sources`); no monetary
   figure is exposed.

`d_archetype_comp.reference_basis_detail` always records exactly which
observation/basis was selected. Every rejected observation is excluded from
every calculation above (`review_status = 'accepted'` filter throughout).

`rebuild_archetype_comp` computes one rolling snapshot per bucket
(`period_start` fixed at 2000-01-01, `period_end = today at rebuild time`
— see §11's period note) and deletes any row whose `period_end` doesn't
match the rebuild just performed, so a same-day rerun updates in place and
a rebuild on a later day retires the prior snapshot rather than
accumulating one stale row per rebuild day.

---

## 10. Gap value (`economics_engine.derive_gap_value_for_bucket`, prompt §11)

The counterfactual: *if this capability gap became evidenced, what would
become structurally reachable, and what does that look like against the
best currently-reachable benchmark?* Never claims the capability has been
acquired.

**Canonical simulation rule**, using only `capability_engine.derive_role_fit`
(recomputed fresh per role, never read from the persisted `d_role_fit`
table — the same "recompute rather than risk a read-after-write ordering
hazard" choice docs/16 §16 already made for coverage-inside-role-fit):

- A role is **reachable** when it has no `blocking_gaps` (any type — this
  is the literal, unqualified reachability definition, not scoped to
  capability-typed gaps alone).
- For capability **C**: a role is **unlocked by C** when its
  `blocking_gaps` consist of exactly `{C}`; **improved by C** when C is one
  of *multiple* blocking gaps, or when C appears in `unverified_required`
  (partial/user-asserted — closing it improves that item's status even
  though it was never blocking).
- Distinct archetypes are aggregated from the roles they unlock/improve; an
  archetype with at least one unlocked role is reported as **unlocked**,
  never double-counted as also **improved**.
- **"Best current reachable"** = the highest qualifying `reference_comp`
  (§9) among archetypes with at least one currently-reachable role, in the
  selected market/currency, at the fixed `component='base',
  pay_period='annual'` (gap value's one canonical monetary comparison —
  bonus/day-rate/total-package figures aren't mixed into it). It does
  **not** mean the user's actual salary (§2).
- `reference_comp_unlocked` = the best qualifying reference among
  **newly-unlocked** archetypes; `comp_delta_vs_best_current_reachable` is
  only computed when **both** figures qualify — otherwise both stay `None`
  and the row keeps its structural unlocked/improved counts (prompt: "do
  not manufacture a currency delta").
- `evidence_quality`: `insufficient` (no qualifying reference at all),
  else banded by total observation count behind the unlocked/improved
  archetypes' benchmarks (`thin` < 10, `moderate` 10–24, `good` ≥ 25) — a
  documented, deterministic rule, not a statistical confidence interval.
- `rank`: deterministic within one `(market, currency)` bucket — qualifying
  `comp_delta` descending first, then `archetypes_unlocked`, then
  `roles_unlocked`, then capability id as a stable tiebreak. A capability
  with no monetary qualification still ranks, by structural opportunity
  alone, clearly labelled `insufficient compensation evidence`
  (`EvidenceQualityBadge` in `Economics.tsx`).

**Which (market, currency) buckets get computed**: every pair with at least
one accepted compensation observation, of any component/pay_period
(`discover_gap_value_buckets`) — a market/currency with zero compensation
evidence has no gap-value rows yet; that is expected, not an error, and is
exactly why `GET /api/economics/gap-value/contexts` can return `[]` on a
freshly-deployed corpus.

### Period — a deliberate v1 simplification

Doc 11/12 never specified how "period" should be bucketed in practice. This
build computes **one rolling "all accepted evidence to date" period** per
bucket (`period_start` fixed at 2000-01-01, `period_end` = the date of the
rebuild that produced the row) rather than true year-over-year buckets —
maximising sample size against a corpus that is, by design, currently
sparse (§13). The Economics UI's period selector therefore shows exactly
one computed option today ("through `<period_end>`"). `period_start`/
`period_end` are real, meaningful, and keyed columns, so true multi-period
comparison can be added later without a schema change; it is simply not
built in this pass.

---

## 11. Phase 4 readiness indicator (prompt §12)

`economics_engine.readiness_summary` / `GET /api/economics/readiness`,
surfaced in the Economics UI's Benchmarks/Readiness tab
(`BenchmarksTab` in `Economics.tsx`):

- **Capability agreement** — reuses `evaluation.capability_agreement(cur)`
  verbatim (the exact same live computation the existing Evaluation page
  already shows), so this can never silently diverge from that number.
  Production has 0 `capability_gold_judgment` rows, so this reads
  `{"measured": false, ...}` today — rendered as **"Not yet evaluated"**,
  never a fabricated pass. See §12 below.
- Total active archetypes / archetypes with at least one assigned role.
- Accepted compensation observation counts, split by
  posting-stated/survey.
- `compensation_sample_sufficiency` — a plain informational flag
  (`sufficient` once ≥5 accepted posting_stated or ≥1 accepted survey
  observation exists anywhere), never a gate that disables the page.

---

## 12. Capability-agreement gate status — read before citing anything as validated

Identical posture to docs/16 §0.1, extended: Phase 4's economics sit on top
of the Phase 3 capability engine, and that engine's own analytical gate
(a real curated capability catalogue, a real ~20-judgment hand-labelled
`capability_gold_judgment` set, `capability_agreement ≥ 0.80` measured
against it) has **not been attempted in production** — production still
has 0 curated capabilities and 0 gold judgments as of this build. Phase 4's
own economics — `d_archetype_demand`, `d_gap_value` specifically — inherit
that same limitation: a gap-value ranking driven by an empty capability
catalogue is, structurally, ranking `not_found` against `not_found`. This
build does not manufacture a passing result to hide that, and the Economics
UI's readiness panel says "Not yet evaluated" rather than staying silent
about it. Economic outputs may be used experimentally; they must not be
described as validated until that human curation exercise happens and the
gate is actually measured (docs/16 §17's own conditions, unchanged and now
also gating Phase 4).

---

## 13. Rebuild semantics

Facts (never disposable): `compensation_observation`, `market`, archetype
assignments (`role_instance.archetype_concept_id`), accepted source
evidence. Derived/disposable: `d_archetype_demand`, `d_archetype_comp`,
`d_gap_value` — every row carries `engine_version`
(`economics-engine-v1`) and `computed_at`; `d_archetype_demand` also
carries `vocabulary_version_id`.

`economics_engine.rebuild_phase4_derivations(cur)` runs, in order,
`rebuild_archetype_demand` → `rebuild_archetype_comp` → `rebuild_all_gap_value`
(gap value reads the just-rebuilt `d_archetype_comp` rather than
recomputing compensation aggregates inline, so this order matters). Every
rebuild function computes fresh then deletes exactly what's now stale
(§5/§9/§10 above) — never truncate-first, so an interrupted rebuild leaves
the previous snapshot in place rather than a half-emptied table, matching
`capability_engine.py`'s established precedent.

`GET` endpoints never rebuild anything silently — `POST
/api/economics/rebuild` (full) and `POST
/api/economics/compensation-observations/backfill` are the only mutating
entrypoints, exactly mirroring `POST /api/capabilities/rebuild`'s existing
convention and auth posture (behind the same central `require_auth`
dependency as every other route — no new public endpoint).

Changing an archetype assignment, accepting/rejecting a compensation
observation, or changing role requirement mapping does not retroactively
update already-persisted `d_*` rows — they become stale until the next
explicit rebuild, exactly as the Phase 3 engine already behaves for
`d_role_fit`/`d_capability_coverage`. This build does not add a staleness
flag beyond what Phase 3 already has; that remains a known limitation
(§14).

---

## 14. Known limitations

- No true personal salary-history timeline (§2) — deferred until
  profile360 exposes a compensation source.
- No multi-period (year-over-year) comparison yet (§10) — one rolling
  "to date" period per bucket.
- No day-rate detection on backfilled postings (§7) — every backfilled row
  is treated as annual base pay.
- Market survey extraction never auto-assigns archetype or market (by
  design, §8) — every survey row needs one explicit curator action before
  it can enter any aggregate.
- No FX conversion anywhere, by design — a market/currency with only
  foreign-currency evidence has no benchmark until same-currency evidence
  exists.
- `d_*` staleness after a source change is not flagged in the UI beyond
  "rebuild economics" being a visible, explicit action — same posture
  Phase 3 already has for its own derived tables.
- The capability-agreement analytical gate has not been run against real
  production data (§12) — this is inherited from Phase 3, not introduced
  here, but it directly limits how much trust Phase 4's own economics can
  currently claim.
- Production currently has 0 curated archetypes and ~23 stated-salary
  postings (14 GBP / 7 EUR / 2 USD) — the Economics page is expected to be
  sparse on first deploy. **This is not a bug.** The first post-deploy
  operator task is curator assignment of a useful archetype set and
  ingestion of representative salary reports; this build deliberately does
  not seed or auto-accept anything to make the page look populated ahead
  of that.

---

## 15. Accepted Vocabulary overview (prompt §14)

Added to the existing Vocabulary page (`AcceptedVocabularyOverviewPanel` in
`Vocabulary.tsx`), rendered **only** when viewing the Accepted status
filter — it never appears above the Pending review queue.
`vocabulary_curation.get_accepted_overview` /
`GET /api/vocabulary/accepted-overview` computes, live, over active
concepts only: total count, count by type, concepts missing a definition,
concepts with no active Concept Dossier, concepts whose active dossier has
no related concepts, alias count, and the 10 most recently
accepted/reviewed concepts. No AI call anywhere in this feature. No new
table — every count is a plain query over `concept`/`concept_alias`/
`concept_dossier`.

Clicking a type count or a maintenance-signal count (missing definition /
no dossier / dossier with no related concepts) narrows the existing
Concept Browser below it to exactly the affected concepts
(`ConceptBrowserFocus`, a small controlled-focus prop added to the existing
`ConceptBrowser` component — no new list endpoint, no large UI rework, per
the brief's own "if this can be done without large UI work" allowance).

Deliberately not built, per the brief: any ontology dashboard, any
semantic-overlap scoring. The per-concept Details drawer
(`ConceptDetailsDrawer.tsx`, docs/22) remains the maintenance surface for
an individual concept; this panel is a summary only.

---

## 16. Frontend

One new top-level page, `Economics.tsx` (`/economics`), four in-page tabs
(the existing `Profile360.tsx` plain-button-toggle pattern, no tab
library): **Gap Value**, **Archetypes**, **Market Data**, **Benchmarks /
Readiness**. Money is formatted via `Intl.NumberFormat` — a new convention
for this codebase (no prior page formatted currency; `RoleDetail.tsx`
prints raw numbers) introduced because Economics is the first page whose
entire point is showing compensation figures legibly; every figure is
still shown beside its sample size/basis or one click away via "Show
trace", never bare. `api.ts` gained one new typed section per Phase 4
subsystem (archetypes, markets/compensation, derived tables, market
survey documents, accepted-vocabulary overview), following the file's
existing flat-object-of-functions convention exactly.

`tsc -b`, `oxlint src/`, and `npm run build` all pass clean (one
pre-existing chunk-size advisory from `vite build`, not an error — the same
advisory Phase 3's own build already carried).

---

## 17. Testing

New backend test files (all against a real disposable Postgres, never
SQLite/mocked): `test_role_requirements.py` (11), `test_archetypes.py` (9),
`test_compensation.py` (15), `test_economics_engine.py` (18),
`test_market_data.py` (11, AI mocked exactly like
`test_document_processing.py` already does — never a live provider call),
`test_economics_routes.py` (5, end-to-end HTTP integration:
backfill → assign → rebuild → read), `test_vocabulary_accepted_overview.py`
(7) — 76 new tests. Full suite: **553 passed, 0 failed**, run against real
Postgres 16 + pgvector (the same environment gate every phase since Phase 2
has used).

Frontend: `tsc -b` clean, `oxlint src/` clean, `npm run build` clean.

A real Chromium browser smoke run (login → Vocabulary → Accepted → the
overview panel → Economics → create an archetype from a title group →
assign the remaining role individually → backfill → rebuild while below
the 5-observation threshold, confirming no monetary figure is shown → cross
the threshold → rebuild again, confirming the posting benchmark now
appears with its sample count → a gap-value row's trace → ingest a
synthetic market-report text source → on-demand extraction against a local
mocked OpenAI-compatible server (no live provider credential exists in
this build environment) → assign market/archetype and accept the resulting
draft observation → rebuild, confirming the survey benchmark now takes
precedence over the posting benchmark exactly as §9's rule requires →
logout) passed all 17 assertions, against a disposable local Postgres
database seeded only with synthetic smoke-test data and dropped
immediately afterward. No real/production database was ever touched by
this build.
