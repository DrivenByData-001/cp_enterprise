"""Shared `jobber.role_instance` column construction for a validated
`JobPostingImport`, factored out of `routes/import_routes.py::posting_columns`
so every import path (legacy JSON, bulk JSON, native AI extraction, and the
raw-document processing pipeline in `app/document_processing.py`) builds the
same flat column dict from the same model, rather than three copies of the
`legacy_scores`/`legacy_analysis` packing logic drifting apart (docs/17 §10).

Deliberately does not touch `jobber.document` at all: some callers create a
brand-new document from AI-composed text (the legacy/native import paths —
`posting_columns` below still does this), others must reuse an existing
immutable document and must never call `create_document()` again
(`document_processing.py::process_job_posting_document` — see docs/17 §10 for
why that distinction matters). `document_id` is therefore always supplied by
the caller, never decided here.
"""

import json

from .models import JobPostingImport


def posting_role_columns(payload: JobPostingImport, document_id: str | None) -> dict:
    """The flat `jobber.role_instance` column dict `upsert_role_instance`
    expects for an `observed_posting`, built from a validated
    `JobPostingImport` — production's real columns (docs/14 §5), including
    packing the pre-capability-model scores/analysis fields into
    `legacy_scores`/`legacy_analysis` JSONB since production has no
    individual column per score. `legacy_analysis["raw_json"]` keeps the full
    validated payload for backward compatibility, even though
    `extraction_run.output_payload` is now the authoritative run-specific
    copy (docs/17 §7/§10).
    """
    job, meta, analysis = payload.job, payload.metadata, payload.analysis

    legacy_scores = {
        "seniority_score": analysis.seniority_score,
        "complexity_score": analysis.complexity_score,
        "specialisation_score": analysis.specialisation_score,
        "transferability_score": analysis.transferability_score,
        "market_demand_score": analysis.market_demand_score,
        "rarity_score": analysis.rarity_score,
        "automation_risk_score": analysis.automation_risk_score,
    }
    legacy_analysis = {
        "top_adjacent_roles": analysis.top_adjacent_roles,
        "key_skills_summary": analysis.key_skills_summary,
        "notes": analysis.notes,
        "raw_json": json.loads(payload.model_dump_json()),
    }

    return {
        "instance_type": "observed_posting",
        "target_basis": None,
        "document_id": document_id,
        "title": job.title,
        "organisation": job.organisation,
        "location": job.location,
        "country": job.country,
        "remote_type": job.remote_type,
        "employment_type": job.employment_type,
        "seniority_level": job.seniority_level,
        "posting_date": job.posting_date,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_estimate_min": analysis.salary_estimate_min,
        "salary_estimate_max": analysis.salary_estimate_max,
        "currency": job.currency,
        "description": job.description,
        "requirements": job.requirements,
        "responsibilities": job.responsibilities,
        "summary": analysis.summary,
        "career_track": analysis.career_track,
        "legacy_scores": legacy_scores,
        "legacy_analysis": legacy_analysis,
        "extraction_status": meta.extraction_status,
        "extraction_notes": meta.notes_for_user,
    }
