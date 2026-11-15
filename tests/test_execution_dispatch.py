"""原审核渠道衔接：使用真实材料生成与派发器，提供方调用由替身代替。"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_execution_control as fixtures
from aireos.external_review import ExternalReview
from aireos.storage import canonical


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
