# Economic pathways, compensation context and archetype context

This build turns Career Planner from "where am I relative to jobs?" into
"where am I, where could I go, what would each destination pay and feel like,
what intermediate moves are available, and which investments offer the
greatest return?" — without inventing a single number the evidence does not
support.

It is one coherent feature built on the existing capability, economics,
archetype, stepping-stone, Profile360 and Role Context infrastructure. No
parallel model was created: there is still exactly one person-compensation
store, one role-requirement model, one archetype table, one capability
engine, and one compensation benchmark layer.

Related reading: `docs/24-phase4-economics-and-vocabulary-overview.md` (the
market economics layer this builds on), `docs/16-phase3-capability-engine.md`
(the evidence states everything here reuses),
`docs/21-role-context-and-vocabulary-splitting.md` (Role Context),
`docs/28-target-mapping-and-performance.md` (the caching/revision machinery).

---

## 1. Personal compensation vs market compensation

The single most important boundary in this build:

| | Lives in | Written by | Read by |
|---|---|---|---|
| **Your own pay** | `profile360.compensation_observation` | profile360's own tool — never this app | `app/personal_earnings.py`, read-only |
| **Role / market pay** | `jobber.compensation_observation`, `jobber.d_archetype_comp` | this app | `app/compensation_resolver.py` |

They are **combined only at read time**, in a comparison computed per
request. No personal compensation fact is ever copied into `jobber`, and no
market figure is ever written into `profile360`. `backend/tests/
test_personal_earnings.py::test_personal_compensation_is_never_copied_into_jobber`
asserts this rather than leaving it to documentation.

The one jobber-side row this build adds on the person's behalf,
`jobber.planning_assumption`, holds **no compensation amount at all** — only
the billable-days-per-year parameter described below. It is a parameter of
this application's analysis, not a person-side source fact.

---

## 2. Current vs latest-known earnings

`app/personal_earnings.py` derives an earnings state from **accepted**
person-side observations only. Unreviewed and rejected rows never
participate.

**Current** is decided from what the sources actually establish, consulting
both the observation's own stated period **and the employment episode it
belongs to** (`profile360.episodes`, joined through
`compensation_observation.episode_id`). Four rules, each of which can only
demote to historical — nothing promotes an observation to current on weak
grounds:

1. the observation's own stated period has ended → historical;
2. it starts in the future → not current yet;
3. the employment episode has ended, or is not in a status recognised as
   ongoing (`active` / `current` / `ongoing`; an unrecognised status is
   treated as not ongoing, so unfamiliar data makes the answer more cautious,
   never less) → historical, **however open-ended the observation row looks
   on its own**;
4. an observation with no dates at all is current only if a linked episode is
   genuinely ongoing — absence of evidence is not evidence of currency.

Rule 3 is the one that matters most for a corpus of historical data: an
open-ended salary observation attached to an employment that ended in 2022
is not your current salary in 2026. Each historical baseline carries an
`evidence_status_reason` explaining in the user's terms why it reads as past,
so the label is never unexplained.

When no current evidence exists at all, the most recent historical evidence
is returned with `status='historical'` and a note saying exactly that. It is
never quietly presented as today's pay.

```
Current earnings              Latest known earnings
Basis: PAYE                   Basis: contract
Base: €105,000 annual         Rate: £650/day
Effective from: 2025-07-01    Evidence period: 2023
Evidence status: current      Status: historical — no current evidence
```

### What is kept separate, always

- **PAYE and contracting.** `employment_basis` is part of the baseline
  grouping key, so these are two baselines, never one blended figure.
- **Currencies.** Two accepted baselines in different currencies come back as
  two baselines. No FX rate exists anywhere in this codebase and none is
  invented.
- **Base pay vs everything else.** Bonus, allowance and employer pension are
  preserved in `other_components`, individually identifiable. A bonus never
  stands in for the base-pay baseline an advertised base salary is compared
  against.
- **Source amount vs derived figure.** A planning equivalent is returned
  *alongside* the observation's own amount, never in place of it.

---

## 3. Contract planning equivalents

A day rate is **never** annualised on its own. With no assumption stated,
`planning_equivalent` is `null` and a comparison against an annual salary
reports itself as not comparable, naming the missing assumption.

Once the user states a billable-days assumption
(`PUT /api/pathways/planning-assumptions`), the derived figure is returned as
a separate, explicitly labelled object:

