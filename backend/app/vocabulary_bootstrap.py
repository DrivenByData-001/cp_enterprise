"""Vocabulary/capability bootstrap (docs/18-consolidation-and-analytical-foundation.md §3).

Production has no meaningfully populated canonical vocabulary — the Phase 3
capability catalogue is 0 rows, and hundreds of `role_skill_observation`
surface forms sit unresolved. The brief for this pass is explicit: do not
hand-author 100-150 capabilities. This module is a deterministic, reviewable
*bootstrap* over evidence the corpus and profile360 already contain — it
proposes, it never accepts. Nothing this module writes is ever `status
='active'`/`'accepted'` — every row it inserts is `status='proposed'`
(concepts) or `status='proposed'` (concept_edge), exactly the existing
values those columns already support for human review before anything here
can affect matching, coverage, or comparison (which all filter on
`status='active'`/`'accepted'` elsewhere in this codebase and are completely
blind to `'proposed'` rows).

Two independent products, both proposal-only:

1. **Atomic-concept clustering** (`compute_cluster_keys`) — a coarser,
   deterministic normalisation on top of `concept_linking.normalize_name`'s
   exact-match key, so obvious lexical duplicates ("Solvency II" / "SII",
   "R" / "R programming") land in one reviewable group instead of two. This
   never changes what `concept_proposal.surface_form` means anywhere it
   already matters (the exact-match auto-link on resolution) — it only adds
   a `cluster_key` column routes/concepts.py can group by.
2. **Candidate capabilities** (`compute_candidate_capabilities`) — atomic
   concepts that co-occur across many roles' skill observations are
   evidence of a higher-order capability those roles actually demand. See
   that function's docstring for the exact deterministic method (frequent-
   itemset-style core detection, then supporting/contextual membership by
   co-occurrence proportion) and how `profile360.capabilities` names are
   used, read-only, purely as a *naming* signal — never copied as evidence.

`run_bootstrap` composes both, `dry_run=True` computes everything and writes
nothing (for `--dry-run` CLI reporting and tests that only want the
proposed shape, not persisted rows).
"""

import re
from dataclasses import dataclass, field
from itertools import combinations

import psycopg

from .concept_linking import normalize_name, run_pass_b
from .embeddings import cosine_similarity, embed_text

# --- atomic concept types this module ever proposes capabilities from/for --
# (docs/11 §2.3 / local_baseline.sql's concept_type seed — the 8 is_atom=true
# types). Deliberately excludes 'capability' and 'role_archetype' themselves.
ATOMIC_TYPE_CODES = (
    "knowledge", "method", "tool", "function", "domain", "product", "regulation", "credential",
)

# --- Step 1: deterministic clustering key (coarser than concept_linking.normalize_name) ---
#
# A *seed list*, not a general-purpose stemmer: each entry is a group of
# surface forms a human would obviously treat as one review decision. The
# canonical key chosen per group is the group's first element — an
# arbitrary but stable choice; it only ever affects grouping, never the
# final canonical_name a curator types when accepting a proposal. Deliberately
# small and explicit rather than a fuzzy general algorithm, so it can never
# silently over-collapse two genuinely different concepts (brief: "Do not
# over-collapse genuinely different concepts") — anything not covered here
# falls through to the conservative generic rules below, or stays ungrouped.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("solvency ii", "sii"),
    ("stochastic modeling", "stochastic modelling", "stochastic models", "stochastic model"),
    ("stakeholder management", "stakeholder engagement"),
    ("r", "r programming", "r programming language"),
    ("internal model", "internal modeling", "internal modelling"),
    ("ifrs 17", "ifrs17"),
    ("ifrs 9", "ifrs9"),
    ("microsoft excel", "excel"),
    ("microsoft powerpoint", "powerpoint"),
    ("python programming", "python"),
    ("sql", "structured query language"),
    ("machine learning", "ml"),
    ("value at risk", "var"),
    ("own risk and solvency assessment", "orsa"),
)

# Deterministic BrEng/AmEng and gerund/agent-noun spelling normalisation —
# generically safe (a spelling variant is never a different concept), unlike
# a generic stemmer. Applied with word boundaries only.
_SPELLING_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bmodelling\b"), "modeling"),
    (re.compile(r"\bmodeller(s?)\b"), r"modeler\1"),
    (re.compile(r"\b(\w+)isation\b"), r"\1ization"),
    (re.compile(r"\b(\w+[^aeiou])ise\b"), r"\1ize"),
    (re.compile(r"\b(\w+[^aeiou])ising\b"), r"\1izing"),
)


