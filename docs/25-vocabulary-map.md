# 25 — Vocabulary Map

**Status:** implemented — a read-only visual exploration/navigation layer over
the existing vocabulary model, tested end-to-end (backend + browser). A
follow-up, frontend-only pass (§14–§17) added a fullscreen workspace and
zoom-aware rendering detail on top of this, unchanged data model and all.
**Not attempted, on purpose:** any of the explicit non-goals below — no second
vocabulary store, no automatic merge/split/accept/reject, no graph database,
no global similarity matrix. **Related:** `docs/18-consolidation-and-
analytical-foundation.md` (the lexical clustering this pass never touches),
`docs/19-vocabulary-prioritisation-and-curation.md` (the Review queue this
pass reuses verbatim for pending evidence), `docs/21-role-context-and-
vocabulary-splitting.md` (Split cluster — honoured transparently, never
re-implemented), `docs/22-concept-vocabulary-maintenance-and-dossiers.md`
(`ConceptDetailsDrawer`, reused unmodified).

---

## 1. Purpose and scope

The Vocabulary Map is a secondary view under the existing Vocabulary page,
alongside the existing curation queue ("Review", now the explicit default
tab). It lets a curator see, spatially, how raw job-posting terms connect to
pending review clusters and to accepted canonical concepts, and to spot
things a flat list makes hard to notice: over-broad clusters, near-duplicate
concepts, or a raw term that's semantically close to something already
accepted but probably deserves its own concept.

**It is a projection, not a new source of truth.** Every node and edge is
computed live from the same tables the Review queue already reads
(`jobber.concept_proposal`, `jobber.role_skill_observation`, `jobber.concept`,
`jobber.concept_alias`, `jobber.concept_edge`). Nothing here persists a graph,
a layout, or a similarity score. Viewing, panning, zooming, selecting, and
focusing never write anything — proven directly by
`test_graph_and_surface_form_endpoints_perform_no_writes` (a full before/
after snapshot of every touched table across five different graph/detail
requests).

Human curation remains authoritative. Accept/Merge/Reject/Split are the
exact same functions the Review tab calls (`vocabulary_curation.py`) — the
Map only ever *navigates* to them.

---

## 2. Model projection

```
job posting wording
→ role_skill_observation.surface_form
→ concept_proposal cluster              (pending_cluster node)
→ human curation
→ jobber.concept canonical vocabulary   (concept node)
→ concept relationships (concept_edge)  (ontology edge)
```

Unchanged from every prior pass. The Map adds no new table, no new column,
and no new persisted derivation — see `backend/app/vocabulary_graph.py`'s
module docstring for the full architecture.

---

## 3. Node kinds

| Kind | Meaning | Rendered as |
|---|---|---|
| `group` | Presentation-only priority band or concept-type bucket, or the synthetic `root` — never a persisted vocabulary item | small pill |
| `pending_cluster` | One `concept_proposal` review unit (docs/19) | circle, coloured by priority band |
| `concept` | One accepted `jobber.concept` row | diamond |
| `surface_form` | One raw/alias lexical form | small circle |

A `surface_form` node's `target` says what selecting it should let the
curator do next: `{"type": "surface_form", "value": ...}` (pending — opens
Word Details) or `{"type": "concept", "id": ...}` (an accepted alias — its
only useful destination is that concept). `pending_cluster`/`concept` nodes
carry the same `target` shape the click-through actions use.

## 4. Edge relations

| Relation | Meaning | Real when |
|---|---|---|
| `contains` | Group → member (layout only) | always presentation-only |
| `member_of` | Surface form → pending cluster | `concept_proposal.cluster_key` |
| `maps_to` | Surface form → concept, *because it was a resolved job-posting proposal* | `concept_alias.origin = 'extraction_proposal'` |
| `alias_of` | Curator-typed alias → concept | `concept_alias.origin = 'curator'` |
| `similar_to` | Advisory embedding similarity | stored `nearest_concept_id`/`nearest_similarity`, or a fresh bounded kNN in focus mode |
| `ontology` | A real, accepted formal relationship | `concept_edge.status = 'accepted'` only (`proposed`/`rejected` edges are never shown) |

The `maps_to` vs. `alias_of` split is the concrete, data-backed answer to the
brief's "do not imply every alias was necessarily observed verbatim"
requirement (§4): a `maps_to` alias really did originate from a resolved
`concept_proposal` (real corpus evidence, its stored `occurrence_count` is
shown); an `alias_of` alias was typed directly by a curator via
`concept_curation.add_alias` and carries no such claim. Verified end-to-end:
`test_accepted_concept_shows_extraction_proposal_aliases_as_maps_to`,
`test_curator_added_alias_is_alias_of_never_maps_to`, and the browser smoke
run (screenshot below).