```
Planning equivalent: £139,750
Assumption: £650 × 215 billable days
This is a planning equivalent, not salary.
```

Clearing the assumption immediately withdraws every derived equivalent —
it is never sticky beyond what the user currently says. The assumption lives
in `jobber.planning_assumption` (a migration-seeded singleton), separate from
every source observation.

---

## 4. Posting stated-compensation review

Raw capture is unchanged and still makes **no AI call**. Compensation
extraction is a separate, explicit action on a captured role:

1. `POST /api/role-instances/{id}/compensation/propose` runs a narrow
   extraction against the role's own immutable source document, records an
   `extraction_run`, and returns a **proposal**. It writes no
   `compensation_observation`.
2. Each proposed item arrives with the server's own validation verdict
   already attached (`acceptable`, `problems`), computed with the same rules
   acceptance re-applies — so the review screen can never offer an Accept the
   server will refuse.
3. `POST /api/role-instances/{id}/compensation/accept` is the only writer. It
   takes the item as the reviewer confirmed it — unchanged (Accept) or
   corrected (Edit & accept). Both take **exactly the same** validation path.
4. Rejecting a *proposal* needs no endpoint: it was never persisted, so
   declining it leaves nothing behind.
5. Correcting, retiring or restoring an *accepted* figure uses this flow's
   own role-aware endpoints — `PATCH /api/role-instances/{id}/compensation/
   {observation_id}` and its `/reject` and `/reaccept` siblings — **not** the
   generic market-data review/PATCH pair. Those set a status or a column
   without re-reading the source, which would let a re-accept recreate two
   accepted base salaries and let a correction detach a stated fact from the
   quote justifying it. Everything that can make a posting-stated observation
   accepted runs the same validation and the same supersession, so neither
   invariant can be reached around.

   The generic endpoints **refuse** `basis='posting_stated'` outright with a
   400 naming the lifecycle that owns those rows. Their *listing* endpoint
   was already survey-only, but both updated by id alone, so nothing stopped
   a caller passing a posting-stated id directly — which is precisely the
   bypass that would have recreated two accepted base salaries. They still
   work unchanged for the survey rows they exist for.

### A correction preserves what it replaces

A correction is **a new accepted observation plus the retirement of the old
one**, never an in-place rewrite. Overwriting the row would give the right
current answer while erasing the fact that a different figure was once
accepted as source-backed financial evidence — exactly what this codebase's
curation model refuses everywhere else (compare
`routes/role_instances.py::_supersede_with_new_claim`, which does the same
non-destructive supersession for requirement claims). After a correction the
history reads:

```
£120,000–£145,000   rejected   "corrected; superseded by reviewed observation <id>"
£125,000–£150,000   accepted
```

(with the corrected row quoting the passage of the advert that states
£125,000–£150,000 — see "A stated figure must be a figure the advert states"
below.)

Both rows survive, exactly one is accepted, and each keeps its own
content-addressed `source_key` describing its own figures. A correction that
changes the component (base → total_package, say) retires the original
explicitly, since supersession otherwise keys on the *new* component and
would leave the old row accepted alongside it.

Numbers are canonicalised before hashing that key: the same figure arrives as
an int from JSON, a float from Pydantic and a Decimal from the database, and
without normalising, re-submitting an unchanged figure would hash to a new
key and duplicate the row instead of being the no-op it is.

The accept payload is deliberately permissive at the HTTP boundary and strict
in the service layer: `bonus_pct` is accepted and `currency` is nullable,
because a bonus percentage genuinely has neither a cash amount nor a currency
of its own and the extraction prompt returns exactly that shape. A required
`currency` on the route model would have rejected the model's own bonus
proposal with a 422 before any of the rules below ran. The per-component
rules decide what is actually valid, in one place, for the proposal screen
and acceptance alike.

Server-side validation on accept, in order:

- the role exists and has a linked source document;
- `evidence_span` occurs **verbatim** in that document's `content_text`;
- the document's `provenance_quality` is `original` — a legacy,
  reconstructed or unknown-provenance document can never back a stated fact,
  however plausible the quote looks;
- the value shape matches the component: an amount range in a three-letter
  currency for base / day_rate / total_package, or a percentage in
  `bonus_pct` with no cash amount for a bonus;
