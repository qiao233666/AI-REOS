"""显式模式、完整启动及旧任务保护；全部使用合成临时项目。"""
import json
import unittest

from aireos import client_guard, execution_recovery
from aireos.errors import FrameworkError
import test_execution_client as fixtures


class ExecutionOptInTests(unittest.TestCase):
    setUp=fixtures.ExecutionClientTests.setUp
    actor=fixtures.ExecutionClientTests.actor
    artifact=fixtures.ExecutionClientTests.artifact
    configure=fixtures.ExecutionClientTests.configure
    begin=fixtures.ExecutionClientTests.begin
    body=fixtures.ExecutionClientTests.body
    install=fixtures.ExecutionClientTests.install
    event=fixtures.ExecutionClientTests.event
    invoke=fixtures.ExecutionClientTests.invoke

    def optin(self):
        self.install();self.config['execution']['scope']='opt_in'
        (self.root/'aiops/aireos/retrieval-policy.json').write_text(json.dumps(self.config),encoding='utf-8')

    def prompt(self,text):
        return client_guard.handle(self.root,'UserPromptSubmit',{'session_id':'s1','prompt':text})

    def enable(self):
        self.optin()
        code,_,reason=self.prompt('开启受管长任务\n完整实现并验证全部原要求')
        self.assertEqual(0,code,reason)

    def parameters(self,task=None):
        return dict(task_id=(task or self.task)['id'],session_id='s1',
            original_text='用户原话：完整实现，并保留科学未知。\n全部原要求不缩减。',
            evidence_rules=[{'criterion':'A1','required_classes':['implementation']},
                            {'criterion':'A2','required_classes':['physical']}],
            max_tools=2,max_seconds=120,criteria=['A1'],purpose='核对原任务实现',expected='留下准确证据')

    def start(self,params=None,key='startup'):
        self.assertTrue(callable(getattr(self.control,'start',None)),'缺少主控启动入口')
        return self.control.start(**(params or self.parameters()),request_id=key)

    def test_default_and_discussion_of_mode_never_opt_in_or_bind(self):
        self.optin()
        for prompt in ['修改一个注释','请先登记一个小任务','我觉得“开启受管长任务”这个名字可以',
                       '> 开启受管长任务\n这是引用','请运行长任务','代码示例：\n开启受管长任务']:
            self.assertEqual(0,self.prompt(prompt)[0])
            self.assertEqual(0,self.event('Write',{'file_path':'out/small.txt','content':'小改'})[0])
        self.assertEqual([],self.owner.store.list('execution_binding'))

    def test_explicit_on_provides_guide_blocks_work_but_not_question_or_exit_before_binding(self):
        self.enable()
        self.assertEqual(2,self.event('Bash',{'command':'python scientific.py'})[0])
        self.assertEqual(0,self.event('AskUserQuestion',{'questions':[]})[0])
        code,reply,_=self.prompt('开启受管长任务\n继续准确登记')
        context=reply['hookSpecificOutput']['additionalContext']
        for expected in ['execution-start','create-task','original_text','s1','无需用户']:
            self.assertIn(expected,context)
        self.prompt('退出受管长任务')
        self.assertEqual(0,self.event('Write',{'file_path':'out/small.txt','content':'正常小改'})[0])

    def test_small_formal_task_does_not_enable_managed_mode(self):
        self.optin()
        self.invoke('create-task',dict(title='注释小改',objective='修正文字',acceptance=['准确'],outputs=['small-output']),'owner')
        self.assertEqual(0,self.event('Write',{'file_path':'small-output/note.md','content':'ok'})[0])
        self.assertEqual([],self.owner.store.list('execution_binding'))

    def test_from_locked_unbound_session_create_and_start_full_cli_then_enforce_budget(self):
        self.enable()
        task=self.invoke('create-task',dict(title='明确长任务',objective='完整目标',acceptance=['实现','物理'],outputs=['fresh-output']),'owner')
        result=self.invoke('execution-start',self.parameters(task),'owner')
        self.assertEqual('running',self.owner.store.get(task['id'])['data']['status'])
        self.assertEqual(result['segment']['id'],self.host.status(task['id'],'s1')['segment_id'])
        self.assertEqual(0,self.event('Read',{'file_path':'fresh-output/x'},'one')[0])
        self.assertEqual(0,self.event('Read',{'file_path':'fresh-output/y'},'two')[0])
        self.assertEqual(2,self.event('Read',{'file_path':'fresh-output/z'},'three')[0])
        path=execution_recovery.input_path(self.root,'s1','create-task')
        self.assertEqual(2,self.event('Write',{'file_path':str(path),'content':json.dumps(dict(title='绕过',objective='绕过',acceptance=['x'],outputs=['other']))},'bypass')[0])

    def test_start_without_explicit_permission_is_refused(self):
        self.optin()
        self.assertTrue(callable(getattr(self.control,'start',None)),'缺少启动入口')
        with self.assertRaises(FrameworkError):self.control.start(**self.parameters(),request_id='no-optin')
        self.assertEqual([],self.owner.store.list('execution_policy'))

    def test_direct_begin_cannot_treat_formal_registration_as_user_opt_in(self):
        self.configure();self.optin()
        with self.assertRaises(FrameworkError):self.begin()
        self.assertEqual([],self.owner.store.list('execution_binding'))

    def test_start_preserves_original_and_replay_never_renews_budget(self):
        self.enable();first=self.start()
        self.assertEqual(self.parameters()['original_text'].encode(),self.owner.store.blob(first['original']['data']['blob']))
        self.host.admit('s1','Read',{'file_path':'out/one.txt'},'one',segment_id=first['segment']['id'],track=False)
        again=self.start()
        self.assertEqual(first['segment']['id'],again['segment']['id'])
        self.assertEqual(1,self.host.status(self.task['id'],'s1')['used'])
        self.host.invalidate('s1');self.start()
        self.assertFalse(self.host.status(self.task['id'],'s1')['allowed'])
        with self.assertRaises(FrameworkError):self.start(key='new-key')

    def test_existing_bound_task_cannot_be_downgraded_by_exit_or_new_default(self):
        self.configure(max_tools=1);self.begin();self.optin()
        self.assertEqual(0,self.event('Read',{'file_path':'out/one.txt'},'first')[0])
        self.prompt('退出受管长任务')
        self.assertEqual(2,self.event('Read',{'file_path':'out/one.txt'},'after-exit')[0])

    def test_invalid_rules_or_missing_permission_have_no_startup_side_effects(self):
        self.enable();bad=self.parameters();bad['evidence_rules']=bad['evidence_rules'][:1]
        self.assertTrue(callable(getattr(self.control,'start',None)),'缺少启动入口')
        for control,params in [(self.control,bad),(self.execute,self.parameters())]:
            with self.assertRaises(FrameworkError):control.start(**params,request_id='invalid')
        self.assertEqual([],self.owner.store.list('execution_policy'))
        self.assertFalse((self.root/'out/execution-original').exists())

    def test_file_only_output_is_refused_before_task_start(self):
        self.enable()
        task=self.owner.create_task('文件输出','目标',['实现','物理'],['one.py'],'file-task')
        with self.assertRaises(FrameworkError):self.start(self.parameters(task))
        self.assertEqual('planned',self.owner.store.get(task['id'])['data']['status'])

    def test_exit_after_exact_hold_keeps_report_and_allows_normal_work(self):
        self.configure();segment=self.begin();self.optin()
        report=self.artifact('report.json',json.dumps(self.body(segment)))
        self.execute.report(segment['id'],report['id'],'submit-hold-report')
        opinion=self.reviewer.review(report['id'],'limit','项目原审核渠道',['准确未知交接'],'hold-review',
            expected_revision=report['rev'],provenance=fixtures.fixtures.review_provenance(self,report))
        self.decide.decide(segment['id'],opinion['id'],'hold','原任务未知保留','hold')
        self.assertEqual(0,self.prompt('退出受管长任务')[0])
        self.assertEqual(0,self.event('Write',{'file_path':'out/ordinary.md','content':'普通工作'})[0])
        self.assertEqual('hold',self.owner.store.get(segment['id'])['data']['status'])
        self.assertFalse(self.host.status(self.task['id'],'s1')['completion_ready'])

    def test_mode_is_per_session_and_damaged_policy_revokes_old_retrieval(self):
        self.enable()
        code,_,reason=client_guard.handle(self.root,'UserPromptSubmit',{'session_id':'s2','prompt':'修改一个注释'})
        self.assertEqual(0,code,reason)
        self.assertEqual(0,self.event('Write',{'file_path':'out/small','content':'ok'},session='s2')[0])
        path=self.root/'aiops/aireos/retrieval-policy.json'
        path.write_text('{broken',encoding='utf-8')
        self.assertEqual(2,self.prompt('开启受管长任务')[0])
        self.assertIsNone(client_guard.status(self.root,'s1'))
