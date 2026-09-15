# Requirement review as a real human-curation gate

Review requirements was previously an Accept/Reject screen over AI output,
but `role_requirements.py`'s loader treated any non-rejected claim —
including a claim nobody had looked at — as authoritative. An unreviewed AI
suggestion could silently become the analytical basis for comparison,
capability/role fit, economics/archetype demand, role filters and facets,
and target/stepping-stone analysis, merely because the user had not gotten
around to rejecting it. This build closes that gap: AI extraction proposes
requirements, a human reviews them, and only what a human accepted (or
never needed reviewing, via the legacy fallback below) is analytically
authoritative.

## 1. The authoritative rule

`role_requirements.load_role_requirements(cur, role_instance_id)` — the one
canonical loader shared by `capability_engine.derive_role_fit`,
`economics_engine.derive_archetype_demand`, `routes/comparison.py`,
`routes/concepts.py`'s facets, the Dashboard concept filter, and
`stepping_stones.path_to_target` — now requires a `requirement_claim` to be
both current (`superseded_by IS NULL`) **and accepted**
(`review_status = 'accepted'`) to count as usable. Unreviewed and rejected
claims are excluded from the "does this role have usable claims" check as
well as the returned item list, so a role whose only claims are unreviewed
falls through to the legacy `role_skill_observation` fallback exactly as a
role with zero claims would — it is not treated as "claimed but empty."

`db.role_skills_with_fallback` — Role Detail's own consumer of
`requirement_claim`, with the *opposite* source precedence (prefers
`role_skill_observation`, falls back to `requirement_claim` only when that
is completely empty) — is held to the same accepted-only, current-only
rule within its own `requirement_claim` branch. This matters more than it
might look: a role captured through source-aware ingest always has an
empty `role_skill_observation` (skills=[] at ingest time), so this branch
is the *only* skills source such a role has. Before this build, a freshly
extracted, never-reviewed posting could show its unreviewed AI proposals as
if they were the role's established "skills" on the very page that now also
carries the "Requirements review pending" indicator saying otherwise.

## 2. Visibility without authority: the review-summary helper

Excluding unreviewed claims from the authoritative set must not make
pending review invisible. `role_requirements.load_requirement_review_summary(_bulk)`
returns, per role, current-claim counts by status (`accepted`,
`unreviewed`, `rejected`) and a `complete` flag (`unreviewed == 0` —
vacuously true for a role with no claims at all). Every UI surface that
could otherwise imply "this is the final requirement set" reads it:

- `GET /api/role-instances/{id}/requirements` returns it alongside the
  claim list, driving the Review requirements page's status summary.
- `GET /api/roles/{id}` (`db.build_role_view`) attaches it as
  `requirement_review`, driving Role Detail's "Requirements review
  pending" indicator.
- `GET /api/comparison/role/{id}` attaches it as `review_summary`, driving
  a banner on the Comparison page.
- `stepping_stones.path_to_target` reads it (bulk, once per request) and
  passes `pending`/`target_pending` counts into `assess_candidate`, which
  gates the assessment to `insufficient_evidence` whenever either side has
  a pending unreviewed claim — a completeness-sensitive conclusion
  (reachability, an intermediate step) must never be drawn from a
  partially-reviewed requirement set, accepted claims notwithstanding.

Economics/archetype demand is a corpus-wide aggregate (how many roles in an
archetype demand a capability), not a per-role judgement, so it is kept
consistent purely through the shared loader rather than annotated with a
per-row completeness flag — there is no single role whose "incompleteness"
an aggregate count could sensibly attach to.

## 3. Correction-aware review: Accept, Edit & accept, Reject, Reopen, Edit

`routes/role_instances.py` replaces the old single `POST .../review`
(`{action: accept|reject}`) endpoint with five dedicated actions, each
valid from exactly the state transitions its name implies (any other
attempt is a 409, and a superseded claim can never be acted on directly —
act on its current replacement instead):

