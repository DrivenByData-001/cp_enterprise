# 22 — Durable Role Detail Fix, Accepted-Vocabulary Maintenance, and Concept Dossiers

This build has two independent objectives: permanently closing out the
remaining 2026 Role Detail crash, and making accepted vocabulary genuinely
maintainable — curator-editable metadata plus a persisted, AI-assisted
Concept Dossier that expands a terse canonical term into something a curator
can actually understand. Neither touches profile360, Phase 4 market-data
work, or any unrelated behaviour.

## 1. Durable Role Detail fix

Production diagnosis (following the earlier fix in docs/21 §1) found the
*actual* remaining cause of the 2026 Role Detail crash: 23 historical/
imported rows — including all three 2026 roles — had
`legacy_analysis.top_adjacent_roles` stored as a JSON **string** (e.g.
`"[\"Deputy Head of Actuarial Function\", \"Head of Actuarial Function\"]"`)
rather than a genuine JSON array. `RoleDetail.tsx` assumes `string[]` and
calls `.join(', ')` on it, which throws when handed a plain string. That
production data has since been corrected manually (23 rows), but this build
does **not** depend on or repeat that mutation — the fix is a defensive,
permanent one at the application layer, so no future malformed row (however
it gets there) can ever crash the page again.

`db.flatten_role_instance` (app/db.py) now runs `top_adjacent_roles` through
`_normalize_top_adjacent_roles` after unpacking it from `legacy_analysis`:

- a genuine `list[str]` is preserved unchanged;
- a string containing a valid JSON array of strings is parsed and returned
  as that array;
- anything else (unparseable text, a JSON object, an array with non-string
  elements, `None`) becomes `null` — never exposed to the frontend raw.

This is a pure read-time projection: it never writes to the database, so the
original (however malformed) `legacy_analysis` JSON is left completely
untouched in persistence — no silent data migration happens on read. The
frontend's `Role.top_adjacent_roles: string[] | null` type is unchanged;
the backend contract is what was fixed. See
`backend/tests/test_role_detail_malformed_top_adjacent_roles.py` (31 tests)
for the full matrix of well-formed/malformed inputs, both at the unit level
and through the real `GET /api/roles/{id}` and `GET /api/roles` endpoints.

## 2. Accepted-vocabulary maintenance

The Vocabulary page's curation queue (docs/19) resolves *pending*
`concept_proposal` clusters into canonical concepts, but had no way to
correct an already-accepted concept afterward. This build adds that without
touching the existing proposal-resolution flow at all.

### 2.1 Concept Details drawer

Every concept row (in the "Concepts" browser at the bottom of the Vocabulary
page, and on a resolved cluster card) now carries a compact **Details**
button. Clicking it opens a right-hand drawer
(`frontend/src/components/ConceptDetailsDrawer.tsx`) — full-width on narrow/
mobile layouts (`.drawer-panel` in `index.css`) — that is the maintenance
interface for that one concept: canonical name, type, definition, aliases,
and active/deprecated status, each behind an explicit Edit → Save/Cancel
step (nothing is exposed as an open form until Edit is selected). Aliases
are addable/removable independently of the rest of the metadata edit.

"Accepted" means curated and protected from automation, **not** immutable.
`app/vocabulary_bootstrap.py` never `UPDATE`s `jobber.concept` for an
existing row — it only ever inserts new proposals — so nothing here can be
silently overwritten by a later bootstrap run.

### 2.2 API and safety rules (`app/concept_curation.py`)

`PATCH /api/concepts/{id}` edits name/type/definition/status;
`POST`/`DELETE /api/concepts/{id}/aliases[/…]` add/remove aliases
independently. Canonical-name uniqueness (`UNIQUE(type_code,
canonical_name)`) is enforced by the existing DB constraint and surfaced as
a 400.

Type changes are the one non-trivial part. Before applying `type_code`,
`concept_curation._edge_violations` checks every `jobber.concept_edge` row
touching the concept (as either side, any status) against
`jobber.concept_edge_rule` with the *new* type substituted in — if any edge
would no longer be a legal `(relation, from_type, to_type)` triple, the
whole change is rejected with a 409 naming the conflicting edge(s), and
nothing is written. This is what makes an ordinary atomic-to-atomic
correction (e.g. `knowledge → tool`) just work, while a transition that
would silently corrupt a `broader_than`/`component_of`/`demands` edge is
caught server-side instead.

