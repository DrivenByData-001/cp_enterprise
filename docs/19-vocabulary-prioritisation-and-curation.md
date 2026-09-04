# 19 — Vocabulary Proposal Prioritisation and Curation UX

**Status:** implemented — cluster-level evidence aggregation, deterministic
curation-priority scoring, priority bands, queue filters/pagination,
accept/reject/merge (idempotent), batch review with a pre-execution
confirmation and whole-batch transactional atomicity, noise/sparse flags,
progress metrics, and empty-vocabulary messaging. **Not attempted in this
pass, on purpose:** capability proposal writes, automatic `component_of`
acceptance, Phase 4 economics, historical re-extraction, and any curation of
the real 1,525 production proposals — see §12/§15. **Related:**
`docs/18-consolidation-and-analytical-foundation.md` §3/§6 (the vocabulary
bootstrap and lexical-clustering pass this build's proposal queue and
`cluster_key` column come from, unchanged here), `docs/11-capability-model-
design.md` (the concept vocabulary this pass curates into).

---

## 0. Starting state and an environment constraint carried forward unchanged

Per the brief, production already has the vocabulary bootstrap's output
written: 1,525 `jobber.concept_proposal` rows (`status='pending'`), 1,507
distinct `cluster_key` values, 0 `jobber.concept`/`concept_edge` rows, and
4,677 unresolved `role_skill_observation` rows over a ~307-role, 2008–2025
corpus. These are taken as given (per the brief), not independently
re-verified against production.

**This build environment has no credential for the real Supabase project** —
the identical constraint docs/18 §0 already recorded for the previous pass,
still true here. Every schema/behaviour change in this pass was developed
and tested against a disposable local Postgres 16 + pgvector instance,
bootstrapped from `backend/scripts/local_baseline.sql` exactly as
`backend/tests/conftest.py` already does. §13 explains what this means for
the brief's first-tranche diagnostic report specifically.

---

## 1. Cluster evidence — computed live, not persisted

`app/vocabulary_curation.py::build_pending_cluster_index` builds the full
per-cluster "review representation" the brief asks for (canonical label,
grouped surface forms, role/observation counts, year span, countries,
seniority levels, career tracks, example role titles, proposal status) by
joining the existing `jobber.concept_proposal` and
`jobber.role_skill_observation`/`jobber.role_instance` tables in Python —
the same live-aggregation pattern `routes/concepts.py::_group_proposals`
already used for occurrence counts, extended to carry the fuller evidence
this brief needs.

**No new table, no new index.** At this corpus's scale (~1,525 pending
proposals, ~4,700 unresolved observations in production) two SELECTs and an
in-memory group-by comfortably run in well under 100ms — measured against
the ~1,481-cluster/~4,000-observation local diagnostic fixture built for
§13, the full `list_clusters` call (aggregate + filter + sort + paginate)
completes in a few tens of milliseconds. Every index this pass's queries
touch (`idx_concept_proposal_cluster`, `idx_rso_role_local`,
`idx_rso_surface_local`) already existed from migration 0009 — nothing new
was added, per the brief's "add indexes only if justified by measured
need," because nothing here is close to needing one.

The suggested canonical label is the longest surface form in the cluster,
ties broken alphabetically (`suggested_canonical_label`) — the same
heuristic the pre-existing `ProposalCard` UI already used, now centralised
so backend and frontend can never disagree.

Once a cluster is fully resolved (no pending member left), its evidence
becomes the lighter `_resolved_cluster_rows` shape (status, resolved
concept, resolved date, surface forms) rather than a re-derived role/year/
country breakdown — a deliberate, documented boundary: after acceptance,
`role_skill_observation.canonical_concept_id` is set and the rows drop out
of the "unresolved" query, so the rich card is reserved for the active
review queue, and accepted/rejected rows are audit history, not a live
re-scored item.

---

## 2. Prioritisation methodology (deterministic, documented)

Full text is served at `GET /api/vocabulary/methodology` and lives in
`app/vocabulary_priority.py::METHODOLOGY_TEXT`. Summary:

```
score = 3.0·log1p(distinct roles)      + 0.5·log1p(observation count)
      + 1.5·log1p(distinct years)      + 1.0·log1p(distinct seniority levels)
      + 1.0·log1p(distinct countries)  + 1.0·log1p(distinct career tracks)
      + 0.5·recency_factor
```

