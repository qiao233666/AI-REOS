"""真实存储上的执行分段、证据和预算回归。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aireos.storage import Store
from aireos.project import Project
from aireos.errors import FrameworkError


class ExecutionControlTests(unittest.TestCase):
    def setUp(self):
        from aireos.execution_control import ExecutionControl
        self.control_type = ExecutionControl
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        init = Store.initialize(self.root)
        self.owner = Project(self.root, Path(init['credential_file']).read_text().strip())
        self.worker = self.actor('worker', ['execute', 'review'])
        self.reviewer = self.actor('reviewer', ['review'])
        self.decider = self.actor('decider', ['decide'])
        self.control = ExecutionControl(self.owner.store)
        self.execute = ExecutionControl(self.worker.store)
        self.decide = ExecutionControl(self.decider.store)
        self.host = ExecutionControl(Store(self.root))
        (self.root / 'spec.txt').write_text('固定输入', encoding='utf-8')
        self.task = self.owner.create_task('全链路', '原目标不漂移', ['实现可检查', '物理证据完整'], ['out'], 'task', inputs=['spec.txt'])
        receipt = self.worker.prepare(self.task['id'], '原目标', 'prepare')
        self.worker.start_task(self.task['id'], receipt['id'], 'start')
        (self.root / 'out').mkdir()
        self.original = self.artifact('original.txt', '原始验收文字')
        self.serial = 0

    def actor(self, name, capabilities):
        grant = self.owner.store.grant(name, capabilities, ['.'], 'grant-' + name)
        return Project(self.root, Path(grant['credential_file']).read_text().strip())

    def artifact(self, name, content):
        (self.root / 'out' / name).write_text(content, encoding='utf-8')
        return self.worker.add_artifact('out/' + name, name, self.task['id'], 'artifact-' + name)

    def configure(self, max_tools=2, max_seconds=120):
        return self.control.configure(self.task['id'], self.original['id'],
            [{'criterion': 'A1', 'required_classes': ['implementation']},
             {'criterion': 'A2', 'required_classes': ['physical']}], max_tools, max_seconds, 'policy')

    def begin(self, session='s1', request='begin'):
        return self.execute.begin(self.task['id'], session, ['A1'], '检查实现', '可核查进展', request)

    def body(self, segment, states=('progress', 'unknown'), evidence=None):
        return dict(schema='aireos.execution-report/1', segment_id=segment['id'],
            contract_hash=segment['data']['contract_hash'], criteria=[
                dict(criterion='A' + str(i + 1), state=state, evidence=(evidence or [[], []])[i],
                     remaining='' if state == 'satisfied' else '尚需后续证据') for i, state in enumerate(states)])

    def report_artifact(self, segment, body=None):
        self.serial += 1
        return self.artifact('report-' + str(self.serial) + '.json',
                             json.dumps(body or self.body(segment), ensure_ascii=False))

    def submit_report(self, segment, body=None):
        artifact = self.report_artifact(segment, body)
        self.execute.report(segment['id'], artifact['id'], 'report-' + artifact['id'])
        return artifact

    def review(self, artifact, verdict='pass'):
        return self.reviewer.review(artifact['id'], verdict, '项目既定审核渠道',
            ['按原任务核对准确版本、实际证据和剩余缺口'],
            'review-' + artifact['id'], expected_revision=artifact['rev'])

    def advance(self, segment):
        artifact = self.submit_report(segment)
        review = self.review(artifact)
        return self.decide.decide(segment['id'], review['id'], 'continue', '诚实局部进展，继续补物理证据', 'continue-' + segment['id'])

    def complete(self, segment):
        implementation = self.artifact('complete-implementation.txt', '完成实现文件')
        physical = self.artifact('complete-physical.txt', '完成物理文件')
        evidence = [[dict(id=implementation['id'], rev=1, **{'class': 'implementation'})],
                    [dict(id=physical['id'], rev=1, **{'class': 'physical'})]]
        report = self.submit_report(segment, self.body(segment, ('satisfied', 'satisfied'), evidence))
        review = self.review(report)
        self.decide.decide(segment['id'], review['id'], 'completed', '全部原验收项已核查', 'complete-' + segment['id'])
        return report, review, physical

    def first_code_stage(self):
        self.configure(max_tools=6)
        first = self.begin()
        code = self.artifact('code.py', '# 第一段实现\nvalue = 1\n')
        evidence = [[dict(id=code['id'], rev=1, **{'class': 'implementation'})], []]
        report = self.submit_report(first, self.body(first, evidence=evidence))
        review = self.review(report)
        self.decide.decide(first['id'], review['id'], 'continue', '批准继续原实现目标', 'code-continue')
        return first, code, report, review

    def test_next_approved_stage_can_iterate_code_and_complete_from_new_snapshot(self):
        _, old_code, _, _ = self.first_code_stage()
        second = self.begin('s1', 'code-second')
        self.assertTrue(self.host.admit('s1', 'write', {'file_path': 'out/code.py'}, 'code-edit', segment_id=second['id'])['allowed'])
        (self.root / 'out/code.py').write_text('# 第二段实现\nvalue = 2\n', encoding='utf-8')
        self.assertTrue(self.host.admit('s1', 'read', {'file_path': 'out/code.py'}, 'after-code-edit', segment_id=second['id'])['allowed'])
        updated = self.worker.add_artifact('out/code.py', '第二段准确源码', self.task['id'], 'code-v2-artifact')
        physical = self.artifact('code-physical.txt', '第二段独立证据')
        evidence = [[dict(id=updated['id'], rev=1, **{'class': 'implementation'})],
                    [dict(id=physical['id'], rev=1, **{'class': 'physical'})]]
        report = self.submit_report(second, self.body(second, ('satisfied', 'satisfied'), evidence))
        review = self.review(report)
        self.decide.decide(second['id'], review['id'], 'completed', '新准确证据满足全部原验收', 'code-complete')
        self.assertTrue(self.host.status(self.task['id'], 's1')['completion_ready'])
        self.assertIn(b'value = 1', self.owner.store.blob(old_code['data']['blob']))
        old_snapshot = self.root / '.aireos/objects' / old_code['data']['blob']
        saved = old_snapshot.read_bytes()
        old_snapshot.write_bytes(b'broken historical completion basis')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])
        old_snapshot.write_bytes(saved)
        self.assertTrue(self.host.status(self.task['id'], 's1')['completion_ready'])
        (self.root / 'out/code.py').write_text('# 未经新核查又改变最新证据\n', encoding='utf-8')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])

    def test_code_change_before_next_begin_is_not_historical_permission(self):
        self.first_code_stage()
        (self.root / 'out/code.py').write_text('# 审核后开段前偷改\n', encoding='utf-8')
        with self.assertRaises(FrameworkError):
            self.begin('s1', 'changed-before-begin')

    def test_historical_evidence_snapshot_corruption_still_blocks_tools(self):
        _, old_code, _, _ = self.first_code_stage()
        second = self.begin('s1', 'code-second')
        (self.root / 'out/code.py').write_text('# 已获准第二段正常迭代\n', encoding='utf-8')
        self.assertTrue(self.host.status(self.task['id'], 's1')['allowed'])
        (self.root / '.aireos/objects' / old_code['data']['blob']).write_bytes(b'broken old snapshot')
        self.assertFalse(self.host.admit('s1', 'read', {}, 'corrupt-old-snapshot', segment_id=second['id'])['allowed'])

    def test_policy_preserves_every_original_acceptance(self):
        policy = self.configure()
        self.assertEqual(self.original['id'], policy['data']['original']['id'])
        with self.assertRaises(FrameworkError):
            self.control.configure(self.task['id'], self.original['id'],
                [{'criterion': 'A1', 'required_classes': ['implementation']}], 2, 120, 'drop-A2')

    def test_budget_and_new_session_cannot_skip_review(self):
        self.configure()
        segment = self.begin()
        self.assertTrue(self.host.admit('s1', 'read', {}, 'e1', segment_id=segment['id'])['allowed'])
        self.assertTrue(self.host.admit('s1', 'read', {}, 'e2', segment_id=segment['id'])['allowed'])
        self.assertFalse(self.host.admit('s1', 'read', {}, 'e3', segment_id=segment['id'])['allowed'])
        with self.assertRaises(FrameworkError):
            self.begin('s2', 'session-bypass')

    def test_delete_acceptance_and_missing_physical_are_rejected(self):
        self.configure()
        segment = self.begin()
        dropped = self.body(segment)
        dropped['criteria'].pop()
        bad = self.report_artifact(segment, dropped)
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'], bad['id'], 'drop-report-A2')
        missing = self.report_artifact(segment, self.body(segment, ('satisfied', 'satisfied')))
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'], missing['id'], 'missing-physical')

    def test_unknown_progress_can_continue_but_not_complete(self):
        self.configure()
        segment = self.begin()
        artifact = self.submit_report(segment)
        review = self.review(artifact)
        with self.assertRaises(FrameworkError):
            self.decide.decide(segment['id'], review['id'], 'completed', '声称完成', 'false-completion')
        self.decide.decide(segment['id'], review['id'], 'continue', '缺口已明确', 'continue')
        next_segment = self.begin('s2', 'begin-2')
        self.assertEqual(2, next_segment['data']['index'])
        self.assertEqual(self.original['id'], next_segment['data']['original']['id'])

    def test_complete_requires_all_classes_and_independent_review(self):
        self.configure()
        segment = self.begin()
        implementation = self.artifact('implementation.txt', '实现证据')
        physical = self.artifact('physical.txt', '物理证据')
        evidence = [[dict(id=implementation['id'], rev=1, **{'class': 'implementation'})],
                    [dict(id=physical['id'], rev=1, **{'class': 'physical'})]]
        artifact = self.submit_report(segment, self.body(segment, ('satisfied', 'satisfied'), evidence))
        with self.assertRaises(FrameworkError):
            self.worker.review(artifact['id'], 'pass', '自审', ['自己通过'], 'self-review', expected_revision=1)
        review = self.review(artifact)
        with self.assertRaises(FrameworkError):
            self.execute.decide(segment['id'], review['id'], 'completed', '越权', 'worker-decides')
        result = self.decide.decide(segment['id'], review['id'], 'completed', '全项证据核查完毕', 'complete')
        self.assertEqual('completed', result['data']['status'])
        self.assertEqual('running', self.owner.store.get(self.task['id'])['data']['status'])
        with self.assertRaises(FrameworkError):
            self.begin('s2', 'after-completed')

    def test_report_and_evidence_tampering_block_decision(self):
        self.configure()
        segment = self.begin()
        artifact = self.submit_report(segment)
        review = self.review(artifact)
        (self.root / artifact['data']['origin']).write_text('{}', encoding='utf-8')
        with self.assertRaises(FrameworkError):
            self.decide.decide(segment['id'], review['id'], 'continue', '旧报告', 'tampered-report')

    def test_changed_evidence_version_or_bytes_cannot_be_used(self):
        self.configure()
        segment = self.begin()
        evidence = self.artifact('evidence.txt', '证据版本1')
        refs = [[dict(id=evidence['id'], rev=2, **{'class': 'implementation'})], []]
        bad = self.report_artifact(segment, self.body(segment, evidence=refs))
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'], bad['id'], 'wrong-evidence-rev')
        refs[0][0]['rev'] = 1
        report = self.submit_report(segment, self.body(segment, evidence=refs))
        review = self.review(report)
        (self.root / evidence['data']['origin']).write_text('证据已改', encoding='utf-8')
        with self.assertRaises(FrameworkError):
            self.decide.decide(segment['id'], review['id'], 'continue', '旧证据', 'tampered-evidence')

    def test_claim_is_not_file_evidence(self):
        self.configure()
        segment = self.begin()
        claim = self.worker.add_item('口头声称', '自称已通过', 'hypothesis', '本任务', [], self.task['id'], 'claim')
        refs = [[dict(id=claim['id'], rev=1, **{'class': 'implementation'})], []]
        artifact = self.report_artifact(segment, self.body(segment, evidence=refs))
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'], artifact['id'], 'claim-evidence')

    def test_hold_requires_revised_report_and_new_independent_review(self):
        self.configure()
        segment = self.begin()
        first = self.submit_report(segment)
        review = self.review(first, 'limit')
        self.decide.decide(segment['id'], review['id'], 'hold', '需要修订报告', 'hold')
        with self.assertRaises(FrameworkError):
            self.begin('s2', 'hold-bypass')
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'], first['id'], 'same-old-report')
        repaired = self.submit_report(segment, self.body(segment, ('unknown', 'unknown')))
        with self.assertRaises(FrameworkError):
            self.decide.decide(segment['id'], review['id'], 'continue', '旧意见不能沿用', 'old-review')
        passed = self.review(repaired)
        self.decide.decide(segment['id'], passed['id'], 'continue', '修订报告诚实陈述未知', 'resume')
        self.assertEqual(2, self.begin('s2', 'after-resume')['data']['index'])

    def test_hold_cannot_resume_by_relabeling_identical_report_bytes(self):
        self.configure()
        segment = self.begin()
        first = self.submit_report(segment)
        review = self.review(first, 'limit')
        self.decide.decide(segment['id'], review['id'], 'hold', '需要修订', 'hold')
        duplicate = self.artifact('duplicate.json', (self.root / first['data']['origin']).read_text(encoding='utf-8'))
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'], duplicate['id'], 'duplicate-blob')

    def test_latest_rejection_blocks_older_pass_and_unrelated_review(self):
        self.configure()
        segment = self.begin()
        artifact = self.submit_report(segment)
        first = self.review(artifact)
        self.reviewer.review(artifact['id'], 'fail', '发现证据类别不实', ['前次漏检'], 'new-fail', expected_revision=1)
        with self.assertRaises(FrameworkError):
            self.decide.decide(segment['id'], first['id'], 'continue', '沿用旧pass', 'stale-pass')
        unrelated = self.review(self.original)
        with self.assertRaises(FrameworkError):
            self.decide.decide(segment['id'], unrelated['id'], 'continue', '借用其他审核', 'other-pass')

    def test_report_object_revision_change_blocks_old_independent_review(self):
        self.configure()
        segment = self.begin()
        artifact = self.submit_report(segment)
        review = self.review(artifact)
        self.owner.store.replace(artifact['id'], 1, {**artifact['data'], 'title': '修订产物元数据'}, 'artifact-revision')
        with self.assertRaises(FrameworkError):
            self.decide.decide(segment['id'], review['id'], 'continue', '沿用旧版本意见', 'old-revision')

    def test_review_revision_change_blocks_following_segment(self):
        self.configure()
        segment = self.begin()
        decided = self.advance(segment)
        review = self.owner.store.get(decided['data']['review']['id'])
        self.owner.store.replace(review['id'], 1, {**review['data'], 'method': '改变审核方法'}, 'review-revision')
        with self.assertRaises(FrameworkError):
            self.begin('s2', 'changed-review')

    def test_snapshot_corruption_is_not_hidden_by_unchanged_source_file(self):
        self.configure()
        segment = self.begin()
        blob = self.root / '.aireos/objects' / self.original['data']['blob']
        blob.write_bytes(b'broken snapshot')
        self.assertFalse(self.host.admit('s1', 'read', {}, 'bad-blob', segment_id=segment['id'])['allowed'])

    def test_foreign_producer_cannot_report_for_executor(self):
        self.configure()
        segment = self.begin()
        path = self.root / 'out/foreign.json'
        path.write_text(json.dumps(self.body(segment)), encoding='utf-8')
        foreign = self.owner.add_artifact('out/foreign.json', '其他执行者', self.task['id'], 'foreign')
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'], foreign['id'], 'foreign-report')

    def test_submitted_and_hold_deny_tools(self):
        self.configure()
        segment = self.begin()
        artifact = self.submit_report(segment)
        self.assertFalse(self.host.admit('s1', 'read', {}, 'submitted-tool', segment_id=segment['id'])['allowed'])
        review = self.review(artifact, 'fail')
        self.decide.decide(segment['id'], review['id'], 'hold', '停止', 'hold')
        self.assertFalse(self.host.admit('s1', 'read', {}, 'hold-tool', segment_id=segment['id'])['allowed'])

    def test_exact_replay_does_not_charge_or_bypass_revocation(self):
        self.configure(max_tools=1)
        segment = self.begin()
        first = self.host.admit('s1', 'read', {'path': 'x'}, 'one', segment_id=segment['id'])
        again = self.host.admit('s1', 'read', {'path': 'x'}, 'one', segment_id=segment['id'])
        self.assertTrue(first['allowed'])
        self.assertTrue(again['allowed'])
        self.assertTrue(again['replayed'])
        self.assertEqual(1, again['used'])
        self.assertFalse(self.host.admit('s1', 'read', {'path': 'y'}, 'one', segment_id=segment['id'])['allowed'])
        self.host.invalidate('s1')
        self.assertFalse(self.host.admit('s1', 'read', {'path': 'x'}, 'one', segment_id=segment['id'])['allowed'])
        self.assertEqual(1, self.host.status(self.task['id'], 's1')['used'])

    def test_cross_session_and_late_old_event_cannot_charge_new_segment(self):
        self.configure()
        first = self.begin()
        self.assertFalse(self.host.admit('other', 'read', {}, 'wrong-session', segment_id=first['id'])['allowed'])
        self.advance(first)
        second = self.begin('s1', 'second')
        late = self.host.admit('s1', 'read', {}, 'late', segment_id=first['id'])
        self.assertFalse(late['allowed'])
        self.assertEqual(0, self.host.status(self.task['id'], 's1')['used'])
        self.assertTrue(self.host.admit('s1', 'read', {}, 'second-first', segment_id=second['id'])['allowed'])

    def test_atomic_counter_across_host_instances(self):
        self.configure(max_tools=2)
        segment = self.begin()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: self.control_type(Store(self.root)).admit(
                's1', 'read', {}, 'parallel-' + str(i), segment_id=segment['id']), range(6)))
        self.assertEqual(2, sum(result['allowed'] for result in results))
        self.assertEqual(2, self.host.status(self.task['id'], 's1')['used'])

    def test_expired_input_changed_and_pending_fail_closed(self):
        self.configure()
        segment = self.begin()
        future = datetime.now(timezone.utc) + timedelta(seconds=360)
        with patch('aireos.execution_control.datetime') as clock:
            clock.now.return_value = future
            clock.fromisoformat.side_effect = datetime.fromisoformat
            self.assertFalse(self.host.admit('s1', 'read', {}, 'expired', segment_id=segment['id'])['allowed'])
        pending = self.worker.request_decision(self.task['id'], 'scope', '需要新授权吗', 'pending')
        self.assertFalse(self.host.admit('s1', 'read', {}, 'pending-event', segment_id=segment['id'])['allowed'])
        self.decider.resolve_decision(pending['id'], 1, '仅按原合同', 'answer')
        self.assertTrue(self.host.admit('s1', 'read', {}, 'after-answer', segment_id=segment['id'])['allowed'])
        (self.root / 'spec.txt').write_text('输入变化', encoding='utf-8')
        self.assertFalse(self.host.admit('s1', 'read', {}, 'changed-input', segment_id=segment['id'])['allowed'])

    def test_original_change_blocks_status_admission_and_next_segment(self):
        self.configure()
        segment = self.begin()
        (self.root / 'out/original.txt').write_text('替换原件', encoding='utf-8')
        self.assertFalse(self.host.status(self.task['id'], 's1')['allowed'])
        self.assertFalse(self.host.admit('s1', 'read', {}, 'changed-original', segment_id=segment['id'])['allowed'])

    def test_lost_counter_is_not_reinitialized_by_begin_replay(self):
        self.configure()
        segment = self.begin()
        database = self.root / '.aireos/cache/execution-control.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('DELETE FROM counters')
        self.begin()
        self.assertFalse(self.host.admit('s1', 'read', {}, 'lost-counter', segment_id=segment['id'])['allowed'])

    def test_unavailable_cache_fails_closed_and_does_not_update_formal_facts(self):
        self.configure()
        segment = self.begin()
        before = len(self.owner.store.events())
        database = self.root / '.aireos/cache/execution-control.sqlite3'
        database.write_bytes(b'corrupted operation cache')
        self.assertFalse(self.host.status(self.task['id'], 's1')['allowed'])
        self.assertFalse(self.host.admit('s1', 'read', {}, 'cache-bad', segment_id=segment['id'])['allowed'])
        self.assertEqual(before, len(self.owner.store.events()))

    def test_status_requires_exact_session_and_does_not_mutate(self):
        self.configure()
        with self.assertRaises(FrameworkError):
            self.host.status(self.task['id'])
        with self.assertRaises(FrameworkError):
            self.host.status(session_id='not-bound')
        before = len(self.owner.store.events())
        status = self.host.status(self.task['id'], 'new-session')
        self.assertFalse(status['allowed'])
        self.assertEqual(2, len(status['criteria']))
        self.assertEqual(before, len(self.owner.store.events()))
        self.assertFalse((self.root / '.aireos/cache').exists())

    def test_cli_routes_real_store_without_host_credentials(self):
        from aireos.cli import main
        self.configure()
        segment = self.begin()
        params = self.root / 'admission.json'
        params.write_text(json.dumps(dict(session_id='s1', tool_name='read', tool_input={},
                                         event_id='cli', segment_id=segment['id'])), encoding='utf-8')
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(['--project', str(self.root), 'execution-admit', '--input', str(params)])
        self.assertEqual(0, result)
        self.assertTrue(json.loads(output.getvalue())['allowed'])
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = main(['--project', str(self.root), '--credential-file', str(self.root / '.aireos/owner.key'),
                           'execution-admit', '--input', str(params)])
        self.assertEqual(2, result)

    def test_completion_is_separate_from_spent_or_expired_tool_budget(self):
        self.configure(max_tools=1)
        segment = self.begin()
        self.assertTrue(self.host.admit('s1', 'read', {}, 'last-tool', segment_id=segment['id'])['allowed'])
        self.complete(segment)
        status = self.host.status(self.task['id'], 's1')
        self.assertFalse(status['allowed'])
        self.assertTrue(status['completion_ready'])
        future = datetime.now(timezone.utc) + timedelta(seconds=360)
        with patch('aireos.execution_control.datetime') as clock:
            clock.now.return_value = future
            clock.fromisoformat.side_effect = datetime.fromisoformat
            status = self.host.status(self.task['id'], 's1')
        self.assertFalse(status['allowed'])
        self.assertTrue(status['completion_ready'])

    def test_completed_for_other_session_or_invalidated_input_cannot_stop_new_request(self):
        self.configure()
        segment = self.begin()
        self.complete(segment)
        self.assertFalse(self.host.status(self.task['id'], 'other-session')['completion_ready'])
        self.host.invalidate('s1')
        status = self.host.status(self.task['id'], 's1')
        self.assertFalse(status['completion_ready'])
        self.assertEqual('completed', status['segment']['data']['status'])

    def test_completion_revalidates_report_and_current_evidence(self):
        self.configure()
        segment = self.begin()
        report, _, physical = self.complete(segment)
        path = self.root / report['data']['origin']
        original = path.read_bytes()
        path.write_text('{}', encoding='utf-8')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])
        path.write_bytes(original)
        self.assertTrue(self.host.status(self.task['id'], 's1')['completion_ready'])
        (self.root / physical['data']['origin']).write_text('篡改物理文件', encoding='utf-8')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])

    def test_completion_requires_current_latest_independent_pass(self):
        self.configure()
        segment = self.begin()
        report, _, _ = self.complete(segment)
        self.reviewer.review(report['id'], 'fail', '复查发现缺口', ['物理解释错误'], 'reject-completed', expected_revision=1)
        status = self.host.status(self.task['id'], 's1')
        self.assertEqual('completed', status['segment']['data']['status'])
        self.assertFalse(status['completion_ready'])

    def test_completion_revalidates_prior_report_and_independent_review(self):
        self.configure()
        first = self.begin()
        prior = self.submit_report(first)
        prior_review = self.review(prior)
        self.decide.decide(first['id'], prior_review['id'], 'continue', '继续阶段二', 'prior-continue')
        second = self.begin('s1', 'second')
        self.complete(second)
        self.assertTrue(self.host.status(self.task['id'], 's1')['completion_ready'])
        path = self.root / prior['data']['origin']
        original = path.read_bytes()
        path.write_text('{}', encoding='utf-8')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])
        path.write_bytes(original)
        self.assertTrue(self.host.status(self.task['id'], 's1')['completion_ready'])
        self.reviewer.review(prior['id'], 'limit', '前段复查', ['仍有局限'], 'prior-limit', expected_revision=1)
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])

    def test_completion_rejects_pending_contract_changes_and_missing_cache(self):
        self.configure()
        segment = self.begin()
        self.complete(segment)
        decision = self.worker.request_decision(self.task['id'], 'new-scope', '完成后还有待决', 'new-pending')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])
        self.decider.resolve_decision(decision['id'], 1, '保持原范围', 'answer-completed')
        self.assertTrue(self.host.status(self.task['id'], 's1')['completion_ready'])
        current = self.owner.store.get(self.task['id'])
        changed = {**current['data'], 'contract': {**current['data']['contract'], 'objective': '扩大目标'}}
        self.owner.store.replace(self.task['id'], current['rev'], changed, 'changed-contract')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])
        self.owner.store.replace(self.task['id'], current['rev'] + 1, current['data'], 'restore-contract')
        self.assertTrue(self.host.status(self.task['id'], 's1')['completion_ready'])
        with sqlite3.connect(self.root / '.aireos/cache/execution-control.sqlite3') as connection:
            connection.execute('DELETE FROM counters')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])

    def test_formal_task_closure_preserves_current_completion_but_not_tools(self):
        self.configure()
        segment = self.begin()
        report, _, _ = self.complete(segment)
        receipt = self.worker.prepare(self.task['id'], '交付全部验收报告', 'prepare-delivery')
        self.worker.submit(self.task['id'], [report['id']], receipt['id'], 'deliver')
        closed = self.decider.close_task(self.task['id'], '全项证据已独立核查并正式结案', 'close')
        self.assertEqual('closed', closed['task']['data']['status'])
        status = self.host.status(self.task['id'], 's1')
        self.assertTrue(status['completion_ready'])
        self.assertFalse(status['allowed'])
        self.host.invalidate('s1')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])

    def test_stopped_without_acceptance_cannot_become_completion(self):
        self.configure()
        segment = self.begin()
        self.complete(segment)
        self.decider.stop_task(self.task['id'], '停止且未按正式流程验收', 'stop-without-acceptance')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])

    def test_completed_label_alone_does_not_replace_all_satisfied_evidence(self):
        self.configure()
        segment = self.begin()
        continued = self.advance(segment)
        self.owner.store.replace(continued['id'], continued['rev'],
            {**continued['data'], 'status': 'completed', 'decision': 'completed'}, 'spoof-status')
        self.assertFalse(self.host.status(self.task['id'], 's1')['completion_ready'])

    def test_unknown_hold_can_handoff_with_pending_and_formal_stopping(self):
        self.configure(); segment = self.begin()
        report = self.submit_report(segment, self.body(segment, ('unknown', 'unknown')))
        self.assertFalse(self.host.status(self.task['id'], 's1')['handoff_ready'])
        review = self.review(report, 'unknown')
        self.worker.request_decision(self.task['id'], 'need-human', '下一步是否投入仍需人决定', 'handoff-pending')
        self.decide.decide(segment['id'], review['id'], 'hold', '保持未知并交接待决', 'formal-hold')
        status = self.host.status(self.task['id'], 's1')
        self.assertTrue(status['handoff_ready'])
        self.assertFalse(status['completion_ready'])
        self.assertFalse(status['allowed'])
        self.decider.stop_task(self.task['id'], '按原停止条件保留未知及待决，未验收', 'stopped-handoff')
        stopped = self.host.status(self.task['id'], 's1')
        self.assertTrue(stopped['handoff_ready'])
        self.assertFalse(stopped['completion_ready'])

    def test_handoff_rejects_cross_session_revoked_and_superseded_hold(self):
        self.configure(); segment = self.begin()
        report = self.submit_report(segment)
        review = self.review(report, 'limit')
        self.decide.decide(segment['id'], review['id'], 'hold', '本段需要补证据', 'handoff-hold')
        self.assertTrue(self.host.status(self.task['id'], 's1')['handoff_ready'])
        self.assertFalse(self.host.status(self.task['id'], 'other')['handoff_ready'])
        self.submit_report(segment, self.body(segment, ('unknown', 'unknown')))
        self.assertFalse(self.host.status(self.task['id'], 's1')['handoff_ready'])
        current = self.owner.store.get(segment['id'])
        report = self.owner.store.get(current['data']['report']['id'])
        review = self.review(report, 'limit')
        self.decide.decide(segment['id'], review['id'], 'hold', '修订后继续诚实hold', 'handoff-held-again')
        self.assertTrue(self.host.status(self.task['id'], 's1')['handoff_ready'])
        self.host.invalidate('s1')
        self.assertFalse(self.host.status(self.task['id'], 's1')['handoff_ready'])

    def test_handoff_revalidates_latest_evidence_and_exact_independent_review(self):
        self.configure(); segment = self.begin()
        evidence = self.artifact('handoff-evidence.txt', '当前交接证据')
        refs = [[dict(id=evidence['id'], rev=1, **{'class': 'implementation'})], []]
        report = self.submit_report(segment, self.body(segment, evidence=refs))
        review = self.review(report, 'limit')
        self.decide.decide(segment['id'], review['id'], 'hold', '证据不足保留未知', 'handoff-evidence-hold')
        self.assertTrue(self.host.status(self.task['id'], 's1')['handoff_ready'])
        path = self.root / evidence['data']['origin']; saved = path.read_bytes()
        path.write_text('交接后篡改现场', encoding='utf-8')
        self.assertFalse(self.host.status(self.task['id'], 's1')['handoff_ready'])
        path.write_bytes(saved)
        self.assertTrue(self.host.status(self.task['id'], 's1')['handoff_ready'])
        self.owner.store.replace(review['id'], review['rev'], {**review['data'], 'method': '审核准确版本被改'}, 'changed-hold-review')
        self.assertFalse(self.host.status(self.task['id'], 's1')['handoff_ready'])


if __name__ == '__main__':
    unittest.main()
