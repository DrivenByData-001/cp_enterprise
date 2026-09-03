# 16 — Phase 3: Capability Engine, Compositional Coverage, Structural Role Fit

**Status:** implemented
**Related:** `docs/11-capability-model-design.md` (original design; §3.1/§5.4/§9/§12.6
are what this phase actually builds), `docs/14-phase2-postgres-architecture.md`
(confirmed schema this phase reads), `docs/15-security-and-rls.md` (unchanged
by this phase)

---

## 0. What Phase 3 answers

> What can I evidence? What is only partially evidenced? Where is the proof?
> What does a target role require? Which requirements are evidenced /
> partial / user-asserted / no-evidence-found, and why? Which required gaps
> are structurally blocking?

It does **not** answer what a gap is worth, what to learn first, or how long
anything would take — that is Phase 4/5 (§13 below).

---

## 1. Capability catalogue

A capability is exactly one `jobber.concept` row (`type_code = 'capability'`)
plus one `jobber.capability_detail` row, created and edited together through
`POST/GET/PUT /api/capabilities[/…]` (`backend/app/routes/capabilities.py`).
Nothing new was created for the catalogue itself — `capability_detail` has
existed since 0002 (Phase 2) and was simply unused until now.

Capabilities are deliberately curated, never auto-generated from co-occurring
atoms — there is no code path that creates a capability concept from
extraction output. AI involvement is limited to Pass C (§7) suggesting a
*mapping* of existing person-side evidence onto an *existing* capability;
nothing in this build lets a model invent a new capability concept or write
an accepted capability requirement.

`economic_salience` is stored and returned but nowhere read by the engine —
grep confirms `capability_engine.py` never touches it. It is catalogue
metadata for a future phase, per brief §5.

## 2. `component_of` edges

Uses the existing `jobber.concept_edge` / `concept_edge_rule` — no new edge
table. Direction is `atomic concept --component_of--> capability`
(`from_concept_id` = atom, `to_concept_id` = capability). `necessity` is one
of `core | supporting | contextual`, defined in `capability_engine.py`'s
module docstring exactly as the brief specifies.

Validation is layered, not frontend-only:

1. **Grammar** — `capability_engine.is_valid_edge` checks
   `(relation='component_of', from_type=<atom's type_code>, to_type='capability')`
   against `jobber.concept_edge_rule` before any edge write
   (`routes/capabilities.py::add_component`). production's 21 seeded grammar
   rows already cover all eight atomic types as legal `component_of` sources
   (confirmed via `backend/scripts/local_baseline.sql`, which mirrors the
   live seed) — no new grammar rows were needed.
