# Personal career workspace refresh

This frontend update makes the Phase 9 workspace easier to navigate, recover after failed requests, and use on smaller screens. It does not change evidence decisions, application status rules, API contracts, or database schemas.

## User-facing changes

- Home puts the next useful action first. Direction, progress, opportunities, and applications remain visible; supporting learning and market context can be expanded.
- A warm neutral and green theme, stronger text contrast, larger supporting text, softer cards, and a consistent heading hierarchy carry through the workspace. Desktop navigation uses a sidebar. Mobile keeps the four main destinations visible and puts secondary tools behind Menu. Existing dark-theme preferences and reduced-motion settings are respected.
- Explore explains Direction, Target, Pathway, Archetype, and Checkpoint in a short glossary. Opportunity cards give the role title and actions more prominence. Sign-in uses the same visual language.
- Applications use server-side status filtering and 25-item pagination. The URL retains status and offset; opening an application and returning restores that list position. Records beyond the former 200-item limit remain reachable.
- Application workspaces offer Overview, Evidence, Documents, and Interviews stages, plus All sections. The `section` query parameter provides direct links. Switching stages keeps the panels mounted, preserving unsaved form edits. This is in-memory preservation within the current application page, not draft persistence across reloads or navigation away.

## Reliability and loading

Home offers a retry after an initial failure and labels an existing view as potentially stale after a refresh failure. A successful checkpoint save is reported separately from its subsequent refresh. Retrying the read does not repeat the checkpoint write. Checkpoint creation is disabled while the view is loading or stale. Obsolete Home and Applications responses cannot overwrite newer results.

Application detail routes retain the Applications navigation highlight. Lazy route loading defers analytical pages until needed; loading and route-error states provide feedback and recovery.

Run `npm run build` followed by `npm run check:bundle` in `frontend`. The check counts the compressed entry and static JavaScript dependencies, excluding dynamic route chunks, and enforces a 150,000-byte budget in CI. The measured initial bundle fell from approximately 585 kB to 87 kB gzip. Large analytical routes still have their own larger bundles when opened.

## Validation and rollout

Regression coverage includes initial-load retry, checkpoint-save/refresh separation, application pagination beyond 200, server-side filtering, stale response handling, nested navigation, keyboard menu dismissal, and draft preservation between application stages. Existing application integration tests continue to exercise the full workspace through All sections.

Production-build browser checks use mocked API responses on desktop (1440 px) and mobile (390 px), including dark mode, navigation, stage switching, checkpoint recovery, and sign-in. The primary next-action button fits in the first 844 px mobile viewport. These checks do not assert production data or live-service behavior.

Deploy through the normal frontend release process after review. No new migration, backend rollout, or data backfill is required by this change. Existing backend migration requirements remain unchanged. Rollback is a frontend revert/rebuild.