Every breadth signal counts **distinct** roles/years/seniority levels/
countries/career tracks — never raw mention counts — specifically so a term
repeated many times in one role, or confined to one narrow historical burst,
cannot dominate the ranking (brief §2). `log1p` compresses each count so
going from 1→2 matters far more than 50→51. Raw observation frequency is
still included (evidence, not noise) but at the smallest weight and
log-dampened. `recency_factor` (exponential decay, 5-year half-life) is the
smallest-weighted, purely secondary signal, exactly as the brief specifies
("recent occurrence as a secondary signal").

**Verified against real, if not raw, evidence**: the diagnostic fixture
built for §13 includes a term ("Repeated buzzword in one role") mentioned
241 times but confined to 15 roles, alongside "Solvency II" (90 roles, 90
mentions). The buzzword term ranks **#19**, the Solvency II cluster **#1** —
see §13's report output. This is the brief's core anti-dominance requirement
demonstrated on real ranked output, not just asserted.

The score is explicitly a **curation priority** — which pending cluster is
most worth reviewing next — never a claim that the underlying concept is
intrinsically more important; this framing is stated in the methodology
text, the API response, and the Vocabulary page's own copy.

Ties are broken deterministically (`role_count` desc, then
`observation_count` desc, then `cluster_key` asc —
`vocabulary_priority.sort_key`), so the queue's order never depends on
incidental row/dict iteration order; `test_priority_ordering_is_
deterministic_and_broad_beats_narrow_loud` asserts two identical requests
return byte-identical ordering.

---

## 3. Priority bands

Read directly off the raw evidence (not the composite score), so "why High"
never requires reverse-engineering a formula:

| Band | Rule |
|---|---|
| **Sparse** | observed in ≤1 distinct role |
| **High** | observed in ≥6 distinct roles **and** recurs (≥2 distinct values) across ≥2 of {year, seniority, country, career track} |
| **Medium** | observed in ≥3 distinct roles (and not High) |
| **Low** | everything else pending (i.e. exactly 2 distinct roles) |

Thresholds are fixed constants (`SPARSE_MAX_ROLE_COUNT`,
`MEDIUM_MIN_ROLE_COUNT`, `HIGH_MIN_ROLE_COUNT`,
`HIGH_MIN_BREADTH_DIMENSIONS`, `BREADTH_DIMENSION_MIN_DISTINCT` in
`vocabulary_priority.py`), chosen once from the reasoning above and never
adjusted to force a specific band size — see §13 for the actual resulting
distribution on the local diagnostic fixture (High: 133 of 1,481 clusters,
comfortably inside the brief's "~50–150 clusters" first-tranche target
without having been tuned toward it).

---

## 4. Queue: filters, search, pagination, sort

`GET /api/vocabulary/clusters` (`app/routes/vocabulary.py`) supports
`status` (pending default | accepted | rejected | all), `q` (search surface
form or canonical label), `min_role_count`, `min_observation_count`,
`observed_from`/`observed_to`, `country`, `seniority`, `type_code`, `band`,
and `sort` (priority default | occurrence | role_count | recent |
alphabetical), plus `limit`/`offset`. The default view is exactly `pending +
highest priority first`, per the brief.

Every filter/sort/paginate step happens server-side over the full computed
set; the response is one page (`{items, total, limit, offset}, ` the same
shape `GET /api/roles` already established) — the browser never receives
more than `limit` rows, regardless of how many of the 1,525 (or, in this
fixture, 1,481) clusters exist. `Vocabulary.tsx` never fetches "all
proposals"; it fetches one page at a time and pages via Previous/Next,
mirroring `Dashboard.tsx`'s existing pagination UI.

---

## 5. The review card

`ClusterCard` (`frontend/src/pages/Vocabulary.tsx`) shows, compactly:
canonical label + aliases, priority band badge, role/observation counts,
year span, countries, seniority levels, a capped sample of example role
titles (each linking to `/roles/:id`), the priority score, and any noise/
sparse flags — everything the brief's §4 lists, without opening the
database. "Show more evidence" expands to the full surface-form list,
cluster key, first/last observed dates, career tracks, and nearest-existing-
concept similarity. Raw advert text is never shown.

---

## 6. Cluster actions: accept / reject / merge

