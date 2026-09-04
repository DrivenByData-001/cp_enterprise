# 18 — Consolidation & Analytical Foundation (post-historical-ingestion)

**Status:** implemented — Space/Dashboard temporal + pagination, vocabulary
bootstrap, corpus trend analytics + classification, capability curation
extensions, partial-extraction surfacing, honest empty-vocabulary states,
embedding-shutdown fix. **Not attempted in this pass, on purpose:** running
the vocabulary bootstrap against production, reprocessing/reanalysing any
historical document or role, and Phase 4 compensation economics — see §9/§10.
**Related:** `docs/17-document-processing-pipeline.md` (the historical-corpus
pipeline this pass builds on top of, unchanged here), `docs/16-phase3-capability-engine.md`
(the capability engine this pass extends with a proposal layer, its
derivation logic untouched), `docs/11-capability-model-design.md` (concept
vocabulary this pass's bootstrap populates candidates for).

---

## 0. Starting state this pass assumes

This build environment has no credential for the real Supabase project (the
same constraint every phase since Phase 0 has recorded) — the "current
production state" this pass was briefed with (307 historical source
documents, 307 role instances, 307 role embeddings, 306 roles with persisted
skill observations, 3 latest-`partial` extraction runs, 0 `running`, 0
`failed`) is taken as given, not independently re-verified against
production, and nothing in this pass connects to or modifies production. All
schema/behaviour work here was developed and tested against a disposable
local Postgres 16 + pgvector instance, bootstrapped from
`backend/scripts/local_baseline.sql` exactly as `backend/tests/conftest.py`
already does — see §11.

Per the task brief, this pass does **not**: reprocess or re-extract the
historical corpus, reanalyse the three `partial` roles, repair the zero-skill
pre-guardrail SCOR role, or touch the ten original pilot roles' legacy
speculative fields. §9 documents these as a controlled, deliberate backlog.

---

## 1. Space: screen-space marker sizing + dense-cloud presentation

**Symptom:** with the full corpus, Space became "an indistinct blob" —
markers were sized in fixed *world* units
(`frontend/src/pages/Space.tsx`'s old `<sprite scale={baseScale}>`), so
zooming the camera closer (a smaller `OrbitControls` distance) made every
marker's *projected screen size* grow, since a perspective camera's apparent
size for a fixed-world-size object is inversely proportional to distance —
exactly the reported "markers balloon and obscure nearby observations."

**Fix:** every marker (`RoleStar`, `ProfileStar`) now recomputes its scale
every frame as `screenSizeConstant * cameraDistanceToMarker * multiplier` —
the standard constant-apparent-size billboard technique, which cancels the
perspective projection's `1/distance` falloff so a marker's size *on screen*
stays approximately constant across the whole `minDistance=3`..`maxDistance=60`
zoom range. Verified visually (Playwright screenshot before/after 15 zoom
steps, `console --errors` clean) — points visibly spread apart on zoom-in
while individual marker size stayed small and constant, not the old
ballooning behaviour.

Default sizes are much smaller than before
(`SCREEN_SIZE_POSTING=0.026` vs. the old fixed `0.9`), targets are modestly
larger (`SCREEN_SIZE_TARGET=0.040`) and the profile star largest
(`SCREEN_SIZE_PROFILE=0.058`) — restrained semantic differences, never large
enough to dominate the cloud. Hover applies a `1.7x` multiplier (the one
"selected/hovered" distinguishing state this app has — clicking navigates
immediately, so there is no separate persistent "selected" state to
distinguish beyond hover, consistent with not redesigning the existing
click-to-navigate architecture).

**Dense-cloud changes**, all in `Space.tsx`:
- The decorative `drei <Stars>` background field (3000 look-alike white
  glowing points) is removed entirely, not dimmed — at a few hundred *real*
  markers that are themselves small white/coloured glowing points on the
  same black background, a large field of fake look-alike points was
  directly working against readability.
- Labels remain hover-only (already true pre-existing behaviour — the `Html`
  tooltip was never always-on).
- Non-hovered postings get reduced opacity (`DEFAULT_OPACITY_DENSE=0.72`)
  once the point count exceeds `DENSE_THRESHOLD=60`, so overlap in a dense
  region doesn't smear into solid colour; targets are exempt (their coloured
  ring should stay legible even in a dense scene).
