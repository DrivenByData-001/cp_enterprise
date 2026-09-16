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
is completely empty) — respects the same curation-gate authority in both
of its branches, though the rule takes a different shape in each:

- Its `requirement_claim`-fallback branch (reached only when
  `role_skill_observation` is completely empty) applies the identical
  accepted-only, current-only filter §1 describes. This matters more than
  it might look: a role captured through source-aware ingest always has an
  empty `role_skill_observation` (skills=[] at ingest time), so this branch
  is the *only* skills source such a role has. Before this build, a freshly
  extracted, never-reviewed posting could show its unreviewed AI proposals
  as if they were the role's established "skills" on the very page that now
  also carries the "Requirements review pending" indicator saying
  otherwise.
- Its `role_skill_observation`-preferred branch (the common case for older,
  legacy-imported roles) does *not* switch to requiring accepted claims —
  observations remain the display source whenever any exist, unchanged.
  But it now also applies `role_requirements.vetoed_concept_ids` (§5): a
  concept a curator has explicitly rejected, or corrected away with nothing
  current left behind for it, as a requirement for this role is excluded
  from the observation list too, so display can no longer contradict
  analysis (an old role whose "Python" requirement a human rejected would
  otherwise keep showing a "Python" skill chip forever, since that branch
  never consulted requirement_claim at all before this fix). Unlike this
  function's own claim-vs-observation switch above, that veto is evaluated
  per concept, independently of whether the role has any accepted claims
  elsewhere — a second code-review pass found that this branch's first
  version over-corrected as a result: *grading* an accepted claim (e.g.
  required → preferred, same concept — the edit endpoint marks the old row
  `corrected` even when the concept itself didn't change) also tripped the
  veto and silently dropped the concept's chip entirely, taking a
  currently-legitimate requirement down with the stale row it replaced.
  §5 covers the fix.

## 2. Visibility without authority: the review-summary helper

Excluding unreviewed claims from the authoritative set must not make
pending review invisible. `role_requirements.load_requirement_review_summary(_bulk)`
returns, per role, current-claim counts by status (`accepted`,
`unreviewed`, `rejected`); `unresolved_proposals` (pending
`jobber.concept_proposal` rows this role has actually contributed to — a
surface form extraction couldn't resolve to any concept becomes one of
these, *never* a `requirement_claim` at all, so it would otherwise be
invisible to this summary entirely); `extraction_attempted` (whether a
`requirement_extract` run has ever been recorded for this role, so a
consumer can tell "never extracted" apart from "reviewed and genuinely
complete" even though both currently have zero current claims); and a
`complete` flag — `unreviewed == 0 AND unresolved_proposals == 0`, vacuously
true for a role with neither. A role can have every one of its claims
accepted while still carrying pending, unresolved proposals for the same
document — those requirements are just as excluded from analysis as an
unreviewed claim would be, so `complete` must not be true while any remain;
an earlier version of this build defined `complete` from the claim counts
alone and missed this.

`jobber.concept_proposal` is deduplicated globally by surface form (one
curation decision per term, however many postings mention it), and its own
`document_id`/`extraction_run_id` columns are set once, on first insert,
and never revisited (`document_id = COALESCE(document_id, %s)` in
`extraction.py`) — a second role whose extraction later produces the exact
same still-pending term bumps `occurrence_count` but stays invisible
through those columns, which still point at whichever role happened to hit
it first. A second code-review pass caught the consequence: role B could
extract the same unresolved term role A already produced, get every other
claim of its own accepted, and be reported `complete` — the term was
"resolved" only from role A's point of view. Migration 0021 adds
`jobber.concept_proposal_occurrence`, a per-(proposal, role) link populated
on every extraction (`extraction.py`) independently of `concept_proposal`'s
own dedup; `unresolved_proposals` now counts through that table, so every
role that has ever contributed a still-pending term sees it, not only the
first.

Every UI surface that could otherwise imply "this is the final requirement
set" reads this summary:

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

Role Detail's indicator and the Comparison banner originally interpolated
only `unreviewed` into their copy, so a role blocked solely by unresolved
vocabulary terms (`unreviewed == 0`, `unresolved_proposals > 0`) read as "0
items not yet reviewed" — technically gated correctly (`complete` was still
`false`), but naming a count of zero as the reason. Both now add
`unresolved_proposals` into the displayed count and, whenever any are
present, append "(including terms not yet matched to the vocabulary)" —
matching the Review requirements page, which already surfaced this
correctly since it reads the server-computed count directly rather than
deriving `complete` from claims alone.

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
- `POST .../{claim_id}/reject` — unreviewed → rejected, in place; **or**
  accepted → rejected ("un-accepting"). A review mistake must be
  recoverable even after Accept — Edit alone cannot express "this isn't a
  requirement at all." Un-accepting never mutates the accepted decision in
  place: it goes through the same non-destructive supersession `edit` uses
  (below), producing a *new* current `rejected` row while the original
  accepted claim is preserved as `corrected` history, exactly as any other
  correction would.
- `POST .../{claim_id}/reopen` — rejected → unreviewed (clears
  `reviewed_at`). Recovers an accidental Reject with no database
  intervention, regardless of whether that rejected row came from a plain
  Reject or from un-accepting.
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
  `exclude_unset` convention). A field-for-field-identical patch is
  rejected as a no-op **server-side** (not only by the frontend, which
  independently collapses it into a plain Accept before ever calling
  `/edit`): it behaves as a plain Accept (or a true no-op, if already
  accepted) rather than manufacturing a pointless replacement/corrected
  pair, so a direct API call bypassing the frontend gets the same
  guarantee. Remapping onto a concept this role already has a separate
  *current* claim for — whether via `/edit` or `POST .../requirements`
  below — is rejected with a 409 rather than creating a second current
  claim for the same concept (see §3a).