`app/vocabulary_curation.py` reuses, rather than reimplements, the existing
proposal-resolution core: `resolve_surface_form_group` (moved here from
`routes/concepts.py`, behaviour byte-for-byte unchanged —
`test_legacy_resolve_endpoints_still_work_after_refactor` guards this).
"Accept" = `accept_new` (creates exactly one concept, aliases every other
surface form onto it). "Merge into existing concept" = `accept_alias`
(already exactly that semantically — every surface form becomes an alias of
the chosen concept, no second concept created). "Reject" preserves
proposal/audit history (`status='rejected'`, no row deleted, no concept/
alias created).

**Idempotency** is layered on top, in `accept_cluster`/`reject_cluster`/
`merge_cluster`: each first checks whether the cluster already has zero
pending members. If so, and every member agrees on the same prior
resolution, the call returns that same result with `idempotent_replay:
true` instead of erroring or creating a duplicate concept. If the cluster
was already resolved to something *else* (e.g. accepted, now being
re-rejected), the call is a `409` rather than silently overwriting a prior
human decision. `test_accept_cluster_is_idempotent_on_retry` and
`test_accept_cluster_conflicts_if_already_resolved_differently` cover both
paths.

"Merge into…" in the UI is a small live-search combobox over
`GET /api/concepts?status=active&q=...` (an existing endpoint, no new
search API needed) rather than a single long dropdown of every accepted
concept.

---

## 7. Batch review

Batch accept/reject (`POST /api/vocabulary/clusters/batch`, preview at
`/batch/preview`) always takes an explicit list of `cluster_key`s the user
selected in the UI — there is no "select all pending" or "accept every
High-band cluster" affordance anywhere in this module or its routes. The
frontend's own "select all on this page" checkbox only ever toggles the
`limit`-bounded set of rows currently visible (never "every cluster matching
the filter across all pages"), and every batch still goes through the same
explicit confirm step before anything writes.

**Confirmation, before any write**: `preview_batch` (read-only) reports
exactly what the brief asks for — clusters ready, resulting concepts,
alias estimate, and role-skill observations affected — verified end-to-end
in the browser smoke test (a batch-accept of 2 clusters correctly previewed
"2 new canonical concepts... ~2 aliases... 72 role-skill observations will
be mapped" before the user confirmed).

**Whole-batch transactional atomicity** (brief §14's required, documented
per-cluster semantics): `execute_batch` issues its writes with no
per-item `SAVEPOINT`, inside the single `db_cursor()` transaction the route
handler already holds. The first failure (an unknown/no-longer-pending
cluster, a canonical-name collision, a validation error) propagates as a
Python exception straight out of that `with db_cursor() as cur:` block,
which rolls back the **entire** connection — nothing from earlier, already-
"succeeded" items in the same batch call is left committed either.
`test_batch_accept_is_all_or_nothing_on_failure` proves this directly: a
2-item batch where the second item collides on canonical name leaves the
*first* item still `pending`, not accepted, after the call fails. This is
deliberately simpler than `vocabulary_bootstrap.persist_candidate_
capabilities`'s per-candidate `SAVEPOINT` pattern (that one wants one bad
candidate to not block the rest of an unattended bootstrap run) — a
user-selected batch review action is one deliberate unit of work, so
failing it as one unit is the safer default, and is what's documented here.

---

## 8. Noise / low-information flags

`app/vocabulary_priority.py::noise_flags` — deterministic, lexical rules
only, never an LLM call (brief §8 is explicit about this): `single_role`,
`single_observation` (evidence-volume flags), `long_phrase` (>6 words or
>60 chars), `possible_fragment` (leading/trailing conjunction/preposition,
2+ commas, "etc"/"n/a"/"tbd"-style boilerplate), `employer_or_process_
specific` (substrings like "in-house", "bespoke", "our team"), and
`malformed` (empty, or no alphabetic character at all). Flags are advisory
badges on the review card only — nothing in this pass ever auto-rejects or
auto-deletes a flagged cluster; `test_no_automatic_acceptance_endpoint_
exists` and the batch-only-on-explicit-selection design both guard the
"never automatic" side of this from the acceptance direction too.

---

## 9. Preserving existing distinctions

No lexical-clustering behaviour changed in this pass — `cluster_key_for`
(docs/18 §6.1) is untouched. The brief's worked examples (reserving vs.
pricing, Solvency II vs. IFRS 17, Python vs. R, capital modelling vs.
internal model validation, stakeholder management vs. people management,
actuarial modelling vs. financial modelling) all remain distinct clusters
under the existing curated synonym-seed-list + generic spelling/pluralisation
rules; `test_cluster_key_does_not_over_collapse_distinct_concepts`
(pre-existing) still passes unchanged, and this pass adds no new clustering
rule that could erode that conservatism. Prioritisation and curation
actions in this pass operate strictly on top of the clusters that pass
already produced — nothing here re-groups, merges, or re-keys surface forms.

