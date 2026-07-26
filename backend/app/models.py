from typing import Optional

from pydantic import BaseModel


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


class ProfileUpdate(BaseModel):
    narrative_text: str


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


class EpisodeCreate(BaseModel):
    kind: str  # employment | project | study | qualification | other
    title: str
    organisation: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    date_precision: str = "month"  # day | month | year
    parent_episode_id: Optional[int] = None
    domain_hint: Optional[str] = None
    context_note: Optional[str] = None


class EpisodeUpdate(EpisodeCreate):
    pass