- `POST .../requirements` (no claim id) — "Add requirement": see §7.

### 3a. At most one current claim per (role, concept)

Two current claims for the same (role, concept) would double-count in
`capability_engine.derive_role_fit` (which iterates every row the
canonical loader returns) and break the rerun-dedup code's assumption
(§6) that at most one current claim exists per (role, concept) to check
against. Migration 0020 adds a partial unique index —
`UNIQUE (role_instance_id, concept_id) WHERE superseded_by IS NULL` — so
this is a database guarantee, not just an application-level convention
that a route handler could forget to check; `/edit` and
`POST .../requirements` also check it themselves first, so the user sees a
clear 409 ("this role already has a current requirement for that concept")
rather than a raw integrity error.

That index creates an ordering puzzle for every non-destructive correction
(`/edit`, and un-accepting via `/reject`): the old row must stop being
current (its slot freed) *before* the new row can claim that same (role,
concept) slot — but a same-concept correction (the common case: editing
type/basis/span/importance without remapping) would otherwise momentarily
need *both* rows current in the same transaction. Reversing the order —
freeing the old row first — runs into the opposite problem: `superseded_by`
is a foreign key pointing at the new row, which doesn't exist yet.
Migration 0020 resolves this by also marking that foreign key
`DEFERRABLE INITIALLY DEFERRED` (Postgres has no equivalent deferral for
partial unique indexes, so the FK is what has to give): application code
(`routes/role_instances.py::_supersede_with_new_claim`, and the equivalent
in `extraction.py`'s rerun path) generates the new row's id in Python
*first*, updates the old row's `superseded_by` to that not-yet-existing id,
then inserts the new row with that id — valid by commit, when the deferred
FK check actually runs, even though the new row doesn't exist yet at the
moment the old row's update executes.

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

Within `load_role_requirements`'s own fallback branch (reached only when a
role has zero usable claims *at all*), a legacy `role_skill_observation`
for a concept is excluded if that concept's `requirement_claim` history on
this role includes a `rejected` or `corrected` row — real curator authority
overrides a legacy signal. The veto is keyed on
`review_status IN ('rejected', 'corrected')`, **not** merely
`superseded_by IS NOT NULL`: §6's rerun supersession can chain one
unreviewed proposal's `superseded_by` to a fresher unreviewed proposal with
no human ever having looked at either. That is proposal churn, not a
decision, and per §1's core rule it must not gain veto power it would
otherwise never have had — "unreviewed claims must not suppress the legacy
observation fallback" has to hold even across a chain of still-unreviewed
proposals, not just for a single lone one. A claim that *is* `corrected`
always has `superseded_by` set too (the edit endpoint sets both together in
the same transaction), so no case that used to veto stops vetoing.

`role_requirements.vetoed_concept_ids` is the same veto extracted into its
own function for `db.role_skills_with_fallback`'s observation-preferred
branch (§1) to reuse — but it adds one refinement that function needs and
`load_role_requirements` structurally never did: it only vetoes a concept
when **no current, accepted** claim for that same concept exists. A second
code-review pass found that without this, *grading* an accepted claim
(required → preferred, same concept, nothing else changed) tripped the
veto by itself — the old row's `review_status` flips to `corrected` exactly
as a real correction's does, even though a live accepted claim for the
concept exists right now — and silently dropped the whole concept from
`role_skills_with_fallback`'s output, rather than just leaving a stale
requirement_type behind. `load_role_requirements` never had this failure
mode: reaching its fallback branch already requires the *entire role* to
have zero accepted claims anywhere (§1), so a concept-level "but is there a
current successor" question can never arise there — either the role has an
accepted claim somewhere, and the whole role reads from `requirement_claim`
instead of observations at all, or it doesn't, and no concept on it can
have a "current accepted successor" to check for. `role_skills_with_fallback`
has no such all-or-nothing switch inside its observation-preferred branch
(accepted claims are consulted only to veto, never to supply the branch's
rows), so the same raw condition needed the extra check once applied there
per concept.