def _build_synonym_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for group in _SYNONYM_GROUPS:
        canonical_key = group[0]
        for member in group:
            lookup[member] = canonical_key
    return lookup


_SYNONYM_LOOKUP = _build_synonym_lookup()


def _apply_spelling_rules(text: str) -> str:
    for pattern, replacement in _SPELLING_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _strip_trailing_plural(text: str) -> str:
    """Conservative fallback, tried only after exact/synonym matching fails.
    Two standard, low-false-positive English pluralisation rules only —
    deliberately not a general stemmer:
    - '...ies' -> '...y' (e.g. 'actuaries' -> 'actuary', 'companies' ->
      'company') when that leaves a stem of at least 3 characters.
    - a bare trailing 's' is otherwise stripped when that leaves a stem of
      at least 4 characters and doesn't create a false stem ending
      'ss'/'us'/'is' (those are not plurals of the obvious kind —
      'business', 'status', 'analysis' must not be mangled)."""
    if text.endswith("ies") and len(text) > 6:
        return text[:-3] + "y"
    if len(text) > 4 and text.endswith("s") and not text.endswith(("ss", "us", "is")):
        return text[:-1]
    return text


def cluster_key_for(surface_form: str) -> str:
    """The deterministic clustering key for one surface form. Always defined
    (never raises, never returns empty for non-empty input) so it can be
    stored unconditionally on every proposal. See module docstring / brief
    examples: 'Solvency II'/'SII', 'stochastic modelling'/'stochastic
    models', 'stakeholder management'/'stakeholder engagement',
    'R'/'R programming', 'internal model'/'internal modelling' all resolve
    to the same key; unrelated terms never do."""
    base = normalize_name(surface_form)
    if not base:
        return base
    spelled = _apply_spelling_rules(base)
    if spelled in _SYNONYM_LOOKUP:
        return _SYNONYM_LOOKUP[spelled]
    if base in _SYNONYM_LOOKUP:
        return _SYNONYM_LOOKUP[base]
    return _strip_trailing_plural(spelled)


def compute_cluster_keys(cur) -> dict:
    """Runs Pass B (idempotent, existing/tested — concept_linking.run_pass_b)
    to make sure every currently-unresolved role_skill_observation has a
    pending exact-surface-form proposal, then computes and stores
    cluster_key on every pending proposal missing one. Also backfills
    `suggested_type` from the proposal's own `nearest_concept_id` (already
    computed by Pass B's embedding search) when that neighbour is a
    reasonably close match (similarity >= 0.5) and no type was suggested yet
    — "this new term is probably the same *kind* of thing as its closest
    existing vocabulary neighbour" is a modest, explainable heuristic, not a
    new inference path; it only ever fills a NULL, never overrides a
    curator- or previously-set value. Returns Pass B's own counts plus how
    many proposals were newly keyed and how many distinct clusters exist."""
    pass_b_result = run_pass_b(cur)

    cur.execute(
        """
        SELECT cp.id, cp.surface_form, cp.suggested_type, cp.nearest_concept_id, cp.nearest_similarity,
               c.type_code AS nearest_type_code
        FROM jobber.concept_proposal cp
        LEFT JOIN jobber.concept c ON c.id = cp.nearest_concept_id
        WHERE cp.status = 'pending'
        """
    )
    rows = cur.fetchall()

    newly_keyed = 0
    for row in rows:
        key = cluster_key_for(row["surface_form"])
        suggested_type = row["suggested_type"]
        if suggested_type is None and row["nearest_type_code"] and (row["nearest_similarity"] or 0) >= 0.5:
            suggested_type = row["nearest_type_code"]
        cur.execute(
            "UPDATE jobber.concept_proposal SET cluster_key = %s, suggested_type = COALESCE(suggested_type, %s) WHERE id = %s",
            (key, suggested_type, row["id"]),
        )
        newly_keyed += 1

    cur.execute(
        "SELECT COUNT(DISTINCT COALESCE(cluster_key, surface_form)) AS n FROM jobber.concept_proposal WHERE status = 'pending'"
    )
    cluster_count = cur.fetchone()["n"]

    return {**pass_b_result, "proposals_keyed": newly_keyed, "pending_clusters": cluster_count}


# --- Step 2: candidate capabilities from atomic-concept co-occurrence ------