2. **Necessity/status enum** — enforced twice: Pydantic
   (`models.ComponentEdgeCreate`/`ComponentEdgeUpdate`) at the API boundary,
   and a guarded `CHECK` constraint added in 0006
   (`concept_edge_status_check`, `concept_edge_necessity_check`) at the
   database boundary. `concept_edge` carried zero rows through Phases 1-2
   (doc 11 §11's own Phase 1 build note), so adding these constraints now
   could not violate any existing row.
3. Every write goes through `origin='curator', status='accepted'`
   (`routes/capabilities.py::add_component`) — there is no code path where an
   AI-proposed edge becomes accepted automatically, satisfying brief §6's
   "Do not let AI-created proposals become accepted edges automatically."
   Only `status='accepted'` edges are ever read by the engine
   (`capability_engine._components`).

`concept_edge.weight` is untouched — still nullable, still unused by any
query in this codebase, exactly as brief §12 requires ("may be retained for
future ranking but should not become an unexplained probability").

## 3. Depth/autonomy ordering

Centralised in `capability_engine.py`:

```python
DEPTH_LEVELS    = ["exposed", "applied", "owned", "set_standard"]
AUTONOMY_LEVELS = ["assisted", "independent", "directed_others", "accountable"]
```

Comparison is always by `list.index(...)`, never lexical. `normalize_depth`/
`normalize_autonomy` recognise only an exact, case-folded match to these
tokens; anything else — `None`, an empty string, or genuinely different
profile360 vocabulary this engine doesn't recognise — normalizes to `None`
("unknown"). `depth_meets`/`autonomy_meets` (`_meets` internally) implement
exactly one rule: a `None` minimum is vacuously satisfied (no requirement);
a `None` value can *never* satisfy a set minimum, however low. This is what
brief §7 requires verbatim ("do not fabricate missing modifiers"), and is
covered directly by `test_capability_engine.py::test_unknown_value_never_satisfies_even_the_lowest_minimum`.

## 4. Person-side evidence inputs

Only two jobber tables feed the engine, exactly as scoped: accepted rows in
`jobber.profile360_claim_mapping` / `jobber.profile360_capability_mapping`
(joined to their live `profile360.claims` / `profile360.capabilities` rows
for display/modifier text), plus `jobber.person_capability_assertion` for
the `user_asserted` fallback. `capability_engine.py` issues **only SELECT**
statements against `profile360` — the read-only invariant from docs/15 §4 is
unbroken (the one exception anywhere in this codebase remains
`profile360_promotion.py`, untouched by Phase 3).

- **Rejected** mappings (`review_status='rejected'`) are excluded from every
  query the engine runs — they simply never appear in a result set.
- **Unreviewed** direct mappings *are* read, and can produce `partial` (never
  `evidenced`) — this mirrors the Phase 2 comparison behaviour exactly
  (`comparison.py`'s old `_person_side` already treated an unreviewed
  mapping as `partial`), now centralised in
  `capability_engine.atomic_concept_evidence` and reused for capability-typed
  requirements too via the direct-evidence path.
  `test_unreviewed_mapping_cannot_produce_evidenced` proves this for the
  capability path specifically.
- **Compositional** evidence deliberately reads only `review_status='accepted'`
  mappings, stricter than the direct-evidence path. This is the load-bearing
  guard against doc 11 §12.6's "most dangerous single line of code" —
  composition is inference-heavy enough already without also crediting
  still-unreviewed component evidence.

## 5. Direct vs. compositional evidence — and the profile360 schema adaptation

**Direct evidence** exists when an accepted/unreviewed mapping points
straight at the capability concept, through either mapping table
(`capability_engine._direct_evidence`). The two mapping kinds are *not*
symmetric, because their underlying profile360 rows are not symmetric:

| Source | Carries `depth`? | Carries `autonomy`? |
|---|---|---|
| `profile360_claim_mapping` → `profile360.claims` | Yes — `claims.depth`, a confirmed column | No column on `claims` at all |
| `profile360_capability_mapping` → `profile360.capabilities` | No | No |

`profile360.episodes` (not `claims`) is the confirmed table that actually
carries an `autonomy` column (docs/14 §5). So for a claim-sourced item, this
engine reads `depth` from the claim itself and `autonomy` from
`claims.episode_id → profile360.episodes.autonomy` — a deliberate,
documented adaptation forced by the real, confirmed schema, not an
invention. A capability-mapping-sourced item has **no** modifier data at
all, by construction of `profile360.capabilities`'s confirmed shape.

The direct consequence, worth stating plainly because it is easy to miss:
**a `profile360_capability_mapping` alone can never make a capability
`evidenced`**, once that capability has any depth/autonomy requirement
(`min_depth` always has a value — it defaults to `owned`). §3's "unknown
never satisfies a set minimum" rule applies to it exactly as to any other
missing modifier. It still counts as direct evidence (`directly_claimed =
true`) and still caps the result at `partial`, distinguishable in the trace
by `source_kind: "capability"` with `depth`/`autonomy` both `null`. This is
tested explicitly: `test_direct_profile360_capability_mapping_alone_is_partial_not_evidenced`.

**Compositional evidence** (`capability_engine._compositional_evidence`):
load the capability's accepted `component_of` edges, split by necessity,
fetch accepted claim mappings onto those atomic concepts, group by
`profile360.claims.episode_id`, and evaluate each episode independently —
never merging components found in different episodes. The "strongest
supported composition" is the single episode scoring highest on
`(core_met, supporting_met, contextual_met)`, tie-broken by the episode's
own recency. This is what makes
`test_components_split_across_episodes_never_credited_together` pass: using
a tool in one job and a function five years later in a different job is
never stitched into one compositional claim.

**Component completeness** (`requires_all_core`/`min_core_required`, brief
§12): when `requires_all_core` is true, "complete" means every core
component was met in the best episode. When false, the smallest additive
schema extension needed to express a transparent rule was one nullable
column — `capability_detail.min_core_required INTEGER` (0006) — read by
`capability_engine._core_required`; if the curator left it unset, the
documented default is "at least one core component" (never a fabricated
percentage). `core_complete` is informational (surfaced in the trace, and
shown in the catalogue/coverage UI); it does **not** by itself decide
`partial` vs `not_found` — that is the separate "meaningful" gate below.

**"Meaningful" compositional evidence** — the gate between `partial` and
falling through to `user_asserted`/`not_found` — requires **at least one
core component** to be met in the best episode whenever the capability has
any core components at all (`_composition_verdict`). Supporting/contextual
evidence alone, with zero core components touched, is never "meaningful" —
this is the direct code expression of doc 11 §12.6's warning that core
components are "necessary to make the capability claim compositionally
plausible" in the first place. A capability curated with *only*
supporting/contextual components (no core atoms at all) falls back to
"any supporting or contextual evidence is meaningful", since there is no
core bar to fail to clear.

## 6. The absolute invariant: composition alone never reaches `evidenced`

`derive_capability_coverage`'s precedence, in order:

1. An **accepted** direct mapping whose depth/autonomy both meet the
   capability's thresholds → `evidenced`.
2. An accepted direct mapping that exists but doesn't meet threshold → `partial`.
3. An unreviewed direct mapping (no accepted one) → `partial`.
4. No qualifying direct evidence, but meaningful compositional evidence → `partial`.
5. No evidence, but a `jobber.person_capability_assertion` row → `user_asserted`.
6. Otherwise → `not_found`.

Steps 2-4 all resolve to `partial` and nothing promotes composition past it
— there is no code path in `derive_capability_coverage` that assigns
`evidenced` without first checking `direct_status == "evidenced"` at step 1.
`test_composition_alone_cannot_produce_evidenced` builds a capability with
every core/supporting/contextual component evidenced at the maximum depth,
in one episode, with zero direct evidence, and asserts the result stays
`partial` — this is the single test that most directly protects the
invariant brief §10 calls "fundamental."

## 7. Pass C — capability attribution

`extraction.map_profile360_claim_to_capability` is the existing
`_map_profile360_row` helper (Phase 2's `map_profile360_claim`/
`map_profile360_capability`), called with `type_codes=["capability"]` — the
same closed-candidate-list, model-may-only-choose-or-decline contract, now
applied to claims instead of only to profile360's own synthesized
capability rows. `extraction.run_pass_c` batches this over profile360 claims
that have no existing capability mapping row yet. Both write into the
**same** `jobber.profile360_claim_mapping` table Phase 2 already built a
review queue for (`Profile360.tsx`'s "Claims" tab), so no new review UI was
needed — accepting a Pass C mapping is indistinguishable, by design, from
accepting any other claim mapping. Nothing Pass C produces is ever written
with `review_status` other than `'unreviewed'`.

## 8. Temporal derivation

`last_demonstrated` = the latest `COALESCE(episode.end_date, today)` over
every episode that contributed qualifying evidence (direct evidence's own
episode, plus every episode touched by compositional evidence — not only
the "best" one). `years_active` = the union, not sum, of those episodes'
`[start_date, effective_end]` spans (`capability_engine.union_years_active`
— a standard sorted interval merge; touching/overlapping intervals merge
into one).

Both are computed fresh on every read/rebuild — neither is ever written
back into `profile360` or into any jobber source table (doc 11 §5.4).

**Missing/insufficient dates**: an episode with no `start_date` at all is
excluded entirely from both computations — there is nothing to place it on
a timeline, and inventing a value would be exactly the "false precision"
brief §13 warns against. An episode with a `start_date` but no `end_date` is
treated as open/ongoing and uses today as its effective end. `date_precision`
is read but not used to adjust the math further: Postgres `DATE` columns
already store whatever precision production captured, and there is no
sub-day math this codebase could add that would be more than a synthetic
guess.

## 9. `d_capability_coverage` / `d_role_fit`

Both created in `backend/migrations/0006_phase3_capability_derivations.sql`,
matching brief §14/§15's suggested shape closely, scoped to a single
authoritative profile360 evidence set — **no `person_id` column** (§14
below explains why). `coverage_score`/`fit_score` are real numbers but
`coverage_score` is written and never read back by any route or the
frontend; it exists only for a future internal ranking use, per brief §14's
explicit "for internal ordering only, never a percentage."

`trace` (JSONB) on both tables is the "machine-readable reason structure"
brief §11 requires — every status the engine assigns is explainable from
`trace` alone, without re-running the engine. See
`capability_engine.derive_capability_coverage`'s own trace construction and
`derive_role_fit`'s `trace.items[].detail` for the exact shape.

`d_role_fit.capabilities_required` generalises past its literal doc 11
naming: brief §16 explicitly requires supporting *both* capability-typed
and atomic-concept-typed requirements on the same role, so this field
counts every requirement item evaluated (of either kind), not only
capability-typed ones — `n_evidenced + n_partial + n_asserted + n_not_found`
always sums to it.

**Atomic-concept requirements** (brief §16): a `requirement_claim` pointing
at a non-capability concept is evaluated by
`capability_engine.atomic_concept_evidence` — the same direct-mapping logic
Phase 2's `comparison.py::_person_side` used, now centralised so capability
and atomic paths share one status-assignment module (brief §11: "do not
duplicate status logic between API routes"; the old private
`comparison.py::_person_side` was deleted, not kept alongside). No atomic
requirement is ever silently promoted into a capability requirement — the
engine only ever evaluates `d_capability_coverage`-style logic for a
`requirement_claim` whose `concept.type_code = 'capability'`, full stop.
`capabilities_containing` supplies the one deliberately-context-only piece
of cross-referencing the brief invites ("that relationship can be displayed
as context") — surfaced in the API/UI as `component_of`, never used to
change a status.

**Blocking vs. unverified** (brief §17): `blocking_gaps` is exactly the set
of `required` items whose status is `not_found`. `unverified_required` is
`required` items in `partial`/`user_asserted` — visibly distinct from a hard
gap, never silently treated as satisfied. `preferred`/`contextual` gaps
appear in neither list.

**`fit_score`** (brief §18): `coverage_engine._fit_score` — a weighted mean
of per-item status points (`evidenced=1, partial=0.5, user_asserted=0.25,
not_found=0`), weighted by requirement type (`required=2, preferred=1,
contextual=0.5`). Deterministic, untrained, documented here and inline in
code. The API/UI never present it as a probability of getting the job, and
the structural counts are always shown first (`Comparison.tsx`: counts and
blocking/unverified cards render above the "secondary signals" line
carrying `fit_score`/`embedding_similarity`).

**`embedding_similarity`** (brief §19): reuses the exact existing
`ensure_profile_embedding` / `get_embedding` / `cosine_similarity` machinery
`routes/roles.py` already used for the Space/search similarity signal — no
new embedding logic. It is stored beside the structural verdict and never
read by any status-assignment code path; the tests
(`test_embedding_similarity_cannot_alter_structural_status`) build a role
whose embedding is deliberately similar to the profile while the underlying
capability has zero evidence, and assert the status still comes back
`not_found`.

## 10. Rebuild semantics and engine version

`ENGINE_VERSION = "capability-engine-v1"`, a hand-set string bumped only
when derivation semantics change (never a timestamp). It is stamped on
every `d_capability_coverage`/`d_role_fit` row and returned by
`POST /api/capabilities/rebuild`/`GET /api/capabilities/{id}/coverage`, so
it is always visible in derived output.

`rebuild_phase3_derivations` = `rebuild_capability_coverage` then
`rebuild_role_fit`. Each: derives fresh rows from source tables only, upserts
them (`ON CONFLICT ... DO UPDATE`), then deletes any derived row whose
concept/role no longer qualifies (deactivated capability, or a deleted
role). Re-running with unchanged source data is a no-op in effect —
`test_rebuild_is_idempotent` asserts the persisted `status`/`trace` are
byte-for-byte identical across two consecutive rebuilds. No extraction path
writes to either derived table directly; the only writers are
`capability_engine._persist_capability_coverage`/`_persist_role_fit`.

`POST /api/capabilities/rebuild` has no additional auth layer — consistent
with every other mutating endpoint in this single-operator, locally-trusted
build (docs/15 §1); this is not a new gap Phase 3 introduces.

## 11. `/api/comparison` upgrade

`routes/comparison.py::compare_role` is now a thin presentation layer over
`capability_engine.derive_role_fit`: it fetches the role, calls the engine
once, then enriches each item with the jobber.document detail the engine
deliberately doesn't know about (role-side document title/provenance/URL —
kept exactly as Phase 2 returned it) before returning. All of Phase 2's
existing response fields are unchanged in shape (`role`, `items[].concept`,
`items[].status`, `items[].role_side.*`, `items[].person_side.mappings`/
`.assertion`) — `test_comparison.py`'s five original Phase 2 tests pass
unmodified against the new implementation. New, additive fields:
`items[].person_side.component_of`/`.coverage` (capability-typed items
only), and top-level `blocking_gaps`, `unverified_required`, `fit_score`,
`embedding_similarity`, `engine_version`.

The `/assert`, `/assert/{id}` (retract), `/assert/{id}/promote` endpoints
are byte-for-byte unchanged from Phase 2 — the brief explicitly asked that
this pathway not be touched absent a regression, and none was found.

## 12. Frontend

- **`Capabilities.tsx`** (`/capabilities`) — catalogue curation: list with
  search/status filter, create, edit every `capability_detail` field
  (including the `min_core_required` toggle), activate/deactivate
  (`concept.status` active ⇄ deprecated), inspect/add/remove/re-necessity
  component edges with a live atomic-concept search, trigger a full
  rebuild. Accepted edges are the only ones ever shown or editable here —
  there is no "proposed capability edge" concept in this build (brief §6
  keeps proposal review to the existing concept-proposal queue on
  `Vocabulary.tsx`; component edges are curator-authored directly, always
  `status='accepted'` on write).
- **`CapabilityCoverage.tsx`** (`/coverage`) — the four-state personal view,
  grouped exactly `Evidenced / Partial / User asserted / No evidence found`,
  each capability expandable into direct evidence, core/supporting/
  contextual met-vs-missing for its strongest episode, strongest depth/
  autonomy, last demonstrated, years active, and the count of supporting
  profile360 claims traced.
- **`Comparison.tsx`** (`/comparison/:id`) — upgraded per brief §31: status
  counts and blocking/unverified-gap cards render first; `fit_score`/
  `embedding_similarity` are a single muted line at the bottom, explicitly
  labelled "secondary signals — not the result." No percentage-style
  pseudo-precision is shown anywhere (`coverage_score` is never rendered).

`tsc -b && vite build` and `oxlint src/` both pass clean (§15 below).

## 13. Evaluation (brief §24-26)

**Schema** (0006): `jobber.gold_document`/`gold_claim`/`eval_run` (UUID
adaptation of doc 11 §9.2, scoped to `jobber.document` — see the honest
scope note below) and `jobber.capability_gold_judgment` (Phase 3's own gate).

**Runner** (`backend/app/evaluation.py`, exposed at `GET /api/eval/report`):

| Metric | Computed from | When unlabelled |
|---|---|---|
| Span validity | Re-validates every stored `stated`/`implied` requirement_claim's span against its document | `measured: false` only if zero such claims exist yet |
| Concept-linking F1 | `gold_claim` vs. the system's own non-superseded `requirement_claim`s on the same gold documents | `measured: false`, no `gold_claim` rows |
| Modifier accuracy | — | **always `measured: false`** — see below |
| Proposals/document | Same computation as `GET /api/concepts/proposals/stats` | `measured: false`, no `job_posting` documents |
| Capability agreement | `capability_gold_judgment.expected_status` vs. `derive_capability_coverage(...).status`, run live | `measured: false`, no judgments |

**Honest scope note on span validity**: this recomputes an invariant
`span_validation.validate_span` already enforces at write time (a claim
whose proposed span fails validation is never stored — `extraction.py`), so
it is structurally expected to read `1.0` once any stated/implied claims
exist. It does *not* measure what fraction of the model's *raw* proposals
had valid spans before filtering (that per-run number lives only in each
`extraction_run.notes`, never aggregated). A value below 1.0 here would mean
the write-time invariant was bypassed — itself the useful signal.

**Honest scope note on modifier accuracy — permanently not applicable in
this build**: this codebase's own extraction pipeline
(`extract_role_requirements`) writes only `requirement_claim` rows, which
describe what a *role* demands, not depth/autonomy modifiers describing how
a *person* demonstrated something. There is no `evidence_claim` table in
this codebase (doc 11's, deliberately not built — see docs/14 §6) and no
extraction pipeline this build owns that produces a depth/autonomy-bearing
claim to score against gold. That extraction belongs to profile360, a
separate tool this build reads from but does not control or have a corpus
to hand-label. `evaluation.modifier_accuracy` always returns
`measured: false` with this explanation, never a fabricated number.

**Honest scope note on the 16-document gold set**: doc 11's original
stratification (5 actuarial-core, 3 adjacent, 4 CV/LinkedIn, 3 project
write-ups, 1 hard case) predates the Phase 2 architectural split. The
CV/LinkedIn/project-write-up strata describe *person-side* documents, which
now live in profile360 — a system this build reads from but never captures
documents into. The only stratum this codebase's own `extract_role_requirements`
pipeline can produce gold labels for is job-posting documents
(actuarial-core + adjacent). §16 below documents this as a permanent scope
boundary, not a temporary gap.

**Sample sizes in this build environment**: this container has no
credential for the real Supabase project and no captured production
corpus — the same constraint every phase since Phase 0 has recorded. Zero
real production-labelled `gold_claim`/`capability_gold_judgment` rows exist
here. `test_evaluation.py` proves the runner's arithmetic is correct against
small, controlled fixtures (perfect match, partial recall, agreement,
disagreement) — that is a test of the *machinery*, not a claim about
production quality. See the Phase 3 completion report for the exact
numbers this build could produce locally.

## 14. Known architectural adaptation from doc 11 (brief §41)

Doc 11's original `d_capability_coverage`/`d_role_fit` DDL carried a
`person_id` column, because it assumed a local `jobber.person` table. That
table does not exist in production (docs/14 §6/§9) and was never
reintroduced anywhere in this build — `grep -r person_id backend/` after
Phase 3 turns up nothing in `capability_engine.py`, the migration, or any
route. Both derived tables are keyed on the concept/role alone, scoped to
the single authoritative profile360 evidence set this application operates
against. `test_migration_compatibility.py::test_phase3_derived_tables_have_no_person_id`
asserts this at the schema level, not just by code review. Multi-person
support, if ever needed, is a future schema decision — nothing here
pre-empts it.

## 15. Validation

- `pytest`: run twice, `166 passed, 0 failed` both times (backend, real
  Postgres 16 + pgvector, never SQLite/mocked DB) — see the Phase 3
  completion report for the exact command and environment.
- `tsc -b`: clean.
- `vite build`: clean (one pre-existing chunk-size advisory, not an error).
- `oxlint src/`: clean.
- Migration compatibility: `test_migration_compatibility.py` extended with
  Phase 3-specific assertions (0006 recorded, UUID identity on every new
  table, no `person_id`, the new `CHECK` constraints actually reject bad
  data) — all pass against the same `local_baseline.sql`-bootstrapped
  database every other migration test already proves against.

## 16. Known limitations

- No real production gold/evaluation data exists in this build environment
  — see §13.
- Compositional evidence only considers `profile360.claims` mapped via
  `profile360_claim_mapping`; it does not read `profile360.evidence` or
  `profile360.claim_concepts` (both still in the reader's generic
  allowlist, docs/14 §5) — those would need their own confirmed-shape
  reasoning before being wired into a status-determining path, which this
  phase's scope did not call for.
- `capability_engine.py` recomputes `derive_capability_coverage` fresh
  inline whenever `derive_role_fit` needs it (rather than reading the
  persisted `d_capability_coverage` row), trading a small amount of
  redundant computation at this application's scale (~100-150 capabilities,
  ~23 roles) for freedom from any read-after-write ordering hazard between
  the two rebuild steps. Revisit only if this ever becomes a real
  performance bottleneck.
- The capability catalogue curation UI does not yet expose a bulk
  AI-suggestion review flow for *component* edges (only Pass C's *mapping*
  suggestions go through a review queue) — brief §22 scopes the catalogue
  itself to responsible, justified curation rather than a synthetic pile,
  and no AI-proposed-edge machinery was requested for this phase.

## 17. Phase 4 boundary

Not built, on purpose: compensation extraction, archetype compensation,
`d_gap_value`, monetary gap ranking, learning-time/transition-effort
estimates, adjacency judgment, prerequisite/adjacent/substitutable
capability graphs, automatic profile360 authoring. `economic_salience` sits
in the schema unused, ready for Phase 4 to start reading it — nothing here
reads it first.