- `POST .../{claim_id}/accept` — unreviewed → accepted, in place. No
  replacement row; the original claim and its `extraction_run_id` are
  untouched.
- `POST .../{claim_id}/reject` — unreviewed → rejected.
- `POST .../{claim_id}/reopen` — rejected → unreviewed (clears
  `reviewed_at`). Recovers an accidental Reject with no database
  intervention.
- `POST .../{claim_id}/edit` — the correction path, valid from either
  `unreviewed` ("Edit & accept" in the UI) or `accepted` ("Edit", so a
  review mistake is always recoverable later, not just at first review).
  Never overwrites the original claim in place: it inserts a *new* current
  claim with the corrected data, `review_status='accepted'`,
  `reviewed_at=now()` and `extraction_run_id=NULL` (it is not the output of
  any extraction run — it is what a human reviewer determined is true, and
  is recorded as such rather than falsely presenting itself as the
  untouched output of the original AI extraction), then marks the old
  claim `review_status='corrected'` and sets its `superseded_by` to the new
  row's id. The edit accepts a partial patch (concept/requirement_type/
  basis/importance/evidence_span, each optional — an omitted field keeps
  the original claim's value, matching `RoleMetadataUpdate`'s
  `exclude_unset` convention); a field-for-field-identical "edit" is
  treated as a plain Accept instead of manufacturing a no-op correction.
- `POST .../requirements` (no claim id) — "Add requirement": see §7.

Concept remapping only accepts an **active** vocabulary concept (a
deprecated or nonexistent id is a 400); the frontend's `ConceptPicker`
searches `GET /api/concepts?status=active&q=...`, debounced, reusing the
same pattern `TargetRequirementPicker` already established. Concept
*type* (`type_code` — tool/knowledge/capability/method/regulation/...) is a
property of the canonical vocabulary concept, not of this posting's claim
on it: the edit form shows it read-only with "Concept type comes from the
accepted vocabulary," and a link to `/vocabulary?focusConceptName=...`
(a small addition to `Vocabulary.tsx`/`VocabularyReviewView.tsx`'s existing
focus-request mechanism) for the user to change it globally if they believe
it's wrong. `requirement_type` (required/preferred/contextual) and `basis`
are posting-specific and belong to the claim, not the concept.

## 4. Provenance and span validation are server-side, always

`routes/role_instances.py::_validate_requirement_fields` re-validates every
edit and manual add — never trusting the frontend alone:

- `concept_id` must resolve to an active concept.
- `requirement_type` ∈ {required, preferred, contextual}; `basis` ∈
  {stated, implied, inferred, user_asserted}; `importance` ∈ {1..5} or
  blank.
- `stated`/`implied` require a linked document *and* an evidence span, and
  the span must be an exact substring of that document's immutable
  `content_text` (`span_validation.validate_span`, the same function
  `extraction.py` already used for AI-extracted spans).
- The linked document's `provenance_quality` must be `'original'` for
  `stated`/`implied` to be accepted at all — a legacy/reconstructed/unknown
  document can never back stronger evidence, however good the typed span
  looks, merely because the edit form allows it. This is a 400, not a
  silent downgrade, since the user explicitly asked for stated/implied.
- A span left over from a stronger basis is dropped (never carried as if
  still quoting the source) once the edit's basis no longer needs one.

## 5. Legacy observation fallback: the veto

Within the fallback branch (reached only when a role has zero usable
claims at all), a legacy `role_skill_observation` for a concept is excluded
if that concept's `requirement_claim` history on this role includes a
`rejected` or `corrected` row — real curator authority overrides a legacy
signal. The veto is keyed on `review_status IN ('rejected', 'corrected')`,
**not** merely `superseded_by IS NOT NULL`: §6's rerun supersession can
chain one unreviewed proposal's `superseded_by` to a fresher unreviewed
proposal with no human ever having looked at either. That is proposal
churn, not a decision, and per §1's core rule it must not gain veto power
it would otherwise never have had — "unreviewed claims must not suppress
the legacy observation fallback" has to hold even across a chain of
still-unreviewed proposals, not just for a single lone one. A claim that *is*
`corrected` always has `superseded_by` set too (the edit endpoint sets both
together in the same transaction), so no case that used to veto stops
vetoing.

