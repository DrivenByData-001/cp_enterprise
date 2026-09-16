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

`db.role_skills_display` — Role Detail's own consumer of the same two
sources — went through two designs. The first (`role_skills_with_fallback`)
gave `role_skill_observation` *precedence*: prefer it whenever any exists,
fall back to `requirement_claim` only when it's completely empty. A third
code-review pass rejected that design outright, not just a bug in it: for
any role with *both* kinds of evidence, a stale or unreviewed legacy signal
could outrank, or silently stand in for, a human-reviewed decision on the
very same concept. Two examples that design could never get right,
regardless of how the veto inside it was tuned (§5's history):

- Legacy Python = required; a curator grades the reviewed claim to
  preferred (same concept). Observations still exist for this role, so the
  observation-preferred branch runs — and shows stale "Python · required"
  forever, because it never looks at `requirement_claim` except to veto.
- Legacy Python = required; a curator remaps it to SQL. Python is
  correctly vetoed (§5) — but the branch that would show the *accepted SQL
  claim* never runs at all, because non-empty `role_skill_observation`
  means requirement_claim is never consulted for anything except the veto.
  The concept doesn't just fail to update — it disappears, and the real
  accepted replacement never appears in its place.

`role_skills_display` replaces it with two clearly separate lists rather
than one fallback-merged one, reusing `role_requirements.
load_role_requirements` directly instead of re-deriving the two-source
split with its own SQL:

- **`skills`** ("Reviewed requirements" in the UI): exactly the loader's
  *claim*-sourced items — never its own observation-fallback items (see
  below for where those go instead). A concept with a current accepted
  claim always shows that claim's current data here, however it got there
  — graded, remapped, or original — because this is the same accepted-
  claim authority analysis already uses, not a second, independently-
  maintained notion of "current."
- **`legacy_skills`** ("Legacy skills" in the UI, with an explicit "not yet
  reviewed" label): every `role_skill_observation` **not** already
  represented in `skills` (by `resolved_concept_id`) and not vetoed
  (`vetoed_concept_ids`, §5). A pre-curation-gate role with no claim history
  at all therefore shows *everything* here — real evidence, but now
  honestly labelled as legacy/unreviewed instead of mixed into a list that
  implies a human looked at it.

Both examples above now resolve correctly: grading Python leaves it in
`skills` with the fresh value (its observation is suppressed from
`legacy_skills` by the concept-id overlap, never shown stale); remapping to
SQL puts SQL in `skills` and drops Python from both lists (vetoed), never
"disappears with nothing in its place." Never merged at the item level in
either direction: `legacy_skills` is exactly the *leftover* evidence, not
a section decorated with reviewed items too.

`db.build_role_view` (`GET /api/roles/{id}`) exposes both as `skills` and
`legacy_skills`; `app/role_context.py`'s Day-in-the-Life generation reads
the concatenation of both as prompt evidence — that generation step isn't
the *display* this split exists to keep honest, and narrowing its evidence
to reviewed claims only would silently regress every legacy-imported role
(the large majority of the corpus) back to the pre-2026-fix "no evidence"
problem docs/21 already solved once.

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
complete" even though both currently have zero current claims);
`needs_reextraction` (§12 — count of distinct concepts a now-*resolved*
vocabulary proposal this role contributed to still has no claim for); and a
`complete` flag — `unreviewed == 0 AND unresolved_proposals == 0 AND
needs_reextraction == 0`, vacuously true for a role with none of the three.
A role can have every one of its claims accepted while still carrying
pending, unresolved proposals for the same document — those requirements
are just as excluded from analysis as an unreviewed claim would be, so
`complete` must not be true while any remain; an earlier version of this
build defined `complete` from the claim counts alone and missed this.

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
deriving `complete` from claims alone. `needs_reextraction` (§12) gets the
same treatment on all three surfaces once it existed: folded into the
count, with its own "(including terms newly added to the vocabulary
awaiting re-extraction)" clause when non-zero — the Review requirements
page's own local `complete` recomputation (it derives claim-status counts
from the claims already on screen rather than re-fetching them, §3) also
had to start tracking it explicitly, the same way it already tracked
`unresolved_proposals` separately for the same reason: neither count can be
derived from the claim list itself.

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
own function for `db.role_skills_display`'s `legacy_skills` list (§1) to
reuse — but it adds one refinement that consumer needs and
`load_role_requirements` structurally never did: it only vetoes a concept
when **no current, accepted** claim for that same concept exists. A second
code-review pass found that without this, *grading* an accepted claim
(required → preferred, same concept, nothing else changed) tripped the
veto by itself — the old row's `review_status` flips to `corrected` exactly
as a real correction's does, even though a live accepted claim for the
concept exists right now — and (in that design's now-replaced
`role_skills_with_fallback` shape) silently dropped the whole concept from
display, rather than just leaving a stale `requirement_type` behind.
`load_role_requirements` never had this failure mode: reaching its
fallback branch already requires the *entire role* to have zero accepted
claims anywhere (§1), so a concept-level "but is there a current successor"
question can never arise there — either the role has an accepted claim
somewhere, and the whole role reads from `requirement_claim` instead of
observations at all, or it doesn't, and no concept on it can have a
"current accepted successor" to check for. Nothing that reuses
`vetoed_concept_ids` against a raw `role_skill_observation` list (as
`role_skills_display`'s `legacy_skills` does now, and its predecessor did
too) has that all-or-nothing switch, so the same raw condition needs the
extra check wherever it's applied per concept.

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

