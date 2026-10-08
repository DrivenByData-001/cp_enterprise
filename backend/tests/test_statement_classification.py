from datetime import datetime, timezone

from app import db, extraction
from app.models import RequirementExtractionResult, RequirementItem
from app.requirement_scope import classification_snapshots
from app.role_requirements import load_requirement_review_summary
from app.vocabulary_curation import resolve_surface_form_group
from tests.test_extraction_vocabulary_outcome import _fake_run, _make_role_with_document, _make_active_concept


def item(term, span, category):
    return RequirementItem(category=category, classification_reason='Meaning in the source context',
                           surface_form=term, evidence_span=span, basis='stated', requirement_type='required')


def test_semantic_routing_without_metadata_labels_and_preserves_all_categories(client, monkeypatch):
    readings = [item('Dublin', 'Our office is in Dublin.', 'role_metadata'),
                item('work permission', 'You need work permission.', 'eligibility_condition'),
                item('Irish regulation', 'Knowledge of Irish regulation is essential.', 'professional_requirement'),
                item('Apply now', 'Apply now via our website.', 'irrelevant')]
    monkeypatch.setattr(extraction, 'run_json_task', lambda **kw: _fake_run(
        RequirementExtractionResult(requirements=readings), kw['task'], kw['prompt_name']))
    with db.db_cursor() as cur:
        role, _ = _make_role_with_document(cur, '\n'.join(r.evidence_span for r in readings))
        concept = _make_active_concept(cur, 'irish regulation', type_code='knowledge')
        result = extraction.extract_role_requirements(cur, role)
        assert result['excluded_metadata_count'] == 3
        snapshot = classification_snapshots(cur, [role])[role]
        assert {s['category'] for s in snapshot['statements']} == {r.category for r in readings}
        cur.execute('SELECT concept_id FROM jobber.requirement_claim WHERE role_instance_id=%s', (role,))
        assert [str(row['concept_id']) for row in cur.fetchall()] == [concept]
        cur.execute('SELECT count(*) n FROM jobber.concept_proposal')
        assert cur.fetchone()['n'] == 0


def test_global_vocabulary_acceptance_does_not_resurrect_classified_metadata(client, monkeypatch):
    reading = item('Dublin', 'Our office is in Dublin.', 'role_metadata')
    monkeypatch.setattr(extraction, 'run_json_task', lambda **kw: _fake_run(
        RequirementExtractionResult(requirements=[reading]), kw['task'], kw['prompt_name']))
    with db.db_cursor() as cur:
        role, document = _make_role_with_document(cur, reading.evidence_span)
        other, other_document = _make_role_with_document(cur, 'Knowledge of Dublin markets required.')
        cur.execute("INSERT INTO jobber.concept_proposal(surface_form) VALUES ('dublin') RETURNING id")
        proposal = cur.fetchone()['id']
        for rid, did, span in [(role, document, reading.evidence_span), (other, other_document, 'Knowledge of Dublin markets required.')]:
            cur.execute('INSERT INTO jobber.concept_proposal_occurrence '
                        '(concept_proposal_id,role_instance_id,document_id,evidence_span,basis,requirement_type) '
                        "VALUES(%s,%s,%s,%s,'stated','required')", (proposal, rid, did, span))
        extraction.extract_role_requirements(cur, role)
        resolve_surface_form_group(cur, ['dublin'], action='accept_new',
            now=datetime.now(timezone.utc), canonical_name='Dublin markets', type_code='domain', definition='Market knowledge')
        cur.execute('SELECT role_instance_id FROM jobber.requirement_claim WHERE role_instance_id=ANY(%s::uuid[])', ([role, other],))
        assert [str(row['role_instance_id']) for row in cur.fetchall()] == [other]
        assert load_requirement_review_summary(cur, role)['needs_reextraction'] == 0
        # Changing source invalidates the old AI classification, including suppression.
        cur.execute("UPDATE jobber.document SET content_text='Knowledge of Dublin markets required.' WHERE id=%s", (document,))
        assert classification_snapshots(cur, [role]) == {}
        assert load_requirement_review_summary(cur, role)['needs_reextraction'] == 1
