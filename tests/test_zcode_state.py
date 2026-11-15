"""复用原生工具表终态，不把权限请求、失联或模型自报当作工具结束。"""
import json
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

from aireos import client_guard
from aireos.errors import FrameworkError
import test_execution_control as fixtures
import test_execution_client as clients


class ZCodeStateTests(unittest.TestCase):
    setUp=fixtures.ExecutionControlTests.setUp
    actor=fixtures.ExecutionControlTests.actor
    artifact=fixtures.ExecutionControlTests.artifact
    configure=fixtures.ExecutionControlTests.configure
    begin=fixtures.ExecutionControlTests.begin
    body=fixtures.ExecutionControlTests.body
    report_artifact=fixtures.ExecutionControlTests.report_artifact
    install=clients.ExecutionClientTests.install

    def native(self):
        path=self.root.parent/'native.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE tool_usage (session_id TEXT, tool_call_id TEXT, status TEXT, completed_at INTEGER, approval_status TEXT, error_type TEXT)')
            db.execute('CREATE UNIQUE INDEX tool_usage_identity ON tool_usage(session_id,tool_call_id)')
        return path

    def add(self,path,session='s1',key='native-call',status='error',completed='now',approval='denied'):
        if completed=='now':completed=int(time.time()*1000)
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO tool_usage VALUES(?,?,?,?,?,?)',(session,key,status,completed,approval,'permission_denied'))

    def tracked(self):
        self.configure(max_tools=5);segment=self.begin()
        result=self.host.admit('s1','read',{'file_path':'out/x'},'native-call',segment_id=segment['id'])
        self.assertTrue(result['allowed'])
        return segment

    def test_native_denied_terminal_unblocks_report_but_does_not_complete_task(self):
        from aireos.zcode_state import reconcile
        segment=self.tracked();report=self.report_artifact(segment);path=self.native();self.add(path)
        with self.assertRaises(FrameworkError):self.execute.report(segment['id'],report['id'],'before-native-result')
        observed=reconcile(self.host,'s1',path)
        self.assertEqual(1,observed['finished'])
        self.execute.report(segment['id'],report['id'],'after-native-result')
        self.assertFalse(self.host.status(self.task['id'],'s1')['completion_ready'])
        self.assertEqual(0,reconcile(self.host,'s1',path)['finished'])

    def test_running_missing_timestamp_and_foreign_session_do_not_clear_work(self):
        from aireos.zcode_state import reconcile
        segment=self.tracked();path=self.native()
        self.add(path,status='running',completed=None)
        self.add(path,session='other-session',status='completed')
        self.assertEqual(0,reconcile(self.host,'s1',path)['finished'])
        with sqlite3.connect(path) as db:db.execute("UPDATE tool_usage SET status='error',completed_at=NULL WHERE session_id='s1'")
        self.assertEqual(0,reconcile(self.host,'s1',path)['finished'])
        with self.assertRaises(FrameworkError):self.host._quiescent(segment['id'])

    def test_missing_or_incompatible_native_store_does_not_create_or_clear(self):
        from aireos.zcode_state import reconcile
        segment=self.tracked();path=self.root.parent/'missing-native.sqlite'
        with self.assertRaises(FrameworkError):reconcile(self.host,'s1',path)
        self.assertFalse(path.exists())
        with sqlite3.connect(path) as db:db.execute('CREATE TABLE unrelated(value)')
        with self.assertRaises(FrameworkError):reconcile(self.host,'s1',path)
        with self.assertRaises(FrameworkError):self.host._quiescent(segment['id'])

    def test_child_work_cannot_be_cleared_by_native_tool_table(self):
        from aireos.zcode_state import reconcile
        self.configure();segment=self.begin();self.host.start_work('s1',segment['id'],'child:fixture')
        path=self.native();self.add(path,key='child:fixture',status='completed')
        self.assertEqual(0,reconcile(self.host,'s1',path)['finished'])
        with self.assertRaises(FrameworkError):self.host._quiescent(segment['id'])

    def test_hook_reconciles_before_governance_recovery_and_keeps_budget(self):
        self.install();segment=self.tracked();path=self.native();self.add(path)
        policy=self.root/'aiops/aireos/retrieval-policy.json'
        config=json.loads(policy.read_text());config['execution']['host_tool_state']='zcode-sqlite'
        policy.write_text(json.dumps(config),encoding='utf-8')
        with patch('aireos.zcode_state.native_path',return_value=path):
            code,_,reason=client_guard.handle(self.root,'PreToolUse',dict(session_id='s1',tool_use_id='next-call',
                tool_name='Read',tool_input={'file_path':'out/x'}))
        self.assertEqual(0,code,reason)
        state=self.host.status(self.task['id'],'s1')
        self.assertEqual(2,state['used'])
        with self.assertRaises(FrameworkError):self.host._quiescent(segment['id'])
        self.host.finish('s1',segment['id'],'next-call','completed')
        self.host._quiescent(segment['id'])

    def test_native_observer_does_not_open_store_without_pending_native_tools(self):
        from aireos.zcode_state import reconcile
        self.configure();self.begin()
        path=self.root.parent/'never-created.sqlite'
        self.assertEqual(0,reconcile(self.host,'s1',path)['finished'])
        self.assertFalse(path.exists())

    def test_native_store_failure_at_stop_is_an_explicit_block(self):
        self.install();self.tracked()
        policy=self.root/'aiops/aireos/retrieval-policy.json'
        config=json.loads(policy.read_text());config['execution']['host_tool_state']='zcode-sqlite'
        policy.write_text(json.dumps(config),encoding='utf-8')
        with patch('aireos.zcode_state.native_path',return_value=self.root.parent/'missing-native.sqlite'):
            code,output,reason=client_guard.handle(self.root,'Stop',{'session_id':'s1'})
        self.assertEqual(0,code)
        self.assertEqual('block',output['decision'])
        self.assertIn('终态',output['reason'])

    def test_invalid_or_out_of_segment_completion_times_remain_inflight(self):
        from aireos.zcode_state import reconcile
        segment=self.tracked();path=self.native();self.add(path)
        for stamp in [time.time(),int(time.time()*1000)+86400000,1700000000000,1700000000000.5]:
            with sqlite3.connect(path) as db:db.execute('UPDATE tool_usage SET completed_at=?',(stamp,))
            self.assertEqual(0,reconcile(self.host,'s1',path)['finished'],repr(stamp))
            with self.assertRaises(FrameworkError):self.host._quiescent(segment['id'])
