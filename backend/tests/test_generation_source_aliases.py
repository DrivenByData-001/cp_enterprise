"""Generation-local source aliases (docs/35, "alias layer").

The model is shown/returns short handles (R001, C001, E001...) — never canonical database
refs/UUIDs. Application code owns alias -> canonical translation; unknown
handles fail closed; only canonical ids are persisted or exposed.
"""

import json
import re
from types import SimpleNamespace

import pytest

import alias_support
from alias_support import last_generation_context  # noqa: F401
from app import ai, application_artifacts as artifacts, application_generation as gen, db
from app.application_generation import GenerationContext, SourceAliasError, SourceEntry
from test_application_artifacts import (
    _cover_letter_output, _cv_output, _grounded_role, _positioning_output, _supporting_statement_output,
    _accepted_claim, _claim, _claim_mapping, _concept, _posting,
)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _model_returns(monkeypatch, make):
    """A model that answers with `make(ctx)`, where ctx is the context the
    application built — i.e. it can only know the aliases it was shown."""
    def _dispatch(*, task, prompt_name, user_input, output_model):
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="v",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=make(alias_support.LAST["ctx"]), run=run)
    monkeypatch.setattr(artifacts, "run_json_task", _dispatch)


def _alias(ctx, ref):
    return next(a for a, r in ctx.alias_to_ref.items() if r == ref)


def _generate(client, setup, kind="positioning"):
    return client.post(f"/api/applications/{setup['application_id']}/artifacts/{kind}/generate", json={})


def _no_draft(setup, kind="positioning"):
    with db.db_cursor() as cur:
        return artifacts._draft_row(cur, setup["application_id"], kind) is None


# A. prompt rendering ---------------------------------------------------------


def test_prompt_shows_short_aliases_never_canonical_refs(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="positioning")
    assert "[SOURCE_REF: R001]" in ctx.prompt_text
    assert re.findall(r"\[SOURCE_REF: ([^\]]+)\]", ctx.prompt_text) and all(
        re.fullmatch(r"[A-Z]\d{3}", a) for a in re.findall(r"\[SOURCE_REF: ([^\]]+)\]", ctx.prompt_text))
    assert f"profile_claim:{setup['claim_id']}" not in ctx.prompt_text
    assert not UUID_RE.search(ctx.prompt_text)  # no UUID of any kind is model-visible
    assert len(set(ctx.alias_to_ref)) == len(ctx.alias_to_ref) == len(ctx.known_source_refs())
    assert set(ctx.alias_to_ref.values()) == ctx.known_source_refs()


# B/C/J. translation, persistence stays canonical ----------------------------


