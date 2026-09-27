"""Phase 4: Application artifact generation API (docs/35). Mounted under the
same /api/applications prefix as routes/applications.py, as its own router —
same "one small dedicated router per sub-feature" precedent
routes/concept_dossier.py already follows relative to routes/concepts.py.
Protected by the same central auth policy as every other router
(app/main.py)."""

from fastapi import APIRouter, HTTPException

from .. import application_artifacts as artifacts
from ..application_generation import ApplicationGenerationSubjectError
from ..db import db_cursor
from ..models import ApplicationArtifactEditRequest, ApplicationArtifactGenerateRequest

router = APIRouter(prefix="/api/applications", tags=["application-artifacts"])


@router.get("/{application_id}/artifacts")
def read_artifacts(application_id: str):
    """Never triggers generation — the one bounded read the workspace needs
    on load (build §13/§20)."""
    with db_cursor() as cur:
        try:
            return artifacts.list_artifacts(cur, application_id)
        except ApplicationGenerationSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.get("/{application_id}/artifacts/{artifact_type}/history")
def read_artifact_history(application_id: str, artifact_type: str):
    with db_cursor() as cur:
        try:
            return {"application_id": application_id, "artifact_type": artifact_type,
                     "history": artifacts.list_artifact_history(cur, application_id, artifact_type)}
        except (ApplicationGenerationSubjectError, artifacts.ApplicationArtifactSubjectError) as e:
            raise HTTPException(404, str(e)) from e


@router.post("/{application_id}/artifacts/{artifact_type}/generate")
def generate_artifact(application_id: str, artifact_type: str, payload: ApplicationArtifactGenerateRequest = ApplicationArtifactGenerateRequest()):
    """Always calls the model and always produces/replaces only the draft
    (build §2/§16) — the frontend relabels this same action "Regenerate"
    once a draft or active version already exists; nothing here changes
    based on that."""
    try:
        return artifacts.generate_artifact(
            application_id, artifact_type, guidance=payload.guidance, target_words=payload.target_words,
        )
    except (ApplicationGenerationSubjectError, artifacts.ApplicationArtifactSubjectError) as e:
        raise HTTPException(404, str(e)) from e
    except artifacts.ApplicationArtifactValidationError as e:
        raise HTTPException(422, str(e)) from e
    except artifacts.ApplicationArtifactGenerationError as e:
        artifacts.raise_for_generation_error(e)


@router.post("/{application_id}/artifacts/{artifact_id}/edit")
def edit_artifact(application_id: str, artifact_id: str, payload: ApplicationArtifactEditRequest):
    try:
        return artifacts.edit_artifact(application_id, artifact_id, payload.content)
    except artifacts.ApplicationArtifactSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except artifacts.ApplicationArtifactStateError as e:
        raise HTTPException(409, str(e)) from e
    except artifacts.ApplicationArtifactValidationError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/{application_id}/artifacts/{artifact_id}/adopt")
def adopt_artifact(application_id: str, artifact_id: str):
    """Only a current draft belonging to this application can be adopted."""
    try:
        return artifacts.adopt_artifact(application_id, artifact_id)
    except artifacts.ApplicationArtifactStateError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/{application_id}/artifacts/{artifact_id}/discard")
def discard_artifact(application_id: str, artifact_id: str):
    """Only a draft can be discarded/superseded."""
    try:
        return artifacts.discard_artifact(application_id, artifact_id)
    except artifacts.ApplicationArtifactStateError as e:
        raise HTTPException(409, str(e)) from e