## 6. Re-extraction: conservative duplicate/supersession handling

`extraction.py`'s claim-insertion loop now checks, per resolved concept,
for an existing *current* claim on the same (role, concept):

- If it is `accepted` or `rejected` — a human already decided this
  concept's requirement for this role — the new proposal is dropped
  entirely; extraction never revisits a human decision.
- If it is `unreviewed` and the new proposal is identical
  (`requirement_type`, `basis`, `evidence_span` all match) — no duplicate
  is inserted (`claims_deduplicated`).
- If it is `unreviewed` and the new proposal genuinely differs — the old
  proposal is superseded (`superseded_by` set, `review_status` stays
  `unreviewed` per §5) and the new one becomes current
  (`claims_superseded`).
- Otherwise (no existing current claim for that concept) — inserted as a
  fresh current unreviewed claim, same as before.

This is intentionally a small, exact-match check on one (role, concept)
pair — not a fuzzy claim-merging subsystem. The extraction summary surfaces
`claims_created`/`claims_superseded`/`claims_deduplicated` alongside the
existing `proposals_created`/`proposals_updated`/`rejected_span_count`, and
the Review requirements page's run summary reports them.

## 7. Manual "Add requirement"

`POST /api/role-instances/{id}/requirements` lets a curator add a
requirement the AI missed, always source-backed: `basis` is restricted to
`stated`/`implied` (both independently span-validated against the role's
own document via the same `_validate_requirement_fields`), so a user cannot
casually invent an unsupported employer requirement and present it as
source-stated. It saves directly as an accepted, current claim with
`extraction_run_id=NULL` — no fake AI provenance. If no suitable concept
exists, the same `ConceptPicker` routes to Vocabulary rather than creating
one inline.

## 8. Default listing excludes history; it stays visible on request

`GET /api/role-instances/{id}/requirements` now defaults to current claims
only (`superseded_by IS NULL`) — a corrected claim's superseded predecessor
no longer renders as a second, duplicate card next to its replacement.
`?history=true` deliberately opts into the full chain for audit purposes;
it is never mixed into the default review list.

## 9. Performance / cache invalidation

Migration 0019's statement-level trigger on `jobber.requirement_claim`
already invalidates the target-path cache (`jobber.target_analysis_revision.path`)
on any insert/update/delete — accept/edit/reject/reopen all just do plain
writes to that table, so no new invalidation wiring was needed; covered by
an integration test in `test_career_workflows.py` that performs a bare
`UPDATE ... SET review_status = 'accepted'` with no manual cache bump and
confirms the next request recomputes. The one new query this build adds to
the hot path (`load_requirement_review_summary_bulk`, one bulk statement,
not per-role) only runs on a cold/rebuild path — a cache hit still resolves
in exactly two database statements, unchanged; see
`test_target_mapping_cache.py::test_target_path_corpus_benchmark`
(849 cold queries against a 1,000-role/200-concept corpus, well under its
3,000-query budget; 2 warm queries).

## 10. No schema migration

`requirement_claim` already had `review_status` (including `corrected`),
`superseded_by` and a nullable `extraction_run_id` — this build is a
semantics and application-code change only.

## 11. Production-data impact

The semantics change means a role that previously showed analytical
requirements sourced only from unreviewed claims will show fewer (or none,
falling back to legacy observations where available) until those claims
are actually reviewed. That is the intended effect of closing the gap this
build exists to close, not a bug to route around by auto-accepting old AI
output. See the build's completion report for the production
accepted/unreviewed/rejected/corrected claim counts at the time of this
change, if production access was available when it was written.