Edges are distinguished by **line pattern and width**, not colour alone
(brief §11/§24's "do not rely only on colour"): `member_of` solid thin,
`maps_to` solid heavy, `alias_of` dashed, `similar_to` finely dotted (always
the least visually assertive line — advisory, never equivalence), `ontology`
dash-dot. `VocabularyMapLegend.tsx` renders the exact same swatches
`VocabularyGraph.tsx` draws, so the two can never drift apart.

---

## 5. Backend API

### `GET /api/vocabulary/graph`

Read-only (`backend/app/routes/vocabulary.py::vocabulary_graph_endpoint` →
`app/vocabulary_graph.py::build_graph`/`build_similarity_focus`).

```
status=pending|accepted|combined        (default pending)
group_by=priority|type|none             (default: priority for pending, type for accepted)
band=high|medium|low|sparse             (pending only)
type_code=<concept type>                (accepted/combined only)
q=<search text>
min_role_count=<n>
min_observation_count=<n>
country=<country>  seniority=<level>  observed_from=<date>  observed_to=<date>   (pending only — reuses list_clusters' own semantics)
limit=<n>                               (default 300, max 500 — a strict total-node budget)
include_similarity=true|false           (default true — pending clusters' own stored nearest-concept edge)
include_ontology=true|false             (default true — real accepted concept_edge rows among rendered concepts)
focus=<cluster:.../concept:.../surface_form:...>   (switches into similarity-focus mode; every other filter is ignored)
similarity_limit=<n>                    (default 8, max 20; focus mode only)
```

`limit` bounds `returned_nodes` in full — clusters/concepts/surface forms
*and* group nodes *and* the synthetic root — never just the "primary" items.
One slot is always reserved for root; each subgraph call accounts for its
own group nodes' cost against the remainder before adding either (see
`build_graph`/`_pending_subgraph`/`_accepted_subgraph`), so `returned_nodes
<= limit` always holds — verified by
`test_result_limit_is_a_strict_total_node_bound_including_groups_and_root`.

`focus` accepts `cluster:<cluster_key>`, `concept:<concept_id>`, or
`surface_form:<literal text>` — **not** necessarily a node `id` copied
verbatim from a prior response. `cluster:`/`concept:` values do match those
nodes' own graph `id`s directly, but a surface-form node's graph `id` is
owner-scoped for uniqueness within one response (e.g.
`surface:<cluster_key>:<text>` or `surface:<concept_id>:<text>`) — for
that node, `focus` takes the literal surface-form text alone
(`surface_form:<text>`), since similarity lookup only ever needs the text,
never which cluster/concept it happened to appear under.

Response shape (`meta`/`nodes`/`edges`), matching the brief's suggested
schema (§17) with field names adapted to this app's real data:

```json
{
  "meta": {
    "total_nodes": 11, "returned_nodes": 15, "returned_edges": 17,
    "truncated": false, "status": "pending", "group_by": "priority",
    "filters": { "band": null, "type_code": null, "q": null, "...": "..." }
  },
  "nodes": [
    { "id": "group:priority:high", "kind": "group", "label": "High", "count": 1 },
    { "id": "cluster:solvency ii", "kind": "pending_cluster", "label": "solvency ii",
      "cluster_key": "solvency ii", "priority_band": "high", "priority_score": 9.9,
      "surface_count": 2, "observation_count": 6, "role_count": 6, "country_count": 6,
      "flags": [], "target": { "type": "cluster_review", "id": "solvency ii" } },
    { "id": "surface:solvency ii:sii", "kind": "surface_form", "label": "sii", "status": "pending",
      "target": { "type": "surface_form", "value": "sii" } }
  ],
  "edges": [
    { "source": "group:priority:high", "target": "cluster:solvency ii", "relation": "contains" },
    { "source": "surface:solvency ii:sii", "target": "cluster:solvency ii", "relation": "member_of" }
  ]
}
```

`total_nodes` counts matching pending clusters + accepted concepts only, not
their child surface-form/group nodes — an approximate "how much more is out
there" signal for the truncation banner, not an exact unbounded node count
(documented limitation, §9 below).

### `GET /api/vocabulary/surface-form?value=<text>`

Single-surface-form evidence (brief §13/§19) —
`app/vocabulary_graph.py::get_surface_form_detail`. A query parameter, not a
path segment, because raw surface-form text can contain characters awkward
for a URL path. 404 if `value` matches nothing in persisted vocabulary state
at all. Returns per-form (not per-cluster) observation/role/country/
seniority/year evidence, the nearest accepted concept if one is stored, and
the resolved concept if the form has already been curated — see
`test_surface_form_detail_returns_per_form_evidence_not_whole_cluster` for
the exact behaviour of two different surface forms in the same cluster
correctly reporting *their own*, distinct counts.

Neither endpoint mutates anything; both sit behind the same central
`require_auth` dependency as every other route in this app.

---

## 6. Reuse of existing curation components

Nothing about proposal resolution, aliasing, or concept editing was
reimplemented:

- **Pending graph construction reuses `vocabulary_curation.list_clusters`
  verbatim** (`vocabulary_graph.py::_pending_subgraph`) — the exact same
  filtered/sorted/paginated queue the Review tab calls. The Map adds no
  parallel cluster-aggregation query; it only reshapes that function's
  already-computed page into nodes/edges. A curator's Split-cluster lock
  (`cluster_key_locked`) is honoured automatically, because `list_clusters`
  reads whatever `cluster_key` is currently persisted —
  `test_split_locked_clusters_use_their_persisted_curated_cluster_keys`
  proves a split (and a subsequent real bootstrap re-run) is reflected
  correctly with zero graph-specific code.
- **Accept/Merge/Reject/Split** — `frontend/src/components/vocabulary/
  ClusterActions.tsx`'s `ClusterActionsPanel` (extracted from the Review
  tab's `ClusterCard`, not duplicated) is used by *both* the Review tab and
  the Map's `PendingClusterMapDetails.tsx`. There is exactly one
  implementation of these mutations in the whole frontend.
- **Concept Details** — the Map's concept entry point opens the existing,
  completely unmodified `ConceptDetailsDrawer.tsx` (metadata edit, aliases,
  Concept Dossier generate/regenerate/adopt — all untouched).
