"""历史检查必须参与开工，短记录不能掩盖更正、缺项或原文变化。"""
import json
from pathlib import Path
import unittest
import test_workflow as workflow
from aireos.errors import FrameworkError, StaleInput
from aireos.history import HistoryArchive, session_context


class PreworkMemoryTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def sources(self, crowded=False):
        folder = self.root / 'history'
        folder.mkdir(exist_ok=True)
        evidence = folder / 'evidence.yaml'
        evidence.write_text(''.join('- id: E-%03d\n  statement: dualroute interface parameters\n' % i
            for i in range(1, 35 if crowded else 2)), encoding='utf-8')
        failure = folder / 'failures.md'
        failure.write_text('## F-001 — dualroute\nVoxel-only thinbar comparison failed. Use verified_solver.py with matching geometry.\n', encoding='utf-8')
        archive = HistoryArchive(self.owner.store)
        archive.configure([{'path':'history/evidence.yaml','format':'yaml-records'},
            {'path':'history/failures.md','format':'markdown'}], 'history-config')
        return archive, evidence, failure

    def test_prepare_binds_legacy_hits_even_without_explicit_task_input(self):
        self.sources()
        packet = self.worker.prepare(self.task['id'], 'dualroute', 'prepare-history')
        self.assertIn('history', packet)
        self.assertIn('F-001', [entry['legacy_id'] for entry in packet['history']['matches']])
        stored = self.owner.store.get(packet['id'])
        self.assertTrue(stored['data']['history_check']['references'])

    def test_changed_selected_legacy_entry_invalidates_receipt(self):
        _, _, failure = self.sources()
        packet = self.worker.prepare(self.task['id'], 'dualroute', 'before-history-change')
        failure.write_text(failure.read_text(encoding='utf-8')+'Correction: prior threshold withdrawn.\n', encoding='utf-8')
        self.assertFalse(self.worker.check_receipt(packet['id'])['valid'])

    def test_unrelated_append_does_not_invalidate_but_new_related_entry_is_reported(self):
        _, evidence, _ = self.sources()
        packet = self.worker.prepare(self.task['id'], 'dualroute', 'before-append')
        with evidence.open('a',encoding='utf-8') as f:
            f.write('- id: E-100\n  statement: unrelatedneedle\n')
        self.assertTrue(self.worker.check_receipt(packet['id'])['valid'])
        with evidence.open('a',encoding='utf-8') as f:
            f.write('- id: E-101\n  statement: dualroute new investigation\n')
        check = self.worker.check_receipt(packet['id'])
        self.assertTrue(check['valid'])
        self.assertIn('E-101', json.dumps(check['warnings']))

    def test_preupgrade_receipt_cannot_claim_registered_history_checked(self):
        old = self.worker.prepare(self.task['id'], 'dualroute', 'old-context')
        self.sources()
        self.assertFalse(self.worker.check_receipt(old['id'])['valid'])

    def test_failure_candidate_is_not_crowded_out_by_interface_hits(self):
        self.sources(crowded=True)
        packet = self.worker.prepare(self.task['id'], 'dualroute interface parameters', 'crowded')
        self.assertIn('history',packet)
        self.assertIn('F-001',[entry['legacy_id'] for entry in packet['history']['matches']])

    def test_missing_required_source_blocks_prepare_and_does_not_create_receipt(self):
        _, _, failure = self.sources()
        failure.rename(failure.with_suffix('.archived'))
        before=len(self.owner.store.list('receipt'))
        with self.assertRaises(FrameworkError):
            self.worker.prepare(self.task['id'],'dualroute','missing-history')
        self.assertEqual(before,len(self.owner.store.list('receipt')))

    def test_whole_prepare_response_respects_budget(self):
        self.sources(crowded=True)
        for cap in (2000,6000):
            packet=self.worker.prepare(self.task['id'],'dualroute','budget-'+str(cap),max_chars=cap)
            self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),cap)
            self.assertIn('history',packet)
            for card in packet['history']['matches']:
                self.assertEqual(len(card['body']),card['source']['fragment_end']-card['source']['fragment_start'])

    def test_document_sources_have_explicit_identity_and_category(self):
        archive,_,_=self.sources()
        doc=self.root/'history/policy.md'
        doc.write_text('Specific task constraint.\n',encoding='utf-8')
        try:
            archive.configure([{'path':'history/policy.md','format':'document','categories':['decisions']}], 'document-config')
        except FrameworkError as e:
            self.fail('缺少明确文档来源适配: '+str(e))
        self.assertEqual('DOC-1',archive.query('constraint')['matches'][0]['legacy_id'])

    def test_paginated_read_keeps_original_snapshot_after_source_changes(self):
        archive,_,failure=self.sources()
        failure.write_text('## F-001 — dualroute\n'+'abcdef'*1000,encoding='utf-8')
        hit=archive.query('F-001')['matches'][0]
        self.assertTrue(hasattr(archive,'read'),'缺少有界原文展开入口')
        first=archive.read('history/failures.md','F-001',hit['source']['sha256'],max_chars=1200)
        failure.write_text('## F-001 — dualroute\nreplacement',encoding='utf-8')
        second=archive.read('history/failures.md','F-001',hit['source']['sha256'],offset=first['next_offset'],max_chars=1200)
        self.assertNotIn('replacement',second['text'])
        self.assertLessEqual(len(json.dumps(second,ensure_ascii=False)),1200)
        self.assertEqual(first['next_offset'],second['offset'])


if __name__=='__main__':
    unittest.main()
