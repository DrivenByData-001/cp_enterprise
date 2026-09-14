"""Audited AI target drafts. Preview never creates a role or accepts evidence."""
import json
from datetime import datetime, timezone

from .ai import AITaskError, AIConfigError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import create_document, db_cursor, to_json_param
from .models import TargetImport

PROMPT = "decompose_target_role.md"


def preview_target(payload):
    text = json.dumps(payload.model_dump(), ensure_ascii=False)
    with db_cursor() as cur:
        document_id, _ = create_document(cur, kind="narrative", content_text=text,
                                        provenance_quality="unknown", title=payload.title,
                                        source="target_draft", content_kind="source")
    started = datetime.now(timezone.utc)
    try:
        model = ai_model_name()
        version = prompt_version(load_prompt(PROMPT))
    except AIConfigError:
        model, version = "unconfigured", "unknown"
    output, error, error_type, output_chars = None, None, None, None
    try:
        result = run_json_task(task="target_decompose", prompt_name=PROMPT,
                               user_input=text, output_model=TargetImport)
        output = result.output.model_dump(mode="json")
        # The model may propose wording, never a reviewed vocabulary decision.
        for skill in output["skills"]:
            skill["concept_id"] = None
            skill["mapping_reviewed"] = False
        # User intent is authoritative, including a real vs imagined choice.
        output["target"].update(title=payload.title, is_imagined=payload.is_imagined,
                                 organisation=payload.organisation)
        output["metadata"]["source"] = "ai_target_draft"
        model, version, output_chars = result.run.model, result.run.prompt_version, result.run.output_chars
    except AITaskError as exc:
        error, error_type = str(exc), type(exc).__name__
    status = "failed" if error else "ok"
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO jobber.extraction_run
            (task, subject_type, document_id, model, prompt_name, prompt_version,
             started_at, finished_at, status, input_chars, output_chars, output_payload, error_type, error_message)
            VALUES ('target_decompose', 'document', %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (document_id, model, PROMPT, version, started, status, len(text), output_chars,
              to_json_param(output), error_type, error))
        run_id = str(cur.fetchone()["id"])
    return {"status": status, "proposal": output, "error": error, "extraction_run_id": run_id}
