# 11 — Capability Model: Design Document

**Status:** proposed, for review
**Supersedes (conceptually):** the narrative-plus-embedding model shipped in v1
**Related:** `docs/10-career-nav-scoping.md` (original scoping — parts now obsolete, see §10)
**Deferred concepts:** `docs/12-architectural-notes-future.md` (potential; market-dependent capability value)

---

## 1. Objective

> **Improve the expected salary trajectory by identifying higher-value reachable
> roles and the capability gaps between the user's evidence and their requirements.**

Everything in this document is justified by that sentence. Where a modelling
choice does not serve it, it is deferred or cut.

Three deliberate exclusions for the first build:

- **Not modelling entrepreneurship, equity, or wealth.** The economic layer
  models employment compensation only. The schema does not block a later
  extension (§4.6), but nothing is built for it now.
- **Not modelling the labour market.** Only roles the user has captured. There
  is no attempt at market-wide coverage, and the system must be honest about
  sampling bias (§7.4).
- **Not estimating learning time.** "Six months vs five years" is a judgment
  layer, deferred to Phase 5 and rendered as clearly labelled judgment, never
  as evidence (§9.6).

### 1.1 The questions the build must answer

| # | Question | Answered by |
|---|---|---|
| Q1 | What capabilities can I credibly demonstrate? | `d_capability_coverage` |
| Q2 | What evidence supports each one? | `evidence_claim` → `document` spans |
| Q3 | Which capabilities does a target role require? | `requirement_claim` on a `role_instance` |
| Q4 | Which are evidenced / partial / asserted / not found? | `d_capability_coverage.status` |
| Q5 | Which capabilities are shared across different careers? | capability composition + archetype demand |
| Q6 | Which gaps are prerequisites or adjacencies to what I have? | `concept_edge` (Phase 5) |
| Q7 | Which roles would materially improve my earnings? | `d_archetype_comp` × `d_role_fit` |
| Q8 | Which gaps are most valuable to close? | `d_gap_value` |

Q1–Q4 are the core. Q7–Q8 are the objective. Q5 falls out of the model for free.
Q6 is deferred and is the weakest link (§9.6).

---

## 2. Conceptual model

### 2.1 The seven kinds of thing

| Kind | Definition | Example | Owns |
|---|---|---|---|
| **Document** | Immutable source text as ingested | A CV, a posting, a project write-up | Nothing — it is evidence |
| **Episode** | A bounded period in which capability was developed or demonstrated | "Senior Actuarial Analyst, Aviva, 2019-03 → 2022-08" | Dates, organisation, context |
| **Concept** | A canonical unit of career vocabulary | "Python", "Solvency II", "reserving" | A name, a type, aliases |
| **Capability** | Something a person can *do*, at economic scale | "Build and own a production actuarial model" | A composition, a demonstration standard |
| **Role instance** | An observed posting or a user-defined target | This specific Chief Actuary posting at L&G | Requirements, compensation |
| **Claim** | An evidence-backed assertion linking a subject to a concept | "Used Python, independently, owning the output, per this span" | Modifiers, evidence, basis |
| **Economic observation** | A compensation fact attached to a role | "£95–110k base, stated in the posting" | Amount, market, date |

The **claim** is the central object, as agreed. But claims are not the *product* —
they are the substrate. The product is the **capability model**: a per-capability
view of what the user can demonstrate, backed by claims, joined against what
roles demand.

### 2.2 Why capability sits above concept

A concept is cheap and portable: "Python" means roughly the same thing on any CV.
A capability is expensive and specific: *"built and maintained a production
actuarial model in Python, independently, in a life-insurance context, over three
years"* is not reducible to `Python — advanced`.

Capabilities matter because they are the level at which:

- **Employers actually buy.** Nobody pays for Python; they pay for someone who can
  own a production model.
- **Vocabulary stops mattering.** Two careers using entirely different words can
  demand the same capability. This is the mechanism behind Q5, and it is why the
  capability layer cannot be skipped — atom-level overlap between an actuarial CV
  and a quant posting is near zero even when the underlying capability is identical.
- **Absence becomes actionable.** "You lack Python" is not a plan. "You have never
  owned a model end-to-end without supervision" is.

### 2.3 The concept typology, and how it is preserved

Ten semantic types, one relational table (§4.2). The distinctions are enforced by
a **relation grammar** (§4.2.4), not by table proliferation.

| Type code | Definition | Examples |
|---|---|---|
| `knowledge` | A body of theory one can know | stochastic calculus, linear algebra, survival models |
| `method` | A named technique one can apply | chain ladder, bootstrap ODP, Monte Carlo valuation |
| `tool` | A named artefact one operates | Python, Prophet, ResQ, SQL, Excel |
| `function` | A business activity | reserving, pricing, capital management, ALM |
| `domain` | A sector or market context | life insurance, general insurance, pensions, banking |
| `product` | A thing sold or managed | annuities, term assurance, DB pension schemes |
| `regulation` | A named regulatory or reporting regime | Solvency II, IFRS 17, Consumer Duty |
| `credential` | An externally-issued, verifiable qualification | FIA, CERA, IFoA CP2, FRM |
| `capability` | Something a person can do, at economic scale | validate an internal model, lead a reserving process |
| `role_archetype` | A recurring role shape across many postings | Chief Actuary, Pricing Manager, Head of Capital |

**Note the omission: there is no `behavioural` type.** This is a deliberate cut,
argued in §9.1. Behavioural content is captured exclusively as capabilities and as
claim modifiers.

`capability` and `role_archetype` are **composite** types — they are defined by
their edges to atoms and to each other. The other eight are **atomic**. The
`concept_type.is_atom` flag carries this, and the relation grammar enforces it.

---

## 3. Design decisions (including the capability question)

### 3.1 Should capability be curated, derived, or both?

**Both — but each in a specific, non-overlapping role. The distinction is between
the capability's *identity* and the user's *possession* of it.**

> **A capability is a curated concept. Possession of a capability is always derived.**

**The capability node is curated.** It is a row in `concept` with
`type_code = 'capability'`, created and named by the curator (the user, with AI
proposals), with a `capability_detail` extension carrying its composition and its
demonstration standard. Curation is non-negotiable for three reasons:

1. **Join stability.** Person-side possession and role-side demand must meet on the
   same identifier. If capabilities were derived independently on each side, a CV
   would generate "own a production model" and a posting would generate "responsible
   for model production and maintenance", and they would never join. The whole
   comparison collapses. A curated node is the fixed point that makes Q3 and Q4
   answerable at all.
2. **Stability over time.** Derived capability sets change every time extraction
   runs. Coverage history becomes meaningless if the things being covered keep
   changing identity.
3. **It is the moat.** A well-curated set of ~120 actuarial capabilities with honest
   demonstration standards is the hardest-to-copy asset in the system. Deriving them
   automatically would produce a generic, low-value set.

**Possession is derived, never extracted.** `d_capability_coverage` is computed by
an engine from accepted claims. Extraction is never permitted to write a coverage
row. This is what prevents the single most likely failure mode: a model reading a CV
and cheerfully asserting "yes, this person can validate an internal model" because
the CV sounds senior.

