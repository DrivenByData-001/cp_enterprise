"""Database contract for complete CV career reads, independent of vocabulary mapping."""
from app import db, profile360_reader as p360


def test_cv_reads_unmapped_claims_and_project_ancestry(client):
    with db.db_cursor() as cur:
        cur.execute("INSERT INTO profile360.episodes (title, organisation) VALUES ('Actuary','Employer') RETURNING id")
        parent = cur.fetchone()['id']
        cur.execute("INSERT INTO profile360.episodes (title, episode_type, parent_episode_id) "
                    "VALUES ('Model project','project',%s) RETURNING id", (parent,))
        project = cur.fetchone()['id']
        cur.execute("INSERT INTO profile360.claims (claim_text, episode_id, evidence_class) "
                    "VALUES ('Validated migration',%s,'stated') RETURNING id", (project,))
        claim = cur.fetchone()['id']
        cur.execute("INSERT INTO jobber.profile360_disposition (kind, profile360_id, disposition) "
                    "VALUES ('claim',%s,'needs_review')", (claim,))
        episodes, claims = p360.cv_career_records(cur)
    assert any(e['id'] == project and e['parent_episode_id'] == parent for e in episodes)
    assert any(c['id'] == claim and c['cv_disposition'] == 'needs_review' for c in claims)