def test_positioning_aliases_translate_to_canonical_and_persist_canonical(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    claim_ref = f"profile_claim:{setup['claim_id']}"
    req_ref = f"role_requirement:{setup['concept_id']}"

    def make(ctx):
        a_req, a_claim = _alias(ctx, req_ref), _alias(ctx, claim_ref)
        return _positioning_output(ref=a_req, person_ref=a_claim, concept_id=a_req)  # concept_id = alias too

    _model_returns(monkeypatch, make)
    resp = _generate(client, setup)
    assert resp.status_code == 200, resp.text
    art = resp.json()["artifact"]
    assert art["content"]["positioning_statement"]["source_refs"] == [req_ref, claim_ref]  # multiple, ordered
    assert art["content"]["requirements_to_lead_with"][0]["concept_id"] == setup["concept_id"]
    blob = json.dumps(art)
    assert not re.search(r'"[A-Z]\d{3}"', blob)
    assert {m["ref"] for m in art["source_manifest"]} >= {claim_ref, req_ref}
    with db.db_cursor() as cur:  # raw_output is persisted too: must be canonical
        row = artifacts._draft_row(cur, setup["application_id"], "positioning")
        assert not re.search(r'"[A-Z]\d{3}"', json.dumps(row["raw_output"]))


def test_cv_cover_letter_supporting_statement_persist_canonical(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    claim_ref, req_ref = f"profile_claim:{setup['claim_id']}", f"role_requirement:{setup['concept_id']}"
    ep_ref = f"profile_episode:{setup['episode_id']}"
    makers = {
        "cv": lambda c: _cv_output(ref=_alias(c, ep_ref), episode_id=_alias(c, ep_ref)),
        "cover_letter": lambda c: _cover_letter_output(ref=_alias(c, req_ref), person_ref=_alias(c, claim_ref)),
        "supporting_statement": lambda c: _supporting_statement_output(
            ref=_alias(c, req_ref), person_ref=_alias(c, claim_ref), concept_id=_alias(c, req_ref)),
    }
    for kind, make in makers.items():
        _model_returns(monkeypatch, make)
        resp = _generate(client, setup, kind)
        assert resp.status_code == 200, (kind, resp.text)
        content = resp.json()["artifact"]["content"]
        refs = {r for r in re.findall(r'"((?:profile|role)_[a-z_]+:[^"]+)"', json.dumps(content))}
        assert refs and all(r in {claim_ref, req_ref, ep_ref} for r in refs), (kind, refs)
        assert not re.search(r'"[A-Z]\d{3}"', json.dumps(content))
        if kind == "cv":
            assert content["experience"][0]["episode_id"] == setup["episode_id"]
        if kind == "supporting_statement":
            assert content["sections"][0]["concept_id"] == setup["concept_id"]


# D/E. fail closed ------------------------------------------------------------


def test_unknown_alias_fails_closed(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    req_ref = f"role_requirement:{setup['concept_id']}"
    _model_returns(monkeypatch, lambda c: _positioning_output(ref=_alias(c, req_ref), person_ref="R999"))
    resp = _generate(client, setup)
    assert resp.status_code == 422 and "R999" in resp.text
    assert _no_draft(setup)


@pytest.mark.parametrize("bad", ["R3", "r001", "R0001", " R001", "R001 ", "S001", ""])
def test_near_miss_aliases_are_not_fuzzy_matched(client, monkeypatch, bad):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    req_ref = f"role_requirement:{setup['concept_id']}"
    _model_returns(monkeypatch, lambda c: _positioning_output(ref=_alias(c, req_ref), person_ref=bad))
    assert _generate(client, setup).status_code == 422
    assert _no_draft(setup)


def test_canonical_ref_returned_directly_is_rejected_even_if_valid(client, monkeypatch):
    """The model cannot bypass aliasing: even a *real* canonical ref from this
    very context is refused, as is a fabricated canonical-looking UUID."""
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    req_ref = f"role_requirement:{setup['concept_id']}"
    for sneaky in (f"profile_claim:{setup['claim_id']}", "profile_claim:53a2b868-a170-4e0a-b81f-cd3a8e560dd"):
        _model_returns(monkeypatch, lambda c, s=sneaky: _positioning_output(ref=_alias(c, req_ref), person_ref=s))
        resp = _generate(client, setup)
        assert resp.status_code == 422, sneaky
        assert _no_draft(setup)


def test_concept_and_episode_fields_must_be_aliases_of_the_right_kind(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    claim_ref, req_ref = f"profile_claim:{setup['claim_id']}", f"role_requirement:{setup['concept_id']}"
    ep_ref = f"profile_episode:{setup['episode_id']}"
    # raw canonical concept id (what the model used to copy) is no longer accepted
    _model_returns(monkeypatch, lambda c: _positioning_output(
        ref=_alias(c, req_ref), person_ref=_alias(c, claim_ref), concept_id=setup["concept_id"]))
    assert _generate(client, setup).status_code == 422
    # an alias of the wrong kind (a claim, not a requirement) is refused
    _model_returns(monkeypatch, lambda c: _positioning_output(
        ref=_alias(c, req_ref), person_ref=_alias(c, claim_ref), concept_id=_alias(c, claim_ref)))
    assert _generate(client, setup).status_code == 422
    _model_returns(monkeypatch, lambda c: _cv_output(ref=_alias(c, ep_ref), episode_id=_alias(c, req_ref)))
    assert _generate(client, setup, "cv").status_code == 422
    _model_returns(monkeypatch, lambda c: _cv_output(ref=_alias(c, ep_ref), episode_id=setup["episode_id"]))
    assert _generate(client, setup, "cv").status_code == 422


# F/G. grounding survives translation ------------------------------------------


def test_role_side_only_alias_still_cannot_substantiate_applicant_claim(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    req_ref = f"role_requirement:{setup['concept_id']}"
    _model_returns(monkeypatch, lambda c: _positioning_output(ref=_alias(c, req_ref)))  # no person-side source
    resp = _generate(client, setup)
    assert resp.status_code == 422 and "person-side" in resp.text
    assert _no_draft(setup)


def test_person_side_alias_satisfies_existing_grounding(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    ref = f"role_requirement:{setup['concept_id']}"
    claim_ref = f"profile_claim:{setup['claim_id']}"
    _model_returns(monkeypatch, lambda c: _positioning_output(ref=_alias(c, ref), person_ref=_alias(c, claim_ref)))
    assert _generate(client, setup).status_code == 200


# H. curated Application Mode -------------------------------------------------


def test_curated_positioning_uses_aliases_and_only_selected_evidence(client, monkeypatch):
    with db.db_cursor() as cur:
        concept = _concept(cur, "Model governance")
        role = _posting(cur)
        _accepted_claim(cur, role, concept)
        chosen = _claim(cur, "Challenged internal model assumptions")
        _claim_mapping(cur, chosen, concept)
        other = _claim(cur, "Unselected alternative example")
        _claim_mapping(cur, other, concept)
    app = client.post("/api/applications", json={"role_instance_id": role}).json()["id"]
    url = f"/api/applications/{app}/mode"
    item = client.get(url).json()["items"][0]
    ref = f"profile_claim:{chosen}"
    src = next(s for s in item["sources"] if s["ref"] == ref)
    assert client.put(f"{url}/evidence/{concept}", json={
        "disposition": "covered", "selected_refs": [ref], "source_revisions": {ref: src["source_revision"]},
        "requirement_fingerprint": item["requirement_fingerprint"], "rationale": "Choose this example"}).status_code == 200
    setup = {"application_id": app}
    req_ref = f"role_requirement:{concept}"
    _model_returns(monkeypatch, lambda c: _positioning_output(
        ref=_alias(c, req_ref), person_ref=_alias(c, ref), concept_id=_alias(c, req_ref)))
    resp = _generate(client, setup)
    assert resp.status_code == 200, resp.text
    refs = resp.json()["artifact"]["content"]["positioning_statement"]["source_refs"]
    assert refs == [req_ref, ref]
    assert f"profile_claim:{other}" not in json.dumps(resp.json())  # curation unchanged
    ctx = alias_support.LAST["ctx"]
    assert chosen not in ctx.prompt_text and "Challenged internal model assumptions" in ctx.prompt_text

    # a model that "remembers" the unselected claim's canonical id is refused
    _model_returns(monkeypatch, lambda c: _positioning_output(
        ref=_alias(c, req_ref), person_ref=f"profile_claim:{other}", concept_id=_alias(c, req_ref)))
    assert _generate(client, setup).status_code == 422


# 11. production-failure regression (no DB) ------------------------------------

CLAIM_A = "profile_claim:53a2b868-a170-4e0a-bc81-fcd3a8e560dd"
CLAIM_B = "profile_claim:738f8f36-a7c0-4e52-af72-01bc163373d8"
CLAIM_C = "profile_claim:08561c68-6ff9-4f06-bc31-46969c09e66c"


def _prod_ctx():
    sources = [SourceEntry(ref=r, kind="profile_claim", label=r[-4:], category=gen.CATEGORY_CANONICAL_EVIDENCE,
                           content=f"claim text {i}") for i, r in enumerate((CLAIM_A, CLAIM_B, CLAIM_C))]
    aliases = gen.assign_source_aliases(sources)
    bundle = SimpleNamespace(role={}, events=[], application={})
    prompt = gen.render_prompt_text(bundle, sources, artifact_type="positioning", guidance=None, aliases=aliases)
    ctx = GenerationContext(bundle=bundle, artifact_type="positioning", active_positioning=None, sources=sources,
                            guidance=None, input_fingerprint="x", prompt_text=prompt, alias_to_ref=aliases)
    return ctx, prompt


def test_production_failure_model_never_handles_the_uuids():
    ctx, prompt = _prod_ctx()
    for u in ("53a2b868-a170-4e0a-bc81-fcd3a8e560dd", "738f8f36-a7c0-4e52-af72-01bc163373d8",
              "08561c68-6ff9-4f06-bc31-46969c09e66c"):
        assert u not in prompt
    assert ctx.alias_to_ref == {"C001": CLAIM_A, "C002": CLAIM_B, "C003": CLAIM_C}
    # what the model returns is short; the *application* produces the exact canonical ids
    out = ctx.resolve_aliases({"themes": [{"source_refs": ["C001", "C002", "C003"]}], "text": "C001 stays prose"})
    assert out["themes"][0]["source_refs"] == [CLAIM_A, CLAIM_B, CLAIM_C]
    assert out["text"] == "C001 stays prose"  # prose is never rewritten
    # the two corrupted values from the incident can no longer be expressed/accepted
    for corrupted in ("profile_claim:53a2b868-a170-4e0a-b81f-cd3a8e560dd",
                      "profile_claim:738f8f36-6ff9-4f06-bc31-46969c09e66c", CLAIM_A):
        with pytest.raises(SourceAliasError):
            ctx.resolve_aliases({"source_refs": [corrupted]})
    with pytest.raises(SourceAliasError):
        ctx.resolve_aliases({"a": [{"b": {"source_refs": ["C001", "R999"]}}]})  # nested, fails closed


def test_aliases_are_deterministic_and_unique():
    ctx1, _ = _prod_ctx()
    ctx2, _ = _prod_ctx()
    assert ctx1.alias_to_ref == ctx2.alias_to_ref
    dup = [SourceEntry(ref="a:1", kind="a", label="", category="c", content="")] * 2 + [
        SourceEntry(ref="a:2", kind="a", label="", category="c", content="")]
    assert gen.assign_source_aliases(dup) == {"X001": "a:1", "X002": "a:2"}


# --- typed namespaces --------------------------------------------------------


def _src(ref, kind):
    return SourceEntry(ref=ref, kind=kind, label="", category=gen.CATEGORY_CANONICAL_EVIDENCE, content="")


def test_aliases_are_typed_numbered_per_kind_and_deterministic():
    sources = [
        _src("role_requirement:r1", "role_requirement"), _src("profile_claim:c1", "profile_claim"),
        _src("profile_claim:c2", "profile_claim"), _src("role_requirement:r2", "role_requirement"),
        _src("profile_episode:e1", "profile_episode"), _src("role_requirement_legacy:l1", "role_requirement_legacy"),
        _src("profile_capability:p1", "profile_capability"), _src("weird:1", "unmapped_kind"),
    ]
    expected = {
        "R001": "role_requirement:r1", "C001": "profile_claim:c1", "C002": "profile_claim:c2",
        "R002": "role_requirement:r2", "E001": "profile_episode:e1", "L001": "role_requirement_legacy:l1",
        "P001": "profile_capability:p1", "X001": "weird:1",
    }
    assert gen.assign_source_aliases(sources) == expected
    assert gen.assign_source_aliases(sources) == expected  # deterministic
    assert len(set(gen.ALIAS_PREFIX_BY_KIND.values())) == len(gen.ALIAS_PREFIX_BY_KIND)  # prefixes unique


def test_every_registry_kind_has_a_prefix(client):
    """No SourceEntry kind the registry can emit may fall into the X bucket."""
    import pathlib
    src = pathlib.Path(gen.__file__).read_text() + pathlib.Path(gen.__file__).with_name("application_mode.py").read_text()
    for kind in set(re.findall(r"kind=[\"']([a-z_]+)[\"']", src)):
        assert kind in gen.ALIAS_PREFIX_BY_KIND, kind


def test_rendered_positioning_prompt_makes_namespaces_obvious(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="positioning")
    text = ctx.prompt_text
    assert "SOURCE HANDLES" in text and "Rnnn = role requirement" in text and "Cnnn = applicant profile claim" in text
    assert "Ennn = career episode" in text
    tags = dict(re.findall(r"\[SOURCE_REF: ([A-Z]\d{3})\]\n(.*)", text))
    assert any(k.startswith("R") and "Requirement:" in v for k, v in tags.items())
    assert any(k.startswith("C") and "Evidence for" in v for k, v in tags.items())
    assert any(k.startswith("E") for k in tags)
    assert not UUID_RE.search(text)
    # role-requirement and claim handles come from different namespaces
    assert _alias(ctx, f"role_requirement:{setup['concept_id']}")[0] == "R"
    assert _alias(ctx, f"profile_claim:{setup['claim_id']}")[0] == "C"
    assert _alias(ctx, f"profile_episode:{setup['episode_id']}")[0] == "E"


def test_positioning_concept_id_uses_requirement_handle_even_with_many_sources(client, monkeypatch):
    """Production incident: concept_id picked a valid-but-wrong 'S013'. With
    typed handles the requirement is simply the one Rnnn handle."""
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    req_ref, claim_ref = f"role_requirement:{setup['concept_id']}", f"profile_claim:{setup['claim_id']}"

    def make(ctx):
        r = _alias(ctx, req_ref)
        assert r == "R001"
        return _positioning_output(ref=r, person_ref=_alias(ctx, claim_ref), concept_id=r)

    _model_returns(monkeypatch, make)
    resp = _generate(client, setup)
    assert resp.status_code == 200, resp.text
    assert resp.json()["artifact"]["content"]["requirements_to_lead_with"][0]["concept_id"] == setup["concept_id"]


@pytest.mark.parametrize("wrong_ref_fmt", ["profile_claim:{claim}", "profile_episode:{episode}"])
def test_positioning_concept_id_wrong_kind_handle_is_rejected(client, monkeypatch, wrong_ref_fmt):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    req_ref, claim_ref = f"role_requirement:{setup['concept_id']}", f"profile_claim:{setup['claim_id']}"
    wrong = wrong_ref_fmt.format(claim=setup["claim_id"], episode=setup["episode_id"])
    _model_returns(monkeypatch, lambda c: _positioning_output(
        ref=_alias(c, req_ref), person_ref=_alias(c, claim_ref), concept_id=_alias(c, wrong)))
    resp = _generate(client, setup)
    assert resp.status_code == 422 and "does not name a" in resp.text
    assert _no_draft(setup)


@pytest.mark.parametrize("episode_field_ref", ["role_requirement:{req}", "profile_claim:{claim}"])
def test_cv_episode_id_wrong_kind_handle_is_rejected(client, monkeypatch, episode_field_ref):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    ep_ref = f"profile_episode:{setup['episode_id']}"
    wrong = episode_field_ref.format(req=setup["concept_id"], claim=setup["claim_id"])
    _model_returns(monkeypatch, lambda c: _cv_output(ref=_alias(c, ep_ref), episode_id=_alias(c, wrong)))
    assert _generate(client, setup, "cv").status_code == 422
    assert _no_draft(setup, "cv")


def test_cv_episode_id_episode_handle_resolves_and_source_refs_mix_namespaces(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    ep_ref, claim_ref = f"profile_episode:{setup['episode_id']}", f"profile_claim:{setup['claim_id']}"
    req_ref = f"role_requirement:{setup['concept_id']}"
    seen = {}

    def make(ctx):
        seen["e"] = _alias(ctx, ep_ref)
        out = _cv_output(ref=_alias(ctx, ep_ref), episode_id=_alias(ctx, ep_ref))
        out.profile_summary.source_refs = [_alias(ctx, ep_ref), _alias(ctx, claim_ref), _alias(ctx, req_ref)]
        return out

    _model_returns(monkeypatch, make)
    resp = _generate(client, setup, "cv")
    assert resp.status_code == 200, resp.text
    assert seen["e"] == "E001"
    content = resp.json()["artifact"]["content"]
    assert content["profile_summary"]["source_refs"] == [ep_ref, claim_ref, req_ref]
    assert content["experience"][0]["episode_id"] == setup["episode_id"]


def test_untyped_and_unknown_typed_handles_fail_closed(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    req_ref = f"role_requirement:{setup['concept_id']}"
    for bad in ("R999", "C999", "E999", "S001"):
        _model_returns(monkeypatch, lambda c, b=bad: _positioning_output(ref=_alias(c, req_ref), person_ref=b))
        assert _generate(client, setup).status_code == 422, bad
        assert _no_draft(setup)


def test_legacy_requirement_handle_allowed_for_gap_concept_but_not_lead_with(client, monkeypatch):
    from test_application_artifacts import _legacy_only_role
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
    leg = f"role_requirement_legacy:{setup['legacy_concept_id']}"
    ep = f"profile_episode:{setup['episode_id']}"

    def gap(ctx):
        assert _alias(ctx, leg) == "L001"
        return _positioning_output(ref=_alias(ctx, leg), person_ref=_alias(ctx, ep), gap_concept_id=_alias(ctx, leg))

    _model_returns(monkeypatch, gap)
    assert _generate(client, setup).status_code == 200
    _model_returns(monkeypatch, lambda c: _positioning_output(
        ref=_alias(c, leg), person_ref=_alias(c, ep), concept_id=_alias(c, leg)))
    resp = _generate(client, setup)
    assert resp.status_code == 422 and "non-accepted" in resp.text


def test_interview_prep_uses_typed_aliases_and_persists_canonical(client, monkeypatch):
    from test_interview_prep import _interview_prep_output
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    req_ref, claim_ref = f"role_requirement:{setup['concept_id']}", f"profile_claim:{setup['claim_id']}"
    _model_returns(monkeypatch, lambda c: _interview_prep_output(role_ref=_alias(c, req_ref), person_ref=_alias(c, claim_ref)))
    resp = _generate(client, setup, "interview_prep")
    assert resp.status_code == 200, resp.text
    blob = json.dumps(resp.json()["artifact"]["content"])
    assert req_ref in blob and claim_ref in blob and not re.search(r'"[A-Z]\d{3}"', blob)
