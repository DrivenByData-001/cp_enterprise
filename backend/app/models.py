from typing import Optional

from pydantic import BaseModel, model_validator


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


class TargetImport(BaseModel):
    metadata: Metadata
    target: TargetRole
    skills: list[Skill] = []


class ConceptCreate(BaseModel):
    type_code: str
    canonical_name: str
    definition: Optional[str] = None
    status: str = "active"  # curator-created concepts are usable immediately


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


# --- Phase 2: AI extraction task schemas (backend/app/extraction.py) --------
#
# These are output_model schemas for app.ai.run_json_task, not API
# request/response models — kept here for consistency with JobPostingImport/
# TargetImport above, which already serve the same dual purpose.

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