Capability transitions get an extra rule on top of the edge check:

- moving **into** `type_code='capability'` requires the caller to supply
  `capability_detail.demonstration_standard` (that table's own NOT NULL
  column) in the same request — otherwise 409, never a concept sitting at
  `type_code='capability'` with no matching `capability_detail` row;
- moving **out of** `capability` is rejected with a 409 if the concept still
  has `component_of`/`demands` edges pointing at it (remove them via the
  Capabilities page first); otherwise its now-meaningless
  `capability_detail` sidecar row is deleted as part of the same
  transaction.

Concepts are never hard-deleted anywhere in this module — deprecation
(`status='deprecated'`, reversible) is the only "remove from active use"
path, the same convention `CapabilityUpdate` already used for capabilities.
See `backend/tests/test_concept_metadata.py` (21 tests) for the full matrix,
including the unsafe-transition-rejected cases.

## 3. Concept Dossier

A first-class, persisted, AI-assisted **Concept Dossier**
(`jobber.concept_dossier`, migration `0012_concept_dossier.sql`) turns a
terse canonical term — "ORSA", "Capital Management", "Prophet" — into a
structured explanation: plain-English definition, classification rationale,
what it means in practice, what sits beneath/around it, stronger/weaker
expressions, boundaries & overlaps with neighbouring concepts, related
canonical concepts, and caveats. It lives in `jobber`, never `profile360`.

### 3.1 Lifecycle

`active | draft | superseded`, with two partial unique indexes enforcing at
most one active and at most one draft per concept — the same pattern
`role_context_enrichment` (docs/21 §3) already established. History is
retained: a row is never deleted, only ever moved to `superseded`.

- **No dossier yet** → *Generate* creates the first active version.
- **Active exists** → *Regenerate* always calls the model again but creates
  a **draft**, never touching/superseding the active row automatically.
  Calling it again while a draft already exists supersedes that draft and
  replaces it — never a second concurrent draft.
- **Adopt** supersedes the active row and promotes the draft to active,
  atomically, in one transaction.
- **Discard** marks the draft superseded; the active row is untouched.
- **Manual edit** (a curator's direct edit of the active dossier's content)
  supersedes the current active row and inserts a new curator-authored one
  — version history retained exactly like an AI regeneration, just with
  `origin='curator'` and no model/guidance/raw output. It never touches
  `related_concepts` — that stays an AI-suggested field, carried over
  unchanged.

Viewing a concept never triggers generation — `GET
/api/concepts/{id}/dossier` is a pure read, proven by a test that makes the
mocked AI call raise `AssertionError` if it's ever reached from a GET.
`GET .../dossier/history` returns every version (active, current draft, and
every superseded row) so "retains version history" is actually retrievable,
not just retained silently.

### 3.2 Generation input and grounding (never profile360)

`app/concept_dossier.py` follows the same discipline as `role_context.py`:
evidence is read and the cursor closed, the model is called with **no** DB
transaction open, and persistence happens in a fresh transaction afterward.
A provider/validation failure raises before any write, so the active
dossier is always left untouched by a failed generate/regenerate.

The prompt (`prompts/concept_dossier_generate.md`) is given: the concept's
canonical name, type, and that type's own definition; its current
definition and aliases; up to 8 representative role-evidence examples
(preferring a verbatim `requirement_claim.evidence_span` quote, falling
back to the plain `role_skill_observation` surface form); and up to 12
candidate related concepts. It never reads profile360 at all — the module
has no query against that schema.

Semantic distinctions the prompt is built around (tool vs. method vs.
regulation vs. domain vs. capability, e.g. "Prophet" the tool vs. "Prophet
modelling" the capability, "Capital Management" vs. "Capital Modelling" as
related-but-distinct capabilities) are spelled out explicitly so generation
doesn't collapse genuinely separate concepts into synonyms, and doesn't
narrow a concept beyond what the corpus evidence actually supports.

