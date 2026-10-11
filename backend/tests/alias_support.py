"""Test helper: plays the role of a well-behaved model under the generation
alias contract (docs/35 §alias layer). Existing tests build outputs from
canonical ids for readability; `to_model_view` rewrites those into the
short handles the model would actually have seen and returned, using the
GenerationContext the application itself built. A value that is NOT a
canonical id of that context (an invented ref, a fabricated uuid) is left
untouched, so it still reaches the app as something the model "made up"."""

import pytest

from app import application_artifacts as artifacts


LAST: dict = {}


@pytest.fixture(autouse=True)
def last_generation_context(monkeypatch):
    holder = LAST
    holder.clear()
    real = artifacts.build_application_generation_context

    def _capture(*a, **kw):
        holder["ctx"] = real(*a, **kw)
        return holder["ctx"]

    monkeypatch.setattr(artifacts, "build_application_generation_context", _capture)
    return holder


def model_view(output):
    """`output` as the model would return it for the context the app just built."""
    return output if output is None else to_model_view(output, LAST["ctx"])


def to_model_view(output, ctx):
    ref_to_alias = {ref: alias for alias, ref in ctx.alias_to_ref.items()}

    def conv(key, v):
        if key == "source_refs" and isinstance(v, list):
            return [ref_to_alias.get(r, r) for r in v]
        if key == "concept_id" and isinstance(v, str):
            return ref_to_alias.get(f"role_requirement:{v}") or ref_to_alias.get(f"role_requirement_legacy:{v}") or v
        if key == "episode_id" and isinstance(v, str):
            return ref_to_alias.get(f"profile_episode:{v}", v)
        return v

    def walk(n):
        if isinstance(n, list):
            return [walk(i) for i in n]
        if isinstance(n, dict):
            return {k: conv(k, walk(v)) if k in ("source_refs", "concept_id", "episode_id") else walk(v) for k, v in n.items()}
        return n

    return type(output).model_validate(walk(output.model_dump()))
