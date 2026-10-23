"""旧账本是原文来源；大文件按条目检索且不自动授信。"""
import json
from pathlib import Path
import unittest
import test_workflow as workflow
from aireos.errors import FrameworkError, PermissionDenied
from aireos.history import HistoryArchive, session_context


class HistoryTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def sources(self, text=None):
        (self.root / 'history').mkdir(exist_ok=True)
        path = self.root / 'history/evidence.yaml'
        path.write_text(text or '- id: E-001\n  statement: 旧结果已撤回\n  status: deprecated\n- id: E-002\n  statement: 独立核查缺少边界条件\n  status: active\n', encoding='utf-8')
        archive = HistoryArchive(self.owner.store)
        archive.configure([{'path':'history/evidence.yaml','format':'yaml-records'}], 'configure-history')
        return archive, path

    def test_large_ledger_exact_identity_preserves_lines_and_does_not_adopt(self):
        padding = 'x' * 300000
        archive, path = self.sources('- schema_version: 1\n  id: E-001\n  statement: '+padding+'\n  status: active\n- schema_version: 1\n  id: E-002\n  statement: 旧结果已撤回\n  status: deprecated\n')
        result = archive.query('E-002')
        hit = result['matches'][0]
        self.assertEqual('E-002', hit['legacy_id'])
        self.assertEqual(5, hit['source']['start_line'])
        self.assertEqual('historical_reference', hit['authority'])
        self.assertEqual('deprecated', hit['legacy_status'])
        original = archive.get(hit['source']['path'], hit['legacy_id'], hit['source']['sha256'])
        self.assertIn('旧结果已撤回', original['text'])
        self.assertEqual([], self.owner.store.list('decision'))
        self.assertEqual([], self.owner.store.list('item'))

    def test_nested_yaml_and_markdown_keep_raw_identifiers(self):
        archive, path = self.sources('tasks:\n  - id: T-001\n    title: 已完成的任务\n    status: complete\n  - id: T-002\n    title: 在做的任务\n    status: running\n')
        note = self.root / 'history/failures.md'
        note.write_text('# 失败记录\n\n## F-07（历史编号）\nStatus: rejected\n不可混用两种功率口径。\n\n## F-008 — 另一个问题\nStatus: unknown\n', encoding='utf-8')
        archive.configure([{'path':'history/evidence.yaml','format':'yaml-records'}, {'path':'history/failures.md','format':'markdown'}], 'configure-both')
        self.assertEqual('F-07', archive.query('F-07')['matches'][0]['legacy_id'])
        self.assertEqual('T-001', archive.query('T-001')['matches'][0]['legacy_id'])

    def test_refresh_and_snapshot_get_keep_old_text_without_chat_context(self):
        archive, path = self.sources()
        first = archive.query('E-001')['matches'][0]
        path.write_text(path.read_text(encoding='utf-8').replace('旧结果已撤回','新版增加适用条件'), encoding='utf-8')
        again = HistoryArchive(self.owner.store).query('E-001')['matches'][0]
        self.assertNotEqual(first['source']['sha256'], again['source']['sha256'])
        self.assertIn('旧结果已撤回', archive.get(first['source']['path'], 'E-001', first['source']['sha256'])['text'])
        self.assertIn('新版增加适用条件', archive.get(first['source']['path'], 'E-001')['text'])

    def test_duplicate_or_unrecognised_entries_fail_instead_of_silently_disappearing(self):
        archive, path = self.sources()
        archive.query('E-001')
        path.write_text('- id: E-001\n  text: a\n- id: E-001\n  text: b\n', encoding='utf-8')
        with self.assertRaises(FrameworkError):
            archive.query('E-001')
        path.write_text('not a supported ledger', encoding='utf-8')
        with self.assertRaises(FrameworkError):
            archive.query('E-001')

    def test_config_requires_authority_and_blocks_escape_and_private_files(self):
        with self.assertRaises(PermissionDenied):
            HistoryArchive(self.worker.store).configure([], 'worker-configure')
        for path in ('../escape.md', '.aireos/owner.key', 'C:/outside.md'):
            with self.subTest(path=path), self.assertRaises(FrameworkError):
                HistoryArchive(self.owner.store).configure([{'path':path,'format':'markdown'}], 'bad-'+path)

    def test_display_budget_and_unknown_are_explicit(self):
        archive, path = self.sources()
        result = archive.query('旧结果', max_chars=1000)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)), 1000)
        absent = archive.query('unfindableword')
        self.assertEqual([], absent['matches'])
        self.assertIn('UNKNOWN', absent['coverage'])
        for bad in (True, 0, 101):
            with self.subTest(limit=bad), self.assertRaises(FrameworkError):
                archive.query('x',limit=bad)

    def test_entry_budget_is_not_raised_to_load_arbitrarily_large_records(self):
        from unittest.mock import patch
        archive, path = self.sources()
        with patch('aireos.history.MAX_ENTRY_BYTES', 8), self.assertRaises(FrameworkError):
            archive.query('E-001')

    def test_session_navigation_is_bounded_and_scope_filtered(self):
        self.sources()
        other = self.owner.create_task('其他任务','不属于试点',['检查'],['elsewhere'],'elsewhere-task')
        result = session_context(self.owner.store, output_prefix='outputs', query='E-001', max_chars=3000)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)), 3000)
        self.assertEqual([self.task['id']], [task['id'] for task in result['tasks']])
        self.assertNotIn(other['id'], json.dumps(result))
        self.assertTrue(result['history']['configured'])

    def test_changed_file_during_read_is_not_claimed_current(self):
        from unittest.mock import patch
        archive, path = self.sources()
        with patch('aireos.history.read_regular', side_effect=OSError('unstable source')), self.assertRaises(FrameworkError):
            archive.query('E-001')

    def test_snapshot_uses_its_original_format_after_reconfiguration(self):
        archive,path=self.sources()
        first=archive.query('E-001')['matches'][0]
        path.write_text('## F-001 — 新格式\nStatus: unknown\n',encoding='utf-8')
        archive.configure([{'path':'history/evidence.yaml','format':'markdown'}],'format-change')
        archive.query('F-001')
        self.assertIn('旧结果已撤回',archive.get('history/evidence.yaml','E-001',first['source']['sha256'])['text'])

    def test_short_word_is_not_lost_when_mixed_with_unmatched_long_word(self):
        archive,path=self.sources('- id: E-001\n  statement: FE 路径\n  status: active\n')
        self.assertEqual('E-001',archive.query('FE unmatchedword')['matches'][0]['legacy_id'])

    def test_adjacent_explicit_markdown_corrections_stay_with_original_record(self):
        archive,path=self.sources()
        note=self.root/'history/failures.md'
        note.write_text('## F-001 — 旧结论\n原说法。\n### F-001 更正段\n旧说法已被否定。\n## F-002 — 其他\n正文。\n',encoding='utf-8')
        archive.configure([{'path':'history/failures.md','format':'markdown'}],'correction-config')
        hit=archive.query('F-001')['matches'][0]
        full=archive.get('history/failures.md','F-001',hit['source']['sha256'])
        self.assertIn('旧说法已被否定',full['text'])
        self.assertEqual(1,full['start_line'])
        self.assertEqual(4,full['end_line'])
        note.write_text('## F-001 — a\n第一条\n## F-001 — b\n第二条\n',encoding='utf-8')
        with self.assertRaises(FrameworkError):
            archive.query('F-001')


if __name__ == '__main__':
    unittest.main()
