"""Phase 2 evaluation debt + Phase 3 capability-agreement evaluation (brief
§24-26/§39). Each metric is exercised both with no gold data — asserting
`measured: False`, never a fabricated pass — and with a small labelled
fixture proving the machinery computes the right number."""

from app import db, evaluation


def _concept(cur, name, type_code="tool"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return cur.fetchone()["id"]


def test_span_validity_not_measured_with_no_claims(client):
    with db.db_cursor() as cur:
        result = evaluation.span_validity(cur)
    assert result["measured"] is False
    assert result["value"] is None


def test_span_validity_reads_1_when_all_stored_spans_are_valid(client):
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="Requires Python.", provenance_quality="original")
        role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "R", "document_id": document_id}, skills=[])
        concept_id = _concept(cur, "Python")
        cur.execute(
            "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span) "
            "VALUES (%s, %s, 'required', 'stated', %s, 'Requires Python.')",
            (role_id, concept_id, document_id),
        )
        result = evaluation.span_validity(cur)
    assert result["measured"] is True
    assert result["value"] == 1.0
    assert result["n"] == 1


def test_proposals_per_document_not_measured_with_no_documents(client):
    with db.db_cursor() as cur:
        result = evaluation.proposals_per_document(cur)
    assert result["measured"] is False


def test_modifier_accuracy_is_not_applicable_in_this_build(client):
    with db.db_cursor() as cur:
        result = evaluation.modifier_accuracy(cur)
    assert result["measured"] is False
    assert "not applicable" in result["note"]


def test_concept_linking_f1_not_measured_without_gold(client):
    with db.db_cursor() as cur:
        result = evaluation.concept_linking_f1(cur)
    assert result["measured"] is False


def test_concept_linking_f1_perfect_match(client):
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="Requires Python.", provenance_quality="original")
        role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "R", "document_id": document_id}, skills=[])
        concept_id = _concept(cur, "Python")
        cur.execute(
            "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span) "
            "VALUES (%s, %s, 'required', 'stated', %s, 'Requires Python.')",
            (role_id, concept_id, document_id),
        )
        cur.execute("INSERT INTO jobber.gold_document (document_id, split, stratum) VALUES (%s, 'dev', 'actuarial_core')", (document_id,))
        cur.execute(
            "INSERT INTO jobber.gold_claim (document_id, concept_id, relation, evidence_span, is_core) VALUES (%s, %s, 'requires', 'Requires Python.', TRUE)",
            (document_id, concept_id),
        )
        result = evaluation.concept_linking_f1(cur)
    assert result["measured"] is True
    assert result["value"] == 1.0
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0


def test_concept_linking_f1_partial_recall(client):
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="Requires Python and SQL.", provenance_quality="original")
        python_id, sql_id = _concept(cur, "Python"), _concept(cur, "SQL")
        cur.execute("INSERT INTO jobber.gold_document (document_id, split, stratum) VALUES (%s, 'dev', 'actuarial_core')", (document_id,))
        cur.execute(
            "INSERT INTO jobber.gold_claim (document_id, concept_id, relation, evidence_span, is_core) VALUES (%s, %s, 'requires', 'Python', TRUE)",
            (document_id, python_id),
        )
        cur.execute(
            "INSERT INTO jobber.gold_claim (document_id, concept_id, relation, evidence_span, is_core) VALUES (%s, %s, 'requires', 'SQL', TRUE)",
            (document_id, sql_id),
        )
        # system only ever found one of the two gold concepts
        role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "R", "document_id": document_id}, skills=[])
        cur.execute(
            "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span) "
            "VALUES (%s, %s, 'required', 'stated', %s, 'Python')",
            (role_id, python_id, document_id),
        )
        result = evaluation.concept_linking_f1(cur)
    assert result["measured"] is True
    assert result["recall"] == 0.5
    assert result["precision"] == 1.0


def test_capability_agreement_not_measured_without_gold(client):
    with db.db_cursor() as cur:
        result = evaluation.capability_agreement(cur)
    assert result["measured"] is False


def test_capability_agreement_scores_engine_against_gold_judgment(client):
    with db.db_cursor() as cur:
        cap_id = _concept(cur, "Some capability", type_code="capability")
        cur.execute(
            "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) VALUES (%s, 'x', 'owned')",
            (cap_id,),
        )
        cur.execute(
            "INSERT INTO jobber.capability_gold_judgment (capability_concept_id, expected_status, notes) "
            "VALUES (%s, 'not_found', 'no evidence exists yet')",
            (cap_id,),
        )
        result = evaluation.capability_agreement(cur)
    assert result["measured"] is True
    assert result["value"] == 1.0
    assert result["n"] == 1
    assert result["details"][0]["agree"] is True


def test_capability_agreement_disagreement_is_scored_honestly(client):
    with db.db_cursor() as cur:
        cap_id = _concept(cur, "Another capability", type_code="capability")
        cur.execute(
            "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) VALUES (%s, 'x', 'owned')",
            (cap_id,),
        )
        cur.execute(
            "INSERT INTO jobber.capability_gold_judgment (capability_concept_id, expected_status) VALUES (%s, 'evidenced')",
            (cap_id,),
        )
        result = evaluation.capability_agreement(cur)
    assert result["value"] == 0.0  # engine says not_found (no evidence configured), gold expected evidenced


def test_eval_report_endpoint(client):
    resp = client.get("/api/eval/report")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("span_validity", "concept_linking_f1", "modifier_accuracy", "proposals_per_document", "capability_agreement"):
        assert key in body
        assert "measured" in body[key]
