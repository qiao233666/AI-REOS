"""复用真实治理存储核对原件、逐项独立意见及续行计划。"""
import copy
import json
import unittest

import test_execution_control as fixtures
from aireos.errors import FrameworkError
from aireos.execution_review import ExecutionReview


class ExecutionReviewTests(unittest.TestCase):
    setUp=fixtures.ExecutionControlTests.setUp
    actor=fixtures.ExecutionControlTests.actor
    artifact=fixtures.ExecutionControlTests.artifact
    configure=fixtures.ExecutionControlTests.configure
    begin=fixtures.ExecutionControlTests.begin
    body=fixtures.ExecutionControlTests.body
    report_artifact=fixtures.ExecutionControlTests.report_artifact
    submit_report=fixtures.ExecutionControlTests.submit_report

    def prepare(self):
        self.configure();segment=self.begin();report=self.submit_report(segment)
        packet=ExecutionReview(self.host.store).packet(segment['id'])
        assessment={'schema':'aireos.execution-review/1','segment_id':segment['id'],
          'contract_hash':packet['policy']['data']['contract_hash'],'original':packet['policy']['data']['original'],
          'report':packet['segment']['data']['report'],'decision':'continue','scope_preserved':True,
          'criteria':[{'criterion':c['criterion'],'judgment':'accurate','evidence':c['evidence'],
                       'finding':'原件要求仍保留；本项如实报告剩余缺口'} for c in packet['report']['criteria']],
          'next_step':{'criteria':['A1'],'purpose':'检查实现','expected':'可核查进展'},'reason':'局部进展可核查，后续仍服务原目标'}
        return segment,report,packet,assessment

    def test_packet_contains_full_original_and_all_contract_criteria(self):
        _,_,packet,_=self.prepare()
        self.assertEqual('原始验收文字',packet['original']['text'])
        self.assertEqual(2,len(packet['task']['data']['contract']['acceptance']))
        self.assertEqual(2,len(packet['report']['criteria']))
        self.assertEqual([],packet['requires_read'])

    def test_generic_pass_cannot_authorize_another_segment(self):
        segment,report,_,_=self.prepare()
        opinion=self.reviewer.review(report['id'],'pass','通用检查',['看起来没问题'],'generic',expected_revision=1)
        with self.assertRaises(FrameworkError):
            self.decide.decide(segment['id'],opinion['id'],'continue','直接继续','decide-generic')

    def test_independent_review_requires_every_original_item_and_scope(self):
        segment,_,_,assessment=self.prepare()
        for key in ('omitted','reduced','overclaimed'):
            bad=copy.deepcopy(assessment)
            if key=='omitted':bad['criteria'].pop()
            if key=='reduced':bad['scope_preserved']=False
            if key=='overclaimed':bad['criteria'][1]['judgment']='overclaimed'
            with self.assertRaises(FrameworkError):
                ExecutionReview(self.reviewer.store).record(segment['id'],bad,'review-'+key)

    def test_self_review_rejected_and_approved_next_step_cannot_be_rewritten(self):
        segment,_,_,assessment=self.prepare()
        with self.assertRaises(FrameworkError):
            ExecutionReview(self.worker.store).record(segment['id'],assessment,'self')
        opinion=ExecutionReview(self.reviewer.store).record(segment['id'],assessment,'independent')
        self.decide.decide(segment['id'],opinion['id'],'continue','按独立核准计划','decide')
        with self.assertRaises(FrameworkError):
            self.execute.begin(self.task['id'],'s2',['A1'],'改做无关支线','只生成摘要','rewrite')
        self.assertEqual(2,self.begin('s2','approved')['data']['index'])
        replay=ExecutionReview(self.reviewer.store).record(segment['id'],assessment,'independent')
        self.assertTrue(replay['_replayed'])

