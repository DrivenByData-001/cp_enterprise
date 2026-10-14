"""The workspace consumes the same application curation as Application Mode."""
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.routes import applications as routes


def test_workspace_exposes_review_and_sources_without_overwriting_profile_matching():
    app_id = UUID('a0000000-0000-0000-0000-000000000001')
    raw = {'concept': {'id': 'concept'}, 'status': 'not_found',
           'role_side': {'requirement_source': 'claim'}}
    comparison = {'items': [raw], 'role': {}, 'review_summary': {},
                  'counts': {'not_found': 1}, 'blocking_gaps': [],
                  'unverified_required': [], 'engine_version': 'test'}
    reviewed = {**raw, 'decision': {'disposition': 'partial', 'selected_refs': ['profile_claim:1']},
                'sources': [{'ref': 'profile_claim:1', 'content': 'Delivered reporting changes'}],
                'stale': False, 'attention_reason': None}
    cur = MagicMock()
    with patch.object(routes, 'db_cursor') as db, \
         patch.object(routes, '_require_application', return_value={'role_instance_id': 'role'}), \
         patch.object(routes, 'build_role_comparison', return_value=comparison), \
         patch.object(routes, '_notes_for_application', return_value=[]), \
         patch.object(routes.mode, 'reviewed_items', return_value=([reviewed], comparison)) as review:
        db.return_value.__enter__.return_value = cur
        result = routes.get_application_evidence(app_id)
    review.assert_called_once_with(cur, str(app_id), comparison)
    item = result['items'][0]
    assert item['decision'] == reviewed['decision']
    assert item['sources'] == reviewed['sources']
    assert item['stale'] is False
    assert item['status'] == 'not_found'
    assert item['role_requirement_reviewed'] is True
    assert result['counts'] == comparison['counts']