- A new track-legend interaction: clicking a legend swatch dims every
  non-matching posting (`DIMMED_OPACITY=0.28`) — a lightweight
  "filter-matched roles get stronger highlighting" affordance reusing UI
  already on the page, rather than a new search control.

No historical roles are removed from the projection to make it look
cleaner — every point that was there before is still there; only visual
weight changed.

## 2. Space: temporal controls

`GET /api/space` (`backend/app/routes/space.py`) gained optional `year` /
`date_from` / `date_to` query params, scoping which **postings** are
selected before the PCA fit runs. Deliberately **defaults to all-time** —
unlike Dashboard (§3), Space's historical cloud shape is itself analytically
useful, so nothing is hidden unless the caller asks (per the
brief and the pre-existing README note this pass now implements and
removes). Targets/synthetic references and the profile point are never
excluded by a temporal filter (`instance_type != 'observed_posting' OR
posting_date IS NULL OR (...)` in the WHERE clause) since they carry no
`posting_date` to filter on in the first place.

The response also gains `year_range` (min/max posting year across the whole
corpus, unfiltered — always present for a year-picker regardless of the
active filter) and each point gains `posting_date`.

**State/API design for a future "time travel"**: `Space.tsx`'s
`TemporalControls` component holds `{mode: 'all'|'year'|'range', year,
dateFrom, dateTo}` and a pure `toFilter()` function mapping that state onto
exactly the same param shape `GET /api/roles` (§3) already uses. A later
animated year-progression only needs to drive `year` on a timer against this
same state/request path — no redesign. Animation itself is **not**
implemented this pass (not judged trivial: a real scrubber needs debouncing,
play/pause, and frame-paced fetches).

## 3. Dashboard: temporal filter + server-side pagination

`GET /api/roles` (`backend/app/routes/roles.py::list_roles`) gained:

- `period` (`recent` default | `all`), `year`, `date_from`/`date_to` —
  `year`/date-range always wins over `period` when given. `recent` means
  `posting_date IS NULL OR posting_date >= today - DEFAULT_RECENT_YEARS
  years` (3 years, a named constant) — a role with **no known posting date
  is never treated as old**, since a freshly captured role is exactly the
  "current" case this default must not hide.
- `limit`/`offset` (default page size 20, capped at 200) — the response
  shape changed from a bare array to `{items, total, limit, offset, period,
  year_range}` (a deliberate, documented breaking change to this endpoint;
  `test_legacy_compatibility.py` and `Dashboard.tsx`/`api.ts` were updated
  alongside it).