- **"Open cluster review"** hands a plain search string back up to
  `pages/Vocabulary.tsx`, which switches to the Review tab and sets its
  existing filter state (`status=pending`, `q=<label>`) — no second
  queue-filtering implementation, no new backend endpoint for this.

---

## 7. Frontend component plan

```
frontend/src/pages/Vocabulary.tsx                          Review/Map tab switch, shared heading, concept-type fetch
frontend/src/components/vocabulary/VocabularyReviewView.tsx  the pre-existing Review page, moved here unchanged in behaviour
frontend/src/components/vocabulary/ClusterActions.tsx        MergeTargetPicker, SplitClusterEditor, ClusterActionsPanel — shared
frontend/src/components/vocabulary/VocabularyMapView.tsx     filter/fullscreen/details-collapsed state, data loading, selection, focus-mode coordination
frontend/src/components/vocabulary/VocabularyMapControls.tsx View/Group by/Priority/Type/Search (debounced)/Min roles/Min obs./Limit/Reset
frontend/src/components/vocabulary/VocabularyMapWorkspace.tsx  shared embedded/fullscreen layout (§14) — graph + details(+legend), one implementation for both modes
frontend/src/components/vocabulary/VocabularyMapFullscreenOverlay.tsx  fixed-position fullscreen chrome (§14) — Escape/scroll-lock/focus-on-open only, layout-agnostic
frontend/src/components/vocabulary/VocabularyGraph.tsx       React Flow wrapper: radial layout, custom node/edge rendering, pan/zoom/selection, semantic zoom (§15), hover tooltip
frontend/src/components/vocabulary/VocabularyMapLegend.tsx   static legend, mirrors VocabularyGraph's actual styling exactly
frontend/src/components/vocabulary/VocabularyMapDetails.tsx  dispatches by node kind
frontend/src/components/vocabulary/SurfaceFormDetails.tsx    Word Details — fetches per-form evidence
frontend/src/components/vocabulary/PendingClusterMapDetails.tsx  full cluster evidence + ClusterActionsPanel
frontend/src/components/vocabulary/shared.tsx                 Badge/PriorityBandBadge/FlagBadges (shared with Review)
frontend/src/components/vocabulary/vocabConstants.ts          BAND_COLOR/BAND_LABEL/FLAG_LABEL/filter-state type+default/ZoomBand+getZoomBand (§15) (plain constants only, kept out of the *.tsx files for Fast Refresh)
```

`Vocabulary.tsx` fetches `conceptTypes` once and hands it to both tabs, so
they never duplicate that request or drift out of sync. Review stays mounted
(`display:none`) while Map is active, preserving its filters/pagination/
selection; Map fully unmounts when inactive (it is graph-render-heavier and
always wants a fresh fetch on re-entry).

---

## 8. Graph library