The *unresolved* side (a surface form that still doesn't map to any
concept) has a parallel rerun concern, at the occurrence level rather than
the claim level: `concept_proposal_occurrence`'s own
`requirement_type`/`basis`/`evidence_span`/`document_id`/
`extraction_run_id` (migration 0022, §12) used to be written once, on
first insert, and never touched again (`ON CONFLICT ... DO NOTHING`) — a
rerun that read the same still-unresolved term *better* the second time
(more surrounding context, a cleaner sentence) couldn't improve the stored
occurrence, and §12's `resolve_occurrences_for_concept` would go on to
build a claim from whichever reading happened to be captured first,
however weak. The upsert now refreshes on conflict, but only when this
run's `requirement_type` is at least as strong as what's already stored
(`role_requirements.requirement_type_rank_sql` — the identical ranking
§12 uses to pick a winner across several occurrences, so "which reading
wins" means the same thing in both places). A weaker rerun changes
nothing — including provenance, which stays pinned to whichever run
actually produced the stronger reading rather than drifting to whatever
ran most recently. All five refreshed fields move together as one
decision, never a stronger `requirement_type` paired with a different
run's `document_id`/`evidence_span`.
`test_requirement_claims.py::test_unresolved_rerun_refreshes_occurrence_when_the_new_reading_is_stronger`
and `::test_unresolved_rerun_does_not_downgrade_occurrence_with_a_weaker_reading`
cover both directions through the real `extract_role_requirements` pipeline.

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

Migration 0022 adds `requirement_type`/`basis`/`evidence_span` to
`concept_proposal_occurrence` (§12's per-occurrence data for rebuilding a
faithful claim once its proposal resolves) and, in the same file, a
best-effort backfill for any proposal already resolved before it runs.
That backfill is a guaranteed no-op against any database first encountering
this migration — the columns it reads don't exist until the `ALTER TABLE`
immediately above it runs, so no pre-existing row can carry them — kept
correct and safe to replay regardless, the same posture as migration
0021's own backfill. It mirrors §12's own three-way outcome in SQL rather
than Python: link an occurrence to a claim that already covers its (role,
concept) pair, insert a fresh unreviewed claim where the occurrence carries
enough data (one per (role, resolved concept), `DISTINCT ON`, never two
current claims for the same pair), or leave the rest for §12's live
re-extraction signal. It also has to guard against a case Python's version
learned the hard way (below): `requirement_claim`'s own `CHECK` constraint
(migration 0003) requires a non-null `evidence_span` whenever `basis` is
`'stated'`/`'implied'` — the backfill's `WHERE` clause excludes any
occurrence that wouldn't satisfy it, rather than letting the `INSERT` fail
outright.

## 11. Production-data impact

The semantics change means a role that previously showed analytical
requirements sourced only from unreviewed claims will show fewer (or none,
falling back to legacy observations where available) until those claims
are actually reviewed. That is the intended effect of closing the gap this
build exists to close, not a bug to route around by auto-accepting old AI
output. See the build's completion report for the production
accepted/unreviewed/rejected/corrected claim counts at the time of this
change, if production access was available when it was written.

## 12. Vocabulary proposal resolution: closing the acceptance gap

`unresolved_proposals` (§2) covers a proposal that is still *pending*. It
does not cover what happens the moment a curator resolves one — accepts it
as a new concept, or merges it into an existing one
(`vocabulary_curation.resolve_surface_form_group`, statuses
`accepted_new`/`accepted_alias`). A third code-review pass found that
resolution alone silently lost the requirement the proposal represented:
the proposal stops being pending (so `unresolved_proposals` correctly drops
it), but nothing created a `requirement_claim` for any role that produced
it. Resolving the *vocabulary term* was being treated as equivalent to
accepting it as *every contributing role's requirement* — a different
decision nobody actually made. A role could extract ten requirements, have
the AI fail to map one, watch a curator later accept that one term into
the vocabulary, and see its review report `complete` with one of its ten
extracted requirements never having entered analysis at all.

`role_requirements.resolve_occurrences_for_concept(cur, proposal_ids,
resolved_concept_id)` closes this. `resolve_surface_form_group` calls it
once, immediately after an `accept_new`/`accept_alias` resolution, over
every proposal id the resolution just moved (the whole cluster, not one
surface form at a time — a role that hit two clustered surface forms for
the same concept must get exactly one claim, not two, which migration
0020's unique index would reject outright). When a role produced *more
than one* occurrence of the resolved concept (that same multi-surface-form
case, or simply two extraction runs against it), exactly one is chosen to
build the claim from — deterministically, by `requirement_type` strength
(`required` > `preferred` > `contextual` > missing,
`role_requirements.requirement_type_rank_sql`), never by which occurrence
happened to be created first. Picking the oldest regardless of strength
would let a vague "contextual" mention from one posting section silently
outrank a "required" reading from another, purely because it was extracted
a moment earlier — a role that hit a concept once as merely contextual and
again as required must build its claim from the *stronger*, more
informative reading. Ties (including two occurrences of equal strength)
fall back to earliest `created_at`, kept only as a stable, deterministic
resolution rather than a meaningful preference.
`test_vocabulary_curation.py::test_accepting_a_cluster_picks_the_strongest_occurrence_even_when_it_is_not_the_oldest`
proves the ordering is driven by strength, not creation time, by
constructing the two deliberately out of sync.

For every role that ever produced an occurrence of one of those proposals
(`concept_proposal_occurrence`, migration 0021), exactly one of three
things happens with the occurrence chosen above — nothing is ever
fabricated:

1. **Already covered.** A current claim already exists for (role,
   resolved concept), from any source — a prior manual add, a prior
   extraction, a prior resolution. Nothing to do.
2. **Enough to build one.** No current claim exists, but the occurrence
   carries `requirement_type` (migration 0022 — only true for an
   occurrence `extraction.py` wrote *after* that migration; never true
   retroactively) and, if `basis` is `'stated'`/`'implied'`, a non-null
   `evidence_span` (`requirement_claim`'s own `CHECK` constraint, migration
   0003, requires one — an occurrence claiming `'stated'` with no span
   should never happen from a real extraction run, since
   `span_validation.validate_span` already rejected that item otherwise,
   but this is still just data, so the check happens rather than letting a
   raw `CheckViolation` surface). A fresh **unreviewed** `requirement_claim`
   is created, carrying whatever `basis`/`evidence_span`/`document_id`/
   `extraction_run_id` provenance the original extraction captured. Still
   unreviewed, deliberately: accepting a vocabulary term is a decision
   about the *concept*, not a verdict on whether it is actually this
   role's requirement — a human still has to look at it, exactly as any
   other extracted requirement would need.
3. **Not enough.** No current claim exists and the occurrence has no
   `requirement_type` (it predates migration 0022, or came from migration
   0021's own document-id-matching backfill, neither of which could ever
   carry one) or fails the `CHECK`-constraint guard above. Nothing is
   created — there is nothing faithful to build from, and inventing a
   `requirement_type` would be exactly the fabrication doc 11's evidence
   discipline forbids.

Case 3 needs its own visibility, or the gap just reopens under a different
name. `needs_reextraction` (§2) is a **live-derived** count, not a stored
flag: per role, the number of distinct concepts with a resolved-proposal
occurrence and *still* no current claim for it
(`role_requirements._ROLES_NEEDING_REEXTRACTION_SQL`). Nothing updates it
directly, and nothing has to: the moment any current claim appears for
that (role, concept) pair — most commonly by running requirement
extraction again for the role, now that the term is a real vocabulary
concept and will resolve normally through the ordinary extraction path —
the count drops on its own, because the query that produces it is a plain
`NOT EXISTS` re-checked on every read. `complete` (§2) folds it in
alongside `unreviewed`/`unresolved_proposals`, and Role Detail/Comparison/
the Review requirements page all name it in their pending-review wording
the same way they already named `unresolved_proposals` (§2's own wording
history).

The full lifecycle, proven end to end in
`test_vocabulary_curation.py::test_accepting_a_cluster_creates_an_unreviewed_claim_for_the_source_aware_role`:
an unresolved term extracts as a `concept_proposal_occurrence` → a curator
accepts it in Vocabulary → the role gains a fresh unreviewed claim and
stays reported incomplete (now for a *different*, equally legitimate
reason — `unreviewed`, not `unresolved_proposals`) → a human accepts that
claim → the requirement participates in `load_role_requirements` and every
consumer built on it, same as any other accepted claim.

## 13. Vocabulary evidence includes source-aware occurrences

A smaller, related gap in the same area: `vocabulary_curation.py`'s
cluster evidence (role/year/country/seniority/example-role counts that
drive priority scoring and the review card, docs/19 §1) was computed only
from `jobber.role_skill_observation`. A role captured via source-aware
ingest + extract-requirements never gets one of those rows at all (§1) —
its unresolved terms only ever appear via `concept_proposal_occurrence` —
so a cluster made up entirely of such roles' occurrences showed zero
role_count/observation_count/countries/seniority/examples despite having
real evidence, understating its priority and evidence flags for no reason
connected to how little evidence actually existed.
`build_pending_cluster_index` and `_group_evidence_map` (the split-cluster
preview) now also join `concept_proposal_occurrence`/`role_instance`
(directly on `concept_proposal_id`, not surface-form text — the occurrence
already carries that link precisely) and fold a role's contribution into
the same counters regardless of source, so a role's evidence counts once,
the same way, whichever pipeline captured it. The global
one-proposal-per-surface-form curation model (`concept_proposal` itself)
is untouched — this only widens where its *evidence* is allowed to come
from. See docs/19 §1 for the full mechanism.
