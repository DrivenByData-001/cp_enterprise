"""Pure contract tests runnable without Postgres; SQL tests live in tests/."""
import copy
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import HTTPException

from app import application_mode as mode, application_generation as gen
from app.routes import application_mode as routes

APP = 'a0000000-0000-0000-0000-000000000001'
CONCEPT = 'c0000000-0000-0000-0000-000000000001'
REF = 'profile_claim:10000000-0000-0000-0000-000000000001'
ITEM = {'concept': {'id': CONCEPT, 'canonical_name': 'Governance'},
        'role_side': {'requirement_claim_id': 'r1', 'evidence_span': 'Model governance'},
        'person_side': {'mappings': [], 'coverage': None}}
SOURCE = {'ref': REF, 'kind': 'profile_claim', 'label': 'Model challenge',
          'content': 'Challenged assumptions in 2019', 'source_revision': 'v1', 'episode_id': None}


class EvidenceContractTests(unittest.TestCase):
    def test_source_text_changes_invalidate_review(self):
        old = mode.review_fingerprint(ITEM, [SOURCE])
        changed = {**SOURCE, 'content': 'Corrected to 2020', 'source_revision': 'v2'}
        self.assertNotEqual(old, mode.review_fingerprint(ITEM, [changed]))

    def test_requirement_changes_invalidate_review(self):
        changed = copy.deepcopy(ITEM)
        changed['role_side']['evidence_span'] = 'Lead validation'
        self.assertNotEqual(mode.review_fingerprint(ITEM, []), mode.review_fingerprint(changed, []))

    def test_candidate_matches_do_not_invalidate_accepted_selection(self):
        changed = copy.deepcopy(ITEM)
        changed['person_side']['mappings'] = [{'id': 'unrelated-new-match'}]
        self.assertEqual(mode.review_fingerprint(ITEM, [SOURCE]), mode.review_fingerprint(changed, [SOURCE]))

    def test_disappeared_source_is_stale_and_review_is_retained(self):
        decision = {'selected_refs': [REF], 'input_fingerprint': mode.review_fingerprint(ITEM, [SOURCE])}
        with patch.object(mode, 'application', return_value={'role_instance_id': 'role'}), \
             patch.object(mode, 'decisions', return_value={CONCEPT: decision}), \
             patch.object(mode, 'candidates', return_value=[]), \
             patch.object(mode, 'canonical_source', side_effect=HTTPException(409, 'missing')):
            items, _ = mode.reviewed_items(MagicMock(), APP, {'items': [ITEM]})
        self.assertTrue(items[0]['stale'])
        self.assertEqual(items[0]['decision'], decision)

    def test_existing_source_is_not_replaced_by_other_matches(self):
        decision = {'selected_refs': [REF], 'input_fingerprint': mode.review_fingerprint(ITEM, [SOURCE])}
        with patch.object(mode, 'application', return_value={'role_instance_id': 'role'}), \
             patch.object(mode, 'decisions', return_value={CONCEPT: decision}), \
             patch.object(mode, 'candidates', return_value=[]), \
             patch.object(mode, 'canonical_source', return_value=SOURCE):
            items, _ = mode.reviewed_items(MagicMock(), APP, {'items': [ITEM]})
        self.assertFalse(items[0]['stale'])
        self.assertEqual(items[0]['sources'], [SOURCE])

    def test_generation_does_not_restore_unselected_profile_sources(self):
        req = {'concept_id': CONCEPT, 'canonical_name': 'Governance', 'type_code': 'tool',
               'requirement_type': 'required', 'basis': 'user_asserted', 'evidence_span': None,
               'person_side': {'mappings': [{'profile360_id': 'unselected', 'mapping_kind': 'claim'}]},
               'application_decision': {'disposition': 'covered', 'rationale': 'Use this example', 'sources': [SOURCE]}}
        bundle = gen.EvidenceBundle(APP, 'role', {}, {}, {}, [req], [], [], None,
                                    [{'id': 'episode', 'responsibilities': 'Unselected career facts'}], curated=True)
        sources = gen.build_source_registry(bundle, artifact_type='positioning', active_positioning=None)
        refs = {s.ref for s in sources}
        self.assertIn(REF, refs)
        self.assertNotIn('profile_claim:unselected', refs)
        episode_source = next(s for s in sources if s.ref == 'profile_episode:episode')
        self.assertNotIn('Unselected career facts', episode_source.content)
        self.assertIn('application_decision:' + CONCEPT, refs)

    def test_incomplete_curation_blocks_generation_but_not_reads(self):
        bundle = gen.EvidenceBundle(APP, 'role', {}, {}, {}, [{'concept_id': CONCEPT}], [], [], None, [], curated=True)
        with patch.object(gen, 'gather_application_evidence', return_value=bundle), \
             patch('app.application_process.require_current_checkpoints'):
            with self.assertRaises(HTTPException) as error:
                gen.build_application_generation_context(MagicMock(), APP, artifact_type='positioning')
        self.assertEqual(error.exception.status_code, 409)

    def check_rejected_write(self, payload, decision=None, source=SOURCE, expected=409):
        cur = MagicMock()
        with patch.object(routes, 'db_cursor') as context, \
             patch.object(mode, 'application', return_value={'role_instance_id': 'role'}), \
             patch.object(routes, 'build_role_comparison', return_value={'items': [ITEM]}), \
             patch.object(mode, 'decisions', return_value={CONCEPT: decision} if decision else {}), \
             patch.object(mode, 'canonical_source', return_value=source):
            context.return_value.__enter__.return_value = cur
            with self.assertRaises(HTTPException) as error:
                routes.save_decision(UUID(APP), UUID(CONCEPT), routes.DecisionInput(**payload))
        self.assertEqual(error.exception.status_code, expected)
        cur.execute.assert_not_called()

    def test_second_tab_cannot_overwrite_review(self):
        self.check_rejected_write({'disposition': 'gap', 'revision': 0,
                                   'requirement_fingerprint': mode.fingerprint(ITEM['role_side'])}, {'revision': 1})

    def test_changed_source_cannot_be_accepted_without_rereview(self):
        self.check_rejected_write({'disposition': 'covered', 'selected_refs': [REF], 'source_revisions': {REF: 'old'},
                                   'requirement_fingerprint': mode.fingerprint(ITEM['role_side'])})

    def test_covered_needs_evidence(self):
        self.check_rejected_write({'disposition': 'covered', 'selected_refs': [],
                                   'requirement_fingerprint': mode.fingerprint(ITEM['role_side'])}, expected=422)

    def test_gap_cannot_have_selected_evidence(self):
        self.check_rejected_write({'disposition': 'gap', 'selected_refs': [REF],
                                   'requirement_fingerprint': mode.fingerprint(ITEM['role_side'])}, expected=422)


if __name__ == '__main__':
    unittest.main()
