# Phase 1 product shell

This phase reorganizes the frontend so the primary interface is organized
around what the user is trying to accomplish, rather than around the app's
internal analytical modules. It is an information-architecture, navigation
and page-framing change only — every analytical capability described
elsewhere in this repository (vocabulary curation, capability engine,
pathways/economics, requirement review, profile360 mappings) is unchanged.

## The four destinations

**Home** (`/`) is the career cockpit: a bounded, honest snapshot of career
direction, current opportunities, profile/evidence and applications, plus
one deterministic next-action suggestion. It is built entirely from existing
data — there is no selected-career-direction concept in the data model yet,
so Home says so explicitly rather than inventing one, and it makes no AI
call.

**Explore my future** (`/future`) is a purely explanatory hub linking
Preferences, Targets, Pathways, Trends/Economics and Career Space in plain
language. It fetches no data of its own — opening it never pre-loads the
pages it links to.

**Opportunities** (`/opportunities`) is the existing Roles/Dashboard list,
reframed: current roles and historical postings are market observations, not
the whole labour market and not all currently open. All existing filtering,
sorting, pagination and Current-roles-default behaviour is unchanged.

**Applications** (`/applications`) is an honest placeholder. The application-
workspace data model does not exist yet (that's Phase 3); this page says so
and points back to Opportunities. It introduces no persistence, including no
browser-local state.

## Secondary navigation

Two compact menus keep supporting tools reachable without competing visually
with the four destinations above:

- **My profile & evidence** — Profile overview, Evidence and mappings,
  Career history, Capability coverage, Preferences.
- **Data & models** — Vocabulary, Capability catalogue, Role map (Career
  Space), Trends, Economics.

Pathways and Targets are reachable from Explore my future and their own
direct/deep-link routes, not duplicated into the secondary menus — the brief
for this phase treats a route as reachable, not as needing every possible
navigation entry point.

## Routing and backward compatibility

| Purpose | Route |
|---|---|
| Home | `/` |
| Explore my future | `/future` |
| Opportunities | `/opportunities` |
| Applications | `/applications` |

`/` used to render the Roles list directly. A compatibility layer
(`App.tsx`'s `Root` component, backed by `hasLegacyRoleListQuery` in
`lib/roleNavigation.ts`) checks the root URL's query string for the specific
parameters the Opportunities list reads (`period`, `track`, `facet`,
`concept`, `sort`, `year`, `offset`); if any are present it redirects to
`/opportunities` with the query string preserved verbatim, otherwise it
renders Home. An unrelated query string on `/` still renders Home — this is
deliberately not a generic "any query string means Opportunities" rule.

`roleListUrl()` (role-list return navigation used by Role Detail's back link
and the delete-role fallback) and Dashboard's own per-row `returnTo` state
now point at `/opportunities?...` instead of `/?...`. `SavedRoleBanner`'s
"Back to Roles" link does the same. Deep links that were already stable
(`/roles/:id`, `/roles/:id/edit`, `/role-instances/:id/requirements`,
`/comparison/:id`, `/vocabulary`, `/pathways/:id`, …) are untouched.

## Known limitations carried into Phase 2+

- **Applications is a shell.** No application-workspace table, no CV/cover
  letter/interview generation. Phase 3 replaces the empty state at
  `/applications` with real, persistent data.
- **Targets are not yet the future target-discovery system.** The existing
  Targets list is explicit roles/imagined descriptions a user adds by hand,
  not the property-driven career-direction builder described in the
  product's longer-term vision.
- **The observed role corpus is not assumed to represent the whole market.**
  Trends, Economics and Opportunities' historical filters describe patterns
  in what has actually been captured, with sample sizes, never a claim about
  the labour market as a whole.
- **No selected-career-direction state exists yet.** Home's Career direction
  card is deliberately an honest empty state rather than picking an
  arbitrary target to call "selected".