- component and pay period are coherent: `day_rate` is `daily`, `base` and
  `total_package` are `annual`;
- **every submitted figure is stated by that span, and means what the span
  says it means** (below);
- component / pay_period / employment_basis are in the controlled sets.

### A stated figure must be a figure the advert states

The verbatim check proves the *quote* is real. It says nothing about whether
the numbers submitted beside it are the numbers that quote gives, and the
shape rules only check shape. Between them, an accept of £999,999 carrying a
perfectly genuine `"£120,000 - £145,000 per annum"` span satisfied every rule
and was stored with `basis='posting_stated'` — the tier the resolver treats
as fact, that feeds the archetype benchmark and the user's own comparison.

So each figure is matched against the numbers the span actually contains:

- `amount_min` / `amount_max` against the amounts written in the span;
- `bonus_pct` against the percentages written in the span;
- a number written as a percentage is **never** an amount, so a quote saying
  "a bonus of up to 15%" does not license a salary of 15.

Normalisation is by value, not by string — an advert writing `£120k` and a
reviewer entering `120000` mean the same figure. The parser reads what
adverts write and nothing more:

| Written in the span | Reads as |
| --- | --- |
| `£120,000`, `120000` | 120000 |
| `£120k`, `120 k` | 120000 |
| `£1.2m` | 1200000 |
| `£120-145k` | 120000 **and** 145000 |
| `15%`, `15 per cent` | 15 percent (never the amount 15) |

`"£120-145k"` states £120,000, not £120: in a range whose upper bound carries
a scale suffix, the lower bound inherits it, and that reading *replaces* the
bare one rather than joining it.

When a figure is not in its quote the answer is to refuse, never to guess
which number was meant. The refusal names what the span does state and points
at the `curator_asserted` basis, because a figure that is the reviewer's own
judgement rather than the advert's is a legitimate thing to record — just not
as `posting_stated`.

This makes a correction **re-anchor rather than drift**: a PATCH that changes
the amounts without changing the span is refused unless the span already
states them, so correcting a figure to one from a different passage means
quoting that passage. A figure the advert states nowhere cannot be reached
from this flow at all.

The rule governs *writes*, and no migration rewrites existing rows: a row
accepted before it existed keeps its stored figures. It is enforced the
moment anything tries to make such a row accepted again, so a `/reaccept` of
an uncorroborated legacy row is refused with the same message rather than
silently restoring it.

### ...and must mean what the advert says it means

Matching the number is half of source fidelity. `component` and `pay_period`
are how the resolver *interprets* that number and `currency` is what it is
denominated in, so a figure quoted correctly but labelled wrongly is still a
fabricated fact. Two cases that passed every numeric check:

```
"Rate: £650 per day"          as base / annual / GBP  →  a £650 annual salary
"Total package up to £180,000" as base                →  a base salary never offered
```

The second is the worse of the two: that figure would then be aggregated into
`d_archetype_comp` as a *salary* benchmark for the archetype.

So the quote is read for three more things:

| What the span says | What it requires |
| --- | --- |
| "per day", "day rate", "per diem", "/day" | `day_rate` + `daily` |
| "per annum", "a year", "annually", "p.a." | `annual` |
| "total package", "OTE", "on-target earnings" | `total_package` — and it may not back `base` or `day_rate` |
| a `total_package` claim | needs package / OTE / total-compensation wording to support it |
| `£`, `€`, or an ISO code | the same currency, never converted |

Each rule is deliberately one-sided: it fires only on wording that settles
the question and stays silent when the passage does not, because silence
leaves the reviewer's judgement in place — the right default for a quote that
genuinely does not say. Three consequences of that stance:

- **The two package vocabularies differ, and the asymmetry is the point.**
  The bare word "package" *supports* a total-package claim but may not
  *contradict* a base one. "Base salary £120,000 plus a benefits package" and
  a `Package:` heading above a salary range are both ordinary advert English,
  and neither says the figure is the whole package. Only wording that
  explicitly totals may overrule the reviewer.
- **Ambiguous currency signs rule nothing out.** `£` reads as GBP and `€` as
  EUR, but `$` (USD, CAD, AUD, SGD, HKD…) and `¥` (JPY, CNY) are deliberately
  absent: a sign that cannot settle the question must not be allowed to
  refuse a currency.
