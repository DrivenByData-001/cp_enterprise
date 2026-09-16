from datetime import date as _date
from typing import Literal, Optional

from pydantic import BaseModel, model_validator, field_validator
from uuid import UUID


class Skill(BaseModel):
    name: str
    category: Optional[str] = None
    importance: Optional[int] = None
    requirement_type: Optional[str] = None


class Metadata(BaseModel):
    captured_at: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    extraction_status: Optional[str] = "ok"
    notes_for_user: Optional[str] = None


class Job(BaseModel):
    title: str
    organisation: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    remote_type: Optional[str] = None
    posting_date: Optional[str] = None
    employment_type: Optional[str] = None
    seniority_level: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    currency: Optional[str] = None
    description: Optional[str] = None
    requirements: Optional[str] = None
    responsibilities: Optional[str] = None


class Analysis(BaseModel):
    summary: Optional[str] = None
    career_track: Optional[str] = None
    seniority_score: Optional[float] = None
    complexity_score: Optional[float] = None
    specialisation_score: Optional[float] = None
    transferability_score: Optional[float] = None
    salary_estimate_min: Optional[float] = None
    salary_estimate_max: Optional[float] = None
    market_demand_score: Optional[float] = None
    rarity_score: Optional[float] = None
    automation_risk_score: Optional[float] = None
    top_adjacent_roles: Optional[list[str]] = None
    key_skills_summary: Optional[str] = None
    notes: Optional[str] = None


class JobPostingImport(BaseModel):
    metadata: Metadata
    job: Job
    skills: list[Skill] = []
    analysis: Analysis = Analysis()


class SkillDecompositionItem(BaseModel):
    skill: str
    examples: list[str] = []


class TechnicalSubjectItem(BaseModel):
    subject: str
    why: Optional[str] = None
    resources: list[str] = []


class TargetRole(BaseModel):
    title: str
    organisation: Optional[str] = None
    is_imagined: bool = False
    career_track: Optional[str] = None
    seniority_level: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    typical_tasks: list[str] = []
    skill_decomposition: list[SkillDecompositionItem] = []
    technical_subjects: list[TechnicalSubjectItem] = []
    grounding_note: Optional[str] = None
    feasibility_note: Optional[str] = None
    is_plausible: Optional[bool] = None


class TargetRequirement(Skill):
    concept_id: Optional[str] = None
    mapping_reviewed: bool = False

    @field_validator("concept_id")
    @classmethod
    def valid_concept_id(cls, value):
        return str(UUID(value)) if value is not None else None


class TargetImport(BaseModel):
    metadata: Metadata
    target: TargetRole
    skills: list[TargetRequirement] = []


class ConceptCreate(BaseModel):
    type_code: str
    canonical_name: str
    definition: Optional[str] = None
    status: str = "active"  # curator-created concepts are usable immediately


# --- Accepted-vocabulary maintenance (cp_round_of_changes.md §B) -----------
#
# Curator editing of an already-accepted concept: canonical name, type,
# definition, active/deprecated status, and (independently) aliases. Distinct
# from ConceptCreate above, which creates a brand-new concept.

class CapabilityDetailInput(BaseModel):
    """Required capability_detail state when a type change moves a concept
    *into* `type_code='capability'` (app/concept_curation.py) — the same
    fields CapabilityCreate/CapabilityUpdate accept, since it's the same
    underlying jobber.capability_detail row."""

    demonstration_standard: str
    min_depth: str = "owned"
    min_autonomy: Optional[str] = None
    requires_all_core: bool = True
    min_core_required: Optional[int] = None
    economic_salience: Optional[str] = None
    notes: Optional[str] = None


