"""Phase 8: Market Coverage & Confidence (docs/39, app/market_coverage.py +
routes/market_coverage.py). Real Postgres throughout, same fixture style as
test_trends.py / test_opportunity_alignment.py / test_role_requirements.py.
"""

import uuid
from datetime import date

from app import market_coverage as mc
from app.concept_linking import get_or_create_current_vocabulary_version
from app.db import create_document, db_cursor, upsert_role_instance
from app.role_requirements import load_requirement_review_summary_bulk
from query_counter import count_queries

# --- Fixtures -----------------------------------------------------------


def _archetype(cur, name, status="active"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('role_archetype', %s, %s, 'curator', now()) RETURNING id",
        (name, status),
    )
    concept_id = str(cur.fetchone()["id"])
    cur.execute("INSERT INTO jobber.role_archetype_detail (concept_id) VALUES (%s)", (concept_id,))
    return concept_id


def _capability(cur, name):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('capability', %s, 'active', 'curator', now()) RETURNING id",
        (name,),
    )
    return str(cur.fetchone()["id"])


def _role(
    cur, title="Role", *, posting_date=None, country=None, seniority_level=None, employment_type=None,
    remote_type=None, archetype_concept_id=None, with_document=False, source="linkedin",
    provenance_quality="original", url=None, document_id=None,
):
    if with_document and document_id is None:
        document_id, _dup = create_document(
            cur, kind="job_posting", content_text=f"{title} posting text.", provenance_quality=provenance_quality,
            title=title, source=source, url=url,
        )
    columns = {
        "instance_type": "observed_posting", "title": title, "posting_date": posting_date, "country": country,
        "seniority_level": seniority_level, "employment_type": employment_type, "remote_type": remote_type,
        "document_id": document_id, "archetype_concept_id": archetype_concept_id,
    }
    return str(upsert_role_instance(cur, None, columns, skills=[]))


def _requirement(cur, role_id, concept_id, *, review_status="accepted", requirement_type="required"):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
        "VALUES (%s, %s, %s, 'user_asserted', %s)",
        (role_id, concept_id, requirement_type, review_status),
    )


def _mapped_observation(cur, role_id, concept_id, name="python"):
    cur.execute(
        "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, canonical_concept_id, observation_basis) "
        "VALUES (%s, %s, %s, 'app_capture')",
        (role_id, name, concept_id),
    )


def _unmapped_observation(cur, role_id, name="raw skill"):
    cur.execute(
        "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, observation_basis) "
        "VALUES (%s, %s, 'app_capture')",
        (role_id, name),
    )


def _unresolved_proposal(cur, role_id, document_id, *, surface_form="foo modelling"):
    cur.execute(
        "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, status) "
        "VALUES (%s, 1, %s, 'pending') RETURNING id",
        (surface_form, document_id),
    )
    proposal_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.concept_proposal_occurrence (concept_proposal_id, role_instance_id, document_id) VALUES (%s, %s, %s)",
        (proposal_id, role_id, document_id),
    )


def _needs_reextraction(cur, role_id, document_id, resolved_concept_id, *, surface_form="bar charting"):
    cur.execute(
        "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, status, resolved_concept_id, resolved_at) "
        "VALUES (%s, 1, %s, 'accepted_new', %s, now()) RETURNING id",
        (surface_form, document_id, resolved_concept_id),
    )
    proposal_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.concept_proposal_occurrence (concept_proposal_id, role_instance_id, document_id) VALUES (%s, %s, %s)",
        (proposal_id, role_id, document_id),
    )


def _extraction_attempted(cur, role_id):
    vocabulary_version_id = get_or_create_current_vocabulary_version(cur)
    cur.execute(
        "INSERT INTO jobber.extraction_run (task, subject_type, role_instance_id, model, prompt_name, "
        "prompt_version, vocabulary_version_id, started_at, finished_at, status) "
        "VALUES ('requirement_extract', 'role_instance', %s, 'm', 'p', 'v', %s, now(), now(), 'ok')",
        (role_id, vocabulary_version_id),
    )


def _market(cur, code=None):
    code = code or f"test-market-{uuid.uuid4().hex[:8]}"
    cur.execute("INSERT INTO jobber.market (code, label) VALUES (%s, %s) RETURNING id", (code, code))
    return str(cur.fetchone()["id"])