PROFILE360_NAME_SIMILARITY_THRESHOLD = 0.75


@dataclass
class CandidateCapability:
    core: list[dict]
    supporting: list[dict]
    contextual: list[dict]
    support_role_count: int
    suggested_name: str
    naming_source: str  # "corpus" | "profile360"
    naming_similarity: float | None = None
    notes: str = field(default="")

    @property
    def all_members(self) -> list[dict]:
        return self.core + self.supporting + self.contextual


def _role_concept_index(cur) -> tuple[dict[str, set[str]], dict[str, dict]]:
    """concept_id -> set(role_instance_id) over ACTIVE atomic concepts only,
    plus a concept_id -> {id, canonical_name, type_code} lookup. Restricted
    to canonical_concept_id IS NOT NULL (i.e. already-resolved observations)
    — co-occurrence evidence must be over the *canonical* vocabulary, not
    raw unresolved surface-form text, so a candidate capability's components
    are always real, addressable concept ids."""
    cur.execute(
        """
        SELECT rso.role_instance_id, c.id AS concept_id, c.canonical_name, c.type_code
        FROM jobber.role_skill_observation rso
        JOIN jobber.concept c ON c.id = rso.canonical_concept_id
        WHERE c.status = 'active' AND c.type_code = ANY(%s)
        """,
        (list(ATOMIC_TYPE_CODES),),
    )
    roles_by_concept: dict[str, set[str]] = {}
    concepts: dict[str, dict] = {}
    for row in cur.fetchall():
        concept_id = str(row["concept_id"])
        roles_by_concept.setdefault(concept_id, set()).add(str(row["role_instance_id"]))
        concepts[concept_id] = {"id": concept_id, "canonical_name": row["canonical_name"], "type_code": row["type_code"]}
    return roles_by_concept, concepts


def _grow_core(seed: frozenset, roles_by_concept: dict[str, set[str]], candidate_pool: list[str], min_support: int) -> frozenset:
    """Greedily extends a seed pair into a maximal core set: repeatedly adds
    any pooled concept whose inclusion keeps co-occurring-role support >=
    min_support, in a fixed (sorted-id) order for full determinism, until no
    addition qualifies or the core reaches 4 members (bounds runtime and
    keeps a capability's "defining" set small enough to be reviewable — a
    9-atom "core" would not read as one coherent capability). This is a
    deliberately simplified, bounded stand-in for full frequent-itemset
    mining (true Apriori across all sizes is unnecessary at this corpus
    scale and harder to keep deterministic/explainable) — documented here,
    not hidden."""
    core = set(seed)
    support = roles_by_concept[next(iter(seed))].copy()
    for member in seed:
        support &= roles_by_concept[member]

    changed = True
    while changed and len(core) < 4:
        changed = False
        for concept_id in sorted(candidate_pool):
            if concept_id in core:
                continue
            trial = support & roles_by_concept[concept_id]
            if len(trial) >= min_support:
                core.add(concept_id)
                support = trial
                changed = True
                break
    return frozenset(core)


def _profile360_capability_names(cur) -> list[str]:
    try:
        cur.execute("SELECT name FROM profile360.capabilities WHERE name IS NOT NULL")
        return [row["name"] for row in cur.fetchall() if (row["name"] or "").strip()]
    except psycopg.errors.UndefinedTable:
        return []