class ConceptMetadataUpdate(BaseModel):
    """PATCH /api/concepts/{id}. Every field is optional — only supplied
    fields are changed (app/concept_curation.py reads `exclude_unset`, not
    plain None-checks, so explicitly clearing `definition` to null is still
    possible while an omitted field is left alone). `capability_detail` is
    only consulted when `type_code` is changing *into* 'capability' — it is
    not a general capability-detail editor (that's PUT /api/capabilities/{id})."""

    canonical_name: Optional[str] = None
    type_code: Optional[str] = None
    definition: Optional[str] = None
    status: Optional[str] = None  # active | deprecated — concepts are never hard-deleted, see app/concept_curation.py
    capability_detail: Optional[CapabilityDetailInput] = None

    @model_validator(mode="after")
    def _check(self):
        if self.status is not None and self.status not in ("active", "deprecated"):
            raise ValueError("status must be one of active, deprecated")
        return self


class ConceptAliasCreate(BaseModel):
    alias: str

    @model_validator(mode="after")
    def _check(self):
        if not self.alias.strip():
            raise ValueError("alias must not be empty")
        return self


class ProposalResolve(BaseModel):
    surface_form: str
    action: str  # accept_new | accept_alias | reject | defer
    type_code: Optional[str] = None       # required if action == accept_new
    canonical_name: Optional[str] = None  # required if action == accept_new
    definition: Optional[str] = None
    concept_id: Optional[str] = None      # required if action == accept_alias

    @model_validator(mode="after")
    def _check_action_fields(self):
        if self.action == "accept_new" and not (self.type_code and self.canonical_name):
            raise ValueError("accept_new requires type_code and canonical_name")
        if self.action == "accept_alias" and not self.concept_id:
            raise ValueError("accept_alias requires concept_id")
        if self.action not in ("accept_new", "accept_alias", "reject", "defer"):
            raise ValueError("action must be one of accept_new, accept_alias, reject, defer")
        return self


class ClusterProposalResolve(BaseModel):
    """Same shape/semantics as ProposalResolve, keyed by cluster_key instead
    of one exact surface_form — resolves every pending proposal sharing that
    cluster (docs/18 §3) in one action, e.g. accepting "Solvency II" also
    resolves the "SII" proposal in the same cluster to the same concept."""

    cluster_key: str
    action: str
    type_code: Optional[str] = None
    canonical_name: Optional[str] = None
    definition: Optional[str] = None
    concept_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_action_fields(self):
        if self.action == "accept_new" and not (self.type_code and self.canonical_name):
            raise ValueError("accept_new requires type_code and canonical_name")
        if self.action == "accept_alias" and not self.concept_id:
            raise ValueError("accept_alias requires concept_id")
        if self.action not in ("accept_new", "accept_alias", "reject", "defer"):
            raise ValueError("action must be one of accept_new, accept_alias, reject, defer")
        return self


# --- Vocabulary curation (Vocabulary Proposal Prioritisation and Curation UX) --

class ClusterAcceptRequest(BaseModel):
    cluster_key: str
    type_code: str
    canonical_name: str
    definition: Optional[str] = None


class ClusterRejectRequest(BaseModel):
    cluster_key: str


class ClusterMergeRequest(BaseModel):
    cluster_key: str
    concept_id: str


class BatchAcceptItem(BaseModel):
    cluster_key: str
    canonical_name: Optional[str] = None  # required for action=accept
    type_code: Optional[str] = None       # required for action=accept
    definition: Optional[str] = None


class ClusterBatchRequest(BaseModel):
    action: str  # accept | reject
    items: list[BatchAcceptItem]

    @model_validator(mode="after")
    def _check(self):
        if self.action not in ("accept", "reject"):
            raise ValueError("action must be one of accept, reject")
        if not self.items:
            raise ValueError("items must not be empty")
        return self


class ClusterSplitGroup(BaseModel):
    surface_forms: list[str]


class ClusterSplitRequest(BaseModel):
    cluster_key: str
    groups: list[ClusterSplitGroup]

    @model_validator(mode="after")
    def _check(self):
        if len(self.groups) < 2:
            raise ValueError("a split must produce at least 2 groups")
        if any(not g.surface_forms for g in self.groups):
            raise ValueError("every group must contain at least one surface form")
        flat = [sf for g in self.groups for sf in g.surface_forms]
        if len(flat) != len(set(flat)):
            raise ValueError("each surface form may appear in exactly one group")
        return self


