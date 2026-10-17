"""Application dates use calendar months and the workspace's Dublin day."""
from calendar import monthrange
from datetime import date, datetime
from zoneinfo import ZoneInfo

WORKSPACE_ZONE = ZoneInfo('Europe/Dublin')
TODAY_SQL = "(CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Dublin')::date"
END_DATE_SQL = "COALESCE(ri.application_closing_date, (COALESCE(ri.posting_date, (ri.created_at AT TIME ZONE 'Europe/Dublin')::date) + INTERVAL '4 months')::date)"


def _date(value):
    if isinstance(value, datetime):
        return value.astimezone(WORKSPACE_ZONE).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10]) if value else None


def application_period(role: dict, today: date | None = None) -> dict:
    posted = _date(role.get('posting_date')) or _date(role.get('created_at'))
    closing = _date(role.get('application_closing_date'))
    if closing is None and posted:
        month_index = posted.year * 12 + posted.month - 1 + 4
        year, month = divmod(month_index, 12)
        closing = date(year, month + 1, min(posted.day, monthrange(year, month + 1)[1]))
    live = closing is not None and closing >= (today or datetime.now(WORKSPACE_ZONE).date())
    return {
        'effective_posting_date': posted.isoformat() if posted else None,
        'posting_date_assumed': not bool(role.get('posting_date')),
        'effective_closing_date': closing.isoformat() if closing else None,
        'closing_date_assumed': not bool(role.get('application_closing_date')),
        'application_period': 'live' if live else 'historic',
        'can_apply': live,
    }