**Both forms of input feed the derivation.** There are two routes to coverage, and
they stay distinguishable by `basis`:

- **Compositional** — the engine sees claims about the capability's core component
  concepts, at sufficient depth, within one episode, and infers coverage.
- **Direct** — extraction produces an `evidence_claim` whose `concept_id` *is* the
  capability, because the source text plainly states the whole thing ("led the
  quarterly reserving process"). This is a claim like any other: it needs a span, it
  carries a basis, it goes through review.

The engine combines both and records which claims supported the result in
`supporting_claim_ids`. A capability evidenced only compositionally (the person used
all the parts but nothing says they did the whole) is materially weaker than one
directly stated, and the UI must show the difference.

**So: curated identity, derived possession, two claim routes in, provenance
preserved throughout.** The user's instinct that it might be "both" is right; the
refinement is that the two forms are not alternatives, they are different layers.

### 3.2 Single concept table, enforced types

The concern — "I do not want everything flattened into an undifferentiated concept
list" — is correct and is addressed without ten tables. Differentiation lives in:

- `concept.type_code`, mandatory and constrained
- `concept_edge_rule` — a table of legal `(relation, from_type, to_type)` triples.
  An edge violating the grammar cannot be written. "Solvency II is a prerequisite
  for Python" is rejected structurally, not by convention.
- Claim validity rules — e.g. `relation = 'held'` is only legal against
  `type_code = 'credential'`.
- Extension tables for the two composite types.

The alternative (a table per type) gives no additional safety and makes every query
a ten-way union. One table plus a grammar is strictly better here.

### 3.3 Two claim tables, not one

Person-side and role-side claims share the concept vocabulary but not their shape:

- `evidence_claim`: subject is an **episode**. Asserts *possession* — with depth,
  autonomy, stakeholder scope, outcome.
- `requirement_claim`: subject is a **role instance**. Asserts *demand* — required
  vs preferred, importance.

Merging them would produce a table where half the columns are always null. The
current `job_role_skills.requirement_type` (`backend/app/db.py:69`) is the seed of
this problem already.

### 3.4 Append-only claims

Claims are never destructively updated. Corrections create a new row and set
`superseded_by` on the old one. Only `review_status`, `reviewed_at` and
`superseded_by` may be mutated in place. This is what makes the corpus re-derivable
and makes "why does the system think I can do this?" always answerable, including
retrospectively.

This is a direct break from the current `upsert_job_role`, which deletes and
re-inserts all skills for a role on every edit (`backend/app/db.py:167`).

### 3.5 SQLite through Phase 3

Stay on SQLite. Every query in this design is a join, an aggregate, or a two-hop
recursive CTE — all supported. Move to Postgres when either (a) vector search over
concepts outgrows a brute-force scan (>~50k concepts, which will not happen), or
(b) more than one person uses the system. Keep the DDL portable: no SQLite-specific
types, integer booleans, ISO-8601 text dates.

---

## 4. Entities and relationships

### 4.1 Layer 0 — Source (immutable)

```sql
CREATE TABLE document (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,      -- cv | linkedin_profile | job_posting |
                                        -- project_writeup | narrative | article | other
    title           TEXT,
    body            TEXT NOT NULL,      -- verbatim as ingested; NEVER edited
    body_sha256     TEXT NOT NULL UNIQUE,
    source          TEXT,               -- linkedin | company_site | user_paste | file
    url             TEXT,
    document_date   TEXT,               -- ISO date the content refers to / was published
    ingested_at     TEXT NOT NULL,
    superseded_by   INTEGER REFERENCES document(id),
    notes           TEXT
);
CREATE INDEX idx_document_kind ON document(kind);
```

A document is never edited. A better version of the same source is a **new row**
with `superseded_by` set on the old one. `body_sha256` makes re-ingestion idempotent
and lets evidence spans be validated against exactly the text they were drawn from.

```sql
CREATE TABLE vocabulary_version (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    concept_count INTEGER NOT NULL,
    note          TEXT
);

CREATE TABLE extraction_run (
    id                    INTEGER PRIMARY KEY,
    document_id           INTEGER NOT NULL REFERENCES document(id),
    task                  TEXT NOT NULL,   -- episode_extract | concept_link |
                                           -- capability_attribute | requirement_extract
    model                 TEXT NOT NULL,
    prompt_version        TEXT NOT NULL,
    vocabulary_version_id INTEGER NOT NULL REFERENCES vocabulary_version(id),
    started_at            TEXT NOT NULL,
    finished_at           TEXT,
    status                TEXT NOT NULL,   -- ok | partial | failed
    notes                 TEXT
);
```

Every claim points at the run that produced it. When the vocabulary or the prompt
changes materially, claims made under the old regime are identifiable and can be
re-extracted selectively rather than wholesale.

### 4.2 Layer 1 — Vocabulary (curated, slow-changing)

#### 4.2.1 Concepts

```sql
CREATE TABLE concept_type (
    code       TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    definition TEXT NOT NULL,
    is_atom    INTEGER NOT NULL,   -- 1 = atomic, 0 = composite
    sort_order INTEGER NOT NULL
);

CREATE TABLE concept (
    id             INTEGER PRIMARY KEY,
    type_code      TEXT NOT NULL REFERENCES concept_type(code),
    canonical_name TEXT NOT NULL,
    definition     TEXT,
    status         TEXT NOT NULL DEFAULT 'proposed',  -- proposed | active | deprecated | merged
    merged_into    INTEGER REFERENCES concept(id),
    origin         TEXT NOT NULL,                     -- curator | extraction_proposal
    created_at     TEXT NOT NULL,
    reviewed_at    TEXT,
    UNIQUE (type_code, canonical_name)
);
CREATE INDEX idx_concept_type ON concept(type_code, status);
```

Only `status = 'active'` concepts are offered to extraction. `merged_into` makes
de-duplication non-destructive: claims against a merged concept stay valid and are
resolved through the pointer at query time.

#### 4.2.2 Composite extensions

```sql
CREATE TABLE capability_detail (
    concept_id             INTEGER PRIMARY KEY REFERENCES concept(id) ON DELETE CASCADE,
    demonstration_standard TEXT NOT NULL,   -- prose: what counts as having done this
    min_depth              TEXT NOT NULL DEFAULT 'owned',  -- exposed|applied|owned|set_standard
    min_autonomy           TEXT,            -- assisted|independent|directed_others|accountable
    requires_all_core      INTEGER NOT NULL DEFAULT 1,
    economic_salience      TEXT,            -- low | medium | high  (curator judgment)
    notes                  TEXT
);

CREATE TABLE role_archetype_detail (
    concept_id                  INTEGER PRIMARY KEY REFERENCES concept(id) ON DELETE CASCADE,
    seniority_band              TEXT,       -- analyst|senior|manager|head|director|chief
    primary_function_concept_id INTEGER REFERENCES concept(id),
    typical_market              TEXT,
    notes                       TEXT
);
```

`demonstration_standard` is the most important prose field in the system. It is the
written-down answer to "what would I have to have done for this to be true?", and it
is what stops capability coverage drifting into wishful thinking.

#### 4.2.3 Aliases and cross-references

```sql
CREATE TABLE concept_alias (
    id         INTEGER PRIMARY KEY,
    concept_id INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    origin     TEXT NOT NULL,     -- curator | extraction | merge
    created_at TEXT NOT NULL,
    UNIQUE (alias, concept_id)
);
CREATE INDEX idx_concept_alias_alias ON concept_alias(alias);

CREATE TABLE concept_xref (
    id         INTEGER PRIMARY KEY,
    concept_id INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    scheme     TEXT NOT NULL,     -- esco | onet | ifoa_syllabus | soa | sfia
    code       TEXT NOT NULL,
    label      TEXT,
    UNIQUE (concept_id, scheme, code)
);
```

`concept_xref` is the entirety of the external-taxonomy commitment. Nothing in the
reasoning path reads it. It exists so that if interoperability is ever needed, the
mapping can be added incrementally without the system ever having depended on a
taxonomy that does not cover actuarial practice.

#### 4.2.4 Edges and the relation grammar

```sql
CREATE TABLE concept_edge (
    id              INTEGER PRIMARY KEY,
    from_concept_id INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    to_concept_id   INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL,
    necessity       TEXT,          -- core | supporting | contextual (component_of only)
    weight          REAL,
    note            TEXT,
    origin          TEXT NOT NULL, -- curator | extraction_proposal | derived
    status          TEXT NOT NULL DEFAULT 'proposed',
    created_at      TEXT NOT NULL,
    UNIQUE (from_concept_id, to_concept_id, relation)
);

CREATE TABLE concept_edge_rule (
    relation  TEXT NOT NULL,
    from_type TEXT NOT NULL REFERENCES concept_type(code),
    to_type   TEXT NOT NULL REFERENCES concept_type(code),
    PRIMARY KEY (relation, from_type, to_type)
);
```

Seed grammar (Phase 1–3 subset):

| Relation | From type | To type | Meaning |
|---|---|---|---|
| `component_of` | knowledge, method, tool, function, domain, product, regulation, credential | capability | This atom is part of what the capability is made of. `necessity` says how essential. |
| `demands` | role_archetype | capability | This role shape typically requires this capability. |
| `broader_than` | *(same type)* | *(same type)* | Taxonomic containment within a type. |
| `governs` | regulation | function | This regime constrains this business activity. |
| `applies_in` | method | domain, product | This technique is used in this context. |
| `senior_to` | role_archetype | role_archetype | Progression ordering. |

Deferred to Phase 5: `prerequisite_of` and `adjacent_to` (both capability→capability),
and `substitutable_for`. These are the adjacency machinery for Q6, and they are
curator judgment, not extraction output.

### 4.3 Layer 2 — Subjects

```sql
CREATE TABLE person (
    id           INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE episode (
    id                INTEGER PRIMARY KEY,
    person_id         INTEGER NOT NULL REFERENCES person(id),
    kind              TEXT NOT NULL,   -- employment | project | study | qualification | other
    title             TEXT NOT NULL,
    organisation      TEXT,
    start_date        TEXT,            -- ISO; may be YYYY or YYYY-MM
    end_date          TEXT,            -- NULL = ongoing
    date_precision    TEXT NOT NULL DEFAULT 'month',  -- day | month | year
    parent_episode_id INTEGER REFERENCES episode(id), -- a project inside a job
    domain_hint       TEXT,            -- free text, used to seed context claims
    context_note      TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX idx_episode_person ON episode(person_id, start_date);

CREATE TABLE episode_document (
    episode_id  INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES document(id),
    PRIMARY KEY (episode_id, document_id)
);
```

**Nested episodes matter.** A significant project inside a job is its own episode
with its own dates and its own claims, parented to the employment episode. This is
how "built the annuity model in 2021" gets distinct recency from "worked at Aviva
2019–2022".

```sql
CREATE TABLE role_instance (
    id                   INTEGER PRIMARY KEY,
    kind                 TEXT NOT NULL,   -- posting | target_real | target_imagined
    document_id          INTEGER REFERENCES document(id),
    archetype_concept_id INTEGER REFERENCES concept(id),
    title                TEXT NOT NULL,
    organisation         TEXT,
    location             TEXT,
    country              TEXT,
    remote_type          TEXT,
    employment_type      TEXT,
    seniority_level      TEXT,
    posting_date         TEXT,
    captured_at          TEXT,
    url                  TEXT,
    summary              TEXT,
    created_at           TEXT NOT NULL
);
CREATE INDEX idx_role_instance_kind ON role_instance(kind, archetype_concept_id);
```

Postings and targets share this table (as they do today via `node_type`) because
they carry identical requirement structure and differ only in provenance. Assigning
`archetype_concept_id` is what makes compensation aggregation possible (§4.5).

### 4.4 Layer 3 — Claims (append-only)

```sql
CREATE TABLE evidence_claim (
    id                    INTEGER PRIMARY KEY,
    episode_id            INTEGER NOT NULL REFERENCES episode(id),
    concept_id            INTEGER NOT NULL REFERENCES concept(id),
    relation              TEXT NOT NULL,  -- performed | used | applied | studied | held | led
    -- modifiers: what the doing actually looked like
    depth                 TEXT,           -- exposed | applied | owned | set_standard
    autonomy              TEXT,           -- assisted | independent | directed_others | accountable
    stakeholder_scope     TEXT,           -- team | department | executive | board | external_regulator
    team_size             INTEGER,
    outcome_note          TEXT,           -- measurable result, if stated
    -- epistemics
    basis                 TEXT NOT NULL,  -- stated | implied | inferred | user_asserted
    document_id           INTEGER REFERENCES document(id),
    evidence_span         TEXT,           -- verbatim quote from document.body
    evidence_offset_start INTEGER,
    evidence_offset_end   INTEGER,
    extraction_run_id     INTEGER REFERENCES extraction_run(id),
    -- lifecycle
    review_status         TEXT NOT NULL DEFAULT 'unreviewed',  -- unreviewed|accepted|rejected|corrected
    reviewed_at           TEXT,
    superseded_by         INTEGER REFERENCES evidence_claim(id),
    created_at            TEXT NOT NULL,
    CHECK (basis <> 'user_asserted' OR extraction_run_id IS NULL),
    CHECK (basis NOT IN ('stated','implied') OR document_id IS NOT NULL)
);
CREATE INDEX idx_evidence_claim_episode ON evidence_claim(episode_id);
CREATE INDEX idx_evidence_claim_concept ON evidence_claim(concept_id, review_status);
```

Note what is **not** here: no `years_of_experience`, no `recency`, no `confidence`.

- Years and recency derive from `episode.start_date`/`end_date` (§5.4).
- Confidence is replaced by `basis` — an ordinal describing the *epistemic route* to
  the claim, which is objective and checkable, rather than an uncalibrated float that
  would inevitably be multiplied into a score.

```sql
CREATE TABLE requirement_claim (
    id                    INTEGER PRIMARY KEY,
    role_instance_id      INTEGER NOT NULL REFERENCES role_instance(id) ON DELETE CASCADE,
    concept_id            INTEGER NOT NULL REFERENCES concept(id),
    requirement_type      TEXT NOT NULL,  -- required | preferred | contextual
    importance            INTEGER,        -- 1-5, only when the posting signals it
    basis                 TEXT NOT NULL,
    document_id           INTEGER REFERENCES document(id),
    evidence_span         TEXT,
    evidence_offset_start INTEGER,
    evidence_offset_end   INTEGER,
    extraction_run_id     INTEGER REFERENCES extraction_run(id),
    review_status         TEXT NOT NULL DEFAULT 'unreviewed',
    reviewed_at           TEXT,
    superseded_by         INTEGER REFERENCES requirement_claim(id),
    created_at            TEXT NOT NULL
);
CREATE INDEX idx_requirement_claim_role ON requirement_claim(role_instance_id);
CREATE INDEX idx_requirement_claim_concept ON requirement_claim(concept_id);
```

### 4.5 Layer 4 — Economics

```sql
CREATE TABLE compensation_observation (
    id                   INTEGER PRIMARY KEY,
    role_instance_id     INTEGER REFERENCES role_instance(id) ON DELETE CASCADE,
    archetype_concept_id INTEGER REFERENCES concept(id),   -- for survey rows with no posting
    component            TEXT NOT NULL,   -- base | bonus_pct | total_package
    amount_min           REAL,
    amount_max           REAL,
    currency             TEXT NOT NULL,
    basis                TEXT NOT NULL,   -- stated | estimated | survey | user_asserted
    market               TEXT,            -- e.g. 'UK life insurance'
    observed_at          TEXT NOT NULL,
    document_id          INTEGER REFERENCES document(id),
    source_note          TEXT,
    CHECK (role_instance_id IS NOT NULL OR archetype_concept_id IS NOT NULL)
);
```

Separating `stated` from `estimated` here is essential. The current schema stores
`salary_min` and `salary_estimate_min` as sibling columns
(`backend/app/db.py:27-30`), which works, but the same distinction must not be lost
when a model-guessed range and a posting-stated range are aggregated into a median.
Any aggregate must report what fraction of its inputs were `stated` (§4.6).

The user's own compensation history attaches to episodes via a
`role_instance`-free row is not modelled in Phase 4 — it is a single extra table
(`episode_compensation`) added when needed. Deferred deliberately: the objective is
about *reachable* roles, and the current salary is one number the user already knows.

### 4.6 Layer 5 — Derived (recomputable, disposable)

Everything here can be dropped and rebuilt from layers 0–4. Nothing else may write
to these tables, and they may not be the sole home of any fact. Every row carries
`engine_version` and `computed_at`.

```sql
CREATE TABLE d_capability_coverage (
    person_id             INTEGER NOT NULL REFERENCES person(id),
    capability_concept_id INTEGER NOT NULL REFERENCES concept(id),
    status                TEXT NOT NULL,   -- evidenced | partial | user_asserted | not_found
    coverage_score        REAL,            -- 0-1, for ranking only, never shown as a percentage
    core_components_total INTEGER,
    core_components_met   INTEGER,
    strongest_depth       TEXT,
    strongest_autonomy    TEXT,
    directly_claimed      INTEGER NOT NULL DEFAULT 0,  -- 1 if a capability-level claim exists
    last_demonstrated     TEXT,            -- derived from episode end dates
    years_active          REAL,            -- derived, union of episode spans
    supporting_claim_ids  TEXT,            -- JSON array of evidence_claim.id
    engine_version        TEXT NOT NULL,
    computed_at           TEXT NOT NULL,
    PRIMARY KEY (person_id, capability_concept_id)
);

CREATE TABLE d_role_fit (
    person_id            INTEGER NOT NULL REFERENCES person(id),
    role_instance_id     INTEGER NOT NULL REFERENCES role_instance(id) ON DELETE CASCADE,
    capabilities_required INTEGER NOT NULL,
    n_evidenced          INTEGER NOT NULL,
    n_partial            INTEGER NOT NULL,
    n_asserted           INTEGER NOT NULL,
    n_not_found          INTEGER NOT NULL,
    blocking_gaps        TEXT,            -- JSON array of capability concept ids, required+not_found
    fit_score            REAL,
    embedding_similarity REAL,            -- one signal, stored beside the structural verdict
    engine_version       TEXT NOT NULL,
    computed_at          TEXT NOT NULL,
    PRIMARY KEY (person_id, role_instance_id)
);

CREATE TABLE d_archetype_comp (
    archetype_concept_id INTEGER NOT NULL REFERENCES concept(id),
    market               TEXT NOT NULL,
    period_start         TEXT NOT NULL,
    period_end           TEXT NOT NULL,
    currency             TEXT NOT NULL,
    n_observations       INTEGER NOT NULL,
    n_stated             INTEGER NOT NULL,   -- how many were actually stated, not estimated
    p25 REAL, p50 REAL, p75 REAL,
    engine_version       TEXT NOT NULL,
    computed_at          TEXT NOT NULL,
    PRIMARY KEY (archetype_concept_id, market, period_start, period_end, currency)
);

CREATE TABLE d_gap_value (
    person_id             INTEGER NOT NULL REFERENCES person(id),
    capability_concept_id INTEGER NOT NULL REFERENCES concept(id),
    archetypes_unlocked   INTEGER NOT NULL,   -- would move from blocked to reachable
    archetypes_improved   INTEGER NOT NULL,   -- already reachable, fit improves
    median_comp_unlocked  REAL,
    comp_delta_vs_best_current REAL,
    n_observations        INTEGER NOT NULL,
    evidence_quality      TEXT NOT NULL,      -- thin | moderate | good
    rank                  INTEGER,
    engine_version        TEXT NOT NULL,
    computed_at           TEXT NOT NULL,
    PRIMARY KEY (person_id, capability_concept_id)
);

CREATE TABLE d_embedding (
    owner_kind  TEXT NOT NULL,   -- concept | episode | role_instance | document
    owner_id    INTEGER NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (owner_kind, owner_id, model)
);
```

`d_embedding` is the concrete expression of the principle that embeddings are a
signal, not a representation. Today they are columns on the primary entities
(`job_roles.embedding`, `profile_snapshots.embedding`); moving them into the derived
layer means a model change is a rebuild, not a migration, and it becomes structurally
impossible for an embedding to be the only record of anything.

**`d_gap_value` is the answer to the objective (Q8)**, and it is deliberately
constructed to avoid inventing learning-time estimates. It asks only: *if this
capability were evidenced, which archetypes would move from blocked to reachable, and
what do those archetypes pay?* That is grounded entirely in observed requirement
claims and observed compensation. It says nothing about how hard the gap is to close
— that is Phase 5 judgment, presented separately.

Because posting-stated salary is sparse, `d_gap_value` must be **presented as an
ordinal ranking with its sample size visible**, never as "+£12,400". `evidence_quality`
is derived from `n_observations` and the stated fraction, and the UI must refuse to
rank at all below a threshold (suggest: n < 5 stated observations → show the unlocked
archetypes but no monetary figure).

---

## 5. Facts, assertions, inferences, and derived outputs

### 5.1 The `basis` vocabulary

Every claim and every economic observation carries exactly one:

| Basis | Meaning | Requires span? | Who writes it |
|---|---|---|---|
| `stated` | Explicitly present in the source text | Yes, verbatim | Extraction |
| `implied` | Strongly entailed by the source text | Yes, the passage entailing it | Extraction |
| `inferred` | Model judgment reaching beyond the text | No | Extraction |
| `user_asserted` | The user says so; no document supports it | No | User only |
| `derived` | Computed by an engine from other rows | No | Engine only, in `d_*` tables |

### 5.2 The invariants

1. `stated` and `implied` claims **must** carry a `document_id` and a span that
   literally occurs in that document's `body`. Enforced by check constraint and
   validated automatically (§8.3).
2. `user_asserted` claims **must not** have an `extraction_run_id`. The user is not a
   model run.
3. `derived` never appears in `evidence_claim` or `requirement_claim`. Derivation
   output lives only in `d_*` tables.
4. Extraction **may not** write to `d_*`. The engine **may not** write to claim
   tables. One direction of flow, always.
5. No `d_*` row is the sole home of any fact. Truncating every `d_*` table and
   rebuilding must be a no-op semantically.

### 5.3 The UI consequence

Every capability status and every number in the interface must be able to state its
own basis on demand. Concretely:

- **Evidenced** — at least one accepted `stated`/`implied` claim meets the
  demonstration standard. Click through to the span, in the document, highlighted.
- **Partial** — some core components met, or met at insufficient depth/autonomy.
  Show precisely which components are missing.
- **User-asserted** — the user claims it; no document backs it. Visually distinct,
  and excluded from any "evidenced" count.
- **Not found** — *no evidence located*. Never rendered as "you lack this". The UI
  string is "no evidence found" with a one-click "I have done this" action that
  creates a `user_asserted` claim.

That last point is a product-level requirement arising from a data-model fact:
extraction recall will be roughly 70–85%, so absence from the record is not absence
from the person. Getting this wrong destroys trust in the whole system on first use.

### 5.4 Derived temporal quantities

Never stored on a claim:

- **Years of experience with concept C** = total span of the union of
  `[episode.start_date, COALESCE(episode.end_date, today)]` over all episodes holding
  an accepted claim for C. Union, not sum — overlapping episodes must not double-count.
- **Recency of C** = most recent `COALESCE(end_date, today)` among those episodes.
- **Recency decay**, where used in ranking, is an engine parameter in
  `engine_version`, not a stored column.

---

## 6. Worked examples

### 6.1 Ingestion of one CV bullet

**Document 12** (`kind = 'cv'`), containing:

> "Rebuilt the annuity reserving model in Python, replacing a legacy Excel process.
> Ran the quarterly valuation independently and presented results to the Chief Actuary."

**Episode 4** — `kind='employment'`, "Senior Actuarial Analyst", Aviva,
2019-03 → 2022-08.
**Episode 9** — `kind='project'`, `parent_episode_id=4`, "Annuity reserving model
rebuild", 2020-06 → 2021-02.

`evidence_claim` rows produced against episode 9:

| concept (type) | relation | depth | autonomy | stakeholder | basis | span |
|---|---|---|---|---|---|---|
| Python (`tool`) | used | owned | independent | — | stated | "…model in Python…" |
| reserving (`function`) | performed | owned | independent | executive | stated | "Ran the quarterly valuation independently…" |
| annuities (`product`) | applied | applied | — | — | stated | "Rebuilt the annuity reserving model…" |
| life insurance (`domain`) | applied | applied | — | — | implied | *(org + product context)* |
| Build and own a production actuarial model (`capability`) | performed | owned | independent | executive | implied | full bullet |

Five claims, one span each, one document, one extraction run. The last is a
**direct capability claim** — the text plainly states the whole thing, so extraction
is permitted to propose it. It still goes through review like any other.

### 6.2 Deriving coverage

Capability **"Build and own a production actuarial model"**, curated:

- `demonstration_standard`: *"Has been the named owner of a model used in a
  reporting or pricing process that others relied on, through at least one full
  cycle, including its maintenance — not a one-off analysis."*
- `min_depth = owned`, `min_autonomy = independent`
- Components: a modelling `tool` (**core**), a business `function` (**core**),
  a `domain` (**contextual**), a `product` (**contextual**), model documentation
  practice (`method`, **supporting**)

Engine result for the user:

```
status               = evidenced
core_components_met  = 2 / 2
strongest_depth      = owned
strongest_autonomy   = independent
directly_claimed     = 1
last_demonstrated    = 2021-02        -- from episode 9 end_date
years_active         = 0.7            -- derived, not asserted
supporting_claim_ids = [1001, 1002, 1005]
```

`directly_claimed = 1` matters: had this been derived purely compositionally — the
user used Python, and separately did reserving — the status would be `partial`, not
`evidenced`, because nothing would evidence that they owned the *whole thing*. This
distinction is exactly what a skills inventory cannot express and is the single
clearest demonstration of why the capability layer earns its cost.

### 6.3 A target role and an honest gap

**Role instance 77** — `kind='target_real'`, "Chief Actuary",
`archetype_concept_id` → concept "Chief Actuary".

Requirement claims (abridged), extracted from the posting document:

| capability | requirement_type | basis |
|---|---|---|
| Sign off technical provisions under Solvency II | required | stated |
| Lead a reserving process | required | stated |
| Influence executive stakeholders | required | implied |
| Build and own a production actuarial model | preferred | implied |
| Manage an actuarial team | required | stated |

`d_role_fit` for the user:

| capability | status | why |
|---|---|---|
| Sign off technical provisions under Solvency II | **not_found** | Holds FIA (`credential`, evidenced) and Solvency II claims at depth `applied`; but `min_autonomy = accountable` is unmet — no claim shows personal accountability for sign-off. **Blocking gap.** |
| Lead a reserving process | **partial** | `reserving` at depth `owned`, autonomy `independent`; capability requires `directed_others`. Missing component: leading others through the cycle. |
| Influence executive stakeholders | **evidenced** | Claim 1002 carries `stakeholder_scope = executive`. |
| Build and own a production actuarial model | **evidenced** | §6.2. |
| Manage an actuarial team | **not_found** | No claim with `team_size` or autonomy `directed_others`. |

This is the output the current architecture cannot produce at any level of tuning.
Cosine similarity between a narrative and a posting yields one number around 0.7;
it cannot say *"you have never been accountable for a sign-off, and that is the
thing standing between you and this role."*

### 6.4 Cross-career transfer (Q5)

"Influence executive stakeholders" is `demands`-linked from **Chief Actuary**,
**Head of Pricing**, **Quantitative Risk Manager**, and **Insurance Consulting
Director** — four archetypes whose posting text shares almost no atomic vocabulary.
The user's evidence for it comes from a single actuarial reserving claim.

No extra machinery is needed for this: it is a two-hop join from an evidence claim,
through a curated capability, to archetype demand. It works precisely *because* the
capability node is curated (§3.1) — a derived capability set would have produced
four differently-worded nodes that never joined.

### 6.5 Gap value (Q8)

Given the above, `d_gap_value` for "Manage an actuarial team":

```
archetypes_unlocked      = 3      -- Chief Actuary, Head of Reserving, Head of Capital
archetypes_improved      = 2
median_comp_unlocked     = <p50 across those archetypes' stated observations>
n_observations           = 7
evidence_quality         = moderate
rank                     = 1
```

Reads as: *"Of everything you cannot currently evidence, team management blocks the
most — and the highest-paid — role shapes you have captured. Based on 7 observations
with stated salary."* No claim is made about how long it would take to close. That
is Phase 5, and it will be labelled as judgment.

---

## 7. Extraction and canonicalisation workflow

### 7.1 Four passes, each with a different contract

| Pass | Input | Output | Vocabulary |
|---|---|---|---|
| **A — Episode extraction** | CV, LinkedIn, narrative | Proposed `episode` rows | Open (free text) |
| **B — Concept linking** | Any document + confirmed episodes/roles | `evidence_claim` / `requirement_claim` + `concept_proposal` | **Closed** |
| **C — Capability attribution** | Accepted claims + capability catalogue | Capability-level claims where text states the whole | **Closed** |
| **D — Compensation extraction** | Job posting documents | `compensation_observation` | N/A |

### 7.2 Closed vocabulary is the pivot

Pass B is the heart of the system, and it must not be an open-ended "extract the
skills" prompt. The current prompts (`prompts/extract_job_posting.md`,
`prompts/decompose_target_role.md`) are open-ended, which is why the same source
would yield "stochastic reserving", "stochastic reserving methods" and "reserving
(stochastic)" across three runs.

