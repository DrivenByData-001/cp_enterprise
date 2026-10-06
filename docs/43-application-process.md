# Application process: next slice

## Design and scope

The application overview recommends the next unfinished stage. The process rail
contains Opportunity, Requirements and Evidence; individual concepts appear only
inside Evidence. Positioning, Documents and Review/submission are visible as later
work, with an explicitly labelled exit to the existing workspace. Mobile uses a
labelled stage selector instead of a horizontally scrolling requirement rail.

Opportunity shows the source, role metadata and compensation when present. Reuse
the role metadata form and domain update helper. Save the deadline and a structured
package (kind, label, required, word limit, instructions) on the application.
Confirmation stores a fingerprint of the target plus package, not a progress flag.
Requirement confirmation similarly fingerprints the reviewed set and target.
Changing either preserves downstream work and makes its checkpoint need attention.

Resume stores a stage and optional entity, not a route. Old evidence bookmarks
redirect to the evidence stage; missing entities recover to that stage overview.
Legacy requirements remain inspectable during requirement curation. Only accepted
claim-backed requirements drive the new Evidence stage. Existing legacy application
decisions remain stored. Saving Opportunity adopts the new process contract;
generation for adopted applications requires current upstream checkpoints.

Requirement editing and extraction reuse the existing domain actions/components.
Vocabulary resolution is restricted server-side to proposals with occurrences on
this role. Resolution reuses the vocabulary domain, and can improve the shared
term globally; it never auto-accepts the resulting role requirement. Existing
concepts can be remapped within the requirement editor without global navigation.

## Role search

One query matches words across title, employer, location, source URL and posting
text. All words must match, potentially across fields; metadata matches rank ahead
of posting-text matches. Search runs before pagination, supports missing employers,
and keeps date/track filters explicit. Search all dates is a visible option. Missing
employers are labelled honestly and can be corrected through the role form.

## Acceptance checks

Exercise target edit → package confirmation → requirement curation → scoped term
resolution → accepted Evidence review, then exit/refresh/resume from each stage.
Check source/requirement changes, stale revisions, removed resume targets, legacy
bookmarks, many requirements, long titles and narrow screens. Audit every outbound
action: stage navigation, explicit workspace/exit, or external source link.

Positioning/Documents migration, immutable submission and interview migration are
deferred as specified in this phase's brief. No production changes are part of PR
validation; use the disposable PostgreSQL suite and fixture-backed browser journeys.

## Implemented boundaries and outbound-action audit

Migration 0037 adds application preparation data and a stage to the existing resume
record. It changes no Profile360 tables. Preparation is server-owned behind the
existing authenticated API. Requirements and vocabulary remain shared domains;
application checkpoints and selected evidence remain application-local.

Saved target/package context participates in generation fingerprints and is supplied
as role-side context, never as applicant evidence. Existing Positioning/CV/letter/
statement versions remain intact; their derived freshness appears beside the
explicit workspace exit. Saving a checkpoint does not adopt a generated document.

| Action | Destination / behavior |
| --- | --- |
| Stage rail, mobile selector, overview tasks | Same application; flush active editor before changing stage |
| Requirement card, concept search and scoped vocabulary resolution | Inline; no global Vocabulary navigation |
| Evidence selection, claim review/correction, queued examples | Inline; retained PR51 acceptance boundaries |
| Save & exit | Saves current work and resume context, then Applications |
| Save & leave mode for workspace | Explicitly labelled exit to this application's existing workspace |
| Open original posting | External HTTP(S) source, labelled and opened in a new tab |
| Missing application recovery | Explicit Return to Applications link |
| Skip-to-content accessibility link | In-page anchor only |

Unconfirmed vocabulary choices are browser-local drafts and are never silently
accepted by Save & exit. Requirement edit/add drafts and Opportunity drafts also
recover on refresh; failed/conflicting server saves retain edits and block stage
navigation. Unfinished add-requirement forms must be completed or cancelled.

Verified locally: frontend lint, typecheck/build, 393 unit tests, initial bundle
87,588 gzip bytes (150,000 budget), 11 backend pure contract tests, and 11 Playwright
journeys across the existing Evidence and new process/search suites. Inspected
desktop/mobile screenshots; mobile coverage includes a long title and 50 evidence
requirements. Database integration results are recorded in the PR's CI checks.

Role search uses literal case-insensitive word matching, not fuzzy or semantic
search. It retains selected filters and offers Search all dates. Existing corpus
scale permits the current server-side filtering/ranking approach; an indexed search
implementation can follow if corpus growth makes latency material.