def _compensation(
    cur, *, role_id=None, archetype_concept_id=None, market_id=None, document_id=None,
    amount_min=60000, amount_max=80000, basis="posting_stated", review_status="accepted",
    reported_sample_size=None, reported_p50=None, raw_role_label=None,
):
    if role_id is None and archetype_concept_id is None and raw_role_label is None:
        raw_role_label = "Test Role"  # compensation_observation's own CHECK requires one of the three
    cur.execute(
        "INSERT INTO jobber.compensation_observation (source_key, role_instance_id, archetype_concept_id, "
        "raw_role_label, market_id, component, pay_period, currency, amount_min, amount_max, reported_p50, "
        "reported_sample_size, basis, review_status, document_id) "
        "VALUES (%s, %s, %s, %s, %s, 'base', 'annual', 'EUR', %s, %s, %s, %s, %s, %s, %s)",
        (
            f"test-comp:{uuid.uuid4()}", role_id, archetype_concept_id, raw_role_label, market_id, amount_min,
            amount_max, reported_p50, reported_sample_size, basis, review_status, document_id,
        ),
    )


def _demand_row(cur, archetype_id, capability_id):
    cur.execute(
        "INSERT INTO jobber.d_archetype_demand (archetype_concept_id, capability_concept_id, engine_version) "
        "VALUES (%s, %s, 'test')",
        (archetype_id, capability_id),
    )


def _comp_benchmark_row(cur, archetype_id, market_id):
    cur.execute(
        "INSERT INTO jobber.d_archetype_comp (archetype_concept_id, market_id, period_start, period_end, currency, "
        "component, pay_period, reference_comp, engine_version) "
        "VALUES (%s, %s, '2024-01-01', '2024-12-31', 'EUR', 'base', 'annual', 70000, 'test')",
        (archetype_id, market_id),
    )


# --- §33 role-corpus completeness --------------------------------------------


def test_empty_corpus(client):
    with db_cursor() as cur:
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["roles"]["total"] == 0
    assert summary["roles"]["known_posting_date"]["proportion"] is None
    assert "meaning" in summary["roles"]["known_posting_date"]
    assert summary["representativeness"] == mc.REPRESENTATIVENESS_UNKNOWN


def test_known_and_unknown_posting_date(client):
    with db_cursor() as cur:
        _role(cur, "Dated", posting_date=date(2024, 1, 1))
        _role(cur, "Undated")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["roles"]["known_posting_date"]["count"] == 1
    assert summary["roles"]["unknown_posting_date"]["count"] == 1
    assert summary["roles"]["known_posting_date"]["total"] == 2


def test_capture_date_never_substituted_for_posting_date(client):
    with db_cursor() as cur:
        _role(cur, "Undated but freshly captured", with_document=True)
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["roles"]["unknown_posting_date"]["count"] == 1
    assert summary["time"]["earliest_known_posting_date"] is None
    assert summary["time"]["latest_known_posting_date"] is None
    assert summary["time"]["freshly_captured_but_posting_date_unknown_count"] == 1


def test_year_filter_excludes_undated_but_discloses_excluded_count(client):
    with db_cursor() as cur:
        _role(cur, "Dated 2024", posting_date=date(2024, 6, 1))
        _role(cur, "Undated")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope(year_from=2024, year_to=2024))
        unscoped = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["roles"]["total"] == 1
    assert summary["roles"]["excluded_undated_count"] == 1
    assert summary["scope"]["dated_scope_active"] is True
    assert unscoped["roles"]["excluded_undated_count"] == 0


def test_known_and_unknown_country(client):
    with db_cursor() as cur:
        _role(cur, "R1", country="Ireland")
        _role(cur, "R2")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["roles"]["known_country"]["count"] == 1
    assert summary["roles"]["unknown_country"]["count"] == 1


def test_known_and_unknown_seniority(client):
    with db_cursor() as cur:
        _role(cur, "R1", seniority_level="senior")
        _role(cur, "R2")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["roles"]["known_seniority"]["count"] == 1
    assert summary["roles"]["unknown_seniority"]["count"] == 1