Pass B is instead: **"Map this text onto these known concepts. Quote a span for each.
Anything you cannot map, list separately as a proposal — do not invent a concept."**

Two output arrays, one of which requires a human. This single change is what makes
extraction reproducible enough for the rest of the model to mean anything.

### 7.3 The canonicalisation cascade

For each candidate surface form in the text:

1. **Exact match** against `concept.canonical_name` and `concept_alias.alias`
   (case-folded). Hit → link, done. This is the majority path once the vocabulary
   matures and it costs nothing.
2. **Embedding kNN** over active concept name+definition vectors in `d_embedding`.
   Retrieve top 10 candidates within the plausible type set.
3. **Model adjudication** — present the surface form, its sentence, and the 10
   candidates. The model picks one or declines. It may only pick from the list.
4. **No confident match** → write a `concept_proposal` row with the nearest
   candidate and its similarity, for human review.

**Extraction never creates a concept.** Every new concept enters through §8.1.
This is the guardrail that keeps the vocabulary from silently ballooning into the
undifferentiated list the model is designed to avoid.

This is also the answer to "where do embeddings belong": step 2, and nowhere in the
reasoning path. They retrieve candidates; the model and the curator decide.

### 7.4 Sampling bias, recorded honestly

The corpus is whatever the user happened to capture. It over-represents roles they
were already interested in and under-represents everything else — which biases
`d_archetype_comp` and therefore `d_gap_value` toward the career they are already on.
There is no fix within the data. The mitigations are: show `n_observations`
everywhere, show `n_stated` vs estimated, and prompt for deliberate capture of
archetypes with thin coverage. This limitation belongs in the UI, not just in this
document.

