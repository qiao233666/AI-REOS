"""整链审查反例：不得用旧通过记录覆盖来源失败、绕过隔离或卡死交接。"""
import json
from pathlib import Path
import unittest
from aireos import client_guard
from aireos.errors import FrameworkError
from aireos.execution_control import ExecutionControl
import test_execution_control as fixtures
import test_execution_client as clients


class ExecutionSafetyTests(unittest.TestCase):
    setUp=fixtures.ExecutionControlTests.setUp
    actor=fixtures.ExecutionControlTests.actor
    artifact=fixtures.ExecutionControlTests.artifact
    configure=fixtures.ExecutionControlTests.configure
    begin=fixtures.ExecutionControlTests.begin
    body=fixtures.ExecutionControlTests.body
    report_artifact=fixtures.ExecutionControlTests.report_artifact
    submit_report=fixtures.ExecutionControlTests.submit_report
    review=fixtures.ExecutionControlTests.review
    complete=fixtures.ExecutionControlTests.complete
    install=clients.ExecutionClientTests.install

    def event(self,tool,values,key='tool'):
        return client_guard.handle(self.root,'PreToolUse',dict(session_id='s1',tool_use_id=key,
            tool_name=tool,tool_input=values))

    def test_control_plane_write_and_unisolated_shell_are_denied(self):
        self.install();self.configure();self.begin()
        path=self.root/'aiops/aireos/retrieval-policy.json'
        self.assertEqual(2,self.event('Write',{'file_path':str(path),'content':'{}'})[0])
        self.assertEqual(2,self.event('Bash',{'command':'python arbitrary.py'},'shell')[0])
        self.assertEqual(2,self.event('Read',{'file_path':str(self.root/'.aireos/owner.key')},'secret')[0])

    def test_latest_evidence_fail_invalidates_completion(self):
        self.configure();segment=self.begin();_,_,physical=self.complete(segment)
        self.reviewer.review(physical['id'],'fail','证据被复核否定',['该版本不能支持完成'],
            'new-evidence-fail',expected_revision=physical['rev'])
        self.assertFalse(self.host.status(self.task['id'],'s1')['completion_ready'])

    def test_sequential_tasks_use_explicit_current_binding(self):
        self.configure();first=self.begin();self.complete(first)
        other=self.owner.create_task('第二任务','同会话继续',['验收'],['out2'],'task2')
        receipt=self.worker.prepare(other['id'],'第二任务','prepare2')
        self.worker.start_task(other['id'],receipt['id'],'start2')
        (self.root/'out2').mkdir()
        (self.root/'out2/original.txt').write_text('第二任务原件',encoding='utf-8')
        original=self.worker.add_artifact('out2/original.txt','第二原件',other['id'],'original2')
        self.control.configure(other['id'],original['id'],[
            {'criterion':'A1','required_classes':['implementation']}],2,120,'configure2')
        second=self.execute.begin(other['id'],'s1',['A1'],'第二任务','产物','begin2')
        self.assertEqual(second['id'],self.host.status(session_id='s1')['segment_id'])

    def test_pending_and_stale_input_allow_honest_handoff_only(self):
        self.install();self.configure();self.begin()
        self.worker.request_decision(self.task['id'],'review-confirmation','等用户批准原渠道审核','pending')
        before=client_guard.handle(self.root,'Stop',{'session_id':'s1'})
        self.assertNotEqual('block',(before[1] or {}).get('decision'))
        self.assertIn('尚未完成',(before[1] or {}).get('systemMessage',''))
        (self.root/'spec.txt').write_text('输入失效',encoding='utf-8')
        self.worker.checkpoint(self.task['id'],'输入失效，保持原任务未完成','等用户处理',0,'checkpoint-stale')
        after=client_guard.handle(self.root,'Stop',{'session_id':'s1'})
        self.assertNotEqual('block',(after[1] or {}).get('decision'))
        self.assertFalse(self.host.status(self.task['id'],'s1')['completion_ready'])
        self.assertFalse(self.host.status(self.task['id'],'s1')['allowed'])

    def test_inflight_tool_must_finish_before_report(self):
        self.configure();segment=self.begin()
        self.assertTrue(self.host.admit('s1','write',{'file_path':'out/x.txt'},'tracked',
            segment_id=segment['id'],track=True)['allowed'])
        report=self.report_artifact(segment)
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'],report['id'],'too-early')
        self.host.finish('s1',segment['id'],'tracked','completed')
        self.execute.report(segment['id'],report['id'],'after-finish')

    def test_new_input_requires_new_checkpoint_for_waiting_handoff(self):
        self.install();self.configure();self.begin()
        (self.root/'spec.txt').write_text('输入失效',encoding='utf-8')
        self.worker.checkpoint(self.task['id'],'如实停止','待人处理',0,'checkpoint-before-reset')
        self.host.invalidate('s1')
        self.assertFalse(self.host.status(self.task['id'],'s1')['waiting_ready'])

    def test_unbound_or_fabricated_review_trace_cannot_continue(self):
        self.configure();segment=self.begin();report=self.submit_report(segment)
        for index,provenance in enumerate((None,{'type':'project_dispatch','directory':str(self.root/'out')})):
            opinion=self.reviewer.review(report['id'],'pass','原渠道意见',['有文字不等于真的派发'],
                'unbound-'+str(index),expected_revision=1,provenance=provenance)
            with self.assertRaises(FrameworkError):
                self.decide.decide(segment['id'],opinion['id'],'continue','不能借用','decide-'+str(index))

    def test_actual_stdin_tampering_revokes_continuation(self):
        self.configure();segment=self.begin();report=self.submit_report(segment)
        opinion=self.review(report)
        self.decide.decide(segment['id'],opinion['id'],'continue','已核对准确派发','continue')
        run=self.owner.store.get(opinion['data']['provenance']['id'])
        (self.root/run['data']['directory']/'stdin.txt').write_text('原任务被替换的派发稿',encoding='utf-8')
        self.assertFalse(self.host.status(self.task['id'],'s1')['allowed'])
        with self.assertRaises(FrameworkError):
            self.begin('s1','cannot-use-tampered-trace')

    def test_async_queue_work_blocks_report_until_known_end(self):
        self.configure();segment=self.begin()
        self.host.start_work('s1',segment['id'],'child:fixture')
        report=self.report_artifact(segment)
        with self.assertRaises(FrameworkError):
            self.execute.report(segment['id'],report['id'],'premature')
        with self.assertRaises(FrameworkError):
            self.host.finish('s1',segment['id'],'child:fixture','unknown')
        self.host.finish('s1',segment['id'],'child:fixture','failed')
        self.execute.report(segment['id'],report['id'],'after-failed-child')

    def test_stale_input_can_archive_full_unknown_report_without_restoring_tools(self):
        self.install();self.configure();segment=self.begin()
        (self.root/'spec.txt').write_text('输入已被外部改变',encoding='utf-8')
        self.submit_report(segment,self.body(segment,('unknown','unknown')))
        state=self.host.status(self.task['id'],'s1')
        self.assertTrue(state['waiting_ready'])
        self.assertFalse(state['allowed']);self.assertFalse(state['completion_ready'])
