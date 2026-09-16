# Career workflow improvements

This change implements the eight recommendations from the repository review.

## Consistent role discovery
Dashboard concept filters and facet counts now use the canonical requirement-evidence loader used by comparison and economics. Current, accepted requirement claims take precedence at role level — an unreviewed claim is a visible proposal only and is not treated as usable evidence merely because nobody has rejected it yet. When no usable claims exist, mapped legacy observations participate, except for a concept a curator has explicitly rejected or corrected away for that role; a claim that is merely superseded without ever having been reviewed (for instance by a newer unreviewed proposal from a re-extraction) does not veto. Facets count distinct observed postings, excluding target roles.

Filters and pagination are represented in the URL. Changing the facet category clears its concept and resets pagination in the same navigation. Obsolete responses cannot overwrite current results. Failed requests offer retry; empty filtered results offer clear filters, while an empty all-years collection offers import.

## Evidence-based intermediate roles
The target path assesses every captured posting, including roles without embeddings. It loads role requirements in bulk and evaluates each distinct concept once per request through the existing capability/atomic evidence engines.

Candidate and target requirements are deduplicated by concept. Only fully evidenced requirements count as covered; partial and personal assertions remain unverified. Candidates require reviewed role requirements with at least one explicitly required item, reviewed target requirements, some accepted person-side evidence, and overlap with target evidence gaps.

A potential intermediate step must have fewer unresolved required requirements than the target, or equal unresolved required requirements with greater overall evidence coverage. Identical target-like roles are not automatically called stepping stones.

Eligible candidates rank by the harmonic mean of:
- fraction of their requirements supported by accepted evidence; and
- fraction of target evidence gaps involved in the candidate role.

Unresolved required counts, profile similarity, target similarity and stable ID break ties. This is a transparent navigation heuristic, not a validated hiring probability or a guarantee that working in a role will develop a skill. Sparse, pending, already-evidenced or non-progressing cases are labelled explicitly. Legacy requirement counts and posting dates remain visible.

## Guided import and target creation
The main posting flow is Add posting → Review details → Review requirements → Compare. PDF text is previewed before capture. Details can be suggested with AI or edited manually. Advanced JSON and original one-step imports remain available. Bulk results name each file and support retry of confirmed failures only; ambiguous network outcomes ask users to inspect roles before resubmitting.

Target creation accepts plain form inputs. AI generation produces an editable draft of tasks, skill examples, study subjects, grounding, feasibility and matching requirements. A manual route works without an AI key. Preview records the supplied draft document and extraction attempt but does not create a target role; Save target performs creation after review. The existing configured AI provider/model is reused. No model-generated target data establishes person-side evidence.

## Actionable comparison
Users can record examples, save notes, undo assertions, and explicitly send examples into the existing profile360 review queue. Assertions apply across roles for the same concept. A development action belongs to a role and concept, with a title, note, optional date and open/done status. It can be completed, reopened or deleted. Action completion never changes capability coverage.

Requirement review, assertion and deletion failures are visible and recoverable. Successful mutations are distinguished from subsequent refresh failures so users do not accidentally repeat an already-saved change.

## Navigation and accessibility
Navigation groups Explore roles, My evidence and Manage vocabulary, with Add posting directly available. Forms use persistent labels and stack on narrow screens. Workflow steps expose the current step; keyboard focus, a skip link, error/status announcements and a not-found route improve navigation.

## Database and rollout
Migrations 0018 + 0019. 0018 adds development_action and its role index, adds a role/concept requirement index, and permits audited target_decompose runs without a vocabulary version; it preserves the existing task allowlist. 0019 adds the target-analysis revision counters (target_analysis_revision) and the d_target_evidence/d_target_path cache tables behind the stepping-stone ranking above, with statement-level triggers on the underlying evidence/role/requirement tables that invalidate them automatically on any change. Normal backend startup applies pending migrations. No production migration or deployment was performed during implementation.

## Validation
- Backend integration suite uses disposable Postgres with pgvector, never production.
- Unit tests cover ranking balance, duplicates, missing data, pending review, assertions and non-progressing candidates.
- Frontend regressions cover stale filter responses, URL state, retries, PDF preview, per-file retries, editable target review, manual fallback and comparison actions.
- CI runs backend tests, frontend tests, lint and the production build.
- Browser smoke checks use mocked API data at desktop and mobile sizes; live AI output quality is not assessed by these tests.