def compute_candidate_capabilities(
    cur, *, min_concept_support: int = 3, min_pair_support: int = 5, max_candidates: int = 150
) -> list[CandidateCapability]:
    """Deterministic candidate-capability detection from `role_skill_observation`
    co-occurrence, evidenced further (naming only) by `profile360.capabilities`.

    Method, spelled out (brief §8: "prefer transparent rules... document the
    methodology"):

    1. Restrict to active atomic concepts appearing in >= `min_concept_support`
       distinct roles (bounds the search space to concepts common enough to
       plausibly anchor a capability).
    2. For every pair of those concepts co-occurring (same role_instance_id)
       in >= `min_pair_support` roles, greedily grow a maximal "core" set
       (`_grow_core`, capped at 4 members) — the core is what *always*
       co-occurs together at or above the support threshold, i.e. the
       defining components of the candidate capability.
    3. Distinct core sets (by member identity) become one candidate each.
       For each, its support-role-set R = the intersection of its core
       members' role sets. Every *other* qualifying concept is classified by
       what fraction of R it also appears in: >=0.5 -> supporting, >=0.2 (and
       <0.5) -> contextual, below that -> not included. This is what makes
       necessity a real, transparent frequency measurement rather than a
       guess: core = always present in the defining set, supporting =
       present alongside it most of the time, contextual = present
       sometimes.
    4. Naming: the deterministic fallback label is the core concepts' own
       canonical names joined with " & ". If any `profile360.capabilities.name`
       (read-only; no other profile360 field is read, and nothing from
       profile360 is stored beyond this short label string — no claim text,
       no id, no evidence — see module docstring's ownership note) embeds
       within `PROFILE360_NAME_SIMILARITY_THRESHOLD` cosine similarity of the
       fallback label, that profile360 name is used as the suggested
       canonical_name instead, purely as a human-readable naming hint a
       curator can freely overwrite.

    Returns candidates sorted by support-role-count descending (most-evidenced
    first), capped at `max_candidates`. Pure/read-only — writes nothing;
    `persist_candidate_capabilities` does the (proposal-only) writing.
    """
    roles_by_concept, concepts = _role_concept_index(cur)
    frequent = [cid for cid, roles in roles_by_concept.items() if len(roles) >= min_concept_support]
    frequent_set = set(frequent)

    seen_cores: set[frozenset] = set()
    candidates: list[CandidateCapability] = []

    for a, b in combinations(sorted(frequent), 2):
        support = roles_by_concept[a] & roles_by_concept[b]
        if len(support) < min_pair_support:
            continue
        core = _grow_core(frozenset((a, b)), roles_by_concept, frequent, min_pair_support)
        if core in seen_cores:
            continue
        seen_cores.add(core)

        support_roles = None
        for member in core:
            support_roles = roles_by_concept[member] if support_roles is None else (support_roles & roles_by_concept[member])
        support_roles = support_roles or set()
        if len(support_roles) < min_pair_support:
            continue

        # Supporting/contextual membership is checked against every atomic
        # concept the corpus has (not only the `frequent` seed pool above) —
        # the min_concept_support threshold exists to bound *pair-generation*
        # search space, not to exclude a concept that is genuinely, if
        # rarely, correlated with an already-identified core. It only needs
        # to be measured against the core's own (already small) support-role
        # set, so this carries no combinatorial cost.
        supporting, contextual = [], []
        for cid in sorted(set(roles_by_concept) - core):
            overlap = len(roles_by_concept[cid] & support_roles) / len(support_roles)
            if overlap >= 0.5:
                supporting.append(concepts[cid])
            elif overlap >= 0.2:
                contextual.append(concepts[cid])

        core_members = [concepts[cid] for cid in sorted(core)]
        fallback_name = " & ".join(c["canonical_name"] for c in core_members)
        candidates.append(
            CandidateCapability(
                core=core_members,
                supporting=supporting,
                contextual=contextual,
                support_role_count=len(support_roles),
                suggested_name=fallback_name,
                naming_source="corpus",
                notes=f"Bootstrap-proposed from {len(support_roles)} role(s) in the historical corpus requiring all of: "
                f"{', '.join(c['canonical_name'] for c in core_members)}.",
            )
        )

    candidates.sort(key=lambda c: -c.support_role_count)
    candidates = candidates[:max_candidates]

    profile_names = _profile360_capability_names(cur)
    if profile_names:
        name_vectors = [(name, embed_text(name)) for name in profile_names]
        name_vectors = [(name, vec) for name, vec in name_vectors if vec]
        for candidate in candidates:
            fallback_vec = embed_text(candidate.suggested_name)
            if not fallback_vec:
                continue
            best_name, best_sim = None, 0.0
            for name, vec in name_vectors:
                sim = cosine_similarity(fallback_vec, vec) or 0.0
                if sim > best_sim:
                    best_name, best_sim = name, sim
            if best_name and best_sim >= PROFILE360_NAME_SIMILARITY_THRESHOLD:
                candidate.suggested_name = best_name
                candidate.naming_source = "profile360"
                candidate.naming_similarity = best_sim
                candidate.notes += (
                    f" Naming signal: similar to an existing profile360 capability name "
                    f"(cosine similarity {best_sim:.2f}) — used as a human-readable label suggestion only, "
                    f"no profile360 evidence was copied."
                )

    return candidates