---

## 8. Review workflow

Three queues. All three must be fast, because curation fatigue is the most likely
cause of death for this system (§9.5).

### 8.1 Concept proposals

```sql
CREATE TABLE concept_proposal (
    id                  INTEGER PRIMARY KEY,
    surface_form        TEXT NOT NULL,
    suggested_type      TEXT REFERENCES concept_type(code),
    suggested_definition TEXT,
    nearest_concept_id  INTEGER REFERENCES concept(id),
    nearest_similarity  REAL,
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    document_id         INTEGER REFERENCES document(id),
    evidence_span       TEXT,
    extraction_run_id   INTEGER REFERENCES extraction_run(id),
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending|accepted_new|accepted_alias|rejected
    resolved_concept_id INTEGER REFERENCES concept(id),
    resolved_at         TEXT
);
```

Four actions, all one click: **new concept** (pick type, write a definition),
**alias of** the nearest match, **reject** (noise), **defer**. Proposals are grouped
by normalised surface form with `occurrence_count`, so a term appearing in thirty
postings is one decision.

### 8.2 Claim review

Sorted by **leverage**, not chronology: unreviewed claims that feed capabilities
which appear in `d_gap_value` top ranks come first. Reviewing 20 high-leverage claims
should shift the analysis more than reviewing 200 arbitrary ones.

