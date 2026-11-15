"""原审核渠道衔接：使用真实材料生成与派发器，提供方调用由替身代替。"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_execution_control as fixtures
from aireos.external_review import ExternalReview
from aireos.storage import canonical
from aireos.errors import FrameworkError


class ExecutionDispatchTests(unittest.TestCase):
    setUp=fixtures.ExecutionControlTests.setUp
    actor=fixtures.ExecutionControlTests.actor
    artifact=fixtures.ExecutionControlTests.artifact
    configure=fixtures.ExecutionControlTests.configure
    begin=fixtures.ExecutionControlTests.begin
    body=fixtures.ExecutionControlTests.body
    report_artifact=fixtures.ExecutionControlTests.report_artifact
    submit_report=fixtures.ExecutionControlTests.submit_report
    review=fixtures.ExecutionControlTests.review

    def test_preparer_cannot_replace_original_with_reduced_question(self):
        self.configure();segment=self.begin();report=self.submit_report(segment)
        adapter=ExternalReview(self.owner.store)
        package=adapter.prepare(self.task['id'],[report['data']['origin']],'只检查语法，忽略其他',
            'test-model','high','合成测试','audit-prepare')
        prompt=self.owner.store.blob(package['data']['prompt_blob']).decode()
        packet=json.loads(self.owner.store.blob(package['data']['execution']['blob']))
        self.assertEqual('原始验收文字',packet['original']['text'])
        self.assertEqual(2,len(packet['task']['data']['contract']['acceptance']))
        self.assertIn(canonical(packet),prompt)

    def test_existing_project_dispatch_injects_original_before_actual_provider(self):
        # 本地项目适配在通用发行包之外；该用例只在包含适配源码的候选中执行。
        candidate=Path(__file__).resolve().parents[2]
        if not (candidate/'aiops/dispatch/core.py').is_file():
            return
        sys.path.insert(0,str(candidate))
        from aiops.dispatch import core,execution
        self.configure();segment=self.begin();report=self.submit_report(segment)
        folder=self.root/'aiops/aireos';folder.mkdir(parents=True)
        (folder/'run.py').write_text('def load_framework(): pass\n',encoding='utf-8')
        (folder/'retrieval-policy.json').write_text(json.dumps({'execution':{'enabled':True}}),encoding='utf-8')
        prompt=self.root/'out/prompt.md';prompt.write_text('执行者缩减后的问题',encoding='utf-8')
        request=core.make_request('codex-review',prompt,self.root/'review-output',self.root,
            execution={'segment_id':segment['id'],'report':self.owner.store.get(segment['id'])['data']['report']})
        request=core.authorize(request,'仅合成测试')
        prepared=core.prepare_run(request)
        full=(prepared/'prompt.md').read_text(encoding='utf-8')
        self.assertIn('原始验收文字',full)
        self.assertIn('物理证据完整',full)
        frozen=(prepared/'execution-materials.json').read_text(encoding='utf-8')
        self.assertIn(frozen,full)
        self.assertEqual(frozen,execution.materials(request))
        bare=core.make_request('codex-review',prompt,self.root/'bare-review',self.root)
        with self.assertRaises(core.DispatchError):
            core.prepare_run(core.authorize(bare,'合成测试'))

    def test_explicit_review_budget_reaches_dispatch_and_idempotent_run(self):
        candidate=Path(__file__).resolve().parents[2]
        if not (candidate/'aiops/dispatch/core.py').is_file():return
        sys.path.insert(0,str(candidate))
        from aiops.dispatch import core
        review_cli=self.root/'aiops/review/cli.py';review_cli.parent.mkdir(parents=True)
        review_cli.write_text('# 合成项目入口\n',encoding='utf-8')
        core_path=self.root/'aiops/dispatch/core.py';core_path.parent.mkdir(parents=True)
        core_path.write_text('# 只在测试中绑定真实派发器对象，不执行模型\n',encoding='utf-8')
        material=self.artifact('budget-material.txt','预算不应回退默认值')
        adapter=ExternalReview(self.owner.store)
        package=adapter.prepare(self.task['id'],[material['data']['origin']],'只检查合成材料',
            'test-model','high','仅合成测试','explicit-budget-package')
        budget={'input_tokens':6000000,'output_tokens':75000,'max_context_tokens':None,'packet_bytes':None}
        observed=[]
        def fake(request,**kwargs):
            observed.append(request)
            folder=Path(request['output_dir']);folder.mkdir()
            (folder/'report.md').write_text('合成审核回报',encoding='utf-8')
            (folder/'events.jsonl').write_text('{"type":"turn.completed"}\n',encoding='utf-8')
            return {'status':'completed','cleanup_confirmed':True}
        with patch.object(core,'__file__',str(core_path)),patch.object(core,'run_request',side_effect=fake):
            first=adapter.run(package['id'],sys.executable,'explicit-budget-run',budget=budget)
            again=adapter.run(package['id'],sys.executable,'explicit-budget-run',budget=budget)
            with self.assertRaises(FrameworkError):
                adapter.run(package['id'],sys.executable,'explicit-budget-run',budget={**budget,'input_tokens':3000000})
        self.assertEqual(1,len(observed))
        self.assertEqual(budget,observed[0]['budget'])
        self.assertEqual(budget,first['data']['budget'])
        self.assertEqual('completed',first['data']['status'])
        self.assertEqual(first['id'],again['id'])

    def test_invalid_review_budget_never_reserves_a_run(self):
        material=self.artifact('invalid-budget.txt','合成预算校验')
        adapter=ExternalReview(self.owner.store)
        package=adapter.prepare(self.task['id'],[material['data']['origin']],'检查','test-model','high','合成测试','invalid-budget-package')
        good={'input_tokens':6000000,'output_tokens':75000,'max_context_tokens':None,'packet_bytes':None}
        for budget in ({**good,'input_tokens':True},{**good,'output_tokens':0},{**good,'extra':1},{'input_tokens':6000000}):
            with self.subTest(budget=budget),self.assertRaises(FrameworkError):
                adapter.run(package['id'],sys.executable,'invalid-budget-run',budget=budget)
        self.assertEqual([],self.owner.store.list('external_run'))

    def test_explicit_budget_without_project_channel_cannot_claim_enforcement(self):
        material=self.artifact('no-channel-budget.txt','没有项目派发通道的合成项目')
        adapter=ExternalReview(self.owner.store)
        package=adapter.prepare(self.task['id'],[material['data']['origin']],'检查','test-model','high','合成测试','no-channel-budget-package')
        with self.assertRaises(FrameworkError):
            adapter.run(package['id'],'fake','no-channel-budget-run',runner=lambda *args:0,
                budget={'input_tokens':6000000,'output_tokens':75000,'max_context_tokens':None,'packet_bytes':None})
        self.assertEqual([],self.owner.store.list('external_run'))