---

## 10. Empty-vocabulary messaging

`GET /api/vocabulary/progress` returns `canonical_vocabulary_curated:
accepted_concepts > 0` — the one signal to read before rendering a
"no match"-style message anywhere canonical vocabulary is consulted. The
Vocabulary page itself shows this explicitly: *"Canonical vocabulary is not
yet curated. 0 concepts have been accepted yet — this is not the same as
'no match found' elsewhere in this app... which says nothing about the
strength of any evidence."* The banner disappears the moment the first
concept is accepted (verified in both the pytest suite and the live browser
smoke test — screenshot `11_accepted_filter.png`).

This pass scopes the fix to the vocabulary/curation surface itself,
deliberately not re-touching `Profile360.tsx`'s existing, already-correct
`no_candidates_available`/`declined_all_candidates` distinction from docs/18
§11, and deliberately not extending into `Comparison.tsx`/
`RoleRequirements.tsx`/`CapabilityCoverage.tsx` — those already read
reasonably honestly today (`RoleRequirements.tsx` already points users at
the Vocabulary page rather than implying a role has no requirements) and
reworking them risks drifting into the capability/Phase-4-adjacent
territory §15 excludes. Recorded here as a documented scope boundary, not a
silent gap.

---

## 11. Progress metrics

`GET /api/vocabulary/progress`: total/pending/accepted/rejected clusters,
high-priority-pending count, accepted concept count, and observations
mapped vs. unresolved (with percentage). The Vocabulary page states plainly
that some observations may legitimately remain unresolved or rejected —
100% mapping is never implied as the goal, per the brief.

---

## 12. Production safety

Nothing in this pass writes to, curates, or connects to production. Every
test runs against a disposable local Postgres 16 + pgvector
(`backend/tests/conftest.py`, unchanged mechanism). The only script that can
read a production `DATABASE_URL` at all
(`backend/scripts/vocabulary_priority_report.py`, §13) never imports
`accept_cluster`/`reject_cluster`/`merge_cluster`/`execute_batch` — it is
wired to be structurally incapable of writing, not just instructed not to.
No batch review action, accept, reject, or merge was run against production
proposal rows as part of this implementation/testing pass.

---

## 13. First-tranche diagnostic report

`backend/scripts/vocabulary_priority_report.py` — read-only, same
deliberate CLI-not-API-button posture as `bootstrap_vocabulary.py` (an
operator points `DATABASE_URL` at whatever database they choose; the script
itself has no opinion and no write path). Reports total clusters ranked,
band distribution, top-N clusters (role/observation counts, year span,
aliases, countries/seniority counts), single-role/single-observation/
noise-flagged counts, and cumulative observation/role coverage for
accepting the top 25/50/100/150 clusters.

**This pass could not run it against real production** — §0's environment
constraint. What follows instead is that script's real output against a
**local, disposable, clearly-labelled fixture** sized to approximate the
brief's stated production shape (307 roles; ~4,000 observations vs. the
stated 4,677; ~1,489 surface forms / 1,481 clusters vs. the stated
1,525/1,507; a long-tail frequency distribution with ~68% single-observation
clusters, close to the audit report's own ~87% singleton-surface-form-
cluster figure) — built by `seed_diagnostic_fixture.py` (not committed; a
one-off local generator), using a *deliberately explicit* mix: ~20 real
high-value terms taken directly from `BOOTSTRAP_DRYRUN_AUDIT_REPORT.md`'s
own sample cluster list (Solvency II, stakeholder management, IFRS 17,
Python, VBA, Prophet, ...) at realistic relative frequencies, 150 synthetic
medium-frequency terms, 300 synthetic 2-role terms, and ~1,010 synthetic
single-role/single-observation terms (including a handful of deliberately
noisy/fragment-like strings) standing in for the corpus's genuine long tail
this sandbox cannot see. **These are illustrative numbers demonstrating the
methodology's real behaviour on realistically-shaped data, not a
prediction of production's actual top-50 list** — running the same,
unmodified script against the real `DATABASE_URL` is the only way to get
production's true numbers, and doing so is read-only and safe per §12.