Bulk-accept is permitted for `basis = 'stated'` claims whose span validates, against
an already-reviewed concept. Everything `inferred` requires an individual decision —
that is the whole point of separating the basis.

Rejection sets `review_status = 'rejected'`. Nothing is deleted, so extraction
quality can be measured from the review record itself (§9.3 note).

### 8.3 Span validation (automatic, no human)

Before any claim reaches a queue, assert that `evidence_span` occurs literally in
`document.body` at the stated offsets. Failures are quarantined, never queued. This
catches fabricated evidence mechanically and needs no gold set — it is the cheapest
quality control in the system and should be built in Phase 2, day one.

### 8.4 Episode confirmation

Pass A proposals are shown as a timeline diff against existing episodes. Dates and
organisation are the fields that matter most (everything temporal derives from them),
so they are always presented for explicit confirmation rather than silently accepted.

---

## 9. Evaluation

### 9.1 What is being measured

Structured extraction fails invisibly: a mis-linked concept looks exactly like a
correct one, and the error propagates into capability coverage, role fit, and gap
ranking without any visible symptom. Unlike the current embedding approach — where
bad neighbours are obvious on the Space view — nothing here self-announces. Hence
explicit measurement, from Phase 1.

### 9.2 Gold set design

**24 documents**, stratified:

