"""Batch reads must preserve verdicts and read changed evidence on the next request."""
from uuid import uuid4

from app import capability_engine as engine, db
from app.evidence_sources import source_context


class CountingCursor:
    def __init__(self, cur):
        self.cur = cur
        self.queries = 0

    def execute(self, *args):
        self.queries += 1
        return self.cur.execute(*args)

    def __getattr__(self, name):
        return getattr(self.cur, name)


def seed(cur, count=30):
    episode, claim = str(uuid4()), str(uuid4())
    cur.execute("INSERT INTO profile360.episodes (id, autonomy) VALUES (%s, 'independent')", (episode,))
    cur.execute("INSERT INTO profile360.claims (id, claim_text, depth, episode_id) VALUES (%s, 'Supported experience', 'owned', %s)", (claim, episode))
    requirements = []
    for index in range(count):
        cid = str(uuid4())
        kind = 'capability' if index % 2 else 'tool'
        cur.execute("INSERT INTO jobber.concept (id,canonical_name,type_code,status,origin) VALUES (%s,%s,%s,'active','curator')",
                    (cid, cid, kind))
        cur.execute("INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id,jobber_concept_id,mapping_basis,review_status) VALUES (%s,%s,'ai_suggested',%s)",
                    (claim, cid, ['accepted', 'unreviewed', 'rejected'][index % 3]))
        requirements.append({'concept_id': cid, 'type_code': kind})
    return requirements, claim


def verdict(cur, requirement, inputs=None):
    if requirement['type_code'] == 'capability':
        return engine.derive_capability_coverage(cur, requirement['concept_id'], inputs=inputs)
    return engine.atomic_concept_evidence(cur, requirement['concept_id'], inputs=inputs)


def test_thirty_requirements_use_eight_reads_and_preserve_individual_verdicts():
    with db.db_cursor() as raw:
        requirements, claim = seed(raw)
        cur = CountingCursor(raw)
        inputs = engine._role_evidence_inputs(cur, requirements)
        actual = [verdict(cur, row, inputs) for row in requirements]
        assert cur.queries == 8
        before = cur.queries
        expected = [verdict(cur, row) for row in requirements]
        assert cur.queries - before > 100
        assert actual == expected
        assert {row['status'] for row in actual} == {'evidenced', 'partial', 'not_found'}

        # No persistent cache: changing the source must change the next read.
        raw.execute("UPDATE profile360.claims SET claim_text='Corrected experience', depth='applied' WHERE id=%s", (claim,))
        fresh = engine._role_evidence_inputs(raw, requirements)
        assert fresh != inputs
        for rows in fresh['direct'].values():
            for row in rows:
                assert row['display'] == 'Corrected experience'
                assert row['depth'] == 'applied'
        # Another role's evidence must never leak into this role's result.
        unrelated, _ = seed(raw, count=2)
        isolated = engine._role_evidence_inputs(raw, unrelated)
        assert set(isolated['direct']).isdisjoint(inputs['direct'])


def test_configured_capability_and_composition_match_the_single_read_path():
    with db.db_cursor() as cur:
        requirements, _ = seed(cur, count=4)
        atomic, capability = requirements[0]['concept_id'], requirements[1]['concept_id']
        cur.execute("INSERT INTO jobber.capability_detail (concept_id,demonstration_standard,min_depth,min_autonomy,requires_all_core) VALUES (%s,'Own the work','owned','independent',true)", (capability,))
        cur.execute("INSERT INTO jobber.concept_edge (from_concept_id,to_concept_id,relation,necessity,status,origin) VALUES (%s,%s,'component_of','core','accepted','curator')", (atomic, capability))
        cur.execute("INSERT INTO jobber.person_capability_assertion (jobber_concept_id,note) VALUES (%s,'Context only')", (atomic,))
        inputs = engine._role_evidence_inputs(cur, requirements)
        assert [verdict(cur, row, inputs) for row in requirements] == [verdict(cur, row) for row in requirements]
        assert verdict(cur, requirements[1], inputs)['status'] == 'partial'


def test_component_evidence_is_batched_across_configured_capabilities():
    with db.db_cursor() as raw:
        requirements, _ = seed(raw)
        atomic = requirements[0]['concept_id']
        for row in requirements:
            if row['type_code'] != 'capability':
                continue
            raw.execute("INSERT INTO jobber.capability_detail (concept_id,demonstration_standard,min_depth,requires_all_core) VALUES (%s,'Own the work','owned',true)", (row['concept_id'],))
            raw.execute("INSERT INTO jobber.concept_edge (from_concept_id,to_concept_id,relation,necessity,status,origin) VALUES (%s,%s,'component_of','core','accepted','curator')", (atomic, row['concept_id']))
        cur = CountingCursor(raw)
        inputs = engine._role_evidence_inputs(cur, requirements)
        actual = [verdict(cur, row, inputs) for row in requirements]
        assert cur.queries == 11
        assert actual == [verdict(raw, row) for row in requirements]


def test_empty_role_does_not_load_the_profile():
    with db.db_cursor() as raw:
        cur = CountingCursor(raw)
        assert engine._role_evidence_inputs(cur, [])['direct'] == {}
        assert cur.queries == 0


def test_reviewed_modifiers_stay_per_mapping_and_expire_when_the_source_changes():
    with db.db_cursor() as cur:
        requirements, claim = seed(cur, count=4)
        cap_ids = [row['concept_id'] for row in requirements if row['type_code'] == 'capability']
        revision = source_context(cur, claim)['revision']
        for cid, depth in zip(cap_ids, ['applied', 'set_standard']):
            cur.execute('SELECT id FROM jobber.profile360_claim_mapping WHERE jobber_concept_id=%s', (cid,))
            mapping_id = cur.fetchone()['id']
            cur.execute("INSERT INTO jobber.evidence_assessment (mapping_id,source_revision,depth,autonomy) VALUES (%s,%s,%s,'assisted')",
                        (mapping_id, revision, depth))
        inputs = engine._role_evidence_inputs(cur, requirements)
        assert [inputs['direct'][cid][0]['depth'] for cid in cap_ids] == ['applied', 'set_standard']
        cur.execute("UPDATE profile360.claims SET claim_text='Changed source' WHERE id=%s", (claim,))
        fresh = engine._role_evidence_inputs(cur, requirements)
        assert [fresh['direct'][cid][0]['depth'] for cid in cap_ids] == ['owned', 'owned']
        assert [fresh['direct'][cid][0]['autonomy'] for cid in cap_ids] == ['independent', 'independent']
