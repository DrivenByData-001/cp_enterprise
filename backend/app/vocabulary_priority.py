"""Deterministic curation-priority scoring for pending vocabulary clusters
(Vocabulary Proposal Prioritisation and Curation UX brief, §2/§7/§8).

Pure, DB-free, fully unit-testable: every function here takes plain numbers
(already aggregated by `app/vocabulary_curation.py` from real corpus evidence)
and returns a score/band/flags. Keeping this module free of SQL means the
ranking rules can be tested in isolation from Postgres and read start-to-end
as the actual, complete methodology — nothing about "what counts" is hidden
in a query.

**What the score is not**: `compute_priority_score` is a *curation* priority
— which pending cluster is most worth a curator's attention next — never a
statement that the underlying concept is intrinsically more important than
one scored lower. A rare but highly diagnostic term (e.g. a specific
credential) may still be worth accepting even at a low score; the score only
orders the review queue.

## Methodology (also served at `GET /api/vocabulary/methodology`)

Every signal below is a *distinct-count* over the roles a cluster's
observations come from — never a raw observation/mention count — precisely
so that many repeated mentions inside one role, or many postings from one
narrow historical burst, cannot dominate the ranking (brief §2: "Avoid
allowing repeated mentions from one role or one narrow historical period to
dominate"). Raw observation frequency is still included, but as the
smallest-weighted, log-dampened term, not the primary signal.

    score = WEIGHT_ROLE_COUNT          * log1p(role_count)
          + WEIGHT_OBSERVATION_COUNT   * log1p(observation_count)
          + WEIGHT_YEAR_COUNT          * log1p(year_count)
          + WEIGHT_SENIORITY_COUNT     * log1p(seniority_count)
          + WEIGHT_COUNTRY_COUNT       * log1p(country_count)
          + WEIGHT_CAREER_TRACK_COUNT  * log1p(career_track_count)
          + WEIGHT_RECENCY             * recency_factor

- `role_count` (distinct roles the cluster was observed in) carries the
  largest weight: how much of the corpus a concept touches is the single
  best proxy for how much analytical value curating it unlocks.
- `observation_count` (raw mention count) carries the *smallest* weight and
  is log-dampened — it is evidence, but must never let one role mentioning a
  term 50 times outrank a term seen once each across 20 different roles.
- `year_count`/`seniority_count`/`country_count`/`career_track_count` are
  breadth-of-recurrence signals (brief §2's "recurrence across multiple
  years / role types / seniority levels / countries") — a concept that
  recurs across many contexts is more likely a stable, general vocabulary
  item than an artefact of one employer, one era, or one job family.
- `recency_factor` is deliberately the smallest, and purely a tie-breaking
  secondary signal (brief §2: "recent occurrence as a secondary signal"): an
  exponential decay (half-life `RECENCY_HALF_LIFE_YEARS`), 1.0 for a cluster
  last observed this year, 0.5 at `RECENCY_HALF_LIFE_YEARS` years old, and so
  on — never zero, never large enough to move a sparse-but-recent cluster
  above a broad historical one.

`log1p` (natural log of 1+x) is used throughout instead of the raw count so
that going from 1->2 occurrences of a signal matters much more than going
from 50->51 — this is what makes the breadth signals ("in how many distinct
years/countries/levels") meaningfully additive with each other rather than
one huge count swamping the rest.

Weights and thresholds are named constants, chosen once and documented here
— never tuned against a particular corpus to force a target count into any
one band (brief §7 is explicit that thresholds must not be manipulated to
hit a round number).
"""

import math
from dataclasses import dataclass

# --- scoring weights ---------------------------------------------------

WEIGHT_ROLE_COUNT = 3.0
WEIGHT_OBSERVATION_COUNT = 0.5
WEIGHT_YEAR_COUNT = 1.5
WEIGHT_SENIORITY_COUNT = 1.0
WEIGHT_COUNTRY_COUNT = 1.0
WEIGHT_CAREER_TRACK_COUNT = 1.0
WEIGHT_RECENCY = 0.5
RECENCY_HALF_LIFE_YEARS = 5.0

# --- priority bands ------------------------------------------------------
#
# Bands are read off the *raw* evidence (role_count + how many other
# dimensions recur), not off the composite score, so a curator can
# understand "why High" without reverse-engineering a formula (brief §7:
# "understandable review bands"). A dimension (year/seniority/country/
# career_track) "recurs" once it has at least BREADTH_DIMENSION_MIN_DISTINCT
# distinct values among the cluster's roles.

