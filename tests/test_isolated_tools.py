"""登记脚本复用原工具运行、原生一次许可和真实隔离后端。"""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from aireos import recall
from aireos.errors import FrameworkError
from aireos.tools import ToolRegistry
import test_execution_control as fixtures


class IsolatedToolsTests(unittest.TestCase):
    setUp=fixtures.ExecutionControlTests.setUp
    actor=fixtures.ExecutionControlTests.actor
    artifact=fixtures.ExecutionControlTests.artifact
    configure=fixtures.ExecutionControlTests.configure
    begin=fixtures.ExecutionControlTests.begin

    def setup_script(self,max_tools=1,code=None):
        folder=self.root/'tools';folder.mkdir();(folder/'registered.py').write_text(code or 'print("registered")',encoding='utf-8')
        manifest=dict(name='registered',version='1',api=1,entry='tools/registered.py',purpose='已批准有界脚本',
            aliases=[],inputs='字符串参数',outputs='out',owner='owner',limitations='本机有界',verification='合成',dependencies=[])
        self.tool=ToolRegistry(self.owner.store).register(manifest,'register')
        self.executor=self.actor('script-executor',['execute'])
        key=next((self.root/'.aireos/keys').glob('script-executor-*.key'))
        self.receipt=self.executor.prepare(self.task['id'],'登记脚本及原验收','script-prepare')
        exe=self.root/'codex.exe';exe.write_bytes(b'fake-native-executable')
        config={'schema':1,'enabled':True,'execution':{'enabled':True,'script_runtime':{
            'enabled':True,'executable':str(exe),'sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
            'backend':'elevated','max_seconds':30,'task_bindings':{self.task['id']:{
                'tools':[recall.reference(self.tool)],'receipt_id':self.receipt['id'],
                'credential_file':key.relative_to(self.root).as_posix()}}}}}
        (self.root/'aiops/aireos').mkdir(parents=True);(self.root/'aiops/aireos/retrieval-policy.json').write_text(json.dumps(config),encoding='utf-8')
        self.configure(max_tools=max_tools);self.segment=self.begin()
        self.values={'resources':[self.tool['id']], 'metadata':{'registered_tool_id':self.tool['id'],'arguments':[],
            'runtime_sha256':hashlib.sha256((self.root/'aiops/aireos/retrieval-policy.json').read_bytes()).hexdigest()},
            'source':{'type':'tool','id':'native-call','messageID':'native-message'}}
        self.payload={'formal':{'task_id':self.task['id'],'session_id':'s1','segment_id':self.segment['id']},
            'native':{'session_id':'native-session','tool_id':'native-call','message_id':'native-message'},
            'tool_id':self.tool['id'],'arguments':[]}

    def admit(self):
        return self.host.admit('s1','aireos_run_tool',self.values,'native-session:permission',segment_id=self.segment['id'])

    def test_native_registered_tool_counts_once_and_rejects_unknown_tool(self):
        self.setup_script()
        self.assertTrue(self.admit()['allowed'])
        self.assertEqual(1,self.host.status(self.task['id'],'s1')['used'])
        other=json.loads(json.dumps(self.values));other['resources']=['unregistered']
        self.assertFalse(self.host.admit('s1','aireos_run_tool',other,'bad',segment_id=self.segment['id'])['allowed'])

    def test_bridge_requires_matching_actual_permission_before_run(self):
        from aireos.isolated_tools import execute_registered
        self.setup_script()
        with self.assertRaises(FrameworkError):execute_registered(self.root,self.payload)
        self.assertEqual([],self.owner.store.list('run'))

    def test_registered_run_reuses_original_idempotency_and_isolated_command(self):
        from aireos.isolated_tools import execute_registered
        self.setup_script();self.assertTrue(self.admit()['allowed'])
        observed=[]
        def run(command,**kwargs):
            observed.append(command)
            self.assertIn('sandbox',command);self.assertFalse(kwargs.get('shell',False))
            return subprocess.CompletedProcess(command,0)
        with patch('aireos.isolated_tools.subprocess.run',side_effect=run):
            first=execute_registered(self.root,self.payload);second=execute_registered(self.root,self.payload)
        self.assertEqual('completed',first['status']);self.assertEqual(first['id'],second['id'])
        self.assertEqual(1,len(observed));self.assertEqual(1,len(self.owner.store.list('run')))
        self.host.finish('s1',self.segment['id'],'native-session:permission','completed')
        self.host._quiescent(self.segment['id'])

    def test_timeout_preserves_physical_work_and_does_not_restart(self):
        from aireos.isolated_tools import execute_registered
        self.setup_script();self.assertTrue(self.admit()['allowed'])
        with patch('aireos.isolated_tools.subprocess.run',side_effect=subprocess.TimeoutExpired('sandbox',30)) as run:
            first=execute_registered(self.root,self.payload);second=execute_registered(self.root,self.payload)
        self.assertEqual('unknown',first['status']);self.assertEqual(first['id'],second['id']);self.assertEqual(1,run.call_count)
        self.host.finish('s1',self.segment['id'],'native-session:permission','failed')
        with self.assertRaises(FrameworkError):self.host._quiescent(self.segment['id'])

    def test_policy_or_native_identity_change_after_permission_cannot_start_process(self):
        from aireos.isolated_tools import execute_registered
        self.setup_script();self.assertTrue(self.admit()['allowed'])
        other=json.loads(json.dumps(self.payload));other['native']['tool_id']='forged'
        with self.assertRaises(FrameworkError):execute_registered(self.root,other)
        p=self.root/'aiops/aireos/retrieval-policy.json';p.write_text(p.read_text()+'\n')
        with self.assertRaises(FrameworkError):execute_registered(self.root,self.payload)
        self.assertEqual([],self.owner.store.list('run'))

    def test_changed_registered_source_is_rejected(self):
        from aireos.isolated_tools import execute_registered
        self.setup_script();self.assertTrue(self.admit()['allowed'])
        (self.root/'tools/registered.py').write_text('print("changed")',encoding='utf-8')
        with self.assertRaises(FrameworkError):execute_registered(self.root,self.payload)
        self.assertEqual([],self.owner.store.list('run'))

    def test_guarded_task_cannot_fall_back_to_legacy_unisolated_tool_run(self):
        self.setup_script()
        with self.assertRaises(FrameworkError):
            ToolRegistry(self.executor.store).run(self.tool['id'],self.task['id'],self.receipt['id'],[],'legacy-bypass')
        self.assertEqual([],self.owner.store.list('run'))

    def test_script_bridge_rejects_executor_with_review_privilege(self):
        from aireos.isolated_tools import execute_registered
        self.setup_script()
        path=self.root/'aiops/aireos/retrieval-policy.json';config=json.loads(path.read_text())
        key=next((self.root/'.aireos/keys').glob('worker-*.key'))
        config['execution']['script_runtime']['task_bindings'][self.task['id']]['credential_file']=key.relative_to(self.root).as_posix()
        path.write_text(json.dumps(config),encoding='utf-8')
        self.values['metadata']['runtime_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertTrue(self.admit()['allowed'])
        with self.assertRaises(FrameworkError):execute_registered(self.root,self.payload)
        self.assertEqual([],self.owner.store.list('run'))