def test_linked_and_unlinked_source_document(client):
    with db_cursor() as cur:
        _role(cur, "Linked", with_document=True)
        _role(cur, "Unlinked")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["roles"]["with_source_document"]["count"] == 1
    assert summary["roles"]["without_source_document"]["count"] == 1
    assert summary["sources"]["with_source_document"]["count"] == 1


def test_source_url_present_and_absent(client):
    with db_cursor() as cur:
        _role(cur, "WithURL", with_document=True, url="https://example.com/a")
        _role(cur, "NoURL", with_document=True)
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["sources"]["url_present"]["count"] == 1
    assert summary["sources"]["url_absent"]["count"] == 1
    assert summary["sources"]["url_present"]["total"] == 2


def test_provenance_quality_breakdown(client):
    with db_cursor() as cur:
        _role(cur, "Original", with_document=True, provenance_quality="original")
        _role(cur, "Legacy", with_document=True, provenance_quality="legacy_extracted")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    values = {d["value"]: d["count"] for d in summary["sources"]["provenance_quality_distribution"]}
    assert values == {"original": 1, "legacy_extracted": 1}


def test_capture_source_breakdown(client):
    with db_cursor() as cur:
        _role(cur, "A", with_document=True, source="linkedin")
        _role(cur, "B", with_document=True, source="linkedin")
        _role(cur, "C", with_document=True, source="indeed")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    values = {d["value"]: d["count"] for d in summary["sources"]["capture_source_distribution"]}
    assert values == {"linkedin": 2, "indeed": 1}


def test_bounded_distributions_report_true_distinct_count(client):
    with db_cursor() as cur:
        for i in range(mc.COUNTRY_DISTRIBUTION_LIMIT + 5):
            _role(cur, f"R{i}", country=f"Country{i}")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert len(summary["geography"]["country_distribution"]) == mc.COUNTRY_DISTRIBUTION_LIMIT
    assert summary["geography"]["distinct_known_country_count"] == mc.COUNTRY_DISTRIBUTION_LIMIT + 5


def test_applied_scope_is_always_echoed(client):
    with db_cursor() as cur:
        summary = mc.build_coverage_summary(cur, mc.CoverageScope(year_from=2020, country="Ireland", seniority_level="senior"))
    assert summary["scope"] == {
        "year_from": 2020, "year_to": None, "country": "Ireland", "seniority_level": "senior",
        "archetype_id": None, "dated_scope_active": True,
    }


def test_every_top_level_proportion_carries_numerator_denominator_and_meaning(client):
    with db_cursor() as cur:
        _role(cur, "R1", posting_date=date(2024, 1, 1), country="Ireland", seniority_level="senior", with_document=True)
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    for section in ("roles", "requirements", "vocabulary"):
        for key, value in summary[section].items():
            if isinstance(value, dict) and "proportion" in value:
                assert {"count", "total", "proportion", "meaning"} <= set(value.keys()), f"{section}.{key}"
                assert isinstance(value["meaning"], str) and value["meaning"]


# --- §34 requirement / vocabulary coverage -----------------------------------


def test_requirement_state_reviewed(client):
    with db_cursor() as cur:
        cap = _capability(cur, "Python")
        role_id = _role(cur, "R1")
        _requirement(cur, role_id, cap, review_status="accepted")
        summaries = load_requirement_review_summary_bulk(cur, [role_id])
    state = mc.classify_requirement_review_state(summaries[role_id], has_legacy_fallback=False)
    assert state == mc.REQUIREMENT_STATE_REVIEWED


def test_requirement_state_review_pending(client):
    with db_cursor() as cur:
        cap = _capability(cur, "Python")
        role_id = _role(cur, "R1")
        _requirement(cur, role_id, cap, review_status="unreviewed")
        summaries = load_requirement_review_summary_bulk(cur, [role_id])
    assert mc.classify_requirement_review_state(summaries[role_id], has_legacy_fallback=False) == mc.REQUIREMENT_STATE_REVIEW_PENDING