# --- Phase 2: AI extraction task schemas (backend/app/extraction.py) --------
#
# These are output_model schemas for app.ai.run_json_task, not API
# request/response models — kept here for consistency with JobPostingImport/
# TargetImport above, which already serve the same dual purpose.

class RoleMetadataUpdate(BaseModel):
    """PATCH /api/role-instances/{id}/metadata — the source-aware counterpart
    to the legacy full-JobPostingImport overwrite (PUT /api/roles/{id}),
    used both for manual Edit on a role with no `raw_json` and for 'Accept
    metadata' after a proposed enrichment review (source-aware ingest
    cleanup, problems #5/#7). Every field optional; only supplied fields
    change (app/db.py's update_role_metadata reads `exclude_unset`) — an
    omitted field is left exactly as stored, and an explicit `null` clears
    it. Never touches skills, the linked document, or requirement claims."""

    title: Optional[str] = None
    organisation: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    remote_type: Optional[str] = None
    employment_type: Optional[str] = None
    seniority_level: Optional[str] = None
    posting_date: Optional[str] = None


class RoleMetadataProposal(BaseModel):
    """AI output schema for the lightweight metadata-enrichment task
    (app/metadata_enrichment.py, prompts/enrich_role_metadata.md) — a small,
    focused sibling of JobPostingImport.Job that proposes only the fields a
    source-aware capture leaves blank, never the full description/
    requirements/skills extraction. Every field must come from the source
    text itself (or stay null) — `posting_date` in particular must never be
    filled from the capture/upload date, only from a date actually stated in
    the source (enforced again deterministically in
    metadata_enrichment.py, not just left to the prompt)."""

    title: Optional[str] = None
    organisation: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    remote_type: Optional[str] = None
    employment_type: Optional[str] = None
    seniority_level: Optional[str] = None
    posting_date: Optional[str] = None


class RequirementItem(BaseModel):
    surface_form: str
    requirement_type: str  # required | preferred | contextual
    basis: str             # stated | implied
    importance: Optional[int] = None
    evidence_span: str


class RequirementExtractionResult(BaseModel):
    requirements: list[RequirementItem] = []


class ConceptAdjudicationDecision(BaseModel):
    item_index: int
    chosen_canonical_name: Optional[str] = None
    reasoning: Optional[str] = None


class ConceptAdjudicationResult(BaseModel):
    decisions: list[ConceptAdjudicationDecision] = []


class ClaimMappingResult(BaseModel):
    chosen_canonical_name: Optional[str] = None
    reasoning: Optional[str] = None


# --- Phase 3: capability catalogue curation (backend/app/routes/capabilities.py) --

_DEPTH_LEVELS = ("exposed", "applied", "owned", "set_standard")
_AUTONOMY_LEVELS = ("assisted", "independent", "directed_others", "accountable")
_NECESSITY_LEVELS = ("core", "supporting", "contextual")


class CapabilitySpecification(BaseModel):
    demonstration_standard: str
    min_depth: str = "owned"
    min_autonomy: Optional[str] = None
    requires_all_core: bool = True
    min_core_required: Optional[int] = None
    economic_salience: Optional[str] = None  # catalogue metadata only — never drives Phase 3 results (brief §5)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.min_depth not in _DEPTH_LEVELS:
            raise ValueError(f"min_depth must be one of {_DEPTH_LEVELS}")
        if self.min_autonomy is not None and self.min_autonomy not in _AUTONOMY_LEVELS:
            raise ValueError(f"min_autonomy must be one of {_AUTONOMY_LEVELS}")
        if self.economic_salience is not None and self.economic_salience not in ("low", "medium", "high"):
            raise ValueError("economic_salience must be one of low, medium, high")
        return self


class CapabilityConfigure(CapabilitySpecification):
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _complete_specification(self):
        if not self.demonstration_standard.strip():
            raise ValueError("demonstration_standard must not be blank")
        if self.min_core_required is not None and self.min_core_required < 0:
            raise ValueError("min_core_required must be nonnegative")
        return self