Historical years remain fully browsable (`period=all`, or an explicit
`year`) — never hidden at the persistence layer, only in the *default* view.
Filtering happens in SQL; similarity ranking (needs a Python cosine over
fetched embeddings, §16 doc's existing approach, unchanged) and the final
page slice happen in Python — the browser still only ever receives one
page's worth of rows, which is the actual constraint the brief cares about
("must not require every role to be loaded into the browser"). Pushing
similarity ranking into pgvector SQL would be a larger architectural change
outside a consolidation pass' scope; documented here as a legitimate future
optimisation, not a current problem at ~300 roles.

`Dashboard.tsx` exposes: a period selector (Recent / All years / a specific
year, populated from `year_range`), Previous/Next pagination showing
`X–Y of total`, and resets to page 1 on any filter change.

## 4. Role navigation

Both existing navigation paths (`Dashboard.tsx`'s role-card `Link
to={\`/roles/${r.id}\`}`, `Space.tsx`'s node-click `navigate(\`/roles/${id}\`)`)
were already architecturally correct — no regression, no redesign needed.
Verified two ways:

1. **Visually**, against a real running instance (Playwright + headless
   Chromium, this sandbox's pre-installed browser) seeded with 120+ fixture
   roles across 2008–2025: clicking a Dashboard role card and a Space point
   both landed on `/roles/:id` with the correct role rendered, zero
   `console --errors`.
2. **As a backend contract regression** (`backend/tests/test_role_navigation.py`,
   since this repo has no frontend test runner — see §11): every id `GET
   /api/roles` returns, across a paginated 55-role fixture set and across
   temporal filters, independently resolves via `GET /api/roles/{id}`; same
   for every `GET /api/space` point id (postings *and* targets), including
   under a temporal filter; and no id repeats or corrupts across Dashboard
   pagination pages.

## 5. Extraction quality (`ok`/`partial`) surfacing

**A real, subtle gap found and fixed**: `role_instance.extraction_status`
(set from the model's own self-reported `metadata.extraction_status`,
`posting_persistence.py`) can **diverge** from the actual, more
authoritative verdict on `jobber.extraction_run.status` for that role's
`job_posting_extract` run — the deterministic empty-skills guardrail and the
historical-extraction-policy backstop (`document_processing.py`) can force a
*run* to `partial` even when the model itself claimed `ok`. The pre-existing
`RoleDetail.tsx` notice keyed only off the self-reported column, so exactly
this divergent case (plausibly some or all of the 3 `partial` roles in the
brief's stated production state) would have shown no notice at all.

**Fix:** `document_processing.role_extraction_quality(cur, role_id)` /
`role_extraction_quality_bulk` look up the run directly
(`result_role_instance_id = role_id`) and return its own `status` — `None`
for a role never processed through this pipeline (legacy/bulk import,
hand-entered), which falls back to the older self-reported field, the only
signal such a role has. Wired into `GET /api/roles/{id}` and `GET
/api/roles` (bulk, no N+1). `RoleDetail.tsx`/`Dashboard.tsx` prefer the
authoritative signal when present.

**Partial ≠ failure**, never treated as such: the UI notice reads "usable
extraction... eligible for later review", never an error state, and
**viewing the page never mutates or reanalyses anything** — the lookup is a
pure read. A disabled "Review / reanalyse extraction" button is present
(prepares the UI/data-model surface the brief asks for) with a tooltip
stating the controlled workflow isn't built yet — no destructive automatic
reanalysis exists or is invoked anywhere in this pass.

## 6. Vocabulary bootstrap (the main analytical-foundation deliverable)

`backend/app/vocabulary_bootstrap.py` — deterministic, reviewable,
**proposal-only**. Nothing it writes is ever `active`/`accepted`; every
write is `status='proposed'`, which every existing status-filtered query in
this codebase (matching, coverage, comparison) is already completely blind
to until a curator accepts it. No new proposal table was needed — the
schema already supported this:

- `jobber.concept.status='proposed'` (the column's own default, unconstrained)
- `jobber.concept_edge.status='proposed'` (already legal since 0006's CHECK)

### 6.1 Deterministic clustering (atomic concepts)

`cluster_key_for(surface_form)` is a coarser key than
`concept_linking.normalize_name`'s exact-match key, so obvious lexical
duplicates land in one review card. Two layers, both deterministic, neither
a general-purpose stemmer (over-collapse risk is asymmetric — the brief
explicitly warns against it):

1. A small curated synonym-group seed list covering the named examples
   exactly (Solvency II/SII, stochastic modelling/models, stakeholder
   management/engagement, R/R programming, internal model/modelling) plus a
   few more obvious actuarial pairs (IFRS 17/IFRS17, ML, VaR, ORSA, ...).
2. Generic, low-risk rules: BrEng/AmEng spelling normalisation
   (`-ise`/`-ize`, `modelling`/`modeling`), and a conservative plural strip
   (`-ies`→`-y`, trailing `-s` with guards against `-ss`/`-us`/`-is`).

`compute_cluster_keys` runs the existing, tested `concept_linking.run_pass_b`
first (creates/refreshes exact-surface-form proposals, unchanged), then
computes and stores `cluster_key` on every pending proposal (new nullable
column, migration 0009) — and opportunistically backfills `suggested_type`
from the proposal's own already-computed `nearest_concept_id`'s type when
similarity is reasonably high, a small, explainable, non-load-bearing
enhancement using data Pass B already computes.

**Review UI reuse**: `routes/concepts.py::_group_proposals` now groups by
`COALESCE(cluster_key, surface_form)` instead of `surface_form` alone —
`Vocabulary.tsx`'s existing `ProposalCard` UI needed only to render
`surface_forms` (plural) instead of one string, and call the new
`POST /api/concepts/proposals/resolve-cluster` (shares its core logic,
`_resolve_surface_form_group`, with the original single-surface-form
`resolve_proposal`, which is byte-for-byte unchanged in behaviour/contract).
Accepting a cluster creates **one** concept and aliases every other member
surface form onto it — accepting "Solvency II" also resolves "SII" to the
same concept, in one action.

### 6.2 Candidate capabilities (corpus co-occurrence + profile360 naming)

`compute_candidate_capabilities` — the method, exactly, because the brief
requires the methodology documented:

1. Restrict to *active* atomic concepts (the 8 `is_atom=true` types)
   appearing in ≥`min_concept_support` (default 3) distinct roles.
2. For every pair co-occurring in ≥`min_pair_support` (default 5) roles,
   greedily grow a maximal **core** set (`_grow_core`, capped at 4 members,
   fully deterministic iteration order) — a bounded, simplified stand-in for
   full frequent-itemset mining, documented as a simplification rather than
   hidden as if it were exhaustive Apriori.
3. Each distinct core's support-role-set `R` = the intersection of its
   members' role sets. Every other concept is classified by what fraction of
   `R` it also appears in: **≥50% → supporting**, **≥20% (and <50%) →
   contextual** — a real, transparent frequency measurement, not a guess.
4. Naming: the deterministic fallback is the core's own concept names joined
   with " & ". `profile360.capabilities.name` (read-only — see the ownership
   note below) is used **only** as a naming hint: if any existing profile360
   capability name embeds within `PROFILE360_NAME_SIMILARITY_THRESHOLD=0.75`
   cosine similarity of the fallback label, that name is suggested instead —
   freely editable by the curator either way.

**Ownership boundary, stated explicitly**: `profile360` remains authoritative
for person-side evidence; `jobber` owns canonical navigation/market
vocabulary. This bootstrap reads `profile360.capabilities.name` — a short
label string a real person's tool already treats as a *name*, not raw
evidence — purely as an in-memory naming signal for a candidate jobber
concept. No profile360 id, claim text, or row is ever stored in any jobber
table; the evidence itself stays exactly where it already lives, in
profile360, unreferenced. `jobber.role_skill_observation` (this app's own
corpus) is the actual evidentiary basis for every proposed concept and every
core/supporting/contextual assignment.

`persist_candidate_capabilities` writes, per candidate: one `jobber.concept`
(`type_code='capability'`, `status='proposed'`, `origin='bootstrap'`), one
`jobber.capability_detail` with a **clearly-marked provisional**
`demonstration_standard` ("[Bootstrap-proposed — needs curator-authored
demonstration standard] ...") and the **lowest** `min_depth` ('exposed')
rather than the curated default ('owned') — a bootstrap guess must never
silently claim a curator's confidence level — and one `concept_edge`
(`component_of`, `status='proposed'`) per core/supporting/contextual member.
Re-running is duplicate-tolerant (a name collision with an
already-reviewed capability is skipped, counted, never a crash) — each
candidate's writes are wrapped in a Postgres `SAVEPOINT`
(`conn.transaction()`) precisely so one collision can't poison the whole
batch's transaction.

**Never run automatically.** `backend/scripts/bootstrap_vocabulary.py` is a
CLI, not an API button — the same reasoning as
`scripts/process_job_documents.py`: a batch write operation should be
something an operator runs deliberately against a chosen `DATABASE_URL`, not
one click away in a UI with no additional auth layer (docs/15 §1). No route
in this codebase runs the bootstrap. `--dry-run` computes and reports
without writing anything.

## 7. Corpus trend analytics

`backend/app/trends.py` + `backend/app/routes/trends.py`
(`/api/trends/overview`, `/top-requirements`, `/requirement-trend`,
`/cooccurrence`, `/compare`, `/methodology`). Read-only, pure aggregation
SQL over `role_instance`/`role_skill_observation` — no new derived/persisted
table (the corpus is small enough that on-the-fly aggregation is simpler and
no less correct than premature materialisation).

**Evidence source, explicitly**: `role_skill_observation` is primary — every
historical role has skill observations from the document-processing
pipeline (docs/17); `requirement_claim` (Phase 2's closed-vocabulary model)
is populated only for roles that separately went through requirement
extraction, which the historical corpus has not. Canonical
`canonical_concept_id` is preferred when resolved; an unresolved
`surface_form` is the documented fallback — exactly the brief's own
ordering.

**Region** is derived, never stored: `_REGION_BY_COUNTRY`, a small explicit
best-effort table; an unlisted country reports `region: None` rather than
guessing. **Role family/archetype**: `role_archetype_detail`/
`archetype_concept_id` exist in the schema but, like the capability
catalogue pre-bootstrap, are not populated in production — `career_track`
is used and documented openly as the practical fallback grouping this
application already uses elsewhere (Dashboard's own filter), never silently
conflated with a real curated archetype.

Every result carries `sample_size` (and per-item/per-period sample sizes
where relevant); `top_requirements`/`requirement_trend` flag
`insufficient_sample`/mark periods unusable below a floor rather than
silently including them — see §8.

## 8. Trend classification

`classify_trend` — deterministic, documented, never a trained/opaque score.
Full methodology text is served at `GET /api/trends/methodology` (and shown
inline in the Trends UI's "How is 'trend' decided?" panel) as well as in
`trends.TREND_METHODOLOGY`. Summary:

1. Only periods with `sample_size >= SPARSE_MIN_SAMPLE` (5) count as usable.
2. Fewer than 2 usable periods → `sparse_insufficient_evidence` — preferred
   over a false-confidence guess.
3. Usable periods split chronologically into an early/late half (early half
   gets the extra period on an odd count); each half's mean proportion is
   compared.
4. Early mean ≤`EMERGING_EARLY_MAX_PROPORTION` (0.02) and late mean above it
   → `emerging`. Late ≥1.3x early → `increasing`; late ≤0.7x early →
   `declining` (`CHANGE_RELATIVE_THRESHOLD=0.30`). Otherwise `persistent`.

Every classification response includes the exact early/late means and
period counts it was computed from — inspectable, not asserted. All labels
are framed as extrapolation from *this corpus's own recorded history*, never
a labour-market forecast (`Trends.tsx`'s standing "Within your collected
role corpus..." framing, and this doc).

## 9. Trends UI

`frontend/src/pages/Trends.tsx` (`/trends`, added to nav). Deliberately a
small number of views, not "dozens of charts": a filter bar (year range,
country, seniority, career track — shared across every view below), a
corpus-overview panel (by year/region/seniority/track, all as
dependency-free CSS bar visualisations matching this app's existing
plain-card visual language — no new charting library dependency), a
ranked "most common requirements" list, and a requirement detail
drill-down (trend-over-time + classification badge, compare-by-dimension,
co-occurring requirements). Every panel shows its sample size; the page
opens with the "Within your collected role corpus..." framing the brief
requires verbatim.

## 10. Capability curation: reviewing bootstrap proposals

Extends the existing Phase 3 catalogue UI (`Capabilities.tsx`) rather than
building a parallel review surface:

- The catalogue's pre-existing status filter (`Active`/`Proposed`/
  `Deprecated`) already worked generically — bootstrap-proposed capabilities
  simply show up under `Proposed` with zero backend change to that
  endpoint.
- `GET /api/capabilities/{id}` now also returns `components_proposed`
  (`capability_engine.load_proposed_components` — a function kept **fully
  separate** from the accepted-only `load_components` the engine itself
  reads, so there is no code path by which a proposed edge could ever be
  mistaken for an accepted one).
- `POST /api/capabilities/{id}/components/{edge_id}/review`
  (`{action: accept|reject}`) — accept re-validates the same
  grammar/active-concept checks a curator-authored edge gets
  (`add_component`), defense in depth against the underlying concept having
  been deprecated since the edge was proposed.
- A proposed capability gets **Accept into catalogue** / **Reject** (both
  thin wrappers over the existing generic `update_capability` status
  transition — `'rejected'` added to the allowed status enum) and
  **Merge into…** (`POST /api/capabilities/{id}/merge`) — re-parents the
  source's `component_of` edges onto the target and marks the source
  `status='merged'`, `merged_into=<target>` (an existing column, unused
  until now).

Verified end-to-end against real seeded fixture data in a running browser
session (screenshot in the deliverable report): a bootstrap-proposed
capability with its placeholder demonstration standard, core/supporting/
contextual proposed components each with working Accept/Reject, and the
Accept/Reject/Merge action row, all render and function correctly.

## 11. Empty canonical-vocabulary mapping states

**A real gap, found and fixed.** `extraction._map_profile360_row` already
had two codepaths that both returned `mapped: false` with no way to tell
them apart: zero embedding candidates existed at all (the routine state
while the catalogue is unpopulated — brief's own stated context, "profile360
AI mapping report[ed] no confident candidate matches") vs. real candidates
existed and the model declined all of them. `Profile360.tsx` showed the
same message either way: *"No confident match found among candidates"* —
which, in the first case, is not a statement about the person's evidence at
all; it's a statement about the vocabulary not existing yet.

**Fix**: both codepaths now return `candidates_considered` (0 vs. N) and a
`reason` (`no_candidates_available` vs. `declined_all_candidates`).
`Profile360.tsx` shows a distinct, honest message for the empty-vocabulary
case: *"No canonical vocabulary candidates exist yet... This says nothing
about the strength of this evidence."* Regression coverage:
`test_map_profile360_claim_with_no_active_concepts_reports_empty_vocabulary_not_weak_evidence`
(also asserts the model is never even called when there is nothing to
adjudicate) alongside the pre-existing declined-with-real-candidates test,
now asserting the distinguishing fields.

## 12. Embedding/connection-pool shutdown warning

**Root cause, confirmed by reading `psycopg_pool` source directly**: the
recurring `couldn't stop thread 'pool-N-worker-*'/'pool-N-scheduler' within
5.0 seconds` warning is `psycopg_pool.ConnectionPool.__del__`'s own
finalizer path — triggered whenever a process exits with a still-open pool
(garbage-collected at interpreter shutdown rather than closed explicitly),
which tries to join the pool's worker/scheduler threads within a 5s timeout
and logs a warning (`_acompat.py::gather`) when that join doesn't land in
time — routine at interpreter teardown, harmless (every connection the pool
managed had already finished its work), but noisy on every CLI run. The
library's own hint text, baked into the warning: *"you can try to call
'close()' explicitly."*

**Fix, narrowly scoped, no concurrency redesign**: `app.db.reset_pool()`
already did exactly this (close + drop the pool reference) for tests; it is
now also called explicitly, in a `finally` block, by every CLI script that
touches the database (`rebuild_embeddings.py`, `process_job_documents.py`,
`seed_phase3_eval_sample.py` — `bootstrap_vocabulary.py`, new in this pass,
follows the same pattern from the start) and by a new `shutdown` event on
the FastAPI app itself (`app/main.py`, symmetric with its existing
`startup` event). `reset_pool`'s docstring documents the mechanism.
Regression coverage: `backend/tests/test_pool_lifecycle.py` (pool actually
closes on `reset_pool()`, a subsequent `get_pool()`/`db_cursor()` still
works, and the FastAPI `shutdown` event closes the pool via a real
`TestClient` context exit).

## 13. Deferred / controlled-reanalysis backlog — recorded, not actioned

Per the brief, none of the following were touched in this pass. Recorded
here as the explicit backlog a future controlled-review pass should work
from:

- **Three `partial`-status latest-extraction-run roles** (per the brief's
  stated production state). `partial` means a usable extraction with
  known/model-declared incompleteness — not failure. §5's
  `role_extraction_quality` now makes these reliably findable (their
  *run's* status is `partial` even if the role's own self-reported
  `extraction_status` might not say so) via the RoleDetail notice; no
  dedicated admin "list all partial roles" view was built this pass (the
  brief scoped this to "restrained indication... eligible for later
  review", not a full review queue) — a natural, small follow-up would be a
  `GET /api/roles?extraction_quality=partial` filter reusing the same bulk
  lookup, deliberately not added speculatively here.
- **One pre-guardrail SCOR role with zero persisted skills.** Not
  identified, not touched, not auto-repaired. A future controlled pass
  should locate it (likely via `role_skill_observation` COUNT per role) and
  decide, by hand, whether it warrants reprocessing under the
  now-refined historical-extraction policy (docs/17 §8a).
- **The original 10 pilot roles** with legacy speculative fields
  (`market_demand_score`/`automation_risk_score`/`top_adjacent_roles`, one
  with an old salary estimate) predating the CP Ent Phase 3B 0.2 policy
  refinement. Docs/17 §8a already states these were deliberately left as-is
  when the policy was fixed for *future* extractions; this pass does not
  revisit that decision. They remain valid historical `role_instance` rows;
  their legacy analysis fields should not be trusted as historical-policy-
  compliant without a deliberate, hand-reviewed reanalysis.
- **`GET /api/documents/{id}/analyse` / the CLI's `--retry-failed`** remain
  the only reanalysis paths that exist, and neither will ever reprocess a
  document that already produced a role (docs/17 §7) — "successful-role
  reanalysis" (needed for all three items above) is still, as docs/17 §17
  already stated, deferred pending role supersession/versioning design.
  Nothing in this pass changes that boundary; §5's disabled "Review /
  reanalyse extraction" button is the UI-side placeholder for whenever that
  design lands.

## 14. Phase 4 boundary — unchanged

Still not built, on purpose, and this pass adds nothing that erodes the
boundary docs/16 §17 already documented: `d_archetype_comp`, `d_gap_value`,
monetary gap ranking, transition-difficulty/learning-time estimates,
Phase 5 adjacency economics. The vocabulary bootstrap in §6 populates
*candidate* capabilities/components for curator review — it does not
itself constitute the "real curated capability catalogue... backed by real
`component_of` edges... with a real ~20-judgment hand-labelled gate" docs/16
§17 requires before Phase 4 may trust capability fit as an economics input.
Accepting bootstrap proposals is necessary progress toward that gate, not
the gate itself — the analytical Phase 4 gate (docs/16 §0.1) remains **not
attempted, not passed** until a human curation pass actually reviews and
accepts a meaningful fraction of what this pass proposes, and a real
hand-labelled gold set is built against it.

## 15. Limitations of the collected sample — stated plainly

- The corpus (~307 roles, 2008–2025) is one person's/one operator's capture
  history, not a market survey — every Trends UI panel says so
  ("Within your collected role corpus..."), and every trend classification
  is extrapolation from this corpus's own recorded history, never a
  forecast (§8).
- Country/seniority/year coverage across the corpus is uneven by
  construction (opportunistic capture, not stratified sampling) — small
  per-bucket sample sizes are common and are surfaced, never hidden
  (`insufficient_sample` flags throughout §7/§8's APIs).
- The vocabulary bootstrap (§6) proposes; it does not curate. Production's
  accepted canonical capability catalogue count does not change merely
  because this pass ran — see §14. Candidate quality is bounded by the
  simplifications documented in §6.2 (bounded core-growth instead of
  exhaustive frequent-itemset mining, a small curated synonym seed list
  instead of a general-purpose stemmer) — both deliberate, both documented,
  both intended to be extended by curator feedback over time rather than by
  a smarter algorithm guessing harder.
- `region_for_country` (§7) covers a small, explicit set of countries;
  anything outside it reports `region: None` honestly rather than guessing.

---

## Deliverable report

See the final chat-turn deliverable report for: exact file list, migration
list, number/type of bootstrap proposals produced against local *fixture*
data only (never production), exact backend test results for two full runs
against real Postgres, frontend `tsc`/`build`/`oxlint` results, and the
commit SHA.
