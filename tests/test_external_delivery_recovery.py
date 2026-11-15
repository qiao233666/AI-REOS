"""目录覆盖超限不吞掉已稳定关键日志；补收只允许原终态字节。"""
from pathlib import Path
import subprocess
from unittest.mock import patch
import unittest

import test_evidence04 as fixtures
from aireos import run_evidence
from aireos.errors import FrameworkError
from aireos.external_review import ExternalReview


class ExternalDeliveryRecoveryTests(unittest.TestCase):
    setUp = fixtures.Evidence04Tests.setUp
    package = fixtures.Evidence04Tests.package
    provider = staticmethod(fixtures.Evidence04Tests.provider)

    @staticmethod
    def many_files(work):
        for i in range(12):
            (work / ('check-' + str(i) + '.txt')).write_text('synthetic', encoding='utf-8')

    def legacy_failure(self, events=b'{"type":"turn.completed"}\n', extra=None):
        adapter, package = self.package()
        calls = []
        original = run_evidence.recheck_inventory
        def legacy(*args, **kwargs):
            kwargs.pop('verified', None)
            return original(*args, **kwargs)
        with patch('aireos.run_evidence.MAX_FILES', 6), patch(
                'aireos.run_evidence.recheck_inventory', side_effect=legacy):
            run = adapter.run(package['id'], 'fake', 'legacy-run',
                              runner=self.provider(events=events, extra=extra or self.many_files, calls=calls))
        self.assertEqual('failed', run['data']['status'])
        return adapter, run, calls

    def test_file_count_overflow_preserves_stable_delivery_and_partial_tree(self):
        adapter, package = self.package()
        with patch('aireos.run_evidence.MAX_FILES', 6):
            run = adapter.run(package['id'], 'fake', 'run',
                              runner=self.provider(extra=self.many_files))
        self.assertEqual('completed', run['data']['status'])
        self.assertTrue(run['data']['partial'])
        self.assertFalse(run['data']['evidence']['complete'])
        self.assertIn('events.jsonl', run['data']['evidence']['verified_files'])
        self.assertIn('report.md', run['data']['evidence']['verified_files'])

    def test_recapture_uses_same_bytes_preserves_failure_and_never_calls_provider(self):
        adapter, old, calls = self.legacy_failure()
        with patch('aireos.run_evidence.MAX_FILES', 6):
            new = adapter.recapture(old['id'], old['rev'], '关键日志已终止，目录覆盖超限', 'recapture')
            replay = adapter.recapture(old['id'], old['rev'], '关键日志已终止，目录覆盖超限', 'recapture')
        self.assertEqual('completed', new['data']['status'])
        self.assertEqual('failed', self.owner.store.get(old['id'], old['rev'])['data']['status'])
        self.assertEqual({'id': old['id'], 'rev': old['rev']}, new['data']['delivery_recovery']['previous'])
        self.assertTrue(new['data']['partial'])
        self.assertEqual(1, len(calls))
        self.assertEqual(new['rev'], replay['rev'])
        self.assertTrue(replay['_replayed'])

    def test_recapture_rejects_changed_report_events_or_stdin(self):
        for name in ('report.md', 'events.jsonl', 'stdin.txt'):
            with self.subTest(name=name):
                # 独立夹具避免一项污染下一项。
                self.setUp()
                adapter, old, calls = self.legacy_failure()
                (self.root / old['data']['directory'] / name).write_text('changed', encoding='utf-8')
                with self.assertRaises(FrameworkError):
                    adapter.recapture(old['id'], old['rev'], '拒绝改变原字节', 'recapture')
                self.assertEqual(old['rev'], self.owner.store.get(old['id'])['rev'])
                self.assertEqual(1, len(calls))

    def test_recapture_cannot_turn_failed_event_into_completed(self):
        adapter, old, calls = self.legacy_failure(events=b'{"type":"turn.failed"}\n')
        with self.assertRaises(FrameworkError):
            adapter.recapture(old['id'], old['rev'], '失败事件不可放行', 'recapture')
        self.assertEqual('failed', self.owner.store.get(old['id'])['data']['status'])
        self.assertEqual(1, len(calls))

    def test_recapture_requires_decider_and_exact_revision(self):
        adapter, old, calls = self.legacy_failure()
        with self.assertRaises(FrameworkError):
            ExternalReview(self.worker.store).recapture(old['id'], old['rev'], '执行者不可补收', 'unauthorized')
        with self.assertRaises(FrameworkError):
            adapter.recapture(old['id'], old['rev'] + 1, '版本不符', 'stale')
        self.assertEqual(1, len(calls))

    def test_recapture_does_not_relax_file_or_total_byte_budget(self):
        adapter, old, calls = self.legacy_failure()
        with patch('aireos.run_evidence.MAX_TOTAL_BYTES', 1), self.assertRaises(FrameworkError):
            adapter.recapture(old['id'], old['rev'], '不能扩大采集预算', 'too-small')
        self.assertEqual(old['rev'], self.owner.store.get(old['id'])['rev'])
        self.assertEqual(1, len(calls))

    def test_recapture_rejects_unknown_or_nonzero_process(self):
        for outcome in ('unknown', 'nonzero'):
            with self.subTest(outcome=outcome):
                self.setUp()
                adapter, package = self.package()
                provider = self.provider()
                def bad(*args):
                    provider(*args)
                    if outcome == 'unknown':
                        raise subprocess.TimeoutExpired(args[0], 1)
                    return 1
                old = adapter.run(package['id'], 'fake', 'bad-run', runner=bad)
                with self.assertRaises(FrameworkError):
                    adapter.recapture(old['id'], old['rev'], '实际执行终态不合格', 'recapture')
                self.assertEqual(old['rev'], self.owner.store.get(old['id'])['rev'])

    def test_recapture_cannot_add_an_unfrozen_critical_file(self):
        adapter, package = self.package()
        with patch('aireos.run_evidence.MAX_FILE_BYTES', 8):
            old = adapter.run(package['id'], 'fake', 'missing-run', runner=self.provider())
        with self.assertRaises(FrameworkError):
            adapter.recapture(old['id'], old['rev'], '原日志没有被冻结', 'recapture')
        self.assertEqual(old['rev'], self.owner.store.get(old['id'])['rev'])

    def test_recapture_rejects_unchanged_but_unbound_stdin(self):
        def wrong(work):
            self.many_files(work)
            (work.parent / 'stdin.txt').write_text('不是原固定材料', encoding='utf-8')
        adapter, old, calls = self.legacy_failure(extra=wrong)
        with self.assertRaises(FrameworkError):
            adapter.recapture(old['id'], old['rev'], '字节相同也必须匹配固定材料', 'recapture')
        self.assertEqual(1, len(calls))


if __name__ == '__main__':
    unittest.main()