- **A passage stating both bases settles neither.** "£650 per day, c.
  £150,000 per annum" supports the day rate *or* the annual equivalent, as
  the reviewer judges.

`bonus_pct` takes only the percentage check. Its period is inert (nothing
reads one off a percentage), its kind is fixed by the component, and its
currency is deliberately *not* read from its own quote — it is the currency
of the pay the bonus applies to, taken from the role's evidence, so checking
it against "up to 15%" would test the wrong thing.

Component and pay period are also checked against each other without a span
at all, since `day_rate`/`annual` is wrong on its own terms whatever the
advert says. The Edit & accept control carries component and pay-period
selectors for that reason: the model can read the right quote and still label
it annual base pay, and without them a reviewer could correct the amount but
not the thing the amount describes. Choosing a component sets the period it
implies, so the pairing cannot be got wrong there.

Only source-supported fields are extracted: amount min/max, currency,
component, pay period if supported, employment basis if supported, and the
exact evidence span. The prompt is explicit that "competitive salary" means
*no stated compensation* — producing a guessed number there is the worst
possible outcome for this task.

Accepted rows are `basis='posting_stated'`, `review_status='accepted'`, with
the quote stored in the new `compensation_observation.evidence_span` column.
The legacy `role_instance.salary_*` columns are never written or cleared, so
the 0013 backfill and every historical role keep working unchanged.

---

## 5. Compensation resolver precedence

`app/compensation_resolver.py` restores the *function* of the old
`salary_estimate_*` field without restoring its opaque implementation. There
is no model call in that module and no new statistical aggregate — the market
tier reads `jobber.d_archetype_comp`, which `economics_engine.py` already
computes.

```
1. accepted stated compensation on this role     -> basis 'advert_stated'
2. otherwise archetype + market benchmark        -> 'market_estimate'
3. otherwise the legacy posting estimate         -> 'legacy_estimate'
4. otherwise                                     -> 'insufficient_evidence'
```

Within tier 1, a **reviewed, source-quoted** observation outranks the
mechanical 0013 backfill projection of the legacy salary columns. Both are
genuinely "stated on the advert"; the quoted one simply has better
provenance.

Every tier produces a different, never-interchangeable basis, and the UI
renders the four as visibly distinct things — **Advert salary**, **Market
estimate**, **Legacy estimate**, **Insufficient evidence**. There is no code
path that promotes a lower tier's number into a higher tier's label, and
`insufficient_evidence` is a real answer rendered as itself, never as a zero.

Every resolution returns basis, currency, min / reference / max, market,
period and as-of date, archetype, evidence counts, reference source,
evidence quality and a trace naming the tier and the row it came from.

`resolve_role_compensation_bulk` resolves any number of roles in three
queries — asserted by a test, because Pathways resolves compensation for
every candidate archetype's supporting postings.

---

## 6. Personal current-vs-opportunity comparison

`compare_to_personal_earnings` computes a delta **only** where the comparison
is genuinely like-for-like. Checked in order, with the first failure reported
as the reason:

1. the opportunity has a figure at all;
2. a personal baseline exists;
3. currency matches exactly — never converted;
4. the component / pay-period family matches, directly or through a stated
   planning equivalent;
5. employment basis is compatible where both sides state one — PAYE and
   contract are never treated as equivalent.

```
Your current base        £105k
Role market range    £120k–£145k
Reference                 £132k

Potential base-pay difference
+£15k to +£40k
```

Where it is not comparable the UI shows the reason, not a number. Valid
comparisons still carry their limitations (planning equivalent in use,
historical baseline, market-benchmark rather than advert figure, different
years, base pay only).

---

## 7. Reviewed archetype classification

`app/archetype_classification.py` adds archetype review to the normal role /
target workflow, under three rules that are structural, not merely
documented:

1. **The AI may only propose an existing active archetype.** The model is
   handed the catalogue and told to copy a name verbatim; the server resolves
   it against `concept(type_code='role_archetype', status='active')` and
   discards anything that does not match exactly. There is no code path from
   a model response to `INSERT INTO jobber.concept` — auto-creation is
   impossible here.
2. **Nothing is assigned silently.** `propose_archetype` writes no
   `role_instance.archetype_concept_id`. Assignment happens only through an
   explicit `PUT`, which also accepts `null` — **leave unclassified is a
   valid, first-class outcome.**