def test_requirement_state_unresolved_vocabulary(client):
    with db_cursor() as cur:
        role_id = _role(cur, "R1", with_document=True)
        cur.execute("SELECT document_id FROM jobber.role_instance WHERE id = %s", (role_id,))
        document_id = cur.fetchone()["document_id"]
        _unresolved_proposal(cur, role_id, document_id)
        summaries = load_requirement_review_summary_bulk(cur, [role_id])
    assert mc.classify_requirement_review_state(summaries[role_id], has_legacy_fallback=False) == mc.REQUIREMENT_STATE_UNRESOLVED_VOCABULARY


def test_requirement_state_needs_reextraction(client):
    with db_cursor() as cur:
        cap = _capability(cur, "Bar Charting")
        role_id = _role(cur, "R1", with_document=True)
        cur.execute("SELECT document_id FROM jobber.role_instance WHERE id = %s", (role_id,))
        document_id = cur.fetchone()["document_id"]
        _needs_reextraction(cur, role_id, document_id, cap)
        summaries = load_requirement_review_summary_bulk(cur, [role_id])
    assert mc.classify_requirement_review_state(summaries[role_id], has_legacy_fallback=False) == mc.REQUIREMENT_STATE_NEEDS_REEXTRACTION


def test_requirement_state_legacy_only(client):
    with db_cursor() as cur:
        cap = _capability(cur, "Python")
        role_id = _role(cur, "R1")
        _mapped_observation(cur, role_id, cap)
        summaries = load_requirement_review_summary_bulk(cur, [role_id])
    assert mc.classify_requirement_review_state(summaries[role_id], has_legacy_fallback=True) == mc.REQUIREMENT_STATE_LEGACY_ONLY


def test_requirement_extraction_attempted_but_incomplete(client):
    with db_cursor() as cur:
        cap = _capability(cur, "Python")
        role_id = _role(cur, "R1")
        _requirement(cur, role_id, cap, review_status="unreviewed")
        _extraction_attempted(cur, role_id)
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["requirements"]["extraction_attempted_incomplete"]["count"] == 1
    assert summary["requirements"]["extraction_never_attempted"]["count"] == 0


def test_requirement_state_not_extracted(client):
    with db_cursor() as cur:
        role_id = _role(cur, "R1")
        summaries = load_requirement_review_summary_bulk(cur, [role_id])
    assert mc.classify_requirement_review_state(summaries[role_id], has_legacy_fallback=False) == mc.REQUIREMENT_STATE_NOT_EXTRACTED


def test_requirement_state_precedence_needs_reextraction_wins_over_unresolved(client):
    """First match wins: a role with both a needs-re-extraction concept and
    an unresolved proposal is reported as needs_reextraction (build §9's
    documented precedence), even though it also has an unresolved-vocabulary
    signal."""
    with db_cursor() as cur:
        cap = _capability(cur, "Bar Charting")
        role_id = _role(cur, "R1", with_document=True)
        cur.execute("SELECT document_id FROM jobber.role_instance WHERE id = %s", (role_id,))
        document_id = cur.fetchone()["document_id"]
        _needs_reextraction(cur, role_id, document_id, cap)
        _unresolved_proposal(cur, role_id, document_id, surface_form="something else")
        summaries = load_requirement_review_summary_bulk(cur, [role_id])
    assert mc.classify_requirement_review_state(summaries[role_id], has_legacy_fallback=True) == mc.REQUIREMENT_STATE_NEEDS_REEXTRACTION


def test_requirements_and_vocabulary_blocks_aggregate_across_scope(client):
    with db_cursor() as cur:
        cap = _capability(cur, "Python")
        reviewed = _role(cur, "Reviewed")
        _requirement(cur, reviewed, cap, review_status="accepted")
        legacy_only = _role(cur, "LegacyOnly")
        _mapped_observation(cur, legacy_only, cap)
        _unmapped_observation(cur, legacy_only)
        never = _role(cur, "Never")

        summary = mc.build_coverage_summary(cur, mc.CoverageScope())

    assert summary["requirements"]["accepted_present"]["count"] == 1
    assert summary["requirements"]["legacy_only"]["count"] == 1
    assert summary["requirements"]["no_usable_evidence"]["count"] == 1
    assert summary["requirements"]["state_distribution"]["reviewed"] == 1
    assert summary["requirements"]["state_distribution"]["legacy_only"] == 1
    assert summary["requirements"]["state_distribution"]["not_extracted"] == 1
    assert sum(summary["requirements"]["state_distribution"].values()) == 3

    assert summary["vocabulary"]["roles_with_accepted_canonical_requirements"]["count"] == 1
    assert summary["vocabulary"]["roles_relying_on_legacy_fallback"]["count"] == 1
    assert summary["vocabulary"]["role_skill_observations_resolved"]["count"] == 1
    assert summary["vocabulary"]["role_skill_observations_unresolved_count"] == 1


