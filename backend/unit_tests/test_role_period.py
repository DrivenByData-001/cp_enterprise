from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.role_period import application_period
from app.routes.applications import ApplicationCreateInput, create_or_reopen_application
from app.routes.applications import ApplicationStatusUpdate, update_application_status
from app.routes.archetypes import ArchetypeSkillsInput
from app.models import Job, RoleMetadataUpdate


@pytest.mark.parametrize('posted, expected', [('2026-10-31', '2027-02-28'), ('2023-10-31', '2024-02-29'), ('2026-12-15', '2027-04-15')])
def test_four_calendar_months(posted, expected):
    result = application_period({'posting_date': posted}, date(2026, 1, 1))
    assert result['effective_closing_date'] == expected
    assert result['closing_date_assumed'] is True


def test_closing_date_is_inclusive_and_overrides_default():
    role = {'posting_date': '2025-01-01', 'application_closing_date': '2026-10-16'}
    assert application_period(role, date(2026, 10, 16))['can_apply'] is True
    assert application_period(role, date(2026, 10, 17))['application_period'] == 'historic'
    assert application_period(role)['closing_date_assumed'] is False


def test_entry_date_used_in_dublin_not_recapture_date():
    result = application_period({'created_at': datetime(2026, 6, 30, 23, 30, tzinfo=timezone.utc), 'captured_at': '2026-10-16'}, date(2026, 10, 16))
    assert result['effective_posting_date'] == '2026-07-01'
    assert result['effective_closing_date'] == '2026-11-01'
    assert result['posting_date_assumed'] is True


def test_closed_role_cannot_create_application_but_existing_history_opens():
    cur = MagicMock()
    role = {'instance_type': 'observed_posting', 'posting_date': '2000-01-01'}
    cur.fetchone.side_effect = [role, None]
    with patch('app.routes.applications.db_cursor') as context:
        context.return_value.__enter__.return_value = cur
        with pytest.raises(HTTPException) as exc:
            create_or_reopen_application(ApplicationCreateInput(role_instance_id=uuid4()))
        assert exc.value.status_code == 409
    assert not any('INSERT' in call.args[0] for call in cur.execute.call_args_list)
    cur.fetchone.side_effect = [role, {'id': 'existing'}]
    with patch('app.routes.applications.db_cursor') as context:
        context.return_value.__enter__.return_value = cur
        assert create_or_reopen_application(ApplicationCreateInput(role_instance_id=uuid4())) == {'id': 'existing', 'created': False}


def test_deadline_input_validation_and_clear():
    assert RoleMetadataUpdate(application_closing_date=None).model_dump(exclude_unset=True) == {'application_closing_date': None}
    with pytest.raises(ValueError):
        Job(title='Role', application_closing_date='2026-02-31')


def test_closed_application_cannot_reopen_after_role_deadline():
    cur = MagicMock()
    cur.fetchone.side_effect = [
        {'status': 'closed', 'role_instance_id': str(uuid4())},
        {'posting_date': '2000-01-01'},
    ]
    with patch('app.routes.applications.db_cursor') as context:
        context.return_value.__enter__.return_value = cur
        with pytest.raises(HTTPException) as exc:
            update_application_status(uuid4(), ApplicationStatusUpdate(status='preparing'))
        assert exc.value.status_code == 409


def test_target_skillset_is_bounded_and_distinct():
    ids = [uuid4() for _ in range(9)]
    assert len(ArchetypeSkillsInput(concept_ids=ids[:8]).concept_ids) == 8
    for invalid in (ids[:2], ids, [ids[0]] * 3):
        with pytest.raises(ValueError):
            ArchetypeSkillsInput(concept_ids=invalid)
