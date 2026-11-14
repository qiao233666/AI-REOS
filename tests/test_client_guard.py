"""客户端每轮先检索；完成记录不代替开工收据或科学审核。"""
import copy
import importlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

from aireos.errors import FrameworkError
from aireos.project import Project
from aireos.storage import Store


class ClientGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        Store.initialize(self.root)
        folder = self.root / 'aiops/aireos'
        folder.mkdir(parents=True)
        self.policy = dict(schema=1, enabled=True, entry='aiops/aireos/run.py',
                           hook='aiops/checks/hooks/retrieval_guard.py',
                           bootstrap_files=['AGENTS.md'], readonly_files={})
        (folder / 'retrieval-policy.json').write_text(json.dumps(self.policy), encoding='utf-8')
        self.guard = importlib.import_module('aireos.client_guard')

    def event(self, event, session='one', **extra):
        return self.guard.handle(self.root, event, dict(session_id=session, **extra))

    def test_no_query_blocks_read_and_stop_but_bootstrap_is_available(self):
        self.assertEqual(2, self.event('PreToolUse', tool_name='Read', tool_input={'file_path':'scratch/result.md'})[0])
        self.assertEqual(0, self.event('PreToolUse', tool_name='Read', tool_input={'file_path':'AGENTS.md'})[0])
        self.assertEqual(2, self.event('PreToolUse', cwd=str(self.root.parent), tool_name='Read', tool_input={'file_path':'AGENTS.md'})[0])
        code, output, reason = self.event('Stop')
        self.assertEqual('block', output['decision'])
        self.assertIn('检索', output['reason'])

    def test_prompt_retrieves_native_records_and_allows_only_this_session(self):
        owner = Project(self.root, (self.root/'.aireos/owner.key').read_text().strip())
        task = owner.create_task('合成任务','测试新记录召回',['记录可召回'],['outputs'],'task')
        receipt = owner.prepare(task['id'],'合成','prepare')
        owner.start_task(task['id'],receipt['id'],'start')
        item = owner.add_item('unique-governance-needle', '可检索的新正式记录', 'unknown', '合成', [], task['id'], 'item')
        code, output, reason = self.event('UserPromptSubmit', prompt='unique-governance-needle')
        self.assertEqual(0, code, reason)
        self.assertIn(item['id'], json.dumps(output))
        self.assertEqual(0, self.event('PreToolUse', tool_name='Read', tool_input={'file_path':'scratch/result.md'})[0])
        self.assertEqual(2, self.event('PreToolUse', session='two', tool_name='Read', tool_input={})[0])
        self.assertIsNone(self.event('Stop')[1])
        self.assertEqual(0, self.event('PreToolUse', tool_name='Read', tool_input={'file_path':str(self.root.parent/'other-result.md')})[0])

    def test_new_turn_invalidates_old_completion_and_late_result_cannot_unlock(self):
        first = self.guard.begin(self.root, 'one', 'first')
        self.guard.retrieve(self.root, 'one', 'first', turn_id=first)
        second = self.guard.begin(self.root, 'one', 'second')
        self.assertEqual(2, self.event('PreToolUse', tool_name='Bash', tool_input={'command':'rg result scratch'})[0])
        with self.assertRaises(FrameworkError):
            self.guard.retrieve(self.root, 'one', 'first', turn_id=first)
        self.guard.retrieve(self.root, 'one', 'second', turn_id=second)
        self.assertEqual(0, self.event('PreToolUse', tool_name='Bash', tool_input={'command':'rg result scratch'})[0])

    def test_invalid_retry_revokes_previously_successful_completion(self):
        for invalid in ['', 'x'*16001, []]:
            self.event('UserPromptSubmit',prompt='有效问题')
            with self.assertRaises(FrameworkError):
                self.guard.retrieve(self.root,'one',invalid)
            self.assertEqual(2,self.event('PreToolUse',tool_name='Read',tool_input={})[0])

    def test_bad_policy_during_new_prompt_cannot_revive_old_completion(self):
        self.event('UserPromptSubmit',prompt='first')
        path=self.root/'aiops/aireos/retrieval-policy.json'
        path.write_text('{broken',encoding='utf-8')
        self.assertEqual(2,self.event('UserPromptSubmit',prompt='second')[0])
        path.write_text(json.dumps(self.policy),encoding='utf-8')
        self.assertEqual(2,self.event('PreToolUse',tool_name='Read',tool_input={})[0])

    def test_wrapper_load_failure_already_revoked_previous_turn(self):
        self.event('UserPromptSubmit',prompt='first')
        entry=self.root/self.policy['entry']; entry.write_text('raise RuntimeError("broken lock")',encoding='utf-8')
        script=self.root/self.policy['hook']; script.parent.mkdir(parents=True)
        script.write_bytes((Path(__file__).resolve().parents[1]/'template/retrieval_guard.py').read_bytes())
        result=subprocess.run([sys.executable,'-B',str(script),'--event','UserPromptSubmit'],
            input=json.dumps(dict(session_id='one',prompt='second')),text=True,encoding='utf-8',capture_output=True,timeout=10,
            env={**__import__('os').environ,'PYTHONUTF8':'1'})
        self.assertEqual(2,result.returncode)
        self.assertEqual(2,self.event('PreToolUse',tool_name='Read',tool_input={})[0])

    def test_recovery_input_can_be_written_without_opening_arbitrary_writes(self):
        self.guard.begin(self.root,'one','question')
        path=self.guard.recovery_input(self.root,'one')
        body=json.dumps({'query':'修复后的具体问题','session_id':'one'})
        self.assertEqual(0,self.event('PreToolUse',tool_name='Write',tool_input={'file_path':str(path),'content':body})[0])
        for name,content in [('scratch/result.md',body),(str(path),'not json'),(str(path),json.dumps({'query':'question','session_id':'two'}))]:
            self.assertEqual(2,self.event('PreToolUse',tool_name='Write',tool_input={'file_path':name,'content':content})[0])

    def test_failed_query_never_unlocks_and_recovery_must_use_real_entry(self):
        from aireos.history import HistoryArchive
        owner = Project(self.root, (self.root/'.aireos/owner.key').read_text().strip())
        legacy = self.root/'old.md'
        legacy.write_text('## F-001\n历史原文\n', encoding='utf-8')
        HistoryArchive(owner.store).configure([{'path':'old.md','format':'markdown'}], 'history')
        legacy.write_bytes(b'\xff\xfe\x00')
        self.event('UserPromptSubmit', prompt='历史原文')
        self.assertEqual(2, self.event('PreToolUse', tool_name='Read', tool_input={})[0])
        entry = (self.root/'aiops/aireos/run.py').as_posix()
        recovery = f'python -X utf8 "{entry}" client-context --input query.json'
        self.assertIn(entry, self.guard.context_text(self.root))
        self.assertEqual(0, self.event('PreToolUse', tool_name='Bash', tool_input={'command':recovery})[0])
        for command in [f'echo {recovery}', recovery+'; cat old.md', 'python other.py client-context', 'pwd; rg result .']:
            self.assertEqual(2, self.event('PreToolUse', tool_name='Bash', tool_input={'command':command})[0], command)
        legacy.write_text('## F-001\n历史原文\n', encoding='utf-8')
        self.guard.retrieve(self.root, 'one', '历史原文')
        self.assertEqual(0, self.event('PreToolUse', tool_name='Read', tool_input={})[0])

    def test_project_isolation_and_session_reset(self):
        self.event('UserPromptSubmit', prompt='project one')
        other = self.root.parent/'other'
        Store.initialize(other)
        folder=other/'aiops/aireos'; folder.mkdir(parents=True)
        (folder/'retrieval-policy.json').write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(2, self.guard.handle(other,'PreToolUse',dict(session_id='one',tool_name='Read',tool_input={}))[0])
        self.event('SessionStart')
        self.assertEqual(2, self.event('PreToolUse',tool_name='Read',tool_input={})[0])

    def test_missing_session_empty_query_and_unknown_event_do_not_pass(self):
        for invalid in ['', None, [], True]:
            self.assertEqual(2, self.guard.handle(self.root,'PreToolUse',dict(session_id=invalid,tool_name='Read',tool_input={}))[0])
        turn=self.guard.begin(self.root,'one','question')
        with self.assertRaises(FrameworkError):
            self.guard.retrieve(self.root,'one','',turn_id=turn)
        self.assertEqual(2,self.event('Unsupported')[0])

    def test_installer_preserves_existing_guards_and_check_detects_missing_event(self):
        existing={'hooks':{'enabled':True,'events':{'PreToolUse':[{'matcher':'Bash','hooks':[{'type':'process','command':'python','args':['destructive.py']}]}]}}}
        before=copy.deepcopy(existing)
        script=self.root/self.policy['hook']; script.parent.mkdir(parents=True); script.write_text('# hook',encoding='utf-8')
        (self.root/self.policy['entry']).write_text('# 固定入口fixture',encoding='utf-8')
        config=self.guard.zcode_config(self.root,sys.executable,script,existing)
        self.assertEqual(before,existing)
        self.assertEqual('destructive.py',config['hooks']['events']['PreToolUse'][0]['hooks'][0]['args'][0])
        target=self.root/'.zcode/config.json'; target.parent.mkdir(); target.write_text(json.dumps(config),encoding='utf-8')
        self.assertEqual([],self.guard.check_adapter(self.root)['errors'])
        config['hooks']['events'].pop('UserPromptSubmit')
        target.write_text(json.dumps(config),encoding='utf-8')
        self.assertTrue(self.guard.check_adapter(self.root)['errors'])

    def test_checker_rejects_non_python_command_and_missing_fixed_entry(self):
        script=self.root/self.policy['hook']; script.parent.mkdir(parents=True); script.write_text('# hook',encoding='utf-8')
        entry=self.root/self.policy['entry']; entry.write_text('# fixture',encoding='utf-8')
        config=self.guard.zcode_config(self.root,sys.executable,script,{})
        target=self.root/'.zcode/config.json'; target.parent.mkdir()
        for groups in config['hooks']['events'].values():
            for group in groups:
                for hook in group['hooks']:
                    hook['command']='echo'
        target.write_text(json.dumps(config),encoding='utf-8')
        self.assertTrue(self.guard.check_adapter(self.root)['errors'])
        config=self.guard.zcode_config(self.root,sys.executable,script,{})
        target.write_text(json.dumps(config),encoding='utf-8')
        entry.unlink()
        self.assertTrue(self.guard.check_adapter(self.root)['errors'])

    def test_disabled_unconfigured_and_readonly_protection(self):
        self.event('UserPromptSubmit',prompt='question')
        self.policy['readonly_files']={'old.md':'unused-hash-for-write-policy'}
        (self.root/'aiops/aireos/retrieval-policy.json').write_text(json.dumps(self.policy),encoding='utf-8')
        self.assertEqual(2,self.event('PreToolUse',tool_name='Edit',tool_input={'file_path':'old.md'})[0])
        self.policy['enabled']=False
        (self.root/'aiops/aireos/retrieval-policy.json').write_text(json.dumps(self.policy),encoding='utf-8')
        self.assertEqual(0,self.event('PreToolUse',tool_name='Read',tool_input={})[0])


if __name__ == '__main__':
    unittest.main()
