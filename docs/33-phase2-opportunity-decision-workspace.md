# Phase 2: Opportunity Decision Workspace

This phase restructures `/roles/:id` — an observed posting's detail page —
into a summary-first decision workspace, so the most decision-relevant facts
are visible before any diagnostic detail. It is a presentation/composition
change: every analytical engine it draws on (requirement review, the
structural comparison engine, compensation resolution, archetype
classification) is unchanged.

## What the workspace is for

When someone opens an observed posting, they should be able to tell, without
scrolling past raw advert text or operational review controls:

- what the role is (title, organisation, location, remote/employment
  information, posting date, with capture date shown as secondary
  provenance);
- how much of its requirements have been reviewed;
- how their own evidence compares against those requirements;
- what is currently known about its economics, and on what basis;
- that no career direction is selected yet, and where to go to work on one;
- what to do next.

The page is a decision surface, not a dump of every analytical module. This
is Phase 2 only: no application workspace, no CV/interview generation, no new
scoring model, no property-driven target discovery.

## Structural evidence vs. source detail

Everything in the Decision Summary (the top-of-page grid) is a **structural
fact already computed by an existing engine** — a count, a basis, a
provenance label. None of it is a verdict:

- **Requirements** reads `role.requirement_review` (the same counts
  `role_requirements.py` has always produced) — reviewed requirements, items
  still needing review, and unresolved vocabulary terms. A role that has
  never been through requirement extraction is labelled "Not yet extracted",
  never presented as "0 requirements, all caught up" — those are different
  facts, and confusing them would make a freshly-saved posting look
  fully reviewed when nothing has been reviewed at all.
- **Evidence** reads `GET /api/comparison/role/{id}` (`capability_engine`) —
  evidenced/partial/user-asserted/not-found counts, and blocking vs.
  unverified required gaps kept visibly distinct. "No accepted evidence
  found" is never presented as "you lack this capability" — the exact
  wording from the product brief is shown verbatim.
- **Economics** reads `GET /role-instances/{id}/compensation` — the
  resolved basis (advert-stated, market-estimate, legacy-estimate or
  insufficient-evidence), the figure, and the personal comparison, with
  source, currency and PAYE-vs-contract distinctions preserved exactly as
  the resolver returns them. Nothing here is recomputed or reformatted into
  a new figure.
- **Career direction** is an honest empty state — there is still no
  persistent selected-career-direction model (Home's cockpit says the same
  thing) — linking to Explore my future rather than guessing a saved target.

Below the summary, "What this role asks for" groups the same reviewed
requirements by required/preferred/contextual before the full chip list
further down the page, and the detailed Economics section keeps its
existing review lifecycle (extract stated compensation, correct/reject an
accepted figure, archetype review, the Pathways entry point) — those stay
exactly as they were, just positioned as detail rather than as the first
thing the page shows.

## Incomplete review is shown, never silently treated as complete

A role can have zero current requirement claims for two very different
reasons: nothing has been extracted yet, or extraction ran and the review
queue is genuinely empty. `requirement_review.extraction_attempted`
distinguishes them, and the workspace does too — "Not yet extracted" is a
different message from "0 items still need review". Wherever review is
incomplete, the exact pre-existing "Requirements review pending" wording
(distinguishing unresolved vocabulary terms and terms awaiting
re-extraction) is shown and links straight to Requirement Review; the rest
of the page — economics, evidence counts for what *has* been reviewed, next
actions — stays fully usable regardless.

## Economics retains provenance

The Decision Summary's Economics tile and the detailed Economics section
below it read the *same* `getRoleCompensation` response (fetched once, by
`RoleDetail`) — never a second, reformatted figure. Basis, currency, the
PAYE/contract distinction, and the "never silently annualise a day rate,
never convert currency, never headline a bonus" rules all live in the
backend resolver and are untouched by this phase. `RoleEconomicsSection`
now receives that response as a prop instead of fetching it itself, and
shows the read-only headline (figure + personal comparison) only for a
target's own detail page — for a posting it is already shown once, in the
Decision Summary, so the detailed section shows only the operational
controls (compensation evidence, extract/correct/reject, archetype review,
Pathways).

## Failure isolation

The comparison fetch and the compensation fetch are independent requests,
each with its own error state. If the comparison engine fails to respond,
the Evidence tile shows a retry action and every other part of the page —
role identity, requirements, economics, next actions — renders normally.
Nothing on this page makes an AI call merely by loading; the two proposal
endpoints (compensation extraction, archetype suggestion) remain explicit,
user-triggered actions exactly as before.

## Targets remain a separate concept

`/targets/:id` reuses the same `RoleDetail` component and loader, but a
target's presentation is unchanged by this phase: its own similarity
framing, feasibility/grounding, potential-steps/stepping-stones, skill
decomposition, technical subjects, Pathways link, and "Back to targets"
all render exactly as they did before. The new Decision Summary and "What
this role asks for" sections, and the relabelled Next Actions ("Review
requirements", "Review evidence in detail", "Correct role details",
"Explore my future"), are gated to `node_type === 'posting'` — a target's
next actions keep their original "Requirements / Compare / Edit / Delete
target" labels. No opportunity-only decision or application language
("current vacancy", "I want to apply") appears on a target page.

## Applications remain Phase 3

There is still no persistent application-workspace model. This phase adds
no `jobber.application`-equivalent table, no dead "I want to apply" button,
and no CV/cover-letter/interview-prep generation. The page structure — a
clear opportunity header, decision summary and next-actions area — is meant
to make a future, real application action easy to add without another
restructure, but nothing here wires it up.

## Requests per page load

For a posting: `getRole`, then (in parallel, once the role has loaded)
`getRoleCompensation`, `compareRole`, and `getRoleContext` — four requests
in two sequential waves, the same wave count as before this phase (which
already fetched `getRole` then `getRoleCompensation`/`getRoleContext` in
parallel); the only change is one more request added to the existing
parallel wave, not a new sequential round trip. For a target, `compareRole`
is never called (three requests total). No endpoint here does a whole-corpus
scan or issues one query per requirement/evidence item.

## Phase 1 cleanup folded in

The last nested `<Link><button>…</button></Link>` navigation controls in
`RoleDetail.tsx` (Requirements/Compare/Edit in the old bottom action row)
are now real `a.button`/`a.button.primary` links, consistent with the rest
of the app (see `docs/32-phase1-product-shell.md`).
