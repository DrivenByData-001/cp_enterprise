# 31. Explicit role save, Current Roles default, and vocabulary feedback

Focused UX/workflow pass (handoff brief: "Career Planner — Explicit Role
Save, Current Roles Default, and Vocabulary Feedback"). Does not touch the
requirement/evidence architecture, the vocabulary model, Career Space,
Pathways, or compensation — only how the existing save/review workflow and
the Dashboard's default view communicate what has actually happened.

## 1. Save posting is the persistence checkpoint

`POST /api/role-instances/ingest` already persisted the immutable source
document and the `role_instance` before any AI enrichment ran (source-aware
ingest, unchanged this pass). What changed is that the UI now says so
explicitly, rather than using language ("Capture text") that left it unclear
whether the posting was safely stored yet:

- `Import.tsx`'s primary action reads **Save posting & continue** (busy
  state: "Saving…"); the duplicate-warning path's "capture anyway" action is
  now **Save anyway**.
- `ImportSteps.tsx`'s first workflow step reads **Save posting** (was "Add
  posting"): `Save posting → Review details → Review requirements →
  Compare`.
- A restrained note sits next to the primary action: "Nothing is saved until
  you choose Save posting." The PDF-preview message says the same thing
  ("Nothing is saved yet.") rather than "captured".

No new persistence column or status was introduced — "saved" already follows
structurally from a real `role_instance` row existing (`document +
role_instance`), exactly as before.

## 2. Metadata/requirements are subsequent review/enrichment

Nothing changed architecturally here: metadata enrichment
(`propose`/`PATCH .../metadata`) and requirement extraction remain separate,
reviewable steps that never gate whether the role is "saved" — they were
already optional, non-blocking follow-ups. What's new is that the workflow
now visibly distinguishes **Saved details** (the role's real, persisted
title/organisation/location) from **Proposed details** (an AI suggestion
that has not been accepted yet): `SavedRoleBanner` shows the former; the
metadata-enrichment panel on `RoleRequirements.tsx` labels its own form
"Proposed details (not yet saved)" once a proposal exists.

## 3. A user can safely leave after Save posting

`frontend/src/components/SavedRoleBanner.tsx` is a small shared component
rendered on every step of the review workflow that already has a persisted
role id — Review details, Review requirements
(`frontend/src/pages/RoleRequirements.tsx`), and Compare
(`frontend/src/pages/Comparison.tsx`). It fetches the role via the ordinary
`GET /api/roles/{id}` (never a second, local-only "saved" flag that could
disagree with the database — once a page has a real `roleId`, the role is
persisted by construction) and shows:

> **Saved to Roles** — This posting is stored. You can leave this workflow
> and return later.
>
> Saved details: *{title} · {organisation} · {location}*
>
> [Open saved role] [Back to Roles]

This is a persistent banner, not a toast — it stays visible through the
whole review workflow, not just immediately after ingest.

## 4/5/6. Requirement extraction's vocabulary outcome

`extract_role_requirements` (`backend/app/extraction.py`) already resolved
extracted surface forms against the canonical vocabulary (exact match on
canonical name/alias, then embedding-candidate retrieval + model
adjudication) before this pass — that resolution logic is unchanged. What
was missing was **reporting** it. The extraction result now additionally
carries:

```json
{
  "vocabulary_outcome": {
    "matched_existing_count": 18,
    "pending_term_count": 4,
    "new_pending_proposal_count": 2,
    "matched_existing": [
      {"surface_form": "ALM", "concept_id": "...", "canonical_name": "asset-liability management (alm)", "type_code": "capability"}
    ],
    "pending_terms": [
      {"surface_form": "NumPy", "proposal_id": "...", "created_new_proposal": true}
    ]
  }
}
```

Semantics (see `app/extraction.py`'s inline comments for the exact
derivation):

- `matched_existing`/`matched_existing_count` — extracted terms that resolved
  to an *already-accepted* concept or alias this run. These never create a
  pending cluster — a term matching an accepted alias (e.g. `ALM` →
  *asset-liability management (alm)*) is expansion of nothing; it is simply
  evidence for an existing, human-curated concept. Deduplicated by surface
  form within one run (a term mentioned twice in one posting is one line).
- `pending_terms`/`pending_term_count` — unresolved terms **this role/run
  contributed to**, whether or not the underlying `concept_proposal` was
  already pending from an earlier role. This is deliberately *not* the same
  as "globally new proposal rows" — a role mentioning an already-pending term
  still contributes real evidence to that pending decision and must be
  visible in this role's own result.
- `new_pending_proposal_count` — the narrower "how many pending proposal rows
  were genuinely new this run" figure (equal to the existing
  `proposals_created` operational counter), kept separate so the two are
  never conflated. An identical rerun correctly reports
  `created_new_proposal: false` for a term it already made pending on an
  earlier run (§8.1 of the brief) — it still counts toward
  `pending_term_count` (this run still touched it), just not as new.
- No accepted concept is ever auto-created by extraction. A new term becomes
  a pending `concept_proposal` — expansion of the *proposal queue*, never the
  authoritative vocabulary — and only becomes canonical after a human
  resolves it in Vocabulary.

No new AI call and no per-item DB round trip: the outcome is derived from the
same `item_concept`/`validated` data the extraction pass already computed,
plus one batched `_concepts_by_ids` lookup for the matched concepts' display
fields.

`RoleRequirements.tsx` renders this as a compact **Vocabulary** panel after
extraction ("N extracted terms matched existing vocabulary." / "N terms need
vocabulary review.") with expandable "Show matches" (surface form → canonical
concept, so an alias match is self-explanatory rather than looking ignored)
and "Review pending terms" (links to `/vocabulary?status=pending&q=<term>`,
which `Vocabulary.tsx` reads to pre-focus the cluster-review queue on that
term — no second curation UI; Vocabulary remains the one place proposals are
actually resolved).

## 7/8. Roles defaults to Current

`GET /api/roles` (`backend/app/routes/roles.py::list_roles`) gained a
`current` period value, now the **default** (replacing `recent` as the
default — `recent` itself is unchanged and still available):

- **Current** = posting year is the current calendar year (`EXTRACT(YEAR
  FROM posting_date) = EXTRACT(YEAR FROM CURRENT_DATE)`), **or** — only when
  `posting_date IS NULL` — the role's linked document was itself captured
  this calendar year. This deliberately makes a freshly saved undated
  posting visible without ever treating its capture date *as* its posting
  date (that substitution never happens; it only ever decides inclusion). A
  dated-but-old role is never pulled in merely because it was recaptured
  recently, and an undated role with a stale capture never counts as
  current — both directions are covered by
  `test_roles_pagination.py::test_default_period_is_current_and_includes_undated_recently_captured_roles`.
- **Default sort** under Current is newest/recently-captured first
  (`captured_at` descending, falling back to `posting_date` for a row with
  no linked document — e.g. a legacy/bulk-imported role — rather than being
  stranded at a meaningless position), not similarity. This only applies
  when the caller omits `sort` entirely; an explicit `sort=similarity` (or
  any other explicit sort/temporal parameter) always wins, unchanged
  precedence rule. Similarity, posting-date, captured-at and title sorts all
  remain fully available and unchanged for explicit use.
- `recent` (last `DEFAULT_RECENT_YEARS` years + undated), `all`, a specific
  `year`, a `date_from`/`date_to` range, and `unknown_date` all remain
  exactly as before — Current is a new *default*, not a replacement for any
  of them. The full ~2008–2025 historical corpus stays one click away, never
  hidden at the persistence layer.

`Dashboard.tsx`'s period selector now reads **Current roles** / Recent (last
few years) / All years / a specific posting year… / Unknown posting date,
with restrained copy under Current: "Roles posted this calendar year, plus
newly captured roles whose posting date is not known." No query-param
change: opening `/` with no parameters is exactly the same request as
`?period=current` (and no explicit `sort`).

## No schema migration

Every fact this pass needed already existed structurally:

- "Saved" = `document + role_instance` exist (source-aware ingest, unchanged).
- "Current" = `role_instance.posting_date` and the linked
  `document.captured_at`, both pre-existing columns.
- The vocabulary outcome is derived from data `extract_role_requirements`
  already computes/writes (`concept`, `concept_alias`, `concept_proposal`,
  `concept_proposal_occurrence`) — no new table or column.

## See also

- `docs/18-consolidation-and-analytical-foundation.md` §3 — the original
  Dashboard temporal-filter/pagination design this pass changes the default
  period/sort of (superseding its "recent default" wording; the filter
  mechanics, pagination, and `year_range` behaviour described there are
  otherwise unchanged).
- `docs/29-requirement-review-curation-gate.md` — the requirement-claim
  review gate `vocabulary_outcome` sits alongside; unchanged by this pass.
