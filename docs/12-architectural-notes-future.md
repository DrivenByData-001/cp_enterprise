# 12 — Architectural Notes: Deferred Concepts

**Status:** notes only — nothing here is scheduled, planned, or in scope
**Related:** `docs/11-capability-model-design.md` (the active design)

This document exists to stop good ideas evaporating out of a design conversation.
Nothing in it changes the phased plan in doc 11. Each note records the idea, why it
is deferred, what it would attach to, and — most importantly — **the invariants it
must not violate if it is ever built**, because both of these ideas are capable of
quietly destroying the honesty properties that the evidence-first model exists to
protect.

---

## 1. Potential

### 1.1 The idea, as stated

> **Potential** — the expected ease with which a person could acquire a capability
> they do not yet possess.

Explicitly **not** evidence-backed, and never to be conflated with demonstrated
capability. An inference layer, potentially based on existing capabilities, learning
history, cognitive adjacency, or later models.

Proposed progression:

> Evidence → Claims → Capabilities → Potential → Reachable Futures

### 1.2 One refinement to that progression

Potential is not a stage *between* capabilities and reachable futures — it is a
**parallel input** to them. Capabilities feed reachable futures directly (what you
can do now) and also feed potential (what you could plausibly acquire). So:

```
Evidence → Claims → Capabilities ─┬─────────────────────────┐
                                  │                         ▼
                                  └─→ Potential ──→ Reachable Futures
                                          ▲                 ▲
                       acquisition cost ──┘                 │
                            market demand ──────────────────┘
```

This matters because it keeps demonstrated capability on an unmediated path to the
output. If potential sat *in* the chain, every statement about what the user can do
today would be filtered through a judgment layer.

### 1.3 Potential and transition cost are duals — build them together or not at all

Doc 11 §11 already defers a "transition judgment layer" (Phase 5) that estimates the
effort to close a gap. Potential is the same quantity viewed from the other side:

- **Acquisition cost** is a property of a *capability* — how hard is this to acquire
  in general, from a given baseline. Capability-generic, curatable, shareable.
- **Potential** is a property of a *(person, capability)* pair — this person's
  modifier on that generic cost.

Decomposing it this way is worth recording because it keeps the expensive,
hand-curated part (generic acquisition cost, ~150 rows) separate from the
person-specific part (a small modifier), rather than requiring a bespoke judgment for
every person × capability cell. If potential is ever built, it should be built as a
modifier on the Phase 5 cost layer, not as an independent estimate.

### 1.4 The failure mode, and the one honest grounding available

Asked "could this person learn X?", a model says yes. Potential is structurally
prone to flattery in a way that demonstrated capability is not, and a flattering
potential score would poison the exact output the system exists to produce — an
honest account of what stands between the user and a higher-paid role.

The only grounding available that is not invented is **the user's own observed
learning history**, and the schema in doc 11 already accrues it for free:

- `episode` rows of `kind='study'` and `kind='qualification'` carry real dates.
- Claim history on a single concept carries depth progression — an `exposed`-depth
  claim in one episode, an `owned`-depth claim on the same concept two episodes later,
  is a measured acquisition interval.
- Union-of-spans recency logic (doc 11 §5.4) already computes the elapsed time.

So: **do not build potential now, but note that it becomes calibratable rather than
invented** once there are roughly ten episodes of reviewed claim history. Around 8–12
observed acquisition intervals is enough to say something defensible about this
person's rate relative to a generic baseline. Before that, any potential score is
prose dressed as a number.

### 1.5 Invariants if it is ever built

1. **A new basis value, not `derived`.** Doc 11 §5.1 reserves `derived` for
   quantities recomputable from evidence. Potential is not — it requires a judgment
   model. It needs its own basis (suggest `projected`) so it can never be mistaken for
   a derivation, and so `basis` remains a complete description of epistemic route.
2. **Its own table.** `d_capability_potential`, keyed `(person_id,
   capability_concept_id)`. Never a column on `d_capability_coverage` — one nullable
   column added there is all it takes for a query to start treating potential as
   coverage.
3. **It must not enter `d_gap_value` or `d_role_fit`.** The moment potential
   contributes to a fit score, "reachable" stops meaning *evidenced against
   requirements* and starts meaning *aspirational*, and the system's single
   differentiating property is gone. Potential is an overlay on those views, computed
   and displayed separately.
4. **Ordinal, not a float**, for the same reason extraction confidence was rejected in
   doc 11 §4.4: an uncalibrated number will be multiplied into a score and acquire
   false authority. Suggest three or four bands with written definitions.
5. **Visually distinct, always labelled as judgment**, alongside the Phase 5
   transition estimates and under the same presentation rules.