class CapabilityCreate(CapabilitySpecification):
    canonical_name: str
    definition: Optional[str] = None
    status: str = "active"

    @model_validator(mode="after")
    def _check_status(self):
        if self.status not in ("proposed", "active", "deprecated", "rejected"):
            raise ValueError("status must be one of proposed, active, deprecated, rejected")
        return self


class CapabilityUpdate(BaseModel):
    canonical_name: Optional[str] = None
    definition: Optional[str] = None
    demonstration_standard: Optional[str] = None
    min_depth: Optional[str] = None
    min_autonomy: Optional[str] = None
    requires_all_core: Optional[bool] = None
    min_core_required: Optional[int] = None
    economic_salience: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.min_depth is not None and self.min_depth not in _DEPTH_LEVELS:
            raise ValueError(f"min_depth must be one of {_DEPTH_LEVELS}")
        if self.min_autonomy is not None and self.min_autonomy not in _AUTONOMY_LEVELS:
            raise ValueError(f"min_autonomy must be one of {_AUTONOMY_LEVELS}")
        if self.economic_salience is not None and self.economic_salience not in ("low", "medium", "high"):
            raise ValueError("economic_salience must be one of low, medium, high")
        if self.status is not None and self.status not in ("proposed", "active", "deprecated", "rejected"):
            raise ValueError("status must be one of proposed, active, deprecated, rejected")
        return self


class CapabilityMerge(BaseModel):
    merge_into_id: str


class ComponentEdgeCreate(BaseModel):
    concept_id: str
    necessity: str

    @model_validator(mode="after")
    def _check(self):
        if self.necessity not in _NECESSITY_LEVELS:
            raise ValueError(f"necessity must be one of {_NECESSITY_LEVELS}")
        return self


class ComponentEdgeUpdate(BaseModel):
    necessity: str

    @model_validator(mode="after")
    def _check(self):
        if self.necessity not in _NECESSITY_LEVELS:
            raise ValueError(f"necessity must be one of {_NECESSITY_LEVELS}")
        return self


class ComponentEdgeReview(BaseModel):
    action: str  # accept | reject

    @model_validator(mode="after")
    def _check(self):
        if self.action not in ("accept", "reject"):
            raise ValueError("action must be one of accept, reject")
        return self


# --- Day-in-the-Life / Role Context enrichment (backend/app/role_context.py) --
#
# The AI output schema for role_context_generate: a role-side market/context
# enrichment, never person-side evidence (brief §6.1/§6.2). Every claim must
# be labelled `advert_grounded` (directly supported by the source posting) or
# `inferred` (reasonable occupational inference, never presented as if
# stated) — enforced here via Literal rather than free text, so a model
# response using any other label fails validation instead of silently
# passing through unlabelled.

Basis = Literal["advert_grounded", "inferred"]
Confidence = Literal["high", "medium", "low"]


class DayInLifeItem(BaseModel):
    time_or_phase: str
    activity: str
    detail: Optional[str] = None
    basis: Basis
    confidence: Confidence = "medium"


class TypicalWeekItem(BaseModel):
    day_or_theme: str
    activity: str
    detail: Optional[str] = None
    basis: Basis
    confidence: Confidence = "medium"


class TeamSizeEstimate(BaseModel):
    min: Optional[int] = None
    max: Optional[int] = None
    basis: Basis = "inferred"
    confidence: Confidence = "medium"


class GroundedNote(BaseModel):
    text: str
    basis: Basis
    confidence: Confidence = "medium"


class TeamContext(BaseModel):
    expected_team_size: Optional[TeamSizeEstimate] = None
    team_work: list[GroundedNote] = []


class ManagerContext(BaseModel):
    likely_manager_title: Optional[str] = None
    title_basis: Optional[Basis] = None
    dynamic: Optional[str] = None
    dynamic_basis: Optional[Basis] = None
    dynamic_confidence: Confidence = "medium"