| Stratum | Count | Why |
|---|---|---|
| Job postings, actuarial core | 8 | The bulk of the corpus |
| Job postings, adjacent (quant/risk/data) | 4 | Where transfer questions live |
| CV / LinkedIn profile | 6 | Person-side extraction, different register |
| Project write-ups | 4 | Where capability-level evidence is richest |
| Deliberately hard (vague, jargon-heavy, non-UK) | 2 | Failure-mode probes |

Split **12 dev / 12 test**. The test half is frozen and looked at only when
promoting a prompt version — otherwise it is fitted to and stops measuring anything.

Labelling is done by the user (as domain expert), one pass, then a second pass a week
later on the same documents to measure self-agreement. If self-agreement is below
~0.85 the *task definition* is too vague and the schema needs sharpening before any
model is blamed.

```sql
CREATE TABLE gold_document (
    document_id INTEGER PRIMARY KEY REFERENCES document(id),
    split       TEXT NOT NULL,   -- dev | test
    stratum     TEXT NOT NULL,
    labelled_at TEXT NOT NULL,
    notes       TEXT
);

CREATE TABLE gold_claim (
    id            INTEGER PRIMARY KEY,
    document_id   INTEGER NOT NULL REFERENCES gold_document(document_id),
    subject_hint  TEXT,            -- which episode/role this belongs to
    concept_id    INTEGER NOT NULL REFERENCES concept(id),
    relation      TEXT NOT NULL,
    depth         TEXT,
    autonomy      TEXT,
    requirement_type TEXT,
    evidence_span TEXT NOT NULL,
    is_core       INTEGER NOT NULL DEFAULT 1  -- 0 = credit if found, no penalty if missed
);

CREATE TABLE eval_run (
    id                    INTEGER PRIMARY KEY,
    split                 TEXT NOT NULL,
    task                  TEXT NOT NULL,
    model                 TEXT NOT NULL,
    prompt_version        TEXT NOT NULL,
    vocabulary_version_id INTEGER NOT NULL REFERENCES vocabulary_version(id),
    precision_micro       REAL,
    recall_micro          REAL,
    f1_micro              REAL,
    span_validity         REAL,
    proposals_per_doc     REAL,
    modifier_accuracy     REAL,
    run_at                TEXT NOT NULL,
    notes                 TEXT
);
```

