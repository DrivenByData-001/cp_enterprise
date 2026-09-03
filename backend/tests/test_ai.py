import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ai  # noqa: E402


class _Widget(BaseModel):
    name: str
    count: int


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _fake_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture(autouse=True)
def _prompt_dir(tmp_path, monkeypatch):
    """Point the prompt loader at a scratch dir so tests don't depend on prompts/."""
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "widget.md").write_text("Extract a widget.", encoding="utf-8")
    monkeypatch.setattr(ai, "PROMPT_DIR", prompt_dir)
    return prompt_dir


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch):
    monkeypatch.setenv("CP_AI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")


# --- missing configuration -------------------------------------------------


def test_missing_model_raises_config_error(monkeypatch):
    monkeypatch.delenv("CP_AI_MODEL", raising=False)
    with pytest.raises(ai.AIConfigError):
        ai.run_json_task(task="t", prompt_name="widget.md", user_input="x", output_model=_Widget)


def test_missing_api_key_raises_config_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ai.AIConfigError):
        ai.run_json_task(task="t", prompt_name="widget.md", user_input="x", output_model=_Widget)


def test_unknown_prompt_raises_config_error():
    with pytest.raises(ai.AIConfigError):
        ai.load_prompt("does_not_exist.md")


# --- successful parsing/validation, provider mocked -------------------------


def test_successful_task_returns_output_and_run_metadata(monkeypatch):
    monkeypatch.setattr(
        ai.OpenAI,
        "__init__",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        ai.OpenAI,
        "chat",
        property(lambda self: SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: _fake_response('{"name": "gizmo", "count": 3}')))),
        raising=False,
    )

    result = ai.run_json_task(task="widget_extract", prompt_name="widget.md", user_input="a gizmo, three of them", output_model=_Widget)

    assert result.output == _Widget(name="gizmo", count=3)
    assert result.run.task == "widget_extract"
    assert result.run.model == "gpt-4o-mini"
    assert result.run.prompt_name == "widget.md"
    assert result.run.status == "ok"
    assert result.run.prompt_version == ai.prompt_version("Extract a widget.")
    assert result.run.started_at <= result.run.finished_at


def test_response_wrapped_in_code_fence_is_stripped(monkeypatch):
    monkeypatch.setattr(ai.OpenAI, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(
        ai.OpenAI,
        "chat",
        property(
            lambda self: SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kw: _fake_response('```json\n{"name": "gizmo", "count": 1}\n```'))
            )
        ),
        raising=False,
    )

    result = ai.run_json_task(task="t", prompt_name="widget.md", user_input="x", output_model=_Widget)
    assert result.output == _Widget(name="gizmo", count=1)


# --- malformed JSON ----------------------------------------------------------


def test_malformed_json_raises_response_format_error(monkeypatch):
    monkeypatch.setattr(ai.OpenAI, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(
        ai.OpenAI,
        "chat",
        property(lambda self: SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: _fake_response("not json at all {")))),
        raising=False,
    )

    with pytest.raises(ai.AIResponseFormatError):
        ai.run_json_task(task="t", prompt_name="widget.md", user_input="x", output_model=_Widget)


# --- schema-invalid JSON ------------------------------------------------------


def test_schema_invalid_json_raises_schema_validation_error(monkeypatch):
    monkeypatch.setattr(ai.OpenAI, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(
        ai.OpenAI,
        "chat",
        # valid JSON, but "count" is missing and required
        property(lambda self: SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: _fake_response('{"name": "gizmo"}')))),
        raising=False,
    )

    with pytest.raises(ai.AISchemaValidationError):
        ai.run_json_task(task="t", prompt_name="widget.md", user_input="x", output_model=_Widget)


# --- provider errors -----------------------------------------------------------


def test_provider_error_raises_provider_error(monkeypatch):
    import openai

    def _boom(**kw):
        raise openai.APIConnectionError(request=SimpleNamespace())

    monkeypatch.setattr(ai.OpenAI, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(
        ai.OpenAI,
        "chat",
        property(lambda self: SimpleNamespace(completions=SimpleNamespace(create=_boom))),
        raising=False,
    )

    with pytest.raises(ai.AIProviderError):
        ai.run_json_task(task="t", prompt_name="widget.md", user_input="x", output_model=_Widget)


# --- prompt versioning ----------------------------------------------------------


def test_prompt_version_is_stable_and_content_sensitive():
    v1 = ai.prompt_version("hello")
    v2 = ai.prompt_version("hello")
    v3 = ai.prompt_version("hello world")
    assert v1 == v2
    assert v1 != v3
