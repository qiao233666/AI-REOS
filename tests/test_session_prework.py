"""接手入口应补回明确更正，并在同一预算中呈现计量和约束。"""
import json
import unittest
import test_workflow as workflow
from aireos.history import HistoryArchive,session_context
from aireos.memory_catalog import MemoryCatalog
from aireos.reading import record,usage


class SessionPreworkTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp

    def test_session_uses_correction_links_even_when_wording_does_not_match(self):
        (self.root/'old').mkdir()
        (self.root/'old/log.yaml').write_text('- id: E-001\n  statement: uniqueneedle\n- id: E-002\n  statement: subsequent correction\n',encoding='utf-8')
        HistoryArchive(self.owner.store).configure([{'path':'old/log.yaml','format':'yaml-records'}],'configure')
        MemoryCatalog(self.owner.store).add_link({'path':'old/log.yaml','legacy_id':'E-002'},
            {'path':'old/log.yaml','legacy_id':'E-001'},'corrects','原文关联','link')
        value=session_context(self.owner.store,query='uniqueneedle')
        self.assertIn('E-002',[row['legacy_id'] for row in value['history']['matches']])

    def test_reading_counter_counts_repeated_return_and_full_serialized_size(self):
        first=record(self.owner.store,'test',{'text':'原文'*50},threshold=200)
        self.assertEqual(len(json.dumps(first,ensure_ascii=False)),first['reading']['chars'])
        second=record(self.owner.store,'test',{'text':'原文'*50},threshold=200)
        self.assertEqual(first['reading']['chars']+len(json.dumps(second,ensure_ascii=False)),second['reading']['chars'])
        self.assertTrue(second['reading']['consider_handoff'])
        self.assertFalse(second['reading']['hard_cap'])
        self.assertEqual(2,usage(self.owner.store,'test')['calls'])

    def test_default_session_recovers_constraints_without_history_scan(self):
        MemoryCatalog(self.owner.store).configure([], [{'key':'scope','text':'保留来源与适用条件','outputs':['outputs']}], 'rules')
        value=session_context(self.owner.store,output_prefix='outputs',max_chars=2000)
        self.assertIn('constraints',value)
        self.assertEqual('scope',value['constraints'][0]['key'])
        self.assertLessEqual(len(json.dumps(value,ensure_ascii=False)),2000)


if __name__=='__main__':
    unittest.main()