def persist_candidate_capabilities(cur, candidates: list[CandidateCapability]) -> dict:
    """Writes each candidate as one `jobber.concept` (type_code='capability',
    status='proposed', origin='bootstrap') + one `jobber.capability_detail`
    row (a clearly-provisional placeholder demonstration_standard, and the
    *lowest* min_depth ('exposed') rather than the curated default ('owned')
    — a bootstrap guess should never silently claim a curator's confidence
    level), plus one `jobber.concept_edge` (relation='component_of',
    status='proposed') per core/supporting/contextual member. Every insert
    is ON CONFLICT-safe / duplicate-tolerant so re-running the bootstrap
    after some proposals have already been accepted/rejected does not
    error or duplicate — a capability name collision (curator already
    accepted one with the same name) is skipped and counted, never a crash
    that aborts the rest of the batch."""
    created = skipped = edges_created = 0
    for candidate in candidates:
        try:
            # A SAVEPOINT (conn.transaction(), nested since db_cursor()'s
            # caller already holds an open transaction) scopes one
            # candidate's writes: a UniqueViolation on the concept insert
            # only rolls back *this* candidate, not the whole batch — without
            # it, Postgres marks the entire outer transaction aborted on the
            # first name collision and every later candidate (even a
            # perfectly valid one) would fail with InFailedSqlTransaction.
            with cur.connection.transaction():
                cur.execute(
                    "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at) "
                    "VALUES ('capability', %s, %s, 'proposed', 'bootstrap', now()) RETURNING id",
                    (candidate.suggested_name, candidate.notes),
                )
                capability_id = cur.fetchone()["id"]

                placeholder_standard = (
                    "[Bootstrap-proposed — needs curator-authored demonstration standard] "
                    f"Provisionally: demonstrated by evidence of using all of "
                    f"{', '.join(c['canonical_name'] for c in candidate.core)} together in one role."
                )
                cur.execute(
                    "INSERT INTO jobber.capability_detail "
                    "(concept_id, demonstration_standard, min_depth, requires_all_core, notes) "
                    "VALUES (%s, %s, 'exposed', TRUE, %s)",
                    (capability_id, placeholder_standard, candidate.notes),
                )

                for necessity, members in (
                    ("core", candidate.core), ("supporting", candidate.supporting), ("contextual", candidate.contextual)
                ):
                    for member in members:
                        cur.execute(
                            "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, necessity, origin, status) "
                            "VALUES (%s, %s, 'component_of', %s, 'bootstrap', 'proposed') "
                            "ON CONFLICT (from_concept_id, to_concept_id, relation) DO NOTHING",
                            (member["id"], capability_id, necessity),
                        )
                        edges_created += 1
        except psycopg.errors.UniqueViolation:
            skipped += 1
            continue
        created += 1

    return {"capabilities_created": created, "capabilities_skipped_existing_name": skipped, "component_edges_proposed": edges_created}


def run_bootstrap(
    cur,
    *,
    min_concept_support: int = 3,
    min_pair_support: int = 5,
    max_candidates: int = 150,
    dry_run: bool = False,
) -> dict:
    """Orchestrates both steps. `dry_run=True` computes and reports without
    writing anything (used by `--dry-run` on the CLI, and by tests that only
    want to assert on the *shape* of proposals)."""
    cluster_result = {"auto_resolved": 0, "proposals_created": 0, "proposals_updated": 0, "proposals_keyed": 0, "pending_clusters": 0}
    candidates: list[CandidateCapability] = []
    persist_result = {"capabilities_created": 0, "capabilities_skipped_existing_name": 0, "component_edges_proposed": 0}

    if not dry_run:
        cluster_result = compute_cluster_keys(cur)
    candidates = compute_candidate_capabilities(
        cur, min_concept_support=min_concept_support, min_pair_support=min_pair_support, max_candidates=max_candidates
    )
    if not dry_run:
        persist_result = persist_candidate_capabilities(cur, candidates)

    return {
        "dry_run": dry_run,
        "atomic_concept_clustering": cluster_result,
        "candidate_capabilities_found": len(candidates),
        "candidate_capabilities": [
            {
                "suggested_name": c.suggested_name,
                "naming_source": c.naming_source,
                "naming_similarity": c.naming_similarity,
                "support_role_count": c.support_role_count,
                "core": [m["canonical_name"] for m in c.core],
                "supporting": [m["canonical_name"] for m in c.supporting],
                "contextual": [m["canonical_name"] for m in c.contextual],
            }
            for c in candidates
        ],
        "persisted": persist_result,
    }