def test_no_requirement_review_or_vocabulary_writes(client):
    """A read-only coverage build must never mutate requirement_claim,
    concept_proposal or role_skill_observation."""
    with db_cursor() as cur:
        cap = _capability(cur, "Python")
        role_id = _role(cur, "R1", with_document=True)
        cur.execute("SELECT document_id FROM jobber.role_instance WHERE id = %s", (role_id,))
        document_id = cur.fetchone()["document_id"]
        _requirement(cur, role_id, cap, review_status="unreviewed")
        _unresolved_proposal(cur, role_id, document_id)

        def _snapshot():
            cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim")
            claims = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM jobber.concept_proposal")
            proposals = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM jobber.role_skill_observation")
            observations = cur.fetchone()["n"]
            return claims, proposals, observations

        before = _snapshot()
        mc.build_coverage_summary(cur, mc.CoverageScope())
        mc.discovery_corpus_disclosure(cur)
        after = _snapshot()
    assert before == after


# --- §35 archetype coverage ---------------------------------------------------


def test_archetype_assigned_and_unassigned(client):
    with db_cursor() as cur:
        archetype = _archetype(cur, "Senior Actuary")
        _role(cur, "Assigned", archetype_concept_id=archetype)
        _role(cur, "Unassigned")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["archetypes"]["assigned_active"]["count"] == 1
    assert summary["archetypes"]["unassigned"]["count"] == 1


def test_active_archetype_with_and_without_support(client):
    with db_cursor() as cur:
        supported = _archetype(cur, "Supported Archetype")
        unsupported = _archetype(cur, "Unsupported Archetype")
        _role(cur, "R1", archetype_concept_id=supported)
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["archetypes"]["active_archetype_count"] == 2
    assert summary["archetypes"]["active_archetypes_with_support"]["count"] == 1
    assert summary["archetypes"]["active_archetypes_without_support_count"] == 1
    assert {a["archetype_concept_id"] for a in summary["archetypes"]["unsupported_active_archetypes"]} == {unsupported}
    assert summary["archetypes"]["support"][0]["archetype_concept_id"] == supported


def test_deprecated_archetype_excluded_from_active_coverage(client):
    with db_cursor() as cur:
        deprecated = _archetype(cur, "Old Archetype", status="deprecated")
        _role(cur, "R1", archetype_concept_id=deprecated)
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["archetypes"]["active_archetype_count"] == 0
    assert summary["archetypes"]["assigned_active"]["count"] == 0
    assert summary["archetypes"]["assigned_deprecated"]["count"] == 1
    assert summary["archetypes"]["support"] == []


def test_archetype_support_reviewed_requirement_count_and_dates_and_countries(client):
    with db_cursor() as cur:
        archetype = _archetype(cur, "Senior Actuary")
        cap = _capability(cur, "Python")
        r1 = _role(cur, "R1", archetype_concept_id=archetype, posting_date=date(2023, 1, 1), country="Ireland")
        _requirement(cur, r1, cap, review_status="accepted")
        _role(cur, "R2", archetype_concept_id=archetype, posting_date=date(2024, 6, 1), country="United Kingdom")
        _role(cur, "R3", archetype_concept_id=archetype)  # undated

        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    row = summary["archetypes"]["support"][0]
    assert row["assigned_posting_count"] == 3
    assert row["reviewed_requirement_posting_count"] == 1
    assert row["known_posting_date_count"] == 2
    assert row["unknown_posting_date_count"] == 1
    assert row["latest_known_posting_date"] == date(2024, 6, 1)
    assert row["distinct_country_count"] == 2