### 1.6 What "Reachable Futures" would need beyond potential

Recorded so the term is not mistaken for a small addition. A reachable-futures view
needs, at minimum: potential, generic acquisition cost, market demand *over time*
(§2.5 below), and some notion of sequencing — capabilities acquired in one order may
be cheaper than the same set in another, which is a path problem over the
`prerequisite_of` edges deferred to Phase 5. It is a genuine phase of its own, not a
view.

---

## 2. Capability value is market-dependent

### 2.1 The idea, as stated

> **Capability + Market = Economic Value**

Economic value is not an intrinsic property of a capability. The same capability may
command very different compensation depending on market, geography, industry,
regulation, supply/demand, or time. Future economic modelling should treat market as a
first-class contextual variable rather than assuming a capability has a single
inherent value.

Agreed, and correct.

### 2.2 Where the current design already honours this

- `compensation_observation.market` — every economic fact is captured with its market.
- `d_archetype_comp` is keyed by `(archetype, market, period, currency)` — aggregates
  are per-market and per-period by construction.

So no Phase 1–3 change is implied, as noted.

### 2.3 Where the current design quietly violates it — flagged for Phase 4

`d_gap_value` (doc 11 §4.6) is keyed `(person_id, capability_concept_id)`. **There is
no market dimension.** That table is precisely the place where "what is this
capability worth to me" gets computed, and as specified it assumes a single answer —
the exact assumption this note exists to reject.

Recommended amendment when Phase 4 is built (not now):

- Add `market` to the `d_gap_value` primary key, so gap rankings are per-market.
- Require the market to be **named and recorded** rather than left as an unstated
  default. Phase 4 will in practice be computed over something like "UK life
  insurance, 2026"; that string should exist in the data and be visible in the UI,
  because an unnamed default is the thing that hardens into an assumed universal.

Both are cheap at build time and expensive to retrofit once a UI reads the table, so
this is recorded here rather than left to be rediscovered.

### 2.4 Market is a composite, not a label

A free-text `market` column will drift — "UK life", "UK life insurance",
"United Kingdom — Life". Recording the eventual shape:

Market is at least **geography × domain × period**, and arguably also regulatory
regime and a supply/demand term. Two of those three are already concept types in doc
11 (`domain`, and geography could join them); period is already on
`d_archetype_comp`. So the future move is to promote market from a string to a
composite key over existing entities, rather than to invent a new taxonomy.

Until then, the free-text column should be understood as a **placeholder for a
dimension**, not as a descriptive label — and it should be populated from a short
controlled list rather than typed freely, which costs nothing in Phase 4 and prevents
the drift.

### 2.5 Two consequences worth recording now

**Market arbitrage becomes a distinct strategy.** If the same capability has different
value in different markets, then "move markets" is a fundamentally different move from
"close a gap" — and it is a *cheaper* one, because it requires no new capability at
all. A system whose objective is maximising career income should eventually surface
both. Doc 11 can only express gap-closing; it structurally cannot express "what you
already have is worth more somewhere else." That is a real omission relative to the
objective, and it becomes available essentially for free once market is first-class.

**Capability value has a trend, not just a level.** IFRS 17 expertise was scarce in
2021 and is closer to commodity by 2026. `d_archetype_comp` already carries
`period_start`/`period_end`, so the raw material for a trend accrues automatically —
but nothing computes one. For a multi-year salary trajectory, the *direction* of a
capability's value is arguably more decision-relevant than its current level:
acquiring something at the top of its scarcity curve is a poor investment even at a
high present value. Recorded as a future output over data the current design already
collects.

### 2.6 Invariant

Market context, wherever it appears, is a property of an **observation or an
aggregate** — never of a capability. `capability_detail.economic_salience` (doc 11
§4.2.2) is a curator's rough prior for prioritising curation effort; it must not
become a stored value figure, or the market-dependence this note asserts is lost in
the one place it matters most.

---

## 3. Relationship to the active plan

Neither note changes doc 11's six phases, exit criteria, or DDL. The single
forward-looking action either one implies is §2.3 — a two-line amendment to
`d_gap_value` at the point Phase 4 is built, recorded here so it is not missed.

Both notes share a common structural requirement, which is the reason they are in one
document: **judgment must remain separable from evidence at the schema level, not
merely by convention.** Doc 11 achieves that with the `basis` vocabulary and the
one-directional flow between layers (§5.2). Potential and market-dependent valuation
are both judgment layers, and both are attractive enough that they will be tempting to
merge into the evidence views for convenience. The invariants in §1.5 and §2.6 exist
to make that temptation fail loudly rather than silently.
