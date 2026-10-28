"""历史来源分类、候选机会与旧快照分页的合成测试。"""
import json
import unittest
import test_workflow as workflow
from aireos.errors import FrameworkError
from aireos.history import HistoryArchive


class HistoryExtensionTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def test_document_snapshot_pagination_and_paths(self):
        folder = self.root / 'history'
        folder.mkdir()
        path = folder / 'notes.md'
        text = '检索目标 ' + '旧内容🙂' * 1600
        path.write_text(text, encoding='utf-8')
        archive = HistoryArchive(self.owner.store)
        archive.configure([dict(path='history/notes.md', format='document')], 'doc-config')
        result = archive.collect('检索目标', paths=['history/notes.md'])
        self.assertEqual('DOC-1', result['matches'][0]['legacy_id'])
        checksum = result['matches'][0]['source_sha256']
        path.write_text('更新内容', encoding='utf-8')
        archive.collect('更新内容')
        chunks, offset = [], 0
        while offset is not None:
            page = archive.read('history/notes.md', 'DOC-1', checksum, offset, max_chars=1000)
            self.assertLessEqual(len(json.dumps(page, ensure_ascii=False)), 1000)
            chunks.append(page['text'])
            offset = page['next_offset']
        self.assertEqual(text, ''.join(chunks))
        with self.assertRaises(FrameworkError):
            archive.collect('检索目标', paths=['history/missing.md'])

    def test_category_fairness_precedes_top_k(self):
        folder = self.root / 'history'
        folder.mkdir()
        (folder / 'work.md').write_text(''.join('## E-%d\nsharedneedle\n' % n for n in range(20)), encoding='utf-8')
        (folder / 'FAILURES.md').write_text('## F-1\nsharedneedle\n', encoding='utf-8')
        archive = HistoryArchive(self.owner.store)
        archive.configure([dict(path='history/work.md',format='markdown'), dict(path='history/FAILURES.md',format='markdown')], 'fair-config')
        result = archive.collect('sharedneedle', limit=2)
        self.assertEqual({'E-0', 'F-1'}, {row['legacy_id'] for row in result['matches']})
        self.assertTrue(result['candidate_truncated'])

    def test_reject_invalid_categories_and_offsets(self):
        archive = HistoryArchive(self.owner.store)
        for categories in ([], ['unknown']):
            with self.subTest(categories=categories), self.assertRaises(FrameworkError):
                archive.configure([dict(path='notes.md',format='document',categories=categories)], 'invalid-'+str(categories))
        for offset in (True, -1):
            with self.assertRaises(FrameworkError):
                archive.read('notes.md','DOC-1','x',offset)

    def test_same_bytes_formats_retain_both_snapshot_interpretations(self):
        folder = self.root / 'history'
        folder.mkdir()
        path = folder / 'notes.md'
        path.write_text('## F-1 test\n正文\n', encoding='utf-8')
        archive = HistoryArchive(self.owner.store)
        archive.configure([dict(path='history/notes.md',format='markdown')], 'format-md')
        old = archive.query('F-1')['matches'][0]
        archive.configure([dict(path='history/notes.md',format='document')], 'format-doc')
        new = archive.collect('正文')['matches'][0]
        self.assertEqual('document',new['source_format'])
        self.assertEqual('document',archive.query('正文')['matches'][0]['source']['format'])
        self.assertEqual('DOC-1',archive.read(new['path'],new['legacy_id'],new['source_sha256'])['legacy_id'])
        self.assertEqual('F-1',archive.get(old['source']['path'],'F-1',old['source']['sha256'])['legacy_id'])

    def test_same_id_different_interpretation_requires_format(self):
        folder = self.root / 'history'
        folder.mkdir()
        (folder / 'notes.md').write_text('前言\n## DOC-1 标题\n首段\n## F-1 标题\n末段\n', encoding='utf-8')
        archive = HistoryArchive(self.owner.store)
        archive.configure([dict(path='history/notes.md',format='markdown')], 'format-md')
        old = archive.collect('DOC-1')['matches'][0]
        archive.configure([dict(path='history/notes.md',format='document')], 'format-doc')
        new = archive.collect('首段')['matches'][0]
        with self.assertRaises(FrameworkError):
            archive.get(new['path'],'DOC-1',new['source_sha256'])
        first = archive.get(old['path'],'DOC-1',old['source_sha256'],source_format='markdown')
        second = archive.read(new['path'],'DOC-1',new['source_sha256'],source_format='document')
        self.assertNotEqual(first['text'],second['text'])
        self.assertTrue(second['text'].startswith('前言'))
        with self.assertRaises(FrameworkError):
            archive.get(new['path'],'DOC-1',new['source_sha256'],source_format='yaml-records')


if __name__ == '__main__':
    unittest.main()