3. **Manual choice always wins.** The reviewer can search the catalogue and
   pick anything active regardless of what was proposed.

A suggestion outside the catalogue is reported as unmatched with the raw
suggestion preserved, so a reviewer can see what the model said and why it
was not offered.

Legacy `role_instance.career_track` is left **completely alone** — not read,
not written, not deprecated. Pathways and the economics layer prefer the
reviewed archetype; the old free-text track remains for everything that
already uses it.

---

## 8. Pathways methodology

`app/pathways.py` computes nothing new. It **composes**:

```
  Profile360 earnings state        personal_earnings.py
+ canonical capability evidence    capability_engine.py via stepping_stones
+ target requirement mapping       target_mapping.py
+ structural stepping stones       stepping_stones.assess_all_candidates
+ reviewed archetype assignment    role_instance.archetype_concept_id
+ archetype compensation benchmark jobber.d_archetype_comp
+ capability gap value             jobber.d_gap_value
```

There is **no AI call anywhere in Pathways**, no compensation in any
embedding, and no new weighted score. Every ranking is by a transparent,
stated input: how many of the target's outstanding required gaps a route
addresses, then the supporting evidence behind it, then compensation where it
exists.

### Route depth: exactly two shapes (v1 limitation)

```
You → Target
You → Intermediate archetype → Target
```

That is the whole search space in this version. **There is no general N-hop
optimiser, no chained archetype sequence, and no "best path" search.** Each
additional hop would compound the uncertainty in every hop's structural
evidence without adding any new evidence, and this corpus does not support
it. The limitation is returned in the `method` block and shown in the UI, so
it is visible in the product rather than only here.

### Intermediate routes are grouped, not invented

An intermediate step is a **reviewed archetype** that at least one observed
posting — judged a genuine structural stepping stone by the existing engine —
is assigned to. The supporting postings are preserved underneath each
archetype and returned with it, so every claim on a node is inspectable back
to the individual adverts.

A posting with **no** reviewed archetype cannot form an intermediate node.
It is not silently dropped either: such postings are returned separately as
`unclassified_supporting_postings`, which is what makes "assign an archetype
to unlock this" visible rather than a mystery.

### Explicit states instead of false confidence

Five conditions each produce an explicit state rather than a confident
number, reported both as booleans in `gates` and as each node's own
`state` / `state_reason`:

- incomplete target requirement review (`review_incomplete`) — using the
  canonical `complete` flag, which is true only when unreviewed claims,
  unresolved vocabulary proposals **and** concepts needing re-extraction are
  all zero. Checking the unreviewed count alone let a target with a dangling
  vocabulary term pass as fully reviewed; `review_blockers` now names
  whichever parts are outstanding, with counts, on the gate and on each
  node's fit;
- incomplete target vocabulary mapping (`mapping_incomplete`);
- incomplete candidate review — scoped to the candidates that actually
  support a route shown here (the postings under an intermediate archetype,
  plus the useful-but-unclassified ones reported alongside them). Checking
  every posting in the corpus made this gate false because of unrelated roles
  the user is not being shown, which is noise rather than signal;
- insufficient compensation evidence (`insufficient_evidence` /
  `route_without_compensation`);
- missing archetype assignment.

---

## 9. Target value vs market option value

For each of the target's outstanding **required** capability gaps, Pathways
reports two distinct kinds of value and never merges them:

- **Target value** — its relevance to the selected target: that it is
  required, that closing it removes one of the target's own gaps, and which
  intermediate archetypes involve it.
- **Market option value** — what else it opens up, read from the
  market-level `d_gap_value` row for the selected market/currency: archetypes
  and roles unlocked or improved, and the highest qualifying reference
  compensation among them.

```
Capital management

Target relevance
Required by Head of Capital
Addresses 1 current target gap

Market option value
2 archetypes unlocked
Highest qualifying reference compensation: £145k

Your context
Current comparable base: £105k
Potential difference to unlocked reference: +£40k

Evidence quality
Moderate
```

**`d_gap_value` stays market-level.** No personal figure is ever written into
it. The personal overlay is computed at request time, through the same
compatibility rules Role Detail uses, and
`test_the_personal_overlay_is_never_written_back_into_d_gap_value` asserts
both that the row is unchanged and that no personal column exists on the
table.

