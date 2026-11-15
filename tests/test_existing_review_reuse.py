"""执行控制直接消费项目既有质量意见，不要求另一套审核格式。"""
import unittest
import test_execution_control as fixtures


class ExistingReviewReuseTests(unittest.TestCase):
    setUp=fixtures.ExecutionControlTests.setUp
    actor=fixtures.ExecutionControlTests.actor
    artifact=fixtures.ExecutionControlTests.artifact
    configure=fixtures.ExecutionControlTests.configure
    begin=fixtures.ExecutionControlTests.begin
    body=fixtures.ExecutionControlTests.body
    report_artifact=fixtures.ExecutionControlTests.report_artifact
    submit_report=fixtures.ExecutionControlTests.submit_report

    def test_project_review_accepts_existing_method_and_natural_language_checks(self):
        self.configure();segment=self.begin();report=self.submit_report(segment)
        opinion=self.reviewer.review(report['id'],'pass','项目既定审核渠道的阶段检查',
            ['已核对原任务和准确文件；实现有进展，物理证据仍缺失'], 'existing-opinion',expected_revision=1)
        self.decide.decide(segment['id'],opinion['id'],'continue','沿项目既有意见继续补缺口','continue')
        self.assertEqual('running',self.owner.store.get(self.task['id'])['data']['status'])
        next_segment=self.execute.begin(self.task['id'],'s1',['A2'],'补充原要求中的物理证据',
            '实际证据或明确未知原因','next')
        self.assertEqual(2,next_segment['data']['index'])

    def test_project_negative_review_uses_existing_hold_without_special_schema(self):
        self.configure();segment=self.begin();report=self.submit_report(segment)
        opinion=self.reviewer.review(report['id'],'fail','项目规定渠道',
            ['报告未能证明原任务要求，保持未完成'], 'failed-opinion',expected_revision=1)
        self.decide.decide(segment['id'],opinion['id'],'hold','按原审核意见修订','hold')
        current=self.host.status(self.task['id'],'s1')
        self.assertTrue(current['handoff_ready']);self.assertFalse(current['completion_ready'])