**Chosen: [`@xyflow/react`](https://reactflow.dev) (React Flow) v12**, plus a
~60-line hand-rolled radial layout function — no `dagre`/`elkjs` dependency.

Why:

- **Fits the existing stack exactly.** This app is React 19 + inline-style-
  driven components with no CSS framework (`ConceptDetailsDrawer.tsx`,
  `Vocabulary.tsx` itself). React Flow's custom nodes/edges *are* React
  components, so a cluster/concept/surface-form node is styled with the same
  `var(--...)` design tokens and the same conventions as every other
  component in this codebase — no separate stylesheet DSL to keep in sync.
- **Batteries included for the concrete requirements**: pan, zoom, fit-view,
  and node selection all ship as `<Controls/>`/built-in event handlers —
  none of it was hand-built.
- **DOM nodes, not canvas.** Tooltips, `<Link>` navigation, and hover states
  are just JSX — critical for a details-rich node like a pending cluster.
- **One dependency**, actively maintained, TypeScript-native, ~2.5M weekly
  downloads. `dagre`/`elkjs` were deliberately not added: the brief's own
  radial-hierarchy diagram (§5) is a closed-form computation (BFS depth +
  even angular division of each parent's slice — the standard "balloon"
  radial-tree technique), not something that needs a general graph-layout
  library.
- Cytoscape.js was considered for its built-in `concentric` layout, but its
  imperative, canvas-based API and its own separate stylesheet DSL for node/
  edge styling would have meant re-deriving this app's design tokens in a
  second styling language, for a corpus-scale (≤500 node) graph where
  React Flow's DOM-based rendering has no real performance disadvantage.

**Layout**: `VocabularyGraph.tsx::computeRadialLayout` builds a parent→
children map from the *structural* edges only (`contains`/`member_of`/
`maps_to`/`alias_of` — never `similar_to`/`ontology`, which are cross-links,
not tree edges), BFS's depth from the synthetic `root`, and divides each
parent's angular slice evenly among its children. Similarity-focus responses
carry no structural edges at all (just a center + neighbours), so they use a
separate one-ring `computeStarLayout` instead, selected automatically via the
`focus: true` flag the backend sets on the centre node.

**Performance assumption**: a few hundred DOM nodes with straight-line SVG
edges is comfortably within React Flow's normal operating range; node
dragging is disabled (`draggable: false`) so the deterministic radial layout
is never accidentally scrambled and never fights a re-fetch's fresh layout.

---

## 9. Filtering, scale, and performance

The full corpus is never sent to the browser. `limit` (default 300, max 500)
is a strict bound on `returned_nodes` — clusters/concepts/surface forms *and*
the presentation-only group/root scaffold nodes all count against it, so a
response never carries more than `limit` nodes in total. Each subgraph
builder accounts for a group node's cost the first time that group is
needed (a group shared by several clusters/concepts is paid for once, not
once per member), and one slot is always reserved for the synthetic root —
see `_pending_subgraph`/`_accepted_subgraph`/`build_graph` in
`vocabulary_graph.py`. When more matches exist than fit, `meta.truncated:
true` and the UI shows *"Showing the first N matching nodes. Narrow the
filters or focus on a node to explore further."*

Per the brief's explicit "do not" list (§25), this pass never:

- sends the full proposal/observation corpus to the browser (pending
  aggregation reuses `list_clusters`' existing bounded, sub-100ms-at-
  production-scale live join — the same computation the Review tab already
  performs on every page load, docs/19 §1);
- computes all-pairs similarity (similarity-focus is one embed-and-kNN call,
  bounded to `similarity_limit`, never a matrix);
- issues one SQL query per graph node (pending: one `list_clusters` call +
  one bulk nearest-concept lookup; accepted: one count + one page query + one
  bulk alias query + one bulk proposal-occurrence lookup + one bulk ontology-
  edge query; similarity focus: one `nearest_concepts` call + one
  `_bulk_concepts` lookup for every neighbour's metadata, never one query per
  neighbour — a fixed, small number of queries regardless of how many
  clusters/concepts/neighbours are returned, verified directly by
  `test_graph_query_count_does_not_scale_linearly_with_cluster_count`: total
  query count stays well under 3× when the seeded cluster count grows 12×);
- calls an AI provider, or regenerates a *concept's* embedding, during a
  graph GET (see §10);
- mutates anything on view (§1's "no writes" test).

Per-cluster surface forms are capped at 25 and per-concept aliases at 20
(silent, documented caps — the same "small fixed cap, no UI truncation
signal" convention `LIST_EXAMPLE_ROLE_LIMIT` etc. already use elsewhere in
this codebase), so one unusually large cluster/concept can't blow the
request's node budget on its own.

---

## 10. Similarity semantics — and an honest limitation

No raw-term-to-raw-term or cluster-to-cluster embedding exists anywhere in
this codebase — only `jobber.concept` rows get a `d_embedding` row
(`concept_linking.py`). So:

- **Base graph** `similar_to` edges are free: they read the
  `nearest_concept_id`/`nearest_similarity` Pass B already computed and
  stored on the proposal — zero embedding calls on an ordinary graph GET.
- **Similarity focus** on a pending cluster or a raw surface form shows its
  nearest *accepted concepts* (a bounded top-N `concept_linking.
  nearest_concepts` call), never a fabricated "nearest cluster" or "nearest
  raw term" score — exactly the brief's own fallback instruction (§20): "If
  raw surface-form embeddings do not exist, do not fabricate raw-term
  similarity... surface-form nodes may show nearest accepted concept."
- Focus mode calls `nearest_concepts(..., backfill=False)` — a new parameter
  added to that pre-existing function specifically so a read-only route can
  never trigger `ensure_concept_embeddings`'s side effect of *computing and
  persisting* a missing concept's embedding (brief §25: "never regenerate
  embeddings during ordinary graph rendering"). A concept with no embedding
  yet simply isn't offered as a neighbour until the existing backfill script
  populates one — `test_similarity_focus_never_backfills_a_missing_concept_
  embedding` proves this directly.
- Embedding the *query* text itself (the search string, never persisted) is
  unavoidable for any embedding search and is the same always-on local
  primitive Pass B/concept curation already call inline — this is not the
  "AI call" the brief's §25 warns against (that means a paid LLM provider
  call, e.g. OpenAI extraction), and it never writes anything either way.
- **Discovered during the browser smoke test**: this sandbox has no network
  access to the embedding model's host — the very first live (non-test)
  call to embed a query string fails. Since similarity is advisory
  decoration on a read endpoint, not something a request should hard-fail
  on, `vocabulary_graph.py::_safe_nearest_concepts` now catches that failure
  and degrades to "no neighbours found" (center node only, empty edge list)
  rather than a 500 — `test_similarity_focus_degrades_gracefully_if_the_
  embedding_model_is_unavailable` covers it. In a deployment with normal
  network egress (or a pre-warmed model cache), this degrades path is never
  exercised; it is exactly analogous to every other AI-provider failure mode
  this codebase already handles explicitly (`role_context.py`'s
  `AIProviderError` → 502, etc.) — advisory features degrade, they don't
  crash the page.

Every similarity link — base graph or focus — is visually advisory
(finely-dotted, lowest visual weight of any edge) and the UI states plainly,
in the page subtitle, the legend, and a persistent footer note: *"Similarity
links show semantically nearby terms; they are not automatic merge
recommendations. Viewing the map never changes curation state."*

---

## 11. Explicit non-goals — confirmed unbuilt

No second vocabulary model, no graph database, no graph-specific persistence
table, no automatic merge/split/accept/reject/ontology-edge/concept
creation, no global all-pairs similarity map, no rebuild of the vocabulary
bootstrap, no reprocessing of the historical corpus, no `profile360` touch,
no compensation/economics change, no bulk Concept Dossier generation, no
unrelated UI redesign. `git diff --stat` for this pass touches only
`backend/app/vocabulary_graph.py` (new), `backend/app/routes/vocabulary.py`
(two new thin routes), `backend/app/concept_linking.py` (one new, opt-in,
backward-compatible `backfill` parameter — every existing caller keeps its
exact prior behaviour), the new `frontend/src/components/vocabulary/*`
tree, `pages/Vocabulary.tsx`, `lib/api.ts`, and `index.css` (one small grid
class).

---

## 12. Known limitations

- **No raw-term-to-raw-term or cluster-to-cluster similarity** — only
  nearest-*accepted-concept* similarity is available, for the reason given
  in §10. Extending this would require computing and storing embeddings for
  proposals/clusters, a materially larger change this pass deliberately did
  not attempt.
- **`meta.total_nodes` is an approximation** (matching primary items only,
  not their child surface-form/group nodes) — sufficient for the truncation
  banner's "there's more, narrow your filters" purpose, not an exact
  unbounded graph size.
- **Search re-fetches server-side rather than adding a dedicated "locate"
  endpoint.** The brief allows either; since `q` is already a full
  server-side filter, a search that doesn't appear in the current bounded
  page is resolved by the same request that would otherwise page through
  results — no second endpoint was judged necessary.
- **This sandbox could not exercise a successful live similarity-focus
  neighbour list** (§10's network constraint) — covered instead by mocked
  backend integration tests that assert the real embedding-search code path
  runs correctly, and by a graceful-degradation test for exactly the
  failure this sandbox hits. A deployment with normal egress exercises the
  real path unchanged.
- **Per-cluster/per-concept fan-out caps (25 surface forms, 20 aliases)**
  are fixed constants, not currently surfaced as their own "N more" UI
  affordance — an unusually large cluster's extra members are simply not
  drawn, consistent with several existing silent caps elsewhere in this
  codebase, but worth revisiting if a cluster of that size turns out to be
  common in the real corpus.

---

## 13. Validation

**Backend**: `pytest -q` — **605 passed, 4 failed** (the 4 failures are
`test_market_data.py`/`test_role_ingestion.py`'s PDF-fixture-generation
tests, which fail identically on an unmodified checkout of this branch — a
pre-existing sandbox/library issue unrelated to this pass, confirmed via
`git stash` before/after comparison). The 18 new tests in
`backend/tests/test_vocabulary_graph.py` cover every item in the brief's
required list (§30): cluster membership, split-lock survival, accepted
concepts/aliases (both `maps_to` and `alias_of`), status/priority/type/
search/evidence-minimum filters, result limits and truncation metadata, zero
writes, rejected vocabulary staying hidden in every mode, bounded/advisory
similarity (base and focus), real-accepted-only ontology edges, per-form
surface-form-detail evidence, and a query-count-boundedness check — plus two
tests added after the browser smoke run surfaced the embedding-backfill/
availability issue (§10).

**Frontend**: `tsc -b` clean, `oxlint` clean (exit 0, zero findings —
including after moving shared constants into their own module to satisfy
Fast Refresh's "component-only file" lint), `npm run build` succeeds (one
pre-existing "chunk >500kB" advisory warning — the Space page's three.js
dependency, now joined by React Flow; unrelated to correctness, same
category already noted in docs/19 §14/docs/21 §5). No dedicated frontend
test framework exists in this repo (unchanged).

**Browser smoke test** (Playwright against this sandbox's pre-installed
Chromium, a local backend + Vite dev server pair against a disposable seeded
Postgres): login → Vocabulary → Review loads by default with its existing
cards → switch to Map → pending graph renders (15 nodes: 4 clusters, 6
surface forms, 4 priority groups, root) → filter to High priority (15→5
nodes) → search "solvency" (→5 nodes) → select the "solvency ii" cluster
node → details panel shows correct evidence (2 surface forms, 6
observations, 6 roles, 2019–2024, 6 countries) → "Open cluster review"
switches to Review pre-filtered to it → back to Map → switch to Accepted →
select the "Python" concept (diamond) → details panel → "Open Concept
Details" opens the real `ConceptDetailsDrawer`, showing the curator-added
alias correctly → zoom in/out/fit-view controls work → "View similarity
neighbourhood" enters focus mode and back out cleanly → Combined view
renders both branches (19 nodes) → Reset returns to defaults → pan retains
the current selection → clicking blank canvas clears it → legend renders →
back on Review, Accept/Merge/Reject/Split are all still present and
untouched. **Zero console errors, zero page errors** on the final run.
Screenshots retained for the deliverable report.

**Data safety check**: `test_graph_and_surface_form_endpoints_perform_no_
writes` snapshots every relevant table (`concept_proposal` status
breakdown, `concept`/`concept_alias`/`concept_edge` counts, mapped-
observation count, every `cluster_key`/`cluster_key_locked` value) before
and after five different graph/detail/focus requests and asserts byte-for-
byte equality. Confirmed separately in the browser smoke run: no data-
affecting action was available anywhere on the Map surface beyond the
existing, unmodified Accept/Merge/Reject/Split/Concept-edit controls
reached through it.

---

## 14. Fullscreen mode

A follow-up, frontend-only pass on top of everything above: a fullscreen
workspace and zoom-aware rendering detail for the same graph, still no
change to the vocabulary model, curation semantics, or backend API.

**Overlay approach.** `VocabularyMapFullscreenOverlay.tsx` is a fixed,
full-viewport in-app CSS layer (`position: fixed; inset: 0`, `z-index: 50`
via `.vocab-map-fullscreen-overlay` in `index.css`) — not the browser
Fullscreen API. This app never needs to escape browser chrome itself, so a
plain overlay avoids `requestFullscreen()`'s permission prompt, vendor
quirks, and the extra `document.fullscreenElement` state that would need
reconciling with React state, for no loss of capability; it also composes
directly with the existing `ConceptDetailsDrawer` (which sits at a higher
z-index, 60/61, so opening Concept Details from inside fullscreen still
layers correctly on top). Rendered inline in the component tree, the same
convention that drawer already uses — no portal was needed.

**Shared state, one graph instance.** Exactly one of the embedded workspace
or the fullscreen overlay is mounted at a time — never both — so there is
only ever one `VocabularyGraph`/React Flow instance alive. `VocabularyMapView`
still owns every piece of state fullscreen needs to preserve (`filters`,
`response`, `selected`, `focusId`/`focusLabel`); it now also owns
`isFullscreen` and `detailsCollapsed`. Toggling fullscreen changes none of
`filters`/`focusId`/`refreshSignal` — the only inputs the graph-loading
effect depends on — so entering or exiting it never causes an extra
`GET /api/vocabulary/graph` call. `VocabularyMapWorkspace.tsx` renders the
graph, details panel, legend, focus banner, error/truncation notices, and
footer note identically for `mode="embedded"` and `mode="fullscreen"`, so
there is exactly one implementation of that layout, not two forks.

**Resize handling.** Because embedded and fullscreen never coexist, entering
or exiting fullscreen always *mounts a fresh React Flow instance inside an
already-correctly-sized container* (the overlay is already full-viewport via
CSS by the time it mounts) rather than resizing a live instance — sidestepping
the classic "stale bounds after a CSS transition" React Flow pitfall
entirely, with no `ResizeObserver` bookkeeping needed. `VocabularyGraph`
exposes an `onReady(api)` callback (`{ fitView }`, deliberately not the raw,
generically-typed `ReactFlowInstance`) fired from React Flow's own `onInit`,
which the fullscreen top bar's **Fit** button calls directly — the same
escape hatch the brief anticipated for a case an initial `fitView` alone
doesn't cleanly cover (e.g. after collapsing/reopening details resizes the
canvas without a remount). Embedded mode relies on React Flow's own built-in
`<Controls/>` fit-view button, unchanged from before this pass.

**Details panel.** `detailsCollapsed` is fullscreen-only: `VocabularyMapWorkspace`
always shows details+legend in embedded mode regardless of its value, so
returning from fullscreen never surprises the embedded view. In fullscreen,
**Hide details**/**Show details** in the top bar toggles it; the underlying
selection and panel content are never discarded, only hidden, and the grid
collapses to a single column (`gridTemplateColumns: '1fr'`) so the graph
reclaims the full width rather than leaving an empty column-track.

**Node-limit behaviour.** Fullscreen reuses `VocabularyMapControls` verbatim
in its own top bar (same component, same Limit `<select>` already offering
100/200/300/500 in embedded mode too) — so "a larger limit selector" needed
no new component. Entering/exiting fullscreen never touches `filters.limit`;
changing it explicitly still goes through the exact same
`GET /api/vocabulary/graph?...limit=N` call and the existing 500-row backend
cap and truncation banner, both untouched.

**Escape / keyboard / accessibility.** `Escape` is bound at `document` level
(not scoped to a focused element) so it always exits regardless of what
currently has focus. The overlay carries `role="dialog"`/`aria-modal="true"`/
an `aria-label`, and moves focus into itself on open — without an
all-or-nothing Tab-cycle trap, so it can never *actually* trap a user, only
start their keyboard focus in the right place. Fullscreen's icon-only control
(`⛶ Fullscreen`) and the Exit/Hide-details buttons all carry explicit
`aria-label`s. Node meaning is still never conveyed by colour alone (shape +
line-pattern distinctions from the base pass are unchanged).

---

## 15. Semantic zoom

**Zoom bands.** A single source of threshold truth,
`getZoomBand(zoom)`/`ZOOM_BAND_THRESHOLDS` in `vocabConstants.ts`:

```
far     zoom < 0.55
medium  0.55 <= zoom < 1.05
close   zoom >= 1.05
```

Read from React Flow's own viewport (`onMove`/`onInit`), never a second
measurement mechanism. The band is cached in a ref and only pushed into
React state when it actually *changes* — continuous panning/zooming inside
one band costs nothing beyond React Flow's own rendering, and the radial
layout (`computeRadialLayout`/`computeStarLayout`) is never recomputed from
zoom: **semantic zoom changes rendering density only, never topology, and
never the underlying vocabulary data.**

**What changes per band** (`VocabularyGraph.tsx`'s node views):

- **Far** — every non-group node's label is unmounted entirely (not just
  visually hidden — the label `<div>` isn't rendered at all): surface forms,
  pending clusters, *and* accepted concepts. Only group pills, node shapes
  (colour + size already carry priority/kind), and edges remain — pure
  topology, per the consolidated requirement-evidence-and-graph-fix brief's
  "far: prioritise topology, hide low-value detail." (Cluster/concept labels
  used to stay visible at far too — a regression this pass fixes; see
  "Regression root cause" below.)
- **Medium** — every node's label reappears at its normal width. This is the
  zoom band a fresh `fitView` on a small/medium graph typically lands in —
  "the normal default embedded experience."
- **Close** — labels widen (140px → 220px) *and* every node kind renders a
  real, always-visible (no hover needed) inline detail sub-line drawn only
  from fields the graph API actually returns (`data-testid` per kind:
  `cluster-close-detail`, `concept-close-detail`, `surface-form-close-detail`):
  a pending cluster shows its priority band plus role/observation/surface-form
  counts; an accepted concept shows its type code plus alias count where
  available; a surface form shows its pending/accepted status plus observation
  count where available. Hovering any non-group node still additionally shows
  the compact tooltip described below. Before this pass, only concept nodes
  had an inline close-zoom sub-line (added by an earlier commit and never
  extended to clusters/surface forms) — clusters and surface forms only grew
  a wider label box at close zoom, which is why zooming into those node kinds
  read as "the same picture, bigger" rather than genuinely richer.

**Regression root cause.** Three separate, compounding causes — the first
two were the initial diagnosis for this pass; the third, and by far the
dominant one in practice, was only found after a live screenshot at
realistic node density (the default `limit=300`) showed hundreds of
surface-form labels and `contains`/`member_of` edges visually merged into
unreadable, overlapping mush, which is what "still isn't sharp — looks like
a scaled image" actually meant. No rasterised/canvas rendering and no
app-authored CSS `transform: scale(...)` cheat exist anywhere in this
codebase — React Flow's own viewport scaling is DOM/SVG-based and already
vector-sharp, not a screenshot zoom — so "sharpness" here was never a
rendering-pipeline problem:

1. Node visual complexity was **static across zoom bands for two of the three
   node kinds** — pending-cluster and surface-form nodes had no close-zoom
   detail at all beyond a wider label box, so zooming into them showed
   strictly the same information, just larger. Only concept nodes had
   genuinely richer close-zoom content. (Fixed: §"What changes per band"
   above.)
2. Far zoom **did not simplify enough** — cluster and concept text labels
   stayed rendered at every zoom level (only surface-form labels ever
   unmounted at far), so an overview never actually read as "topology first";
   it read as the same label-dense view, shrunk. (Fixed: same section.)
3. **The dominant cause, found via a live screenshot, not code review alone:**
   `computeRadialLayout` (now `vocabLayout.ts`) placed every node at a fixed
   `depth * RING_SPACING` radius and split each parent's angular slice
   *evenly* among its children, regardless of how many children it had. With
   the real-world node counts this graph is meant to show (hundreds of
   surface forms unevenly spread across a handful of clusters, clusters
   unevenly spread across priority bands), many siblings ended up a small
   fraction of a degree apart — genuinely, geometrically almost coincident
   in world-space coordinates. Since on-screen zoom scales *pixel* distance
   between nodes, never their underlying world-space separation, **no amount
   of zooming in could ever visually pull such siblings apart** — this, not
   any rendering artifact, is what made zooming in "just enlarge the same
   overlapping mess" rather than resolve it, and it's what produced the
   solid-looking grey "tube" bands in the reported screenshot: hundreds of
   nearly-coincident thin edges, anti-aliased on top of each other.
   Fixed by two changes to the layout algorithm itself (`vocabLayout.ts`):
   (a) a parent's angular slice is now divided among children in proportion
   to each child's own subtree size (leaf-descendant count) rather than
   evenly, so a cluster with fifty surface forms gets proportionally more
   room than a sibling with two; (b) each node's children are placed at
   `own radius + max(RING_SPACING, whatever radius the most tightly-sliced
   one of its own children needs for at least 26px of real world-space
   separation)` — computed **per parent**, not shared across an entire ring,
   so one crowded branch doesn't inflate spacing for every unrelated branch
   too. Verified directly: `VocabularyGraph.layout.test.ts` builds a
   60-surface-form dense cluster and asserts every sibling ends up more than
   10px apart in world-space (the pre-fix version placed dozens of them
   within a couple of pixels of each other, at any zoom); a manual
   Playwright screenshot pass at realistic density (~200-300 nodes) confirms
   the far-zoom overview is now legible topology (no overlapping mass) and
   that panning/zooming into any single cluster or surface form reaches
   clean, non-overlapping labels — see the reported screenshot vs. the
   fixed version in the delivery report for this pass.
   This third fix is the one that actually addresses "genuinely sharper at
   closer zoom, not just a bigger version of the same overlapping mess" —
   (1) and (2) alone were real but secondary improvements; without (3), a
   realistically-sized graph stayed illegible at every zoom level regardless
   of what per-node detail was added.

**Hover tooltip.** Close-zoom only, content derived solely from the
already-loaded graph node (never a fetch triggered by hover or by a zoom
threshold crossing) — a surface form's status/observation count, a pending
cluster's priority band + role/observation counts, or an accepted concept's
type/alias count. Position is captured once on `mouseenter` (not tracked on
every `mousemove`), so hovering costs at most one extra render per node
entered/left. `data-testid="node-tooltip"`/`"node-label"` exist purely to
make this and the label-density rules assertable from tests without brittle
style-string matching.

**Performance.** Verified by construction, not just intent: the per-band
node-data recompute (`rfNodes`'s `useMemo`) only runs when the zoom *band*
changes (at most twice per continuous zoom gesture, thanks to the ref-guard
above), the expensive layout computation has its own, zoom-independent
`useMemo`, and no code path here calls the vocabulary API — zooming, panning,
and hovering are all purely client-side rendering decisions over data the
page already has.

---

## 16. Known limitations (fullscreen / semantic zoom pass)

- **jsdom cannot meaningfully exercise real React Flow rendering** (no layout
  engine, no CSS transform geometry) — `VocabularyMapView.test.tsx`
  deliberately mocks `VocabularyGraph` out entirely and only covers this
  component's own state transitions (fullscreen open/close/Escape, selection/
  filter/focus/node-limit preservation, details collapse) plus, separately,
  the pure `getZoomBand` threshold helper (`vocabConstants.test.ts`). That gap
  is exactly why CI could pass while the real zoom-band regression (§15)
  shipped unnoticed. It is now closed by `frontend/e2e/vocabulary-map-zoom.spec.ts`
  (Playwright, real Chromium, the real `@xyflow/react` graph — see §17) rather
  than another one-off manual pass: this suite runs in CI/on demand via
  `npm run test:e2e` and is the durable regression guard against this
  happening again, not the ephemeral smoke test §17 used to describe.
- **The close-zoom tooltip is position-captured on hover-enter, not
  cursor-tracked.** A deliberate simplification (brief explicitly allows a
  "lightweight" tooltip): it stays anchored to where the pointer entered the
  node rather than following every subsequent pixel of mouse movement, which
  avoids extra renders per mouse-move at the cost of not sliding smoothly if
  the pointer wanders far across a large node while it's open.
- **No focus-trap cycling inside the fullscreen overlay** — only an
  on-open focus move plus a document-level `Escape` handler. This is a
  deliberate reading of the brief's own "does not trap the user" requirement
  over "reasonably as practical" containment: a full Tab-cycle trap is more
  machinery for the same accessibility outcome and a bigger risk of a bug
  that *actually* traps someone.
- Every known limitation from §12 (no raw-term-to-raw-term similarity,
  approximate `total_nodes`, fixed per-cluster/per-concept fan-out caps, this
  sandbox's lack of egress to the embedding host) is unchanged by this pass.

---

## 17. Validation (fullscreen / semantic zoom pass; regression fix + e2e pass)

**Frontend unit/component**: `tsc -b` clean, `oxlint` clean (exit 0, zero
findings). `npm test` (Vitest) — 123 passed, 0 failed, covering
`getZoomBand`'s thresholds, `VocabularyMapView`'s fullscreen/selection/
filter/focus/node-limit/details-collapse state transitions (`VocabularyGraph`
mocked, per §16), `vocabLayout.ts`'s `computeRadialLayout` dense-graph
anti-overlap properties (`VocabularyGraph.layout.test.ts` — see "Regression
root cause" cause 3 above: a 60-surface-form dense cluster, a lopsided
dense-vs-sparse sibling comparison, and a sparse-graph no-unnecessary-
blow-up check), and the requirement-review evidence-list UI (a separate,
unrelated component touched by the same build — see the requirement-evidence
half of this deliverable).

**Frontend real-browser (new)**: `frontend/e2e/vocabulary-map-zoom.spec.ts`,
run via `npm run test:e2e` (Playwright, against this sandbox's pre-installed
Chromium — `playwright.config.ts` points `launchOptions.executablePath` at
the pre-provisioned browser directly since this sandbox has no egress to
fetch a matching-revision download). The suite mocks every network call the
page needs (`e2e/fixtures.ts`: `/api/auth/status`, `/api/vocabulary/graph`,
and 404-stubs for the still-mounted-but-hidden Review tab's own calls) so it
needs neither a running backend nor a database, and asserts against the real
`@xyflow/react` graph:

- Pending view: far zoom → zero `[data-testid="node-label"]` elements in the
  DOM (not just hidden) for both the cluster and the surface form; medium
  zoom → both labels visible, no close-only detail yet; close zoom →
  `cluster-close-detail` visibly contains the cluster's real role/observation/
  surface-form counts and priority band, `surface-form-close-detail` contains
  its real status — all read straight from the mocked API response, nothing
  fabricated.
- Hovering a node at close zoom → `node-tooltip` renders with real fields.
- Accepted view: close zoom → `concept-close-detail` shows the real type code
  and alias count; the accepted surface form's close detail shows its real
  status and observation count.
- Combined view: both a pending-cluster and a concept node render their own
  kind-specific close-zoom detail side by side.
- Crossing every zoom-band threshold (far → medium → close → far again) never
  issues a second `/api/vocabulary/graph` request beyond the initial load —
  proves semantic zoom stays purely client-side rendering, per §15.
- Entering fullscreen after reaching close zoom: the viewport (and therefore
  the zoom band, and its close-only detail) survives the embedded → fullscreen
  remount unchanged, and zoom-band transitions still work afterward — proves
  viewport preservation (`VocabularyMapView.tsx`'s `viewportRef`) does not
  freeze future band transitions.

Zoom itself is driven through React Flow's own `Controls` +/− buttons
(`.react-flow__controls-zoomin`/`-zoomout`) and verified by reading the real
rendered viewport transform (`.react-flow__viewport`'s `scale(...)`) after
each click, rather than assuming a fixed step size per click — the tests read
the same, and only, zoom value the app itself reads (docs' own "never a
second measurement mechanism" rule, §15).

**Backend**: `pytest` — 977 passed, 7 pre-existing failures unrelated to this
build (`ModuleNotFoundError: No module named '_cffi_backend'` inside this
sandbox's PDF-fixture tests, present before this pass and unrelated to
vocabulary/requirement code). No backend file changed for the semantic-zoom
half of this pass (`backend/app/vocabulary_graph.py` was inspected, not
modified — the close-zoom detail added client-side already had every field
it needed).

**Data safety**: unaffected — every control this pass touches (zoom bands,
close-zoom detail, hover tooltip) is pure client-side rendering over data the
page already fetched from the same read-only `GET /api/vocabulary/graph`.
