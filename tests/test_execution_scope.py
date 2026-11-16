"""覆盖未绑定会话启动、提问与严格执行恢复的真实存储反例。"""
import json
import unittest
from unittest.mock import patch

from aireos import client_guard, execution_recovery
from aireos.errors import FrameworkError
import test_execution_client as fixtures


class ExecutionScopeTests(unittest.TestCase):
    setUp=fixtures.ExecutionClientTests.setUp
    actor=fixtures.ExecutionClientTests.actor
    artifact=fixtures.ExecutionClientTests.artifact
    configure=fixtures.ExecutionClientTests.configure
    begin=fixtures.ExecutionClientTests.begin
    install=fixtures.ExecutionClientTests.install
    event=fixtures.ExecutionClientTests.event

    def scope(self, value='bound_sessions'):
        self.config['execution']['scope'] = value
        (self.root/'aiops/aireos/retrieval-policy.json').write_text(json.dumps(self.config),encoding='utf-8')

    def test_unbound_session_can_work_and_stop_after_retrieval(self):
        self.install(); self.scope()
        for tool, values in [('Read',{'file_path':'out/one.txt'}),('Bash',{'command':'python ordinary.py'}),
                             ('Write',{'file_path':'out/progress.md','content':'普通任务'})]:
            self.assertEqual(0,self.event(tool,values)[0],tool)
        stop = client_guard.handle(self.root,'Stop',{'session_id':'s1'})
        self.assertNotEqual('block',(stop[1] or {}).get('decision'))

    def test_bound_session_keeps_budget_revocation_and_default_global_strictness(self):
        self.configure(max_tools=1); self.begin(); self.install(); self.scope()
        self.assertEqual(0,self.event('Read',{'file_path':'out/one.txt'},'first')[0])
        self.assertEqual(2,self.event('Read',{'file_path':'out/one.txt'},'second')[0])
        self.host.invalidate('s1')
        self.assertEqual(2,self.event('Read',{'file_path':'out/one.txt'},'revoked')[0])
        self.assertEqual('block',client_guard.handle(self.root,'Stop',{'session_id':'s1'})[1]['decision'])

    def test_scoped_sessions_do_not_skip_retrieval_or_frozen_ledger_protection(self):
        self.install(); self.scope()
        client_guard.invalidate(self.root,'s1')
        self.assertEqual(2,self.event('Read',{'file_path':'out/one.txt'})[0])
        self.install(); self.scope()
        self.config['readonly_files']={'out/ledger.yaml':'unused'}
        (self.root/'aiops/aireos/retrieval-policy.json').write_text(json.dumps(self.config),encoding='utf-8')
        self.assertEqual(2,self.event('Write',{'file_path':'out/ledger.yaml','content':'overwrite'})[0])

    def test_scope_errors_and_store_failure_remain_closed(self):
        self.install(); self.scope('typo')
        self.assertEqual(2,self.event('Read',{'file_path':'out/one.txt'})[0])
        self.scope()
        with patch('aireos.storage.Store.connection',side_effect=FrameworkError('store unavailable')):
            self.assertEqual(2,self.event('Read',{'file_path':'out/one.txt'})[0])

    def test_user_question_remains_available_without_retrieval_or_budget(self):
        self.configure(max_tools=1); self.begin(); self.install()
        self.event('Read',{'file_path':'out/one.txt'},'consume')
        client_guard.invalidate(self.root,'s1')
        self.assertEqual(0,self.event('AskUserQuestion',{'questions':[{'question':'如何继续？'}]},'question')[0])
        self.assertEqual(2,self.event('mcp__mail__send',{'message':'unsafe'},'external')[0])
        self.assertEqual(1,self.host.status(self.task['id'],'s1')['used'])

    def test_recovery_error_exposes_required_fields_and_command_names(self):
        self.configure(max_tools=1); self.begin(); self.install()
        path = execution_recovery.input_path(self.root,'s1','execution-status')
        code,_,reason = self.event('Write',{'file_path':str(path),'content':'{}'})
        self.assertEqual(2,code)
        for expected in ['execution-status','task_id','session_id','必需','白名单']:
            self.assertIn(expected,reason)

    def test_fresh_global_session_still_reports_recovery_instructions(self):
        self.install()
        code,_,reason = self.event('Read',{'file_path':'out/one.txt'})
        self.assertEqual(2,code)
        self.assertIn('execution-status',reason)
        self.assertIn('task_id',reason)