class CareerStep(BaseModel):
    step: str
    basis: Basis
    confidence: Confidence = "medium"


class GroundingSummary(BaseModel):
    advert_grounded_points: list[str] = []
    inferred_points: list[str] = []


class RoleContextGeneration(BaseModel):
    """The full structured output `role_context.py::run_json_task` validates
    the model's response into. Deliberately several small typed sections
    rather than one untyped blob (brief §6.5), so provenance/basis is
    available at the granularity of each individual claim, not just once for
    the whole response."""

    day_in_life: list[DayInLifeItem] = []
    typical_week: list[TypicalWeekItem] = []
    team_context: TeamContext = TeamContext()
    manager_context: ManagerContext = ManagerContext()
    stakeholders: list[GroundedNote] = []
    career_progression: list[CareerStep] = []
    grounding_summary: GroundingSummary = GroundingSummary()
    caveats: Optional[str] = None


# --- Concept Dossier (backend/app/concept_dossier.py) -----------------------
#
# The AI output schema for concept_dossier_generate: a curator-facing
# explanatory dossier for one accepted canonical concept (cp_round_of_changes
# .md §C/§D/§E/§F). `related_concepts` is an explanatory annotation only — it
# is never written as a formal jobber.concept_edge row (§C: "must not
# confuse a dossier's explanatory decomposition with formal ontology
# edges"), and every `concept_id` the model returns is filtered, after
# validation, down to only the candidate ids it was actually offered (§F:
# "ONLY among real accepted canonical concepts supplied as candidates") —
# app/concept_dossier.py does that filtering; nothing here can enforce it at
# the schema level since a concept_id is just a string.

class RelatedConceptSuggestion(BaseModel):
    concept_id: str
    relationship: str  # e.g. related to | informs | used in | broader/narrower | commonly combined with
    explanation: str


class ConceptDossierGeneration(BaseModel):
    plain_definition: str
    classification_rationale: str = ""
    practical_meaning: str
    underlying_elements: list[str] = []
    stronger_expressions: list[str] = []
    weaker_expressions: list[str] = []
    boundaries_and_overlaps: str = ""
    related_concepts: list[RelatedConceptSuggestion] = []
    caveats: Optional[str] = None


class ConceptDossierGenerateRequest(BaseModel):
    """Body for both POST .../dossier/generate and .../dossier/regenerate —
    the optional "Guide the AI" input (§D), persisted alongside whichever
    version it produced."""

    guidance: Optional[str] = None


# --- Phase 4: role archetypes (backend/app/routes/archetypes.py) -----------
#
# A role archetype is one jobber.concept row (type_code='role_archetype')
# plus one role_archetype_detail row, created/edited together — the same
# two-row-created-together pattern CapabilityCreate/CapabilityUpdate already
# use for capability_detail (prompt §3).

class ArchetypeCreate(BaseModel):
    canonical_name: str
    seniority_band: Optional[str] = None
    primary_function_concept_id: Optional[str] = None
    typical_market: Optional[str] = None
    notes: Optional[str] = None
    role_instance_ids: list[str] = []


class ArchetypeUpdate(BaseModel):
    canonical_name: Optional[str] = None
    seniority_band: Optional[str] = None
    primary_function_concept_id: Optional[str] = None
    typical_market: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None  # active | deprecated — never hard-deleted

    @model_validator(mode="after")
    def _check(self):
        if self.status is not None and self.status not in ("active", "deprecated"):
            raise ValueError("status must be one of active, deprecated")
        return self


class ArchetypeAssign(BaseModel):
    role_instance_ids: list[str]

    @model_validator(mode="after")
    def _check(self):
        if not self.role_instance_ids:
            raise ValueError("role_instance_ids must not be empty")
        return self


# --- Phase 4: market dimension (backend/app/routes/economics.py) -----------

class MarketCreate(BaseModel):
    code: str
    label: str
    country: Optional[str] = None
    geography: Optional[str] = None
    domain_concept_id: Optional[str] = None
    notes: Optional[str] = None


