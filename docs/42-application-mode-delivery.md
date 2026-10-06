# Application Mode first-slice delivery — 6 October 2026

## Implemented

* Application-owned evidence overview and requirement review under
  `/applications/:id/prepare/:conceptId?`, with the global shell removed.
* Continue entry from Applications and the existing workspace. The former
  Comparison → supporting evidence escape now enters the scoped requirement.
* Durable source selections, four human dispositions, rationale, source-change
  detection, optimistic review revisions and application resume. Saving a review
  also records its resume point. A failed save keeps the user in the editor.
* Scoped inspection and career-claim search. Selection belongs to the application
  and does not mutate shared Profile360 mappings or evidence.
* Reviewed new examples can be saved as application notes and explicitly queued
  through the existing Profile360 promotion route. Pending is visibly distinct
  from accepted evidence; a failed promotion can reuse the saved note on retry.
* Generator context uses curated source selections and decisions, excludes broad
  profile snapshots/unselected episode narratives, and retains only linked episode
  chronology for CV construction. Incomplete/stale reviews block new generation;
  existing documents remain readable. Uncurated applications keep their previous
  source assembly and fingerprint format.
* Design contract for ownership, acceptance, checkpoints, migration, and immutable
  submission packages in [41-application-mode.md](41-application-mode.md).

## Validation

* TypeScript/Vite production build, oxlint and bundle budget passed. Initial gzip
  bundle is approximately 87.6 KB against the 150 KB budget.
* 393 frontend tests passed across 28 files.
* 11 backend contract tests passed (`python -m unittest discover -s unit_tests`).
* Four Chromium browser tests passed: complete save/refresh/exit/resume journey;
  conflicting save and recovered edits; mobile source-change/pending-promotion
  flow; removed requirement recovery. APIs in these browser tests are fixtures.
* Desktop/mobile screenshots were inspected. No horizontal overflow at 390 px.
* Supplemental PGlite smoke validation applied migration 0035 to minimal parent
  tables and checked inserts, revision increments, constraints, resume and cascade.
  This is not a substitute for the Python/Postgres integration suite.
* 82 selected database integration tests were skipped because no disposable
  PostgreSQL server was available, including the three new integration tests.
  Production databases and deployed services were not modified.

## Required before declaring the complete evidence slice finished

1. Identify/access the Profile360 owning repository/service and implement its
   accepted-addition/correction response contract. The current queue has no
   canonical accepted-result identifiers, so immediate accepted write-back and
   automatic reuse cannot yet be implemented honestly. Queue processing must not
   be interpreted as acceptance. Existing accepted claims can be searched and
   selected manually after review.
2. Run database integration tests with disposable PostgreSQL + pgvector and
   validate the authenticated flow against the real Profile360 integration.

## Deliberate later migrations

The immutable submission package is designed, not implemented. Existing submission
events and statuses retain their prior behaviour; no historical snapshot guarantee
is claimed. Opportunity/requirement/vocabulary curation, positioning, documents,
and interviews still use the existing workspace, reached through an explicit exit.
This is the first vertical slice, not the full report's end-to-end redesign.

Review editing currently uses explicit Save, with browser-session draft recovery
and Save & exit flushing the review and any typed application example. It is not
server autosave on every keystroke. Accepted work and resume are durable server
records; unsaved local drafts are browser-specific. The comparison engine's
concept-level requirement grouping is retained. Profile360 search is bounded to
30 matching career claims per query; canonical source reads are per candidate,
so query batching is a follow-up if larger evidence sets warrant it.