`gold_claim.is_core` distinguishes claims that *must* be found from ones that are
credited if found but not penalised if missed. Without it, recall is dominated by
arguable marginal claims and stops being a useful signal.

### 9.3 Metrics and gates

| Metric | Definition | Gate |
|---|---|---|
| **Span validity** | Fraction of claims whose span occurs verbatim in the source | **≥ 0.99** — hard gate, blocks Phase 2 |
| **Concept-linking F1** | Micro-averaged over core gold claims, per type | ≥ 0.75 dev before Phase 3 |
| **Modifier accuracy** | Exact match on `depth`/`autonomy` where gold specifies | ≥ 0.60 — these are hard, and inflated modifiers are what fake seniority |
| **Proposals per document** | New concept proposals per doc, rolling | Trending to < 2 by end of Phase 2 |
| **Capability agreement** | Engine coverage status vs hand-labelled, over ~20 capabilities | ≥ 0.80 before Phase 4 |

**Proposals-per-document is the health metric for the whole system.** If it does not
fall as the vocabulary matures, canonicalisation is not converging, and everything
downstream is being computed over a vocabulary that keeps changing shape. Watch it
before anything else.

Review outcomes give a second, free signal: the acceptance rate of unreviewed claims,
sliced by `basis`, is a continuous precision estimate over the whole corpus rather
than just the gold set. If `inferred` claims are accepted below ~40%, stop emitting
them.

---

## 10. Migration from the current schema

The existing database is three tables (`backend/app/db.py:8-88`). Nothing is deleted;
the migration is additive, and the old tables are dropped only after the new model is
serving the UI.

| Current | Becomes | Notes |
|---|---|---|
| `job_roles` where `node_type='posting'` | `role_instance` (`kind='posting'`) + `document` (`kind='job_posting'`) | Document body = `description` ⧺ `requirements` ⧺ `responsibilities`; `raw_json` retained on the document as `notes` |
| `job_roles` where `node_type` LIKE `target_%` | `role_instance` (`kind='target_*'`) + `document` (`kind='narrative'`) | `typical_tasks` / `skill_decomposition` become the document body — they are the only text a target has |
| `job_role_skills` | → `concept_proposal`, then `requirement_claim` | See below |
| `job_roles.skill_decomposition` | → `concept_proposal` for the skill; `examples` become the document body for span-matching | The richest source in the current data |
| `profile_snapshots` | `person` (1 row) + one `document` (`kind='narrative'`) per snapshot | Narrative is demoted from representation to input; snapshots keep their timestamps |
| `job_roles.embedding`, `profile_snapshots.embedding` | `d_embedding` | Recompute rather than copy; the model may change |
| `job_roles.*_score`, `top_adjacent_roles`, `career_track` | `legacy_role_analysis` (parked) | See below |

### 10.1 Skills → claims

Each distinct `job_role_skills.name` becomes one `concept_proposal` with
`occurrence_count` set, ordered by frequency. The user reviews them **once** — this
is the seeding event for the vocabulary, and it is the single highest-value hour in
the whole migration.

Once resolved, each original skill row becomes a `requirement_claim` with:

- `concept_id` from the resolution
- `requirement_type` carried across
- `importance` carried across
- **`basis = 'inferred'`**, `evidence_span = NULL`, `review_status = 'unreviewed'`

This is the honest treatment: the current extraction produced no spans, so its output
cannot claim to be `stated`. Migrated data will therefore appear correctly as
lower-grade evidence in the UI from day one, and re-extracting a posting to upgrade
its claims is a visible, worthwhile action rather than an invisible one.

### 10.2 The analysis scores

`seniority_score`, `complexity_score`, `specialisation_score`,
`transferability_score`, `market_demand_score`, `rarity_score`,
`automation_risk_score`, `top_adjacent_roles` and `career_track` are model judgments
stored beside extracted facts with no provenance and no way to recompute them
(`backend/app/db.py:40-48`, written at `backend/app/routes/import_routes.py:53-61`).

They move to a `legacy_role_analysis` table keyed by `role_instance_id`, are wired
into nothing, and are dropped once the new model supersedes them. They are not
migrated into the derived layer, because a derived row that cannot be recomputed
violates §5.2 invariant 5.

`career_track` is the exception worth noting: it is genuinely useful as a UI facet
today (it drives filtering and the Space view colouring). It survives as a
`legacy_role_analysis` column until `domain`/`function` concept links replace it in
Phase 1, at which point the facet becomes evidence-backed rather than a single
model-assigned label.

### 10.3 What is not migrated

The user's career history does not exist in the current database in any structured
form — it is prose inside `profile_snapshots.narrative_text`. Pass A can propose
episodes from it, but **the user should expect to enter and confirm 8–15 episodes by
hand**. That is perhaps an hour of work and it is the foundation for everything else.
There is no automated path worth waiting for.

---

## 11. Staged implementation plan

Each phase ships something usable. No phase is only scaffolding.

### Phase 0 — Episodes and documents *(~1 week)*

**Build:** `document`, `person`, `episode`, `episode_document`, `vocabulary_version`,
`extraction_run`. Migrate existing postings and profile snapshots into `document`.
Episode CRUD UI, career timeline view. Derived years/recency helpers.

**Ships:** the user's career history exists as structured data with real dates for the
first time. The timeline is immediately useful on its own.

**Why first:** every temporal derivation and every evidence claim hangs off episodes.
Nothing downstream is possible without them.

### Phase 1 — Vocabulary *(~1.5 weeks)*

**Build:** `concept_type` (10 seeded types), `concept`, `concept_alias`,
`concept_xref`, `concept_edge`, `concept_edge_rule`, `concept_proposal`,
`d_embedding`. Migrate `job_role_skills` → proposals. Proposal review UI.
Concept-linking Pass B over the existing posting corpus. Gold set labelling begins.

