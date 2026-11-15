"""原任务材料汇集是只读辅助，不新建审核入口或审核格式。"""
import unittest
import test_execution_control as fixtures
from aireos.execution_materials import ExecutionMaterials
from aireos.cli import COMMANDS, READ_ONLY


class ExecutionMaterialsTests(unittest.TestCase):
    setUp=fixtures.ExecutionControlTests.setUp
    actor=fixtures.ExecutionControlTests.actor
    artifact=fixtures.ExecutionControlTests.artifact
    configure=fixtures.ExecutionControlTests.configure
    begin=fixtures.ExecutionControlTests.begin
    body=fixtures.ExecutionControlTests.body
    report_artifact=fixtures.ExecutionControlTests.report_artifact
    submit_report=fixtures.ExecutionControlTests.submit_report

    def test_materials_keep_full_original_and_all_criteria_without_writing_reviews(self):
        self.configure();segment=self.begin();self.submit_report(segment)
        before=self.host.store.events()
        materials=ExecutionMaterials(self.host.store).packet(segment['id'])
        self.assertEqual('原始验收文字',materials['original']['text'])
        self.assertEqual(2,len(materials['task']['data']['contract']['acceptance']))
        self.assertEqual(2,len(materials['report']['criteria']))
        self.assertEqual([],materials['requires_read'])
        self.assertEqual(before,self.host.store.events())
        self.assertFalse(hasattr(ExecutionMaterials,'record'))

    def test_cli_reuses_review_and_does_not_offer_a_parallel_review_writer(self):
        self.assertIn('review',COMMANDS)
        self.assertIn('execution-materials',READ_ONLY)
        self.assertNotIn('execution-review',COMMANDS)
        self.assertNotIn('execution-review-packet',COMMANDS)