def test_archetype_compensation_benchmark_and_demand_availability(client):
    with db_cursor() as cur:
        archetype = _archetype(cur, "Senior Actuary")
        cap = _capability(cur, "Python")
        market_id = _market(cur)
        _role(cur, "R1", archetype_concept_id=archetype)
        _demand_row(cur, archetype, cap)
        _comp_benchmark_row(cur, archetype, market_id)

        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    row = summary["archetypes"]["support"][0]
    assert row["demand_derivation_available"] is True
    assert row["compensation_benchmark_available"] is True


def test_archetype_with_one_posting_carries_thin_evidence_caveat(client):
    with db_cursor() as cur:
        archetype = _archetype(cur, "Solo Archetype")
        _role(cur, "R1", archetype_concept_id=archetype)
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    row = summary["archetypes"]["support"][0]
    assert row["evidence_depth"]["state"] == mc.EVIDENCE_DEPTH_THIN


def test_archetype_stale_economics_disclosed(client):
    with db_cursor() as cur:
        archetype = _archetype(cur, "Senior Actuary")
        _role(cur, "R1", archetype_concept_id=archetype)
        detail = mc.archetype_coverage_detail(cur, archetype)
    assert detail["economics_freshness"]["state"] == "never_rebuilt"
    assert detail["economics_freshness"]["fresh"] is False


def test_archetype_coverage_detail_unscoped_is_corpus_wide(client):
    """The default `scope=None` — what opportunity_alignment.py/pathways.py
    use — must report corpus-wide support, independent of any other page's
    active filters."""
    with db_cursor() as cur:
        archetype = _archetype(cur, "Senior Actuary")
        _role(cur, "IE", archetype_concept_id=archetype, country="Ireland")
        _role(cur, "UK", archetype_concept_id=archetype, country="United Kingdom")
        detail = mc.archetype_coverage_detail(cur, archetype)
    assert detail["assigned_posting_count"] == 2
    assert detail["distinct_country_count"] == 2


def test_archetype_coverage_detail_not_found(client):
    with db_cursor() as cur:
        detail = mc.archetype_coverage_detail(cur, str(uuid.uuid4()))
    assert detail["found"] is False


def test_no_archetype_auto_classification(client):
    """A GET-only coverage build must never assign an archetype to a role."""
    with db_cursor() as cur:
        _archetype(cur, "Senior Actuary")
        role_id = _role(cur, "R1")
        mc.build_coverage_summary(cur, mc.CoverageScope())
        cur.execute("SELECT archetype_concept_id FROM jobber.role_instance WHERE id = %s", (role_id,))
        assert cur.fetchone()["archetype_concept_id"] is None


# --- §36 compensation coverage (composes market_analytics) -------------------


