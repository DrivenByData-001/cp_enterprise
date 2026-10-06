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
