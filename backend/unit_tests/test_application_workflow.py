from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException
from app import application_process as process, role_requirements as requirements

@pytest.mark.parametrize('disposition', ['covered', 'partial', 'gap'])
def test_acknowledged_evidence_allows_generation(disposition):
    with patch.object(process, 'preparation', return_value={'revision': 1}), \
         patch.object(process, 'checkpoints', return_value=(None, None, {}, [], None, None, True, True)), \
         patch.object(process.mode, 'reviewed_items', return_value=([{'decision': {'disposition': disposition}, 'stale': False}], None)):
        assert process.generation_readiness(None, 'app') == {'ready': True, 'blockers': []}


def test_blockers_link_to_exact_steps_and_guard_uses_same_policy():
    with patch.object(process, 'preparation', return_value={'revision': 1}), \
         patch.object(process, 'checkpoints', return_value=(None, None, {}, [], None, None, True, False)), \
         patch.object(process.mode, 'reviewed_items', return_value=([{'decision': None, 'stale': False}], None)):
        result = process.generation_readiness(None, 'app')
        assert [b['stage'] for b in result['blockers']] == ['requirements', 'evidence']
        assert result['blockers'][0]['href'] == '/applications/app/prepare/requirements'
        with pytest.raises(HTTPException) as exc:
            process.require_current_checkpoints(None, 'app')
        assert exc.value.status_code == 409
        assert 'requirement set' in exc.value.detail
        assert 'Opportunity' not in exc.value.detail


def test_recovery_is_scoped_and_creates_unreviewed_with_provenance():
    cur = MagicMock()
    occurrence = dict(role_instance_id='role', document_id='doc', extraction_run_id='run',
                      requirement_type='required', basis='stated', evidence_span='source text')
    cur.fetchall.return_value = [occurrence]
    cur.fetchone.side_effect = [None, {'id': 'new'}]
    with patch.object(requirements, 'filter_classified_occurrences', side_effect=lambda cur, rows: rows):
        result = requirements.resolve_occurrences_for_concept(cur, ['proposal'], 'concept', role_id='role')
    assert result['claims_created'] == 1
    calls = cur.execute.call_args_list
    assert calls[0].args[1] == (['proposal'], 'role', 'role')
    assert 'o.document_id = (SELECT document_id' in calls[0].args[0]
    assert "'unreviewed'" in calls[2].args[0]
    assert calls[3].args[1] == ('new', 'doc', 'source text', 'stated', 'run')


def test_recovery_preserves_current_rejection():
    cur = MagicMock()
    cur.fetchall.return_value = [dict(role_instance_id='role', requirement_type='required', basis='stated', evidence_span='source')]
    cur.fetchone.return_value = {'exists': True}
    with patch.object(requirements, 'filter_classified_occurrences', side_effect=lambda cur, rows: rows):
        result = requirements.resolve_occurrences_for_concept(cur, ['p'], 'c', role_id='role')
    assert result['already_covered'] == 1
    assert not any('INSERT' in c.args[0] for c in cur.execute.call_args_list)


def test_recovery_skips_incomplete_occurrences():
    cur = MagicMock()
    cur.fetchall.return_value = [dict(requirement_type='required', basis='stated', evidence_span=None)]
    with patch.object(requirements, 'filter_classified_occurrences', side_effect=lambda cur, rows: rows):
        result = requirements.resolve_occurrences_for_concept(cur, ['p'], 'c', role_id='role')
    assert result['claims_created'] == 0
    assert cur.execute.call_count == 1
