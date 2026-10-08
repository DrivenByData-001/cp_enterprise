"""Source-bound semantic classification before capability vocabulary matching."""

from hashlib import md5

from psycopg.types.json import Jsonb

from .concept_linking import normalize_name


def source_hash(text: str) -> str:
    # A change detector, not a security primitive. Matches PostgreSQL md5(text).
    return md5(text.encode('utf-8'), usedforsecurity=False).hexdigest()


def classification_snapshots(cur, role_ids: list[str]) -> dict[str, dict]:
    if not role_ids:
        return {}
    cur.execute(
        "SELECT DISTINCT ON (s.role_instance_id) s.* "
        "FROM jobber.requirement_classification s "
        "JOIN jobber.role_instance r ON r.id=s.role_instance_id AND r.document_id=s.document_id "
        "JOIN jobber.document d ON d.id=s.document_id AND s.source_hash=md5(d.content_text) "
        "WHERE s.role_instance_id=ANY(%s::uuid[]) "
        "ORDER BY s.role_instance_id, s.created_at DESC, s.id DESC", (role_ids,),
    )
    return {str(row['role_instance_id']): dict(row) for row in cur.fetchall()}


def save_classification(cur, role_id, document, run_id, items):
    statements = [{**item.model_dump(), 'surface_key': normalize_name(item.surface_form)} for item in items]
    cur.execute(
        'INSERT INTO jobber.requirement_classification '
        '(role_instance_id,document_id,extraction_run_id,source_hash,statements) VALUES (%s,%s,%s,%s,%s)',
        (role_id, document['id'], run_id, source_hash(document['content_text']), Jsonb(statements)),
    )


def excluded_surface_keys(snapshot: dict) -> set[str]:
    """A professional reading wins if the same term has several meanings."""
    professional = {item['surface_key'] for item in snapshot['statements']
                    if item['category'] == 'professional_requirement'}
    return {item['surface_key'] for item in snapshot['statements']} - professional


def pending_requirement_proposals(cur, role_ids: list[str]) -> list[dict]:
    """Use the same scope for application review and completeness counts.

    Legacy proposals remain visible until AI has classified them in this role's
    current document. No shared vocabulary status is modified by classification.
    """
    if not role_ids:
        return []
    cur.execute(
        "SELECT cp.*, o.role_instance_id, o.evidence_span AS role_evidence_span "
        "FROM jobber.concept_proposal cp "
        "JOIN jobber.concept_proposal_occurrence o ON o.concept_proposal_id=cp.id "
        "WHERE o.role_instance_id=ANY(%s::uuid[]) AND cp.status='pending' "
        "ORDER BY cp.surface_form, cp.id", (role_ids,),
    )
    return filter_classified_occurrences(cur, [dict(row) for row in cur.fetchall()])


def filter_classified_occurrences(cur, rows: list[dict]) -> list[dict]:
    snapshots = classification_snapshots(cur, list({str(row['role_instance_id']) for row in rows}))
    excluded = {role: excluded_surface_keys(snapshot) for role, snapshot in snapshots.items()}
    return [row for row in rows
            if normalize_name(row['surface_form']) not in excluded.get(str(row['role_instance_id']), set())]
