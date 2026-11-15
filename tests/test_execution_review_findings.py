"""原渠道外审反例：报告争议与宿主真实路径不能绕过执行控制。"""
from pathlib import Path
import unittest
from aireos import client_guard
from aireos.errors import FrameworkError
import test_execution_control as fixtures
import test_execution_client as clients


class ReviewFindingsTests(unittest.TestCase):
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

    def dispute(self,report):
        case=self.worker.challenge(report['id'],'完成声明存在实质冲突',[],
            'reviewer',2,'独立核对','challenge-'+report['id'])
        self.reviewer.assess_case(case['id'],True,'确认该准确报告有实质争议','assess-'+report['id'])

    def test_confirmed_report_dispute_revokes_completion(self):
        self.configure();segment=self.begin();report,_,_=self.complete(segment)
        self.dispute(report)
        self.assertFalse(self.host.status(self.task['id'],'s1')['completion_ready'])

    def test_confirmed_prior_report_dispute_blocks_current_stage(self):
        self.configure();first=self.begin();report=self.submit_report(first);opinion=self.review(report)
        self.decide.decide(first['id'],opinion['id'],'continue','按原验收继续','continue')
        self.begin('s1','second');self.dispute(report)
        self.assertFalse(self.host.status(self.task['id'],'s1')['allowed'])

    def test_disputed_report_still_allows_explicit_unfinished_hold(self):
        self.configure();segment=self.begin();report=self.submit_report(segment);opinion=self.review(report)
        self.dispute(report)
        self.decide.decide(segment['id'],opinion['id'],'hold','争议保留，明确未完成交接','hold')
        state=self.host.status(self.task['id'],'s1')
        self.assertTrue(state['handoff_ready']);self.assertFalse(state['completion_ready'])

    def test_hook_uses_host_cwd_for_relative_write(self):
        self.install();self.configure();self.begin()
        outside=self.root.parent/'elsewhere';(outside/'out').mkdir(parents=True)
        code,_,reason=client_guard.handle(self.root,'PreToolUse',dict(session_id='s1',cwd=str(outside),
            tool_use_id='outside-cwd',tool_name='Write',tool_input={'file_path':'out/x','content':'x'}))
        self.assertEqual(2,code,reason)

    def test_hook_allows_actual_nested_output_path(self):
        self.install();self.configure();self.begin()
        code,_,reason=client_guard.handle(self.root,'PreToolUse',dict(session_id='s1',cwd=str(self.root/'out'),
            tool_use_id='inside-cwd',tool_name='Write',tool_input={'file_path':'x','content':'x'}))
        self.assertEqual(0,code,reason)

    def test_recursive_search_cannot_enter_control_descendants(self):
        self.configure(max_tools=6);segment=self.begin()
        (self.root/'out/nested/.zcode').mkdir(parents=True)
        for index,(tool,values) in enumerate([
            ('Grep',{'path':str(self.root),'pattern':'owner','glob':'.aireos/**'}),
            ('Glob',{'pattern':'**/*'}),
            ('Grep',{'path':'out/nested','pattern':'secret'}),
        ]):
            self.assertFalse(self.host.admit('s1',tool,values,'search-'+str(index),
                segment_id=segment['id'])['allowed'])

    def test_recursive_search_in_plain_scientific_directory_remains_available(self):
        self.configure();segment=self.begin();(self.root/'out/plain').mkdir()
        self.assertTrue(self.host.admit('s1','Grep',{'path':'out/plain','pattern':'target'},
            'scoped-search',segment_id=segment['id'])['allowed'])
