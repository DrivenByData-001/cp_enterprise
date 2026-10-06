# Application Mode implementation contract

## Scope and interaction design

Application owns the journey; Profile360 owns enduring career facts; role and
vocabulary domains retain their existing ownership. The first vertical slice is
Applications → Continue → evidence list → requirement → inspect/select evidence
or record a gap → save → exit → resume the same requirement. Desktop uses a
task rail and work area; narrow screens place the rail above the work area.

The eventual task groups are Opportunity, Evidence, Positioning, Documents,
Review & submission, then Interviews & outcome. Required material/word limits
belong to Opportunity so they can inform evidence selection. Evidence gaps are
advisory, never a claim that the person is incapable or ineligible to apply.

The first slice has an application-owned `/applications/:id/prepare` namespace.
Existing workspace URLs and PR #50 links remain valid. Documents, role curation
and interviews remain in the existing workspace until migrated; the explicit
workspace exit names this boundary. They must not be represented as completed
Application Mode stages. Global navigation is absent inside the new namespace.

## State and source ownership

* Application evidence decisions are keyed by application and requirement concept
  (the comparison engine's current granularity). Store selected source references,
  disposition, rationale, source fingerprint and an optimistic revision. If
  requirement claims need separate judgments in future, migrate this key explicitly.
* `covered`, `partial`, `investigate`, and `gap` are human judgments. `covered`
  requires selected evidence; `gap` has none. Candidate matches are not selections.
* A source/requirement fingerprint invalidates a review when its inputs change.
  Stale decisions remain visible with a reason and require renewed acceptance.
  The evidence task is complete only when all current requirements have a current
  reviewed disposition other than investigate. An empty list is not completion.
* Resume stores the concept being worked on, separately from review completion.
  A removed requirement falls back to the evidence list with an explanation.
* Saving a decision is atomic and rejects a stale source or stale revision (409).
  Unsaved edits remain visible on failure. Exit waits for save success. Browser
  Back retains browser semantics; task links provide explicit navigation.
* Generation must use selections for curated requirements, carry gap/rationale
  context, and refuse stale decisions. Existing uncurated applications retain the
  legacy generation path. Adopting a document is not proof that its claims are true.

## Profile360 acceptance contract

The current integration reads canonical Profile360 data and writes only to its
manual import queue. Queue processing alone does not prove acceptance. Proposed
additions/corrections must remain labelled pending until the owning service returns
accepted evidence identifiers. Never write directly to canonical tables merely
because the frontend is scoped to an application.

Required owner-service contract: idempotent proposal ID, operation (add/correct),
source revision for corrections, reviewed payload, actor/acceptance timestamp,
result state (pending/accepted/rejected/failed), canonical evidence IDs and source
revision. Link accepted IDs back to the originating application/requirement only
after validating them. Retries must not duplicate career facts; partial failures
must preserve the proposal and offer retry. A correction invalidates affected
application reviews but never rewrites submitted history.

Until that owner contract is available, the slice can queue a reviewed application
note, inspect existing canonical evidence, and select it. It must say that queued
material awaits Profile360 review and cannot claim immediate canonical acceptance.

## Submission contract (design now, migration separately)

Recording submission must atomically capture a new immutable package, selected
artifact IDs/versions, **resolved** CV chronology and rendered content, target and
reviewed requirements, selected evidence content/provenance, package requirements,
recorded submission time/channel, and any acknowledged gaps. A version ID or a
fingerprint alone is insufficient: the current CV renderer reads live episodes.
Retain exact exported bytes/hash when those are available; user-entered external
submission records must distinguish reported attachments from captured exports.

Use an idempotency key and optimistic package revision; submission/status/event
writes succeed together. A later correction creates an append-only correction or
replacement submission, linked to its predecessor. Legacy submitted statuses/events
have no reconstructable package guarantee; label them historical records without a
snapshot, never fabricate one from today's active artifacts. Interview generation
should use the submitted package, with later facts explicitly distinguished.

## Verification and release criteria

Exercise the full scoped journey, refresh, two tabs, changed/removed sources,
invalid application/requirement, failed saves, pending Profile360 import, and
mobile navigation. Enumerate links: scoped task link, explicit workspace exit,
or external source. Verify generated context contains selections and dispositions,
and excluded evidence cannot return through a second source path. Browser smoke
tests complement API/state tests; mocked browser APIs do not prove the live
Profile360 acceptance path. Record this distinction in the delivery report.
