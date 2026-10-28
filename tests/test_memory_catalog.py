"""迁移只登记历史及来源，不能凭摘要生成科学通过或采纳。"""
import json
import unittest
import test_workflow as workflow
from aireos.errors import FrameworkError, PermissionDenied
try:
    from aireos.memory_catalog import MemoryCatalog
except ImportError:
    MemoryCatalog=None
from aireos.history import HistoryArchive


class MemoryCatalogTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp

    def catalog(self):
        self.assertIsNotNone(MemoryCatalog,'缺少历史转换与来源关系服务')
        (self.root/'history').mkdir(exist_ok=True)
        self.path=self.root/'history/evidence.yaml'
        self.path.write_text('- id: E-001\n  statement: oldroute conclusion\n  status: active\n- id: E-002\n  statement: correction text\n  status: active\n',encoding='utf-8')
        HistoryArchive(self.owner.store).configure([{'path':'history/evidence.yaml','format':'yaml-records'}],'catalog-history')
        return MemoryCatalog(self.owner.store)

    def test_import_is_idempotent_and_never_adopts_legacy_status(self):
        catalog=self.catalog()
        first=catalog.import_history('import')
        again=catalog.import_history('import')
        self.assertEqual(first['mapping'],again['mapping'])
        self.assertEqual(2,len(first['mapping']))
        self.assertEqual([],self.owner.store.list('decision'))
        self.assertEqual([],self.owner.store.list('review'))
        self.path.write_text(self.path.read_text(encoding='utf-8').replace('oldroute conclusion','revised statement'),encoding='utf-8')
        updated=catalog.import_history('import-next')
        key='history/evidence.yaml#E-001'
        self.assertEqual(first['mapping'][key]['id'],updated['mapping'][key]['id'])
        self.assertEqual(2,updated['mapping'][key]['rev'])
        old=self.owner.store.get(first['mapping'][key]['id'],1)
        self.assertEqual('historical_reference',old['data']['authority'])

    def test_summary_source_change_is_visible_and_revisions_are_preserved(self):
        catalog=self.catalog()
        note=catalog.add_note('route-note','route hint','Use matching geometry','only this case',
            [{'path':'history/evidence.yaml','legacy_id':'E-001'}],'note')
        self.assertEqual('source_current',catalog.note_state(note)['state'])
        self.path.write_text(self.path.read_text(encoding='utf-8').replace('oldroute conclusion','changed'),encoding='utf-8')
        self.assertEqual('source_changed',catalog.note_state(note)['state'])
        new=catalog.add_note('route-note','route hint','Recheck changed source','only this case',
            [{'path':'history/evidence.yaml','legacy_id':'E-001'}],'note-next')
        self.assertEqual(note['id'],new['id'])
        self.assertEqual(2,new['rev'])
        self.assertEqual('Use matching geometry',self.owner.store.get(note['id'],1)['data']['text'])

    def test_explicit_correction_is_recalled_without_matching_query(self):
        catalog=self.catalog()
        catalog.add_link({'path':'history/evidence.yaml','legacy_id':'E-002'},
            {'path':'history/evidence.yaml','legacy_id':'E-001'},'corrects','核对原文中的更正关系','link')
        packet=self.worker.prepare(self.task['id'],'oldroute','linked-context')
        ids=[entry['legacy_id'] for entry in packet['history']['matches']]
        self.assertIn('E-001',ids)
        self.assertIn('E-002',ids)
        self.assertEqual([],self.owner.store.list('decision'))

    def test_new_correction_marks_existing_summary_for_refresh(self):
        catalog=self.catalog()
        note=catalog.add_note('old-note','oldroute','Original result','historical',
            [{'path':'history/evidence.yaml','legacy_id':'E-001'}],'before-link')
        catalog.add_link({'path':'history/evidence.yaml','legacy_id':'E-002'},
            {'path':'history/evidence.yaml','legacy_id':'E-001'},'corrects','后续更正','later-link')
        self.assertEqual('source_changed',catalog.note_state(note)['state'])

    def test_summary_only_query_binds_its_raw_source_for_later_validation(self):
        catalog=self.catalog()
        catalog.add_note('alias-note','uniquefizz','uniquefizz compact rendering','historical',
            [{'path':'history/evidence.yaml','legacy_id':'E-001'}],'alias-note')
        packet=self.worker.prepare(self.task['id'],'uniquefizz','alias-context')
        self.path.write_text(self.path.read_text(encoding='utf-8').replace('oldroute conclusion','changed'),encoding='utf-8')
        self.assertFalse(self.worker.check_receipt(packet['id'])['valid'])

    def test_summary_only_query_detects_later_correction_link(self):
        catalog=self.catalog()
        catalog.add_note('alias-note','uniquefizz','uniquefizz rendering','historical',
            [{'path':'history/evidence.yaml','legacy_id':'E-001'}],'alias-note')
        packet=self.worker.prepare(self.task['id'],'uniquefizz','alias-context')
        catalog.add_link({'path':'history/evidence.yaml','legacy_id':'E-002'},
            {'path':'history/evidence.yaml','legacy_id':'E-001'},'corrects','后续更正','new-link')
        self.assertFalse(self.worker.check_receipt(packet['id'])['valid'])

    def test_required_source_policy_cannot_be_satisfied_by_empty_configuration(self):
        catalog=self.catalog()
        catalog.configure(['history/not-registered.md'],[],'required-source')
        with self.assertRaises(FrameworkError):
            self.worker.prepare(self.task['id'],'oldroute','missing-required')

    def test_scope_and_role_are_checked_for_catalog_writes(self):
        self.catalog()
        with self.assertRaises(PermissionDenied):
            MemoryCatalog(self.worker.store).import_history('worker-import')


if __name__=='__main__':
    unittest.main()