SPARSE_MAX_ROLE_COUNT = 1
MEDIUM_MIN_ROLE_COUNT = 3
HIGH_MIN_ROLE_COUNT = 6
HIGH_MIN_BREADTH_DIMENSIONS = 2
BREADTH_DIMENSION_MIN_DISTINCT = 2

BAND_HIGH = "high"
BAND_MEDIUM = "medium"
BAND_LOW = "low"
BAND_SPARSE = "sparse"
PRIORITY_BANDS = (BAND_HIGH, BAND_MEDIUM, BAND_LOW, BAND_SPARSE)

# --- noise / low-information heuristics (brief §8) ------------------------
#
# Deterministic, lexical, conservative — never an LLM call (brief §8: "Do
# not use an LLM merely to classify obvious lexical noise if deterministic
# rules suffice"). These are *flags for lower review priority*, never an
# automatic reject — a flagged cluster still needs a human decision.

NOISE_LONG_PHRASE_WORD_COUNT = 6
NOISE_LONG_PHRASE_CHAR_COUNT = 60

# Conjunctions/prepositions/articles that, as the first or last word of a
# proposed label, usually mean an extraction accidentally captured a
# fragment of a longer sentence rather than a standalone skill/term.
_FRAGMENT_EDGE_WORDS = {
    "and", "or", "but", "nor", "with", "for", "of", "the", "a", "an", "to",
    "in", "on", "at", "by", "from", "as", "&",
}

# Substrings suggesting employer/process-specific wording rather than a
# reusable, market-general concept (brief §8's "obvious employer/
# process-specific wording").
_EMPLOYER_PROCESS_TOKENS = (
    "in-house", "in house", "bespoke", "proprietary", "internal system",
    "internal tool", "internal process", "the company", "the firm",
    "the business", "our team", "our company", "our organisation",
    "our organization",
)

NOISE_SINGLE_ROLE = "single_role"
NOISE_SINGLE_OBSERVATION = "single_observation"
NOISE_LONG_PHRASE = "long_phrase"
NOISE_POSSIBLE_FRAGMENT = "possible_fragment"
NOISE_EMPLOYER_SPECIFIC = "employer_or_process_specific"
NOISE_MALFORMED = "malformed"


@dataclass(frozen=True)
class ClusterSignals:
    """The plain-number evidence `compute_priority_score`/`assign_priority_band`
    need. Built by `app/vocabulary_curation.py` from real corpus rows —
    every field here is already a *distinct* count over the cluster's
    contributing roles, never a raw row count (see module docstring)."""

    role_count: int
    observation_count: int
    year_count: int
    seniority_count: int
    country_count: int
    career_track_count: int
    most_recent_year: int | None
    current_year: int


def _log1p(n: int) -> float:
    return math.log1p(max(0, n))


def recency_factor(signals: ClusterSignals) -> float:
    """1.0 for a cluster last observed this year, decaying by half every
    RECENCY_HALF_LIFE_YEARS years; 0.0 only when no year is known at all
    (never negative, never observed-but-zero)."""
    if signals.most_recent_year is None:
        return 0.0
    years_since = max(0, signals.current_year - signals.most_recent_year)
    return 0.5 ** (years_since / RECENCY_HALF_LIFE_YEARS)


def compute_priority_score(signals: ClusterSignals) -> float:
    """The documented, deterministic curation-priority score — see module
    docstring for the full methodology. Higher = review sooner. This is a
    queue-ordering heuristic, not a judgement of intrinsic importance."""
    return (
        WEIGHT_ROLE_COUNT * _log1p(signals.role_count)
        + WEIGHT_OBSERVATION_COUNT * _log1p(signals.observation_count)
        + WEIGHT_YEAR_COUNT * _log1p(signals.year_count)
        + WEIGHT_SENIORITY_COUNT * _log1p(signals.seniority_count)
        + WEIGHT_COUNTRY_COUNT * _log1p(signals.country_count)
        + WEIGHT_CAREER_TRACK_COUNT * _log1p(signals.career_track_count)
        + WEIGHT_RECENCY * recency_factor(signals)
    )


def breadth_dimension_count(signals: ClusterSignals) -> int:
    """How many of {year, seniority, country, career_track} recur (>=
    BREADTH_DIMENSION_MIN_DISTINCT distinct values) for this cluster —
    the secondary axis priority bands use alongside role_count."""
    dims = (signals.year_count, signals.seniority_count, signals.country_count, signals.career_track_count)
    return sum(1 for d in dims if d >= BREADTH_DIMENSION_MIN_DISTINCT)


