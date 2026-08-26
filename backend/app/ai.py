import json
import os
from pathlib import Path
from typing import TypeVar

from anthropic import Anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"


class AIConfigError(RuntimeError):
    pass


def ai_model_name() -> str:
    model = os.getenv("CP_AI_MODEL")
    if not model:
        raise AIConfigError("CP_AI_MODEL is not set")
    return model


def _client() -> Anthropic:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise AIConfigError("ANTHROPIC_API_KEY is not set")
    return Anthropic()


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def run_json_task(*, prompt: str, user_input: str, output_model: type[T], max_tokens: int = 8192) -> T:
    response = _client().messages.create(
        model=ai_model_name(),
        max_tokens=max_tokens,
        system="You are a precise information-extraction engine. Return only valid JSON matching the requested schema.",
        messages=[
            {
                "role": "user",
                "content": f"{prompt}\n\n---\n\nINPUT TO PROCESS:\n{user_input}",
            }
        ],
    )
    text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return output_model.model_validate(json.loads(text))