class MarketUpdate(BaseModel):
    label: Optional[str] = None
    country: Optional[str] = None
    geography: Optional[str] = None
    domain_concept_id: Optional[str] = None
    status: Optional[str] = None  # active | deprecated
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.status is not None and self.status not in ("active", "deprecated"):
            raise ValueError("status must be one of active, deprecated")
        return self


# --- Phase 4: compensation observations -------------------------------------


def _validate_iso_date(value: str, field_name: str) -> None:
    """Validate supplied dates safely (prompt §2) — a malformed date string
    would otherwise only surface as an opaque database error at INSERT/
    UPDATE time. Never invents or corrects a date; only rejects one that
    isn't genuinely ISO 8601 (YYYY-MM-DD)."""
    try:
        _date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field_name} must be an ISO 8601 date (YYYY-MM-DD), got {value!r}") from None


_COMPENSATION_COMPONENTS = ("base", "bonus_pct", "total_package", "day_rate")
_PAY_PERIODS = ("annual", "daily")
_EMPLOYMENT_BASES = ("permanent", "contract", "unknown")


class CompensationObservationCreate(BaseModel):
    """Curator-asserted compensation observation — the one path that lets a
    human directly record a benchmark with no posting/survey document behind
    it (basis is always forced to 'curator_asserted' server-side, never
    accepted from the payload, per prompt §16 "every economic fact has a
    source" — this endpoint IS that source)."""

    role_instance_id: Optional[str] = None
    archetype_concept_id: Optional[str] = None
    raw_role_label: Optional[str] = None
    market_id: str
    component: str
    pay_period: str
    employment_basis: Optional[str] = None
    amount_min: Optional[float] = None
    amount_mid: Optional[float] = None
    amount_max: Optional[float] = None
    currency: str
    reported_p25: Optional[float] = None
    reported_p50: Optional[float] = None
    reported_p75: Optional[float] = None
    bonus_pct: Optional[float] = None
    observed_at: Optional[str] = None
    reported_sample_size: Optional[int] = None
    source_note: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.component not in _COMPENSATION_COMPONENTS:
            raise ValueError(f"component must be one of {_COMPENSATION_COMPONENTS}")
        if self.pay_period not in _PAY_PERIODS:
            raise ValueError(f"pay_period must be one of {_PAY_PERIODS}")
        if self.employment_basis is not None and self.employment_basis not in _EMPLOYMENT_BASES:
            raise ValueError(f"employment_basis must be one of {_EMPLOYMENT_BASES}")
        if not (self.role_instance_id or self.archetype_concept_id or self.raw_role_label):
            raise ValueError("one of role_instance_id, archetype_concept_id, raw_role_label is required")
        return self


class CompensationObservationReview(BaseModel):
    action: str  # accept | reject

    @model_validator(mode="after")
    def _check(self):
        if self.action not in ("accept", "reject"):
            raise ValueError("action must be one of accept, reject")
        return self


class CompensationObservationCorrect(BaseModel):
    """PATCH .../compensation-observations/{id} — correcting extracted
    numeric/assignment fields before or after review (prompt §8's "correct
    extracted numeric fields", "assign/change role archetype",
    "assign/change market"). Every field optional; only supplied fields
    change."""

    archetype_concept_id: Optional[str] = None
    market_id: Optional[str] = None
    raw_role_label: Optional[str] = None
    component: Optional[str] = None
    pay_period: Optional[str] = None
    employment_basis: Optional[str] = None
    amount_min: Optional[float] = None
    amount_mid: Optional[float] = None
    amount_max: Optional[float] = None
    currency: Optional[str] = None
    reported_p25: Optional[float] = None
    reported_p50: Optional[float] = None
    reported_p75: Optional[float] = None
    reported_mean: Optional[float] = None
    bonus_pct: Optional[float] = None
    reported_sample_size: Optional[int] = None
    geography_reported: Optional[str] = None
    domain_or_practice_area: Optional[str] = None
    seniority_band_reported: Optional[str] = None
    experience_band: Optional[str] = None
    pqe_band: Optional[str] = None
    source_kind: Optional[str] = None
    source_quality: Optional[str] = None
    source_note: Optional[str] = None
    # A curator correcting a genuinely wrong/missing report date (prompt
    # §2) — never set automatically here; extraction sets these from the
    # source document's own source_date (market_data_processing.py).
    observed_at: Optional[str] = None
    period_end: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.component is not None and self.component not in _COMPENSATION_COMPONENTS:
            raise ValueError(f"component must be one of {_COMPENSATION_COMPONENTS}")
        if self.pay_period is not None and self.pay_period not in _PAY_PERIODS:
            raise ValueError(f"pay_period must be one of {_PAY_PERIODS}")
        if self.employment_basis is not None and self.employment_basis not in _EMPLOYMENT_BASES:
            raise ValueError(f"employment_basis must be one of {_EMPLOYMENT_BASES}")
        if self.observed_at is not None:
            _validate_iso_date(self.observed_at, "observed_at")
        if self.period_end is not None:
            _validate_iso_date(self.period_end, "period_end")
        return self