Ranking is by transparent inputs in a stated order — capabilities that block
outright before ones that are merely unverified, then how much the market
opens up, then the money, then name. There is no new opaque weighted score.

---

## 10. Transition cost — conservative v1

Pathways reports only what the system actually knows: blocking gaps,
unverified gaps, evidence coverage, which of the target's gaps a route
addresses, and the user's own development-action planning entries.

Everything it does **not** know is named explicitly in `not_estimated` and
shown in the UI:

- no learning duration is estimated;
- no hiring or success probability is estimated;
- no universal learning-difficulty rating is applied.

`jobber.development_action` gains two optional human planning fields,
`planned_start_date` and `estimated_effort_hours`. Nothing derives, estimates
or defaults them, and **a development action never becomes capability
evidence** — it has no path into `requirement_claim`,
`person_capability_assertion` or any profile360 table, and completing one
changes no capability status.

---

## 11. Archetype Day-in-the-Life

`app/archetype_context.py` is the archetype-level sibling of Role Context,
with the same lifecycle and the same invariants:

- `GET` is a pure read — never generates, never calls the model;
- generation is explicit and on-demand; a repeated plain generate is a no-op
  that makes no model call;
- regenerate is a separate deliberate action that supersedes the prior
  version;
- a failed generation leaves the prior active version completely untouched;
- exactly one active row per archetype, enforced by a partial unique index;
- model, generator version, source fingerprint and full grounding provenance
  are stored on every row;
- **nothing is ever bulk-generated** — there is no such entry point.

### Grounding

Only archetype-level, market-side evidence: the archetype's metadata, its
assigned postings, the accepted/current requirements on those postings (via
the canonical `role_requirements` loader), the derived `d_archetype_demand`
rates, and the assigned postings' own Role Context enrichments.

**profile360 is never read**, directly or indirectly — this describes what
the archetype is like to do, not the user's fit for it. Compensation is also
deliberately withheld from the prompt: pay is resolved elsewhere with its own
basis, and narrative text is the wrong place for a number that would arrive
with no basis attached.

An archetype with no assigned postings refuses to generate rather than
describing itself from its own name.

### Caveats are computed, not only asked for

Thin evidence produces deterministic, evidence-derived caveats from this
module — one posting assigned, fewer than four, no reviewed requirements, no
derived demand — prepended to whatever the model itself said. The UI always
states that archetype context is a **synthesis across available evidence**,
not a fact about every job in the archetype.

---

## 12. Target Day-in-the-Life inherits archetype context

When a target has a reviewed archetype **and** that archetype has an active
context, target generation includes it as clearly-labelled **secondary**
grounding, after the target's own material, framed as:

- the role's own material always wins where the two disagree;
- anything taken from the archetype block is `inferred`, never
  `advert_grounded` — the archetype did not state it about this role;
- use it to fill gaps the target leaves open, not to pad the answer.

A target with no archetype, or an archetype with no generated context,
generates exactly as it did before — this can never turn a working
generation into a failing one. `GET /api/roles/{id}/context` reports
`archetype_grounding.available` so the UI can say what a generation will draw
on before the user clicks.

---

## 13. Why salary does not affect 3D Space geometry

Space stays semantically driven by role/profile embeddings and PCA. Financial
navigation belongs in Pathways.

- Compensation is never injected into embedding text.
- Nodes are never repositioned by earnings.
- Compensation is never required for a role to appear in Space.

`backend/tests/test_space_salary_independence.py` asserts each of these as an
invariant: two otherwise-identical roles with different salaries embed
identically, accepting compensation evidence does not move a role, an
unpriced role still appears, and the Space response carries no compensation
field.

---

## 14. Old JSON fields deliberately not revived

The monolithic JSON posting analysis is **not** restored. In particular,
these opaque LLM scores remain absent and no equivalent was introduced:

- `complexity_score`
- `specialisation_score`
- `transferability_score`
- `rarity_score`
- `market_demand_score`
- `automation_risk_score`
- `seniority_score` (structured seniority already exists)

`top_adjacent_roles` is **not** restored as a free-floating AI list. Its
useful function is replaced by structural stepping stones, archetype/pathway
evidence and compensation-aware navigation — all of which trace back to
specific postings and specific accepted requirements.

Old flat skills are not restored as analytical truth: the reviewed/legacy
split established by the requirement-review curation gate is unchanged.