## 6. Re-extraction: conservative duplicate/supersession handling

`extraction.py`'s claim-insertion loop now checks, per resolved concept,
for an existing *current* claim on the same (role, concept):

- If it is `accepted` or `rejected` — a human already decided this
  concept's requirement for this role — the new proposal is dropped
  entirely; extraction never revisits a human decision.
- If it is `unreviewed` and the new proposal is identical
  (`requirement_type`, `basis`, `evidence_span` all match — deliberately
  *not* `importance`, which isn't part of what a requirement *is* the way
  those three are, and plays no role in `capability_engine`'s fit
  calculation; a rerun that only reproduces a fresher importance guess is
  the same proposal, not a reason to churn the review queue) — no
  duplicate is inserted (`claims_deduplicated`).
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

## 10. Schema migration

`requirement_claim` already had `review_status` (including `corrected`),
`superseded_by` and a nullable `extraction_run_id`, so the initial build
was a semantics and application-code change only. A code-review pass
against that build found a genuine gap needing one: nothing stopped two
current claims from coexisting for the same (role, concept) (§3a).
Migration 0020 adds the partial unique index that closes it, plus makes
the `superseded_by` foreign key deferrable to keep non-destructive
correction working under that new constraint.

That unique index is only safe to add if no such duplicate already exists
wherever the migration runs. The *old* application code (before this
build's conservative rerun-dedup, §6) could produce exactly this kind of
duplicate — two current unreviewed claims for the same (role, concept) —
over repeated extraction, so a second code-review pass added an explicit
preflight: migration 0020 opens with a `DO` block that counts
`(role_instance_id, concept_id)` pairs having more than one current row and
`RAISE EXCEPTION`s with a diagnostic query and remediation guidance
(supersede all but one per pair, never silently delete) if it finds any,
*before* attempting `CREATE UNIQUE INDEX`. Without it, an ordinary
index-creation error would be the first anyone learned of a production
duplicate, mid-deploy, with no guidance on what to do about it.
`test_migration_compatibility.py::test_migration_0020_preflight_rejects_existing_duplicate_current_claims`
proves the preflight fires, by replaying every migration up to (but
excluding) 0020 against a fresh database, seeding that exact duplicate, and
confirming 0020's SQL raises rather than reaching the index.

Migration 0021 adds `jobber.concept_proposal_occurrence` (§2's
per-(proposal, role) attribution fix for `unresolved_proposals`), populated
going forward by `extraction.py` on every extraction and, for proposals
that already existed before this migration, backfilled best-effort by
matching each `concept_proposal`'s recorded `document_id` to a
`role_instance` — recovering the same "first role" attribution the old
`document_id` column already gave it, made explicit, though not recovering
attribution for any *other* role that hit the same term before this
migration ran (that history was never recorded anywhere to recover from).

Its own `extraction_run_id` column is nullable and references
`jobber.extraction_run` with no `ON DELETE` clause (`NO ACTION`) — the same
shape `jobber.concept_proposal.extraction_run_id` already had, which
`db.delete_role_instance` already had to null out explicitly before it can
delete a role's own extraction_run rows (that function's own docstring has
the full chain). Adding this table introduced a second such reference that
the same delete path now has to clear too, or deleting any role that ever
produced an unresolved proposal fails with a `ForeignKeyViolation` instead
of the 200 every other role delete gets —
`test_delete_role_with_extraction_run_history_succeeds` (already exercising
exactly this shape for `concept_proposal`) caught it immediately. Unlike
`concept_proposal`, the occurrence row itself does not survive: its
`role_instance_id` is `NOT NULL` and `ON DELETE CASCADE`, so once nulling
its `extraction_run_id` clears the way, the role_instance `DELETE` a few
lines later cascades the occurrence row away with it — nulling only exists
to let that cascade reach it at all.

## 11. Production-data impact

The semantics change means a role that previously showed analytical
requirements sourced only from unreviewed claims will show fewer (or none,
falling back to legacy observations where available) until those claims
are actually reviewed. That is the intended effect of closing the gap this
build exists to close, not a bug to route around by auto-accepting old AI
output. See the build's completion report for the production
accepted/unreviewed/rejected/corrected claim counts at the time of this
change, if production access was available when it was written.
