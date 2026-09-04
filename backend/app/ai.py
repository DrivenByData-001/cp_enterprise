"""
Central AI task abstraction (native task layer).

A small, provider-agnostic way to run one AI extraction task: load a
versioned prompt from `prompts/`, call the configured model, and validate
its JSON output into a caller-supplied Pydantic model. OpenAI is the current
provider — swapping providers later means changing `_client()`/`run_json_task()`
here, not touching call sites.

See docs/13-ai-task-layer.md for the architecture and the Phase 2
(`extraction_run`) integration plan.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generic, TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"

_DEFAULT_SYSTEM_PROMPT = (
    "You are a precise information-extraction engine. "
    "Return only valid JSON matching the requested schema."
)


class AITaskError(RuntimeError):
    """Base class for all native AI task layer failures."""


class AIConfigError(AITaskError):
    """Missing credentials, missing/invalid model configuration, or an unknown prompt."""


class AIProviderError(AITaskError):
    """The provider was unreachable or returned an error."""


class AIResponseFormatError(AITaskError):
    """The model's response text was not valid JSON."""


class AISchemaValidationError(AITaskError):
    """The model's JSON did not satisfy the requested schema."""


@dataclass(frozen=True)
class AITaskRun:
    """
    Metadata about one AI task execution.

    This is intentionally *not* persisted anywhere yet — Phase 1 has no
    `role_instance`/`document` row to hang an `extraction_run` off for a
    posting import. It carries exactly the fields `extraction_run` (see
    docs/11-capability-model-design.md §4.1) needs, so Phase 2 can persist
    a run by writing these fields into that table rather than inventing a
    new shape. See docs/13-ai-task-layer.md.
    """

    task: str
    model: str
    prompt_name: str
    prompt_version: str
    started_at: str
    finished_at: str
    status: str  # ok | failed
    input_chars: int
    output_chars: int


@dataclass(frozen=True)
class AITaskResult(Generic[T]):
    output: T
    run: AITaskRun


def ai_model_name() -> str:
    model = os.getenv("CP_AI_MODEL")
    if not model:
        raise AIConfigError("CP_AI_MODEL is not set")
    return model


def _client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise AIConfigError("OPENAI_API_KEY is not set")
    return OpenAI()


def load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.is_file():
        raise AIConfigError(f"Prompt '{name}' was not found in {PROMPT_DIR}")
    return path.read_text(encoding="utf-8")


def prompt_version(prompt_text: str) -> str:
    """
    A prompt's version is a hash of its own content: versioned and
    inspectable with no separate bookkeeping to fall out of sync. Any edit
    to the prompt file is automatically a new version.
    """
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:12]


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def run_json_task(
    *,
    task: str,
    prompt_name: str,
    user_input: str,
    output_model: type[T],
    max_tokens: int = 8192,
) -> AITaskResult[T]:
    """
    Execute one AI task end to end: load the named prompt from `prompts/`,
    call the configured model with `user_input` appended, and validate the
    JSON it returns into `output_model`.

    `task` is a short label (e.g. "job_posting_extract") carried on the
    returned run metadata for future traceability — it does not affect
    execution.

    Raises one of AIConfigError / AIProviderError / AIResponseFormatError /
    AISchemaValidationError on failure — never a raw provider, JSON, or
    Pydantic exception — so callers can map each failure mode to a clear
    response.
    """
    prompt_text = load_prompt(prompt_name)
    model = ai_model_name()
    client = _client()
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        response = client.chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"{prompt_text}\n\n---\n\nINPUT TO PROCESS:\n{user_input}",
                }
            ],
            response_format={"type": "json_object"},
        )
    except openai.APIError as e:
        raise AIProviderError(f"OpenAI API error: {e}") from e

    raw_text = response.choices[0].message.content or ""
    text = _strip_code_fence(raw_text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIResponseFormatError(f"Model response was not valid JSON: {e}") from e

    try:
        output = output_model.model_validate(data)
    except ValidationError as e:
        raise AISchemaValidationError(f"Model response did not match {output_model.__name__}: {e}") from e

    finished_at = datetime.now(timezone.utc).isoformat()
    run = AITaskRun(
        task=task,
        model=model,
        prompt_name=prompt_name,
        prompt_version=prompt_version(prompt_text),
        started_at=started_at,
        finished_at=finished_at,
        status="ok",
        input_chars=len(user_input),
        output_chars=len(text),
    )
    return AITaskResult(output=output, run=run)