# --- Phase 4: market survey document ingestion (backend/app/routes/market_data.py) --

class MarketDataIngest(BaseModel):
    text: str
    publisher: Optional[str] = None
    report_title: Optional[str] = None
    report_date: Optional[str] = None
    source_url: Optional[str] = None
    methodology_notes: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.report_date is not None:
            _validate_iso_date(self.report_date, "report_date")
        return self


# --- Phase 4: derived-table rebuild scoping ---------------------------------

class GapValueQuery(BaseModel):
    market_id: str
    currency: str


# --- Phase 4: market survey AI extraction (backend/app/market_data_processing.py) --
#
# Output schema for compensation_extract. Deliberately loose/optional on
# every field — "do not infer missing sample sizes or precise percentiles"
# (prompt §8) — validated against the DB's controlled vocab only at
# persistence time; an item whose component/pay_period/currency cannot be
# recognised is dropped rather than guessed.

class MarketSurveyExtractionItem(BaseModel):
    raw_role_label: Optional[str] = None
    geography: Optional[str] = None
    domain_or_practice_area: Optional[str] = None
    seniority_band: Optional[str] = None
    experience_band: Optional[str] = None
    pqe_band: Optional[str] = None
    source_kind: Optional[str] = None  # respondent_survey | recruiter_benchmark | other
    employment_basis: Optional[str] = None  # permanent | contract | unknown
    component: Optional[str] = None  # base | bonus_pct | total_package | day_rate
    pay_period: Optional[str] = None  # annual | daily
    amount_min: Optional[float] = None
    amount_mid: Optional[float] = None
    amount_max: Optional[float] = None
    currency: Optional[str] = None
    reported_p25: Optional[float] = None
    reported_p50: Optional[float] = None
    reported_p75: Optional[float] = None
    reported_mean: Optional[float] = None
    bonus_pct: Optional[float] = None
    reported_sample_size: Optional[int] = None
    page_reference: Optional[str] = None
    table_reference: Optional[str] = None
    source_note: Optional[str] = None


class MarketSurveyExtractionResult(BaseModel):
    items: list[MarketSurveyExtractionItem] = []
    methodology_notes: Optional[str] = None


class ConceptDossierManualEdit(BaseModel):
    """PUT .../dossier — a curator's direct edit of the active dossier's
    content. Deliberately excludes `related_concepts`: those are an AI
    suggestion surface (§F), not a manually-curated field on this endpoint.
    Every field optional; only supplied fields change (app/concept_dossier.py
    reads `exclude_unset`), the rest carried over unchanged from the version
    being superseded."""

    plain_definition: Optional[str] = None
    classification_rationale: Optional[str] = None
    practical_meaning: Optional[str] = None
    underlying_elements: Optional[list[str]] = None
    stronger_expressions: Optional[list[str]] = None
    weaker_expressions: Optional[list[str]] = None
    boundaries_and_overlaps: Optional[str] = None
    caveats: Optional[str] = None