Audit metadata (model, prompt version, the guidance used, and the full raw
AI response) is persisted directly on `jobber.concept_dossier`, not
`jobber.extraction_run`. That table's `subject_type`/subject-FK CHECK
constraints (0003) cover four subject kinds, both unnamed at creation —
extending them for a fifth (`concept`) would mean altering two unnamed
constraints on a table every other AI feature in this app also writes to,
which is a materially riskier change than this feature needs. Every other
audit/safety invariant is preserved exactly; `raw_output` is never returned
by the normal read API, same posture as `role_context_enrichment`.

### 3.3 Related concepts (never formal ontology edges)

Candidates are grounded and ranked *before* the AI ever sees them
(`concept_dossier._candidate_related_concepts`): concepts co-occurring with
the target across roles (via `role_skill_observation`, ranked by
co-occurrence count), plus any concept already joined to it by an accepted
`concept_edge`. The model may only choose relationships among the ids it was
actually given — `_filter_related_concepts` silently drops any `concept_id`
in the response that isn't one of those candidates (a real-but-unoffered
concept and an outright invented id are both discarded the same way; see
`test_related_concepts_restricted_to_accepted_candidates`). Nothing here
ever writes a `jobber.concept_edge` row — a dossier's explanatory
decomposition ("Prophet modelling sits around the tool Prophet") is
deliberately kept separate from the formal ontology.

Stored related-concept entries carry `concept_id`, `canonical_name`,
`type_code`, `relationship`, and a one-sentence `explanation`, so the
drawer's chips need no extra lookup to render or to jump to that concept —
clicking one loads it into the same drawer (`onNavigate` in
`ConceptDetailsDrawer.tsx`).

### 3.4 Drawer UX

Kept visually quiet, per the suggested structure: name + Edit; type/
definition/aliases; a divider; the dossier's read-only content; related-
concept chips; a collapsible "Guide AI" input plus Regenerate. If a draft
exists it renders in a clearly bordered, separately labelled "AI draft — not
yet adopted" block underneath the active version — the distinction between
current curated content and an unadopted AI draft is never ambiguous.

## 4. API surface added

```
PATCH  /api/concepts/{id}                    concept metadata edit (name/type/definition/status)
POST   /api/concepts/{id}/aliases            add alias
DELETE /api/concepts/{id}/aliases/{alias_id} remove alias

GET    /api/concepts/{id}/dossier            active + draft (pure read)
GET    /api/concepts/{id}/dossier/history    every version, newest first
POST   /api/concepts/{id}/dossier/generate   first-time generation (no-op if active exists)
POST   /api/concepts/{id}/dossier/regenerate always calls the model, creates/replaces a draft
POST   /api/concepts/{id}/dossier/adopt      promote draft -> active
POST   /api/concepts/{id}/dossier/discard    drop the draft
PUT    /api/concepts/{id}/dossier            curator manual edit -> new active version
```

All of the above sit behind the existing central auth policy
(`dependencies=[Depends(require_auth)]` in `app/main.py`) like every other
router — nothing here is a new public route.

## 5. Testing

- `backend/tests/test_role_detail_malformed_top_adjacent_roles.py` — 31 tests (§1).
- `backend/tests/test_concept_metadata.py` — 21 tests (§2).
- `backend/tests/test_concept_dossier.py` — 24 tests (§3).
- Full existing backend suite: 465 tests passed, no regressions.
- Frontend: `tsc -b` (typecheck), `oxlint` (lint), `npm run build` (production
  build) all clean.
- A real Chromium browser smoke run (login → the 2026 role's Role Detail →
  Vocabulary's accepted concepts → Details drawer → metadata edit → guided
  regenerate against an unconfigured AI provider → related-concept chip
  navigation → Generate on a fresh concept → the active/draft distinction →
  a genuine Adopt (no AI call needed) → logout) passed all 17 assertions
  against a disposable Postgres database seeded with the exact malformed
  `top_adjacent_roles` shape production had. No OpenAI credentials/network
  access exist in the build sandbox, so a *successful* AI generation could
  not be exercised through the browser; that path is covered instead by the
  24 mocked-AI backend integration tests above, which assert the actual
  generated content, not just that the button doesn't crash the page.