Full output: see the deliverable report (chat turn) and
`vocabulary_priority_report.py`'s own `--json` mode for machine-readable
output. Headline results on the fixture: High 133 / Medium 38 / Low 300 /
Sparse 1,010 (of 1,481); top 50 acceptance would map an estimated 37.7% of
unresolved observations across 99.0% of roles-with-unresolved-observations;
top 100 would reach 50.7%/100%. The anti-dominance property is directly
visible in the ranked output (§2).

---

## 14. Tests

`backend/tests/test_vocabulary_priority.py` (14 tests, pure/no DB) and
`backend/tests/test_vocabulary_curation.py` (25 tests, real Postgres 16 +
pgvector) cover: cluster aggregation, deterministic priority ordering,
priority bands, anti-dominance (raw mentions vs. distinct-role breadth,
both as unit tests on `ClusterSignals` and as an integration test seeding
real roles/observations), filtering, pagination (25 clusters paged at 7 per
page, no duplicates/gaps), search, cluster acceptance, alias creation,
observation mapping, idempotent acceptance, rejection, merge into an
existing concept, batch accept, batch reject, batch all-or-nothing failure
(the transaction-boundary test), no-automatic-acceptance (an empty batch is
a validation error, not "select everything"; scoring/listing never mutates
status; no accept-all-shaped route exists), progress counts, sparse/noise
flags end-to-end, empty-vocabulary messaging, and a regression guard that
the legacy `/api/concepts/proposals/resolve*` endpoints are unchanged after
the `resolve_surface_form_group` move. No database test is skipped.

Both full backend suite runs (`pytest -q`, `TEST_DATABASE_URL` pointed at a
disposable local Postgres 16 + pgvector) passed: **329 passed, 0 failed, 0
skipped**, twice.

Frontend: `tsc -b` clean, `vite build` succeeds (one pre-existing "chunk
>500kB" advisory warning, unrelated to this pass — the Space page's
three.js/react-three-fiber dependency, not new code here), `oxlint` clean
(exit 0, zero findings). No frontend test framework exists in this repo, so
per the brief a live browser smoke test was run instead: Playwright against
this sandbox's pre-installed Chromium, driving a local backend+frontend dev
server pair against a seeded local Postgres fixture (54 roles, 289
observations, 36 pending clusters spanning all four bands). Every flow was
exercised — empty-vocabulary banner, methodology panel, band/search
filters, evidence expansion, accept, batch-select + batch-accept with its
confirmation modal, merge (live concept search), reject, status-filter
switch, and pagination — with **zero console or page errors** throughout
(screenshots retained for the deliverable report).

---

## 15. Scope exclusions — unchanged

Per the brief, this pass implements none of: capability proposal writes,
automatic `component_of` acceptance, Phase 4 compensation, `d_archetype_
comp`, `d_gap_value`, monetary ranking, learning-time/transition difficulty,
Space animation/time travel, historical role re-extraction, or any review of
the three partial roles / the zero-skill SCOR role / the ten legacy pilot
roles. These remain exactly the backlog docs/18 §13/§14 already recorded.

---

## 16. Limitations, stated plainly

- The diagnostic report in §13 runs on a synthetic fixture, not real
  production data — see that section's own caveats. In particular, the
  fixture's country/seniority/career_track values are drawn independently
  and uniformly at random per role, which makes the High band's
  cross-dimension-breadth check easier to satisfy than it would likely be
  in a real corpus with genuine correlation structure (a niche regulatory
  term more likely to cluster within one country/seniority band in real
  data than in this fixture) — the fixture's exact High/Medium split should
  not be read as a production prediction, only its *ordering behaviour* and
  *mechanism* should be.
- Filtering by `country`/`seniority` in the queue API is an exact,
  case-sensitive string match against whatever free-text values
  `role_instance.country`/`seniority_level` already contain — there is no
  country-name normalisation layer (consistent with docs/18 §15's own
  `region_for_country` precedent: a small, honest, unnormalised surface
  rather than a guessed one).
- Accepted/rejected clusters in the queue (`status=accepted|rejected`) show
  audit information (what happened, onto what, when) rather than a
  re-derived role/year/country breakdown — see §1's documented boundary.
- Batch review supports accept and reject, not merge — merging is
  inherently a single-target search interaction (brief §5's own framing);
  bulk-merging many clusters onto one target was judged unlikely to be a
  common enough curation motion to justify a second batch UI, and was not
  requested by the brief's batch section (§6 only lists accept/reject).