def test_compensation_coverage_composes_market_analytics(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        doc_id, _ = create_document(cur, kind="market_survey", content_text="survey", provenance_quality="original", source="Mercer")
        _compensation(cur, market_id=market_id, document_id=doc_id, reported_p50=70000, reported_sample_size=40)
        _compensation(cur, market_id=market_id, document_id=doc_id, review_status="unreviewed")  # excluded

        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    coverage = summary["compensation"]["coverage"]
    assert coverage["accepted_observation_count"] == 1
    assert coverage["distinct_source_document_count"] == 1
    assert coverage["with_reported_median_count"] == 1
    assert coverage["with_sample_size_count"] == 1


def test_compensation_role_linked_coverage_is_scoped_to_roles(client):
    with db_cursor() as cur:
        role_with_comp = _role(cur, "WithComp")
        role_without = _role(cur, "WithoutComp")
        _compensation(cur, role_id=role_with_comp)

        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    linked = summary["compensation"]["role_linked_coverage"]
    assert linked["with_accepted_role_linked_compensation"]["count"] == 1
    assert linked["without_accepted_role_linked_compensation"]["count"] == 1
    assert linked["with_accepted_role_linked_compensation"]["total"] == 2


def test_compensation_section_is_not_narrowed_by_role_corpus_filters(client):
    """Build §3/§12: role-corpus filters (year/country/seniority/archetype)
    never narrow the corpus-wide compensation section."""
    with db_cursor() as cur:
        market_id = _market(cur)
        _compensation(cur, market_id=market_id)
        scoped = mc.build_coverage_summary(cur, mc.CoverageScope(country="A Country That Matches Nothing"))
    assert scoped["compensation"]["coverage"]["accepted_observation_count"] == 1


def test_compensation_bases_kept_separate(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        _compensation(cur, market_id=market_id, basis="posting_stated")
        _compensation(cur, market_id=market_id, basis="survey")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    values = {d["value"] for d in summary["compensation"]["basis_distribution"]}
    assert values == {"posting_stated", "survey"}


def test_market_analytics_suite_unaffected():
    """Phase 8 composes market_analytics.py rather than modifying it — its
    own suite (test_market_analytics.py) is the regression guard and is run
    unmodified as part of the full backend suite."""
    import app.market_analytics  # noqa: F401 — importable, unchanged module


# --- §37 evidence-depth rules --------------------------------------------------


def test_structural_evidence_depth_zero_is_insufficient():
    result = mc.structural_evidence_depth(archetype_active=False, posting_count=0, reviewed_requirement_count=0, distinct_countries=0)
    assert result["state"] == mc.EVIDENCE_DEPTH_INSUFFICIENT


def test_structural_evidence_depth_small_support_is_thin():
    result = mc.structural_evidence_depth(archetype_active=True, posting_count=1, reviewed_requirement_count=0, distinct_countries=1)
    assert result["state"] == mc.EVIDENCE_DEPTH_THIN


def test_structural_evidence_depth_more_support_reaches_supported_then_broader():
    supported = mc.structural_evidence_depth(archetype_active=True, posting_count=3, reviewed_requirement_count=2, distinct_countries=1)
    assert supported["state"] == mc.EVIDENCE_DEPTH_SUPPORTED
    broader = mc.structural_evidence_depth(archetype_active=True, posting_count=8, reviewed_requirement_count=5, distinct_countries=2)
    assert broader["state"] == mc.EVIDENCE_DEPTH_BROADER_SUPPORT


def test_compensation_evidence_depth_zero_is_insufficient():
    result = mc.compensation_evidence_depth(observation_count=0, distinct_document_count=0, distinct_provider_count=0, economics_fresh=True)
    assert result["state"] == mc.EVIDENCE_DEPTH_INSUFFICIENT


def test_compensation_evidence_depth_thin_then_supported():
    thin = mc.compensation_evidence_depth(observation_count=1, distinct_document_count=1, distinct_provider_count=1, economics_fresh=True)
    assert thin["state"] == mc.EVIDENCE_DEPTH_THIN
    supported = mc.compensation_evidence_depth(observation_count=3, distinct_document_count=2, distinct_provider_count=1, economics_fresh=True)
    assert supported["state"] == mc.EVIDENCE_DEPTH_SUPPORTED


def test_stale_economics_cannot_produce_broader_support_compensation_state():
    fresh = mc.compensation_evidence_depth(observation_count=10, distinct_document_count=4, distinct_provider_count=3, economics_fresh=True)
    assert fresh["state"] == mc.EVIDENCE_DEPTH_BROADER_SUPPORT
    stale = mc.compensation_evidence_depth(observation_count=10, distinct_document_count=4, distinct_provider_count=3, economics_fresh=False)
    assert stale["state"] == mc.EVIDENCE_DEPTH_SUPPORTED  # never the strongest state while stale


def test_evidence_depth_reason_names_the_supporting_facts():
    result = mc.structural_evidence_depth(archetype_active=True, posting_count=5, reviewed_requirement_count=2, distinct_countries=1)
    assert "5" in result["reason"]
    assert result["posting_count"] == 5


def test_trend_evidence_depth_reuses_trends_sparse_rule():
    from app import trends

    sparse = trends.classify_trend([], min_sample_size=trends.SPARSE_MIN_SAMPLE)
    assert mc.trend_evidence_depth(sparse)["state"] == mc.EVIDENCE_DEPTH_INSUFFICIENT

    series = [
        {"period": y, "role_count": 6, "total_roles": 10, "proportion": 0.6, "sample_size": 10}
        for y in range(2018, 2024)
    ]
    classification = trends.classify_trend(series)
    result = mc.trend_evidence_depth(classification)
    assert result["state"] in (mc.EVIDENCE_DEPTH_SUPPORTED, mc.EVIDENCE_DEPTH_BROADER_SUPPORT)


def test_no_global_numeric_evidence_depth_score():
    """Every evidence-depth result is a named state plus a reason — never a
    bare numeric confidence figure."""
    result = mc.structural_evidence_depth(archetype_active=True, posting_count=3, reviewed_requirement_count=1, distinct_countries=1)
    assert result["state"] in (mc.EVIDENCE_DEPTH_INSUFFICIENT, mc.EVIDENCE_DEPTH_THIN, mc.EVIDENCE_DEPTH_SUPPORTED, mc.EVIDENCE_DEPTH_BROADER_SUPPORT)
    assert "score" not in result
    assert "confidence" not in result


# --- §38 representativeness ----------------------------------------------------


def test_representativeness_always_unknown_on_whole_corpus(client):
    with db_cursor() as cur:
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["representativeness"]["known"] is False


def test_high_metadata_completeness_does_not_make_representativeness_known(client):
    with db_cursor() as cur:
        for i in range(20):
            _role(cur, f"R{i}", posting_date=date(2024, 1, 1), country="Ireland", seniority_level="senior",
                  employment_type="permanent", remote_type="hybrid", with_document=True, url="https://example.com")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["roles"]["known_posting_date"]["proportion"] == 1.0
    assert summary["representativeness"]["known"] is False


def test_many_capture_sources_does_not_make_representativeness_known(client):
    with db_cursor() as cur:
        for i in range(10):
            _role(cur, f"R{i}", with_document=True, source=f"source-{i}")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    assert summary["representativeness"]["known"] is False


def test_concentration_is_described_as_observed_not_measured_bias(client):
    with db_cursor() as cur:
        for i in range(5):
            _role(cur, f"R{i}", country="Ireland")
        summary = mc.build_coverage_summary(cur, mc.CoverageScope())
    # The note explicitly disclaims measuring bias — it must never *assert*
    # bias, only concentration, so "no ... bias" is the only acceptable
    # appearance of the word.
    assert "no external target distribution" in summary["concentration"]["note"].lower()
    assert "concentrat" in summary["concentration"]["note"].lower()
    assert any("concentrated" in note.lower() for note in summary["limitations"])
    assert not any("biased" in note.lower() for note in summary["limitations"])


# --- Route tests ---------------------------------------------------------------


def test_summary_route_requires_no_setup_and_reflects_scope(client):
    resp = client.get("/api/market-coverage/summary", params={"year_from": 2020, "country": "Ireland"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"]["year_from"] == 2020
    assert body["scope"]["country"] == "Ireland"


def test_archetype_detail_route_404_for_unknown_archetype(client):
    resp = client.get(f"/api/market-coverage/archetypes/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_archetype_detail_route_matches_summary_scope(client):
    with db_cursor() as cur:
        archetype = _archetype(cur, "Senior Actuary")
        _role(cur, "R1", archetype_concept_id=archetype, country="Ireland")
        _role(cur, "R2", archetype_concept_id=archetype, country="United Kingdom")

    resp = client.get(f"/api/market-coverage/archetypes/{archetype}", params={"country": "Ireland"})
    assert resp.status_code == 200
    assert resp.json()["assigned_posting_count"] == 1


# --- §31 performance: bounded query count, no N+1 ------------------------------


def test_summary_query_count_does_not_grow_with_role_count(client):
    with db_cursor() as cur:
        archetype = _archetype(cur, "Senior Actuary")
        for i in range(3):
            _role(cur, f"Small{i}", archetype_concept_id=archetype, posting_date=date(2024, 1, 1), country="Ireland")
    with db_cursor() as cur, count_queries() as small_count:
        mc.build_coverage_summary(cur, mc.CoverageScope())

    with db_cursor() as cur:
        for i in range(40):
            _role(cur, f"Big{i}", archetype_concept_id=archetype, posting_date=date(2024, 1, 1), country="Ireland")
    with db_cursor() as cur, count_queries() as big_count:
        mc.build_coverage_summary(cur, mc.CoverageScope())

    assert big_count.count == small_count.count  # same query count regardless of role count — no per-role loop
