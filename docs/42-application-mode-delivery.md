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
* Direct human acceptance for new or corrected career claims, after a separate
  review step. Confirmed against the `profile360` schema in Supabase `open-brain`.
  The backend atomically writes claim, provenance and audit receipt; optimistic
  source revisions reject conflicting corrections and operation UUIDs make retries
  idempotent. Accepted claims are immediately selectable in the same requirement.
  Existing application decisions become stale when selected claims change.
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
* Six Chromium browser tests passed: complete save/refresh/exit/resume journey;
  conflicting save and recovered edits; mobile source-change/pending-promotion
  flow; removed requirement recovery; explicit acceptance and immediate reuse;
  correction conflict with stable retry identity and retained draft. APIs in these
  browser tests are fixtures.
* Desktop/mobile screenshots of both evidence review and the new acceptance
  confirmation were inspected. No horizontal overflow at 390 px.
* Supplemental PGlite smoke validation applied migration 0035 to minimal parent
  tables and checked inserts, revision increments, constraints, resume and cascade.
  This is not a substitute for the Python/Postgres integration suite.
* Local database tests initially skipped because no disposable PostgreSQL server
  was available. GitHub Actions subsequently ran the full backend suite against
  PostgreSQL 16 + pgvector: **1,426 passed**, including the four new integration
  tests. Frontend CI also passed. Validated code commit: `4bdcb3f`;
  [CI run](https://github.com/DrivenByData-001/cp_enterprise/actions/runs/37517193430).
  Production databases and deployed services were not modified.
* Acceptance integration CI passed on code commit `d97ec80`: **1,429 backend
  tests passed** against PostgreSQL 16 + pgvector; frontend CI passed as well.
  This includes canonical/provenance rollback, idempotent acceptance, immediate
  generator reuse and correction invalidation across two applications.
  [Acceptance CI run](https://github.com/DrivenByData-001/cp_enterprise/actions/runs/37525781815).

## Profile360 integration and release boundary

The ownership ambiguity is resolved: Profile360 is a schema in the same Supabase
project, not a required separate service. Read-only catalog inspection confirmed
claims, episodes and provenance shapes and found no acceptance functions/triggers.
The authenticated backend now owns the narrow review-and-accept command described
in doc 41. The database reader and the older manual-import queue retain their roles.

Migration 0036 adds the acceptance audit in `jobber`; production Profile360 tables
are already present. The local disposable baseline now includes its existing
evidence table. The backend database role needs write access to claims/evidence
and the new audit table. No browser Supabase key or new public schema grant is used.
Catalog checks found no schema usage or claim read/write privileges for `anon` or
`authenticated`; disabled RLS on existing claims is not, by itself, public access.

Production was inspected only. No migration, test claim, deployment or live
acceptance was performed. The authenticated disposable database tests exercise
the actual SQL boundary; browser tests use API fixtures. An operator smoke check
with a real reviewed fact remains part of deployment verification.

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