def assign_priority_band(signals: ClusterSignals) -> str:
    """Understandable, evidence-based bands (brief §7) — read directly off
    role_count + cross-dimension recurrence, not off the composite score, so
    "why High" never requires reverse-engineering a formula. Thresholds are
    fixed constants chosen once (see module docstring) and never adjusted to
    force a particular band size for any one corpus."""
    if signals.role_count <= SPARSE_MAX_ROLE_COUNT:
        return BAND_SPARSE
    if signals.role_count >= HIGH_MIN_ROLE_COUNT and breadth_dimension_count(signals) >= HIGH_MIN_BREADTH_DIMENSIONS:
        return BAND_HIGH
    if signals.role_count >= MEDIUM_MIN_ROLE_COUNT:
        return BAND_MEDIUM
    return BAND_LOW


def sort_key(cluster_key: str, score: float, role_count: int, observation_count: int) -> tuple:
    """Full deterministic ordering key: score desc, then role_count desc and
    observation_count desc as tie-breakers, then cluster_key asc so ties are
    never left to incidental row/dict order. Used for both the review queue
    and the diagnostic report so both agree byte-for-byte on ordering. Takes
    plain values (not a ClusterSignals) so any caller holding a summary dict
    can call it without reconstructing a dataclass."""
    return (-round(score, 9), -role_count, -observation_count, cluster_key)


def noise_flags(surface_forms: list[str]) -> list[str]:
    """Deterministic, lexical low-information/noise flags for a cluster's
    surface forms (brief §8). Advisory only — never causes automatic
    rejection or deletion; callers combine this with `single_role`/
    `single_observation` (already implied by the band) to lower review
    priority or surface a warning badge. A flag fires if *any* member
    surface form trips it."""
    flags: set[str] = set()
    for form in surface_forms:
        label = (form or "").strip()
        if not label:
            flags.add(NOISE_MALFORMED)
            continue
        if not any(ch.isalpha() for ch in label):
            flags.add(NOISE_MALFORMED)
            continue

        words = label.split()
        if len(words) > NOISE_LONG_PHRASE_WORD_COUNT or len(label) > NOISE_LONG_PHRASE_CHAR_COUNT:
            flags.add(NOISE_LONG_PHRASE)

        lowered = label.casefold()
        first_word = words[0].casefold().strip(".,;:") if words else ""
        last_word = words[-1].casefold().strip(".,;:") if words else ""
        if (
            first_word in _FRAGMENT_EDGE_WORDS
            or last_word in _FRAGMENT_EDGE_WORDS
            or label.count(",") >= 2
            or lowered.startswith(("etc", "n/a", "tbd", "-", "/"))
        ):
            flags.add(NOISE_POSSIBLE_FRAGMENT)

        if any(token in lowered for token in _EMPLOYER_PROCESS_TOKENS):
            flags.add(NOISE_EMPLOYER_SPECIFIC)

    return sorted(flags)


def evidence_flags(signals: ClusterSignals) -> list[str]:
    """The evidence-volume half of the noise/sparse flag set — kept separate
    from `noise_flags` (lexical shape) because these come purely from
    corpus counts, not the label text."""
    flags = []
    if signals.role_count <= SPARSE_MAX_ROLE_COUNT:
        flags.append(NOISE_SINGLE_ROLE)
    if signals.observation_count <= 1:
        flags.append(NOISE_SINGLE_OBSERVATION)
    return flags


METHODOLOGY_TEXT = """Curation priority ranks PENDING clusters only; it is a queue order, not a \
statement of intrinsic importance.

Score = 3.0*log1p(distinct roles) + 0.5*log1p(observation count) + \
1.5*log1p(distinct years) + 1.0*log1p(distinct seniority levels) + \
1.0*log1p(distinct countries) + 1.0*log1p(distinct career tracks) + \
0.5*recency_factor.

All breadth signals (roles/years/seniority/countries/career tracks) count \
DISTINCT values, never raw mentions, so repeated mentions inside one role or \
one narrow historical burst cannot dominate the ranking. Raw observation \
frequency is included but log-dampened and given the smallest weight. \
recency_factor decays by half every 5 years since the cluster's most recent \
observation and is the smallest-weighted, purely secondary signal.

Priority bands (High/Medium/Low/Sparse) are read directly off distinct-role \
count plus how many other dimensions recur (>=2 distinct values), not off \
the composite score, so a curator can see "why High" without the formula:
  Sparse: observed in at most 1 role.
  High:   observed in >=6 distinct roles AND recurs across >=2 of \
{year, seniority, country, career track}.
  Medium: observed in >=3 distinct roles (and not High).
  Low:    everything else pending (i.e. observed in exactly 2 roles).

Noise/sparse flags (single_role, single_observation, long_phrase, \
possible_fragment, employer_or_process_specific, malformed) are advisory \
only — deterministic, lexical rules, never an LLM judgement and never an \
automatic rejection."""