**Concise role summary (§12 of the build brief):** deliberately *not* added
as a new AI field. Role Context's existing `grounding_summary` already
provides a short, grounded, clearly-derivative restatement of what a role
rests on, and adding a second narrative summary would have meant a second
opaque derivative competing with it. The original advert remains the source
of truth and accepted requirement claims remain the analytical truth.

---

## 15. Performance

Performance is a design constraint here, not an afterthought.

- **Raw capture remains fast and AI-independent.** Nothing in this build runs
  during capture; `test_raw_capture_makes_no_ai_call_and_creates_no_compensation`
  asserts it.
- **No AI fan-out on open.** Role Detail, Target Detail, Economics and
  Pathways all load from pure reads. `test_pathways_never_calls_the_ai_provider`
  asserts it by making the provider raise.
- **Bulk loading, no N+1.** Compensation resolution is three queries for any
  number of roles; Pathways reuses `stepping_stones.assess_all_candidates`,
  which bulk-loads requirements, review summaries and embeddings and
  evaluates each distinct concept at most once per request. Both have
  query-count regression tests (`backend/tests/query_counter.py`).
- **Derivation freshness is separate from cache invalidation.** This is the
  subtlest correctness rule in the build. The `economics` counter makes a
  Pathways cache entry *miss* when compensation changes — but a miss only
  recomposes Pathways **from** `d_archetype_comp` / `d_archetype_demand` /
  `d_gap_value`, which are written only by an explicit
  `POST /api/economics/rebuild`. Without a separate check, accepting a salary
  produced a freshly-computed answer over stale derived numbers, presented as
  current.

  `jobber.economics_rebuild_state` (migration 0024) records which source
  state the last rebuild saw — all three counters, since `path` changes
  archetype demand and `evidence` changes the structural model gap value is a
  counterfactual over. `app/economics_freshness.py` compares them at read
  time and returns `fresh` / `stale` / `never_rebuilt`.

  The planning assumption is **not** one of those inputs. Migration 0023 had
  put it on the economics trigger, which was right for cache invalidation and
  wrong once 0024 gave the same counter a second job: billable days cannot
  change a single derived figure, so marking every benchmark stale for them
  would have withheld the lot until the user rebuilt. Migration 0025 drops
  that trigger; the assumption still reaches Pathways through the personal
  fingerprint below.

  When not fresh, the market-estimate tier is **skipped entirely**: role and
  archetype resolution fall through to `insufficient_evidence` naming the
  rebuild, and gap value reports no market option value at all. Nothing is
  auto-rebuilt — a rebuild walks every role's fit and is far too expensive
  for a request path — so the honest response is to say so and withhold,
  which Pathways and Role Detail both surface with a "Rebuild economics"
  action.

- **Review-gate inputs invalidate the cache too.** Migration 0024 adds
  `path` triggers to `concept_proposal` and `concept_proposal_occurrence`,
  which 0019 did not cover. They are part of the canonical review summary, so
  without them a pending proposal could appear while a cached Pathways result
  kept reporting the old review verdict.

- **Pathways cache.** `jobber.d_pathways` is keyed by
  `(target_role_id, context_key)` where `context_key` is the selected
  market/currency, and carries a `revision` folding in:
  - the existing evidence and path revision counters (migration 0019);
  - a new `economics` counter (migration 0023), advanced by statement
    triggers on `compensation_observation`, `market`, `planning_assumption`,
    `d_archetype_comp`, `d_gap_value` and `d_archetype_demand`;
  - **a fingerprint of the user's profile360 compensation rows and the
    employment episodes they link to.** profile360 is externally owned, so no
    trigger can cover it — hashing is how an edit made in profile360's own
    tool invalidates a cached result. Every review status is included,
    because accepting or rejecting an observation changes the answer even
    though no amount moved; and the linked episodes are included because,
    since `_is_current` began consulting them, an episode moving from ongoing
    to ended changes a salary from current to historical without touching the
    salary row at all. Only episodes a compensation observation actually
    points at are hashed — an unrelated episode edit changes no earnings
    answer. (`target_cache.revisions` also hashes all of
    `profile360.episodes` for capability-coverage reasons, so the Pathways
    cache was incidentally protected; this fingerprint now covers its own
    contract rather than depending on that.)
  - the planning assumption, which is why removing it from the economics
    trigger loses nothing.

  The `economics` counter is separate from `path` on purpose: accepting a
  compensation observation changes every Pathways answer but changes no
  target path, and vice versa. The replacement trigger function preserves
  0019's existing `evidence`/`path` behaviour exactly.