**Ships:** the whole posting corpus becomes faceted — filter and group by domain,
regulation, tool, function, product. Strictly better than today's flat skill list,
and it builds the vocabulary as a by-product of being useful.

**Exit criteria:** ~300–500 active atomic concepts; proposals-per-document trending
down; span validation harness in place.

### Phase 2 — Evidence *(~2 weeks)*

**Build:** `evidence_claim`, `requirement_claim`, span validation, claim review queue,
Pass B over CV / LinkedIn / project write-ups, `user_asserted` claim creation.
First `eval_run` against the dev gold set.

**Ships:** *"what can I evidence, and where is the proof"* — with click-through to
the highlighted span in the source document. The first thing the current app cannot do
at all.

**Exit criteria:** span validity ≥ 0.99; concept-linking F1 ≥ 0.75 on dev.

### Phase 3 — Capabilities *(~2 weeks)*

**Build:** `capability_detail`, `component_of` edges, capability catalogue curation UI,
Pass C, the derivation engine, `d_capability_coverage`, `d_role_fit`. Embedding
similarity retained as one column in `d_role_fit`.

**Ships:** the core deliverable — capability-level gap analysis against any target,
with four honest states and per-capability evidence. Q1–Q5 all answered.

**Exit criteria:** ~100–150 curated capabilities; capability agreement ≥ 0.80 on the
hand-labelled subset.

### Phase 4 — Economics *(~2 weeks)*

**Build:** `role_archetype` concepts + assignment UI, `demands` edges derived from
posting requirement claims, `compensation_observation`, Pass D, `d_archetype_comp`,
`d_gap_value`. Gap ranking UI with sample sizes shown throughout.

**Ships:** the stated objective — *which gaps are most valuable to close, ranked by
their effect on access to higher-paid roles.* Q7–Q8 answered.

**Exit criteria:** ≥ 5 stated compensation observations for each archetype that is
ranked; archetypes below that show unlocked roles but no monetary figure.

### Phase 5 — Adjacency and judgment *(later, deliberately)*

**Build:** `prerequisite_of` / `adjacent_to` / `substitutable_for` capability edges
(curated); a separate, explicitly-labelled transition-judgment layer estimating
effort to close a gap.

**Ships:** Q6, and the "six-month project vs five-year change" framing — presented
throughout as curator judgment, visually distinct from everything evidence-backed,
and never mixed into `d_gap_value`.

**Deliberately last** because it is the only part of the system that cannot be
grounded in evidence, and shipping it early would contaminate the parts that can.

### Cross-cutting

- Gold set labelling runs through Phases 1–2; `eval_run` gates Phases 2, 3 and 4.
- The Space view survives on `d_embedding` throughout, unchanged in behaviour.
- Prompts are versioned from Phase 1 (`prompt_version` on every run), so a regression
  is always attributable.

---

## 12. Where this framing risks unnecessary complexity

Six specific pushbacks on the proposed model, beyond the capability question already
answered in §3.1.

### 12.1 Cut the `behavioural` concept type

"Communication" and "leadership" as atomic concepts are close to worthless. They
appear in every posting, discriminate nothing, and inflate every coverage count. They
are only meaningful with context and scope attached — which is exactly what a
capability is. So:

- Behavioural content is modelled **only** as capabilities ("influence executive
  stakeholders", "manage an actuarial team") and as claim modifiers
  (`stakeholder_scope`, `team_size`, `autonomy`).
- The ten-type list in §2.3 therefore has no `behavioural` type, against the original
  proposal. This is a simplification with no expressive loss.

### 12.2 `knowledge` vs `method` will be contested at extraction time

Is "chain ladder" knowledge or a method? Is "stochastic reserving" either? The
boundary is genuinely fuzzy and extraction will be inconsistent about it.

Two options: collapse them into one type, or keep both and accept that the *type* is
advisory while the *concept identity* is what matters. Recommendation: **keep both,
but never branch reasoning on the distinction** — it is a UI facet, not a semantic
constraint. If gold-set labelling shows self-agreement below ~0.8 on this boundary,
collapse them without ceremony.

### 12.3 `product` may be too granular for Phase 1

Annuities, term assurance, bulk purchase annuities, DB schemes, DC schemes — this
type will generate a long tail of concepts that rarely change any answer. Recommend
seeding it thinly (~15 concepts) and letting proposals grow it only where it
demonstrably discriminates between roles.

### 12.4 Role archetypes need corpus scale to be worth it

`d_archetype_comp` is meaningless with three postings per archetype. Archetype
assignment is cheap, but the *aggregation* only earns its keep at roughly 8–10
postings per archetype in a comparable market. If the corpus is smaller than
~150 postings when Phase 4 starts, do archetype assignment but present compensation
per-posting rather than aggregated, and say so plainly in the UI.

### 12.5 Curation fatigue is the real risk to the project

Everything here depends on a human resolving proposals. Hard budget for Phase 1–3:
**~500 atomic concepts and ~150 capabilities.** If the vocabulary is heading past
that, the concepts being admitted are too granular and the system will spend its
value on maintenance. Concretely: if a concept is claimed by fewer than two episodes
and demanded by fewer than three postings, it probably should have been an alias.

A useful forcing function: review queues that cannot be cleared in 20 minutes a week
will not be cleared at all. Design the queues to that budget, and prioritise by
leverage (§8.2) rather than completeness.

### 12.6 The compositional derivation will over-credit if left unchecked

The most dangerous single line of code in this design is the rule that infers
capability coverage from component claims. "Used Python" + "did reserving" does not
mean "can own a production actuarial model", and a permissive rule will manufacture
seniority the user does not have — the exact failure the whole architecture exists to
avoid.

Mitigations, all cheap: require components within a **single episode** rather than
across a career; default `requires_all_core = 1`; make `min_depth` default to `owned`
rather than `applied`; and hold compositional-only coverage at `partial` unless a
direct capability claim exists. Then check it against the hand-labelled capability set
before Phase 4 depends on it.

---

## 13. Open questions for review

1. **Capability granularity.** Is "lead a reserving process" one capability, or does
   it split by domain (life vs general)? Recommendation: keep it single and let
   `domain` be a contextual component — but this is a curation policy that should be
   decided once, up front, and written into `capability_detail.notes`.
2. **Depth scale wording.** `exposed | applied | owned | set_standard` — does this
   survive contact with real actuarial episodes, or is a fifth level needed between
   `applied` and `owned`?
3. **Whether targets belong in `role_instance`.** They share requirement structure
   with postings, which is why they are together here, but they have no market
   compensation and never should. If target-specific fields accumulate, split them.
4. **Whether to model the user's own compensation history** in Phase 4 rather than
   deferring it. It would make "trajectory" measurable rather than notional, at the
   cost of one table.
5. **Gold-set labelling cost.** 24 documents is a real time commitment. If it proves
   too much, cut to 16 (10 dev / 6 test) rather than skipping evaluation — some
   measurement radically beats none.
