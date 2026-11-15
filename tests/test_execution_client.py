"""检索与执行资格分别核对；接入真实事件处理函数。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
import test_execution_control as fixtures
from aireos import client_guard
from aireos import execution_recovery


class ExecutionClientTests(unittest.TestCase):
    setUp=fixtures.ExecutionControlTests.setUp
    actor=fixtures.ExecutionControlTests.actor
    artifact=fixtures.ExecutionControlTests.artifact
    configure=fixtures.ExecutionControlTests.configure
    begin=fixtures.ExecutionControlTests.begin
    body=fixtures.ExecutionControlTests.body

    def install(self):
        folder=self.root/'aiops/aireos';folder.mkdir(parents=True,exist_ok=True)
        self.config={'schema':1,'enabled':True,'entry':'aiops/aireos/run.py','hook':'guard.py','execution':{'enabled':True}}
        (folder/'retrieval-policy.json').write_text(json.dumps(self.config),encoding='utf-8')
        self.entry=folder/'run.py'
        self.entry.write_text('from pathlib import Path\nimport sys\nfrom aireos.cli import main\nraise SystemExit(main(["--project",str(Path(__file__).resolve().parents[2]),*sys.argv[1:]]))\n',encoding='utf-8')
        turn=client_guard.begin(self.root,'s1','原任务全部验收与完整报告')
        client_guard.retrieve(self.root,'s1','原任务全部验收与完整报告',turn_id=turn)

    def event(self, tool, values, key='event', session='s1'):
        return client_guard.handle(self.root,'PreToolUse',dict(session_id=session,tool_use_id=key,
            tool_name=tool,tool_input=values))

    def write(self, path, body, key='write'):
        content=json.dumps(body,ensure_ascii=False)
        self.assertFalse(path.exists(),'本回归必须从未预置的恢复文件开始')
        code,_,reason=self.event('Write',{'file_path':str(path),'content':content},key)
        self.assertEqual(0,code,reason)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(content,encoding='utf-8')

    def invoke(self, command, body, actor=None):
        path=execution_recovery.input_path(self.root,'s1',command)
        # 输入参数可以替换本命令的旧草稿；它不作为artifact或授权真源。
        content=json.dumps(body,ensure_ascii=False)
        code,_,reason=self.event('Write',{'file_path':str(path),'content':content},command+'-input')
        self.assertEqual(0,code,reason)
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8')
        argv=[sys.executable,'-B','-X','utf8',str(self.entry),command,'--input',str(path)]
        if actor:
            credential=(self.root/'.aireos/owner.key' if actor=='owner' else
                        next((self.root/'.aireos/keys').glob(actor+'-*.key')))
            argv.extend(['--credential-file',str(credential),'--request-id',command+'-'+str(self.serial)])
            self.serial+=1
        shell=' '.join('"'+value.replace('\\','/')+'"' for value in argv)
        code,_,reason=self.event('Bash',{'command':shell},command+'-call')
        self.assertEqual(0,code,reason)
        framework=str(Path(__file__).resolve().parents[1])
        env={**os.environ,'PYTHONPATH':os.pathsep.join([framework,os.environ.get('PYTHONPATH','')])}
        result=subprocess.run(argv,text=True,encoding='utf-8',capture_output=True,timeout=10,env=env)
        self.assertEqual(0,result.returncode,result.stderr)
        return json.loads(result.stdout)

    def review_parameters(self, segment, verdict):
        current=self.owner.store.get(segment['id'])
        return dict(item_id=current['data']['report']['id'], expected_revision=current['data']['report']['rev'],
                    verdict=verdict, method='项目既定审核渠道', checks=['按原任务及准确文件核对进展与缺口'])

    def recover_report(self, segment, body):
        path=execution_recovery.report_path(self.root,'s1',body)
        self.write(path,body,'report-write')
        artifact=self.invoke('add-artifact',dict(task_id=self.task['id'],path=path.relative_to(self.root).as_posix(),title='完整原验收阶段报告'),'worker')
        return self.invoke('execution-report',dict(segment_id=segment['id'],artifact_id=artifact['id']),'worker')

    def test_shell_expansion_and_wrong_interpreter_never_receive_governance_exemption(self):
        self.install()
        path=execution_recovery.input_path(self.root,'s1','get')
        self.write(path,{'identifier':self.task['id']})
        prefix='"'+sys.executable.replace('\\','/')+'" "'+str(self.entry).replace('\\','/')+'"'
        valid=prefix+' get --input "'+str(path).replace('\\','/')+'"'
        self.assertTrue(client_guard._control_command(self.root,self.config,dict(session_id='s1',tool_name='Bash',tool_input={'command':valid})))
        for unsafe in ['$(python malicious.py)','$INPUT','%INPUT%','*.json','[a].json','~/.json','a.json>out','a.json;python malicious.py','a.json|python malicious.py','a.json\nwhoami','a.json`whoami','a.json^&whoami']:
            for suffix,checker in [('get',client_guard._control_command),('client-context',client_guard._recovery)]:
                command=prefix+' '+suffix+' --input "'+unsafe+'"'
                self.assertFalse(checker(self.root,self.config,dict(session_id='s1',tool_name='Bash',tool_input={'command':command})),command)
        evil=valid.replace('"'+sys.executable.replace('\\','/')+'"','"C:/untrusted/python.exe"',1)
        self.assertFalse(client_guard._control_command(self.root,self.config,dict(session_id='s1',tool_name='Bash',tool_input={'command':evil})))
        alias=valid.replace('"'+sys.executable.replace('\\','/')+'"','python',1)
        self.assertFalse(client_guard._control_command(self.root,self.config,dict(session_id='s1',tool_name='Bash',tool_input={'command':alias})))

    def test_from_zero_inputs_unbound_budget_hold_and_independent_recovery(self):
        self.install()
        self.assertEqual(2,self.event('Write',{'file_path':'out/science.py','content':'arbitrary computation'})[0])
        self.invoke('execution-configure',dict(task_id=self.task['id'],original_item_id=self.original['id'],
            evidence_rules=[{'criterion':'A1','required_classes':['implementation']},{'criterion':'A2','required_classes':['physical']}],
            max_tools=1,max_seconds=120),'owner')
        segment=self.invoke('execution-begin',dict(task_id=self.task['id'],session_id='s1',criteria=['A1'],purpose='检查实现',expected='可核查进展'),'worker')
        self.assertEqual(0,self.event('Read',{'file_path':'out/one.txt'},'last-budget')[0])
        self.assertEqual(2,self.event('Write',{'file_path':'out/science.py','content':'arbitrary computation'},'over-budget')[0])
        submitted=self.recover_report(segment,self.body(segment))
        packet=self.invoke('execution-materials',{'segment_id':segment['id']})
        self.assertEqual('原始验收文字',packet['original']['text'])
        opinion=self.invoke('review',self.review_parameters(segment,'limit'),'reviewer')
        self.invoke('execution-decide',dict(segment_id=segment['id'],review_id=opinion['id'],decision='hold',reason='先修订未知项陈述'),'decider')
        stop=client_guard.handle(self.root,'Stop',{'session_id':'s1'})
        self.assertEqual(0,stop[0]); self.assertIn('原任务尚未完成',stop[1]['systemMessage'])
        self.assertNotEqual('block',stop[1].get('decision'))
        self.assertEqual('block',client_guard.handle(self.root,'Stop',{'session_id':'other'})[1]['decision'])
        self.assertEqual(2,self.event('Read',{'file_path':'out/one.txt'},'held-tool')[0])
        self.recover_report(segment,self.body(segment,('unknown','unknown')))
        opinion=self.invoke('review',self.review_parameters(segment,'pass'),'reviewer')
        self.invoke('execution-decide',dict(segment_id=segment['id'],review_id=opinion['id'],decision='continue',reason='逐项报告真实且下一步已批准'),'decider')
        next_segment=self.invoke('execution-begin',dict(task_id=self.task['id'],session_id='s1',criteria=['A1'],purpose='检查实现',expected='可核查进展'),'worker')
        self.assertEqual(2,next_segment['data']['index'])
        self.assertEqual(0,self.host.status(self.task['id'],'s1')['used'])

    def test_old_hold_cannot_allow_stop_after_new_input_or_without_formal_decision(self):
        self.configure(); segment=self.begin(); self.install()
        self.recover_report(segment,self.body(segment,('unknown','unknown')))
        self.assertEqual('block',client_guard.handle(self.root,'Stop',{'session_id':'s1'})[1]['decision'])
        opinion=self.invoke('review',self.review_parameters(segment,'limit'),'reviewer')
        self.invoke('execution-decide',dict(segment_id=segment['id'],review_id=opinion['id'],decision='hold',reason='诚实未知交接'),'decider')
        self.assertIn('尚未完成',client_guard.handle(self.root,'Stop',{'session_id':'s1'})[1]['systemMessage'])
        code,_,reason=client_guard.handle(self.root,'UserPromptSubmit',{'session_id':'s1','prompt':'新的用户请求，不得复用旧hold结束'})
        self.assertEqual(0,code,reason)
        self.assertEqual('block',client_guard.handle(self.root,'Stop',{'session_id':'s1'})[1]['decision'])

    def test_expired_segment_can_write_only_accurate_new_report(self):
        self.configure(max_tools=1);segment=self.begin();self.install()
        future=datetime.now(timezone.utc)+timedelta(seconds=360)
        body=self.body(segment)
        path=execution_recovery.report_path(self.root,'s1',body)
        with patch('aireos.execution_control.datetime') as clock:
            clock.now.return_value=future;clock.fromisoformat.side_effect=datetime.fromisoformat
            self.assertEqual(2,self.event('Read',{'file_path':'out/one.txt'},'expired')[0])
            self.write(path,body)
            artifact=self.invoke('add-artifact',dict(task_id=self.task['id'],path=path.relative_to(self.root).as_posix(),title='到期后诚实报告'),'worker')
            submitted=self.invoke('execution-report',dict(segment_id=segment['id'],artifact_id=artifact['id']),'worker')
            self.assertEqual('submitted',submitted['data']['status'])
        self.assertEqual(0,self.host.status(self.task['id'],'s1')['used'])
        self.assertEqual(2,self.event('Write',{'file_path':str(path),'content':json.dumps(body)},'overwrite')[0])

    def test_recovery_rejects_cross_session_arbitrary_output_extra_schema_and_cache_artifact(self):
        self.configure(max_tools=1);segment=self.begin();self.install()
        self.event('Read',{'file_path':'out/one.txt'},'consume')
        body=self.body(segment)
        report=execution_recovery.report_path(self.root,'s1',body)
        bad=dict(body);bad['script']='run arbitrary science'
        samples=[(str(report),bad),('out/science.json',body),
            (str(execution_recovery.input_path(self.root,'s2','execution-status')),{'task_id':self.task['id'],'session_id':'s2'}),
            (str(execution_recovery.input_path(self.root,'s1','execution-status')),{'task_id':self.task['id'],'session_id':'s1','execute':'evil'}),
            (str(execution_recovery.input_path(self.root,'s1','add-artifact')),{'task_id':self.task['id'],'path':'.aireos/cache/secret.json','title':'cache不可登记'})]
        for path,value in samples:
            self.assertEqual(2,self.event('Write',{'file_path':path,'content':json.dumps(value)},'reject-'+path)[0],path)
        missing=self.body(segment,('satisfied','satisfied'))
        self.assertEqual(2,self.event('Write',{'file_path':str(report),'content':json.dumps(missing)},'missing-classes')[0])
        self.assertEqual(2,self.event('Write',{'file_path':str(report),'content':'{"segment_id":"x","segment_id":"y"}'},'duplicate-fields')[0])

    def test_recovery_rejects_hardlinks_oversize_and_nonliteral_input_file(self):
        self.configure(max_tools=1);segment=self.begin();self.install()
        self.event('Read',{'file_path':'out/one.txt'},'consume')
        target=execution_recovery.input_path(self.root,'s1','get')
        source=self.root/'spec.txt'
        before=source.read_bytes()
        os.link(source,target)
        value=json.dumps({'identifier':self.task['id']})
        self.assertEqual(2,self.event('Write',{'file_path':str(target),'content':value},'hardlink')[0])
        self.assertEqual(before,source.read_bytes())
        query=client_guard.recovery_input(self.root,'s1')
        os.link(source,query)
        self.assertEqual(2,self.event('Write',{'file_path':str(query),'content':json.dumps({'query':'仅查询','session_id':'s1'})},'query-hardlink')[0])
        report=execution_recovery.report_path(self.root,'s1',self.body(segment))
        large=self.body(segment);large['criteria'][0]['remaining']='x'*65536
        self.assertEqual(2,self.event('Write',{'file_path':str(report),'content':json.dumps(large)},'too-large')[0])
        outside=self.root.parent/'execution-reports/escaped.json'
        self.assertEqual(2,self.event('Write',{'file_path':str(outside),'content':json.dumps(self.body(segment))},'outside-root')[0])
    def test_retrieval_success_does_not_bypass_execution_budget(self):
        self.configure(max_tools=1)
        segment=self.begin()
        folder=self.root/'aiops/aireos';folder.mkdir(parents=True)
        policy={'schema':1,'enabled':True,'entry':'aiops/aireos/run.py','hook':'guard.py','execution':{'enabled':True}}
        (folder/'retrieval-policy.json').write_text(json.dumps(policy),encoding='utf-8')
        turn=client_guard.begin(self.root,'s1','原任务要求')
        client_guard.retrieve(self.root,'s1','原任务要求',turn_id=turn)
        def event(key):
            return client_guard.handle(self.root,'PreToolUse',{'session_id':'s1','tool_use_id':key,'tool_name':'Write','tool_input':{'file_path':'out/work.txt'}})
        self.assertEqual(0,event('one')[0])
        self.assertEqual(2,event('two')[0])
        self.assertEqual(1,self.host.status(session_id='s1')['used'])
        self.assertEqual('block',client_guard.handle(self.root,'Stop',{'session_id':'s1'})[1]['decision'])

    def test_new_prompt_revokes_previous_execution_even_after_real_query(self):
        self.configure();self.begin()
        folder=self.root/'aiops/aireos';folder.mkdir(parents=True)
        (folder/'retrieval-policy.json').write_text(json.dumps({'schema':1,'enabled':True,'entry':'aiops/aireos/run.py','hook':'guard.py','execution':{'enabled':True}}),encoding='utf-8')
        code,_,_=client_guard.handle(self.root,'UserPromptSubmit',{'session_id':'s1','prompt':'任务要求发生新的补充'})
        self.assertEqual(0,code)
        self.assertEqual(2,client_guard.handle(self.root,'PreToolUse',{'session_id':'s1','tool_use_id':'late','tool_name':'Bash','tool_input':{'command':'python long_job.py'}})[0])