- **Metrics.** Pathways reports the same metric vocabulary as the existing
  target-path analysis (`cache_hit`, `candidates`, `archetypes_assessed`,
  `distinct_concepts`, `concepts_evaluated`, `elapsed_ms`) and logs it, so
  the two can be read side by side. A benchmark is recorded as a JUnit
  property, like the existing target-path benchmark.

---

## 16. Migrations 0023 / 0024 and backward compatibility

`backend/migrations/0023_economic_pathways.sql` is **additive only**: no
existing column is dropped or retyped, no existing row is touched, and every
new column is nullable or defaulted. It adds:

1. `compensation_observation.evidence_span` — nullable, because every
   existing row legitimately has no quote (the backfill is a mechanical
   projection; survey rows cite page/table references instead);
2. `jobber.planning_assumption` — a migration-seeded singleton holding no
   compensation amount;
3. `jobber.archetype_context_enrichment` — same shape, lifecycle and
   one-active invariant as `role_context_enrichment`;
4. three new `extraction_run` tasks added to the vocabulary-exemption
   allow-list (carrying every previously exempted task forward, as
   migration 0017 warns);
5. `target_analysis_revision.economics` plus the replacement trigger
   function and the new economics triggers, and `jobber.d_pathways`;
6. two optional human planning columns on `development_action`.

`backend/migrations/0024_economics_rebuild_state.sql` is likewise additive:
`jobber.economics_rebuild_state` (a seeded singleton whose NULL revisions
read as "never rebuilt"), plus the two `path` triggers on
`concept_proposal` / `concept_proposal_occurrence` described in §15. The
rebuild-state row deliberately has *no* invalidation trigger of its own —
bumping the economics counter from it would immediately invalidate the
rebuild it just recorded.

**Deployment note:** after applying 0024, market estimates read as
`never_rebuilt` and are withheld until `POST /api/economics/rebuild` runs
once. That is the intended behaviour — the derived tables genuinely have no
recorded provenance until then — and the UI says so with a rebuild action
rather than failing.

Handled without any destructive backfill: old full-JSON roles, source-aware
roles, legacy salary fields, existing compensation observations, roles with
no compensation, roles with and without archetypes, targets with and without
archetypes, existing Role Context, legacy `career_track`, legacy salary
estimates. Nothing about app startup requires a backfill, and the one
existing backfill (`POST /api/economics/compensation-observations/backfill`)
remains explicit, deterministic and idempotent.

---

## 17. API surface added

| Endpoint | What it does |
|---|---|
| `GET /api/pathways/{target_id}` | The composed Pathways answer. Pure read, cached. |
| `GET /api/pathways/market-contexts` | (market, currency) pairs with accepted evidence. |
| `GET /api/pathways/personal-earnings` | Current / latest-known earnings state. |
| `GET|PUT /api/pathways/planning-assumptions` | The billable-days assumption, editable from Pathways' "Contract comparison assumption" control. |
| `GET /api/role-instances/{id}/compensation` | Resolved figure + personal comparison + evidence. |
| `POST /api/role-instances/{id}/compensation/propose` | Explicit extraction. Writes no observation. |
| `POST /api/role-instances/{id}/compensation/accept` | The only writer of reviewed stated compensation. |
| `GET /api/role-instances/{id}/archetype` | Current reviewed assignment or unclassified state. |
| `GET /api/role-instances/archetype-catalogue` | The active archetypes a reviewer may choose from. |
| `POST /api/role-instances/{id}/archetype/propose` | Explicit suggestion. Writes nothing. |
| `PUT /api/role-instances/{id}/archetype` | Accept / choose another / leave unclassified. |
| `GET /api/archetypes/{id}/context` | Pure read. Never generates. |
| `POST /api/archetypes/{id}/context/generate` | Explicit first generation. No-op if active. |
| `POST /api/archetypes/{id}/context/regenerate` | Deliberate regeneration. |
| `GET /api/archetypes/{id}/compensation` | Archetype benchmark through the shared resolver. |

Every one of these is behind the existing central auth policy
(`app/main.py`), asserted by `tests/test_auth.py` for every registered route.
