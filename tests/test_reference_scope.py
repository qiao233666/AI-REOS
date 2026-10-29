"""历史快照可供研究，实际输入、提交和采纳仍按准确来源严格约束。"""
import json
import unittest

import test_workflow as workflow
from aireos.errors import ConflictError, FrameworkError, StaleInput
from aireos.workflow import Workflow


class ReferenceScopeTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def material(self, changed=True):
        (self.root/'outputs').mkdir(exist_ok=True)
        path=self.root/'outputs/history.txt'
        path.write_text('historical-v1',encoding='utf-8')
        obj=self.worker.add_artifact('outputs/history.txt','archivedneedle旧实现',self.task['id'],'history-artifact')
        if changed:
            path.write_text('current-v2',encoding='utf-8')
        return path,obj

    def fresh_task(self, key='fresh', inputs=None):
        return self.owner.create_task('archivedneedle新工作','研究旧材料，不把它当现役实现',
            ['新交付准确核查'],[key],key+'-task',inputs=inputs or [])

    def test_searching_old_artifact_does_not_block_start_and_reports_history_warning(self):
        path,old=self.material()
        task=self.fresh_task()
        packet=self.owner.prepare(task['id'],old['id'],'history-context')
        check=self.owner.check_receipt(packet['id'])
        self.assertTrue(check['valid'],check)
        self.assertTrue(check['warnings'])
        self.assertTrue(packet['freshness']['warnings'])
        self.assertEqual(b'historical-v1',self.owner.store.blob(old['data']['blob']))
        self.owner.start_task(task['id'],packet['id'],'history-start')
        self.assertEqual('running',self.owner.store.get(task['id'])['data']['status'])
        self.assertEqual('needs_review',self.owner.status(old['id'])['status'])

    def test_history_file_changed_after_prepare_does_not_become_a_live_input(self):
        path,old=self.material(changed=False)
        task=self.fresh_task()
        packet=self.owner.prepare(task['id'],old['id'],'before-history-update')
        path.write_text('legitimate-v2',encoding='utf-8')
        self.assertTrue(self.owner.check_receipt(packet['id'])['valid'])

    def test_declared_file_inputs_keep_strict_bytes_for_both_usages(self):
        path,old=self.material(changed=False)
        for usage in ('basis','reference'):
            path.write_text('historical-v1',encoding='utf-8')
            task=self.fresh_task(usage,[{'type':'file','path':'outputs/history.txt','usage':usage}])
            packet=self.owner.prepare(task['id'],old['id'],usage+'-context')
            path.write_text('changed-input',encoding='utf-8')
            self.assertFalse(self.owner.check_receipt(packet['id'])['valid'])
            with self.assertRaises(StaleInput):
                self.owner.start_task(task['id'],packet['id'],usage+'-start')

    def test_selected_claim_transitively_binds_actual_support_files(self):
        path,old=self.material(changed=False)
        claim=self.worker.add_item('新推断','基于该文件','inference','合成',[old['id']],self.task['id'],'supported-claim')
        packet=self.worker.prepare(self.task['id'],old['id'],'selected-context',working_items=[claim['id']])
        path.write_text('changed-support',encoding='utf-8')
        self.assertFalse(self.worker.check_receipt(packet['id'])['valid'])
        with self.assertRaises(StaleInput):
            self.worker.submit(self.task['id'],[claim['id']],packet['id'],'selected-submit')

    def test_empty_working_selection_cannot_authorize_an_unbound_submission(self):
        _,old=self.material(changed=False)
        packet=self.worker.prepare(self.task['id'],old['id'],'empty-selection',working_items=[])
        before=len(self.owner.store.events())
        with self.assertRaises(StaleInput):
            self.worker.submit(self.task['id'],[old['id']],packet['id'],'unbound-submit')
        self.assertEqual(before,len(self.owner.store.events()))

    def test_batch_redelivery_uses_new_snapshot_without_old_same_path_deadlock(self):
        folder=self.root/'outputs';folder.mkdir(exist_ok=True)
        path=folder/'result.txt';path.write_text('first-version',encoding='utf-8')
        flow=Workflow(self.worker)
        spec=[{'key':'result','path':'outputs/result.txt','title':'当前实现'}]
        first=flow.deliver(self.task['id'],spec,[],'deliver-first')['data']['result']['items'][0]
        path.write_text('second-version',encoding='utf-8')
        second=flow.deliver(self.task['id'],spec,[],'deliver-second')['data']['result']['items'][0]
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(b'first-version',self.owner.store.blob(self.owner.store.get(first['id'])['data']['blob']))
        review=self.reviewer.review(second['id'],'pass','实际检查新版',['核对second-version'],
                                    'new-review',expected_revision=second['rev'])
        finalized=Workflow(self.owner).finalize(self.task['id'],[{'item_id':second['id'],'review_id':review['id']}],
                                              '新版核查后采纳，旧版只作历史。','finalize-new')
        self.assertEqual('closed',self.owner.store.get(self.task['id'])['data']['status'])
        self.assertEqual('adopted',self.owner.status(second['id'])['status'])

    def test_normal_batch_finalization_survives_recalled_historical_mismatch(self):
        _,old=self.material()
        task=self.fresh_task()
        (self.root/'fresh').mkdir();(self.root/'fresh/result.txt').write_text('new-work',encoding='utf-8')
        delivered=Workflow(self.owner).deliver(task['id'],[{'key':'result','path':'fresh/result.txt','title':'新成果'}],
                                              [],'fresh-delivery',query=old['id'])
        item=delivered['data']['result']['items'][0]
        review=self.reviewer.review(item['id'],'pass','检查新成果',['new-work'],
                                    'fresh-review',expected_revision=item['rev'])
        Workflow(self.owner).finalize(task['id'],[{'item_id':item['id'],'review_id':review['id']}],
                                      '历史参考未阻碍新的独立交付。','fresh-finalize')
        self.assertEqual('adopted',self.owner.status(item['id'])['status'])

    def test_corrupt_historical_blob_still_blocks_readiness(self):
        _,old=self.material()
        (self.root/'.aireos/objects'/old['data']['blob']).write_bytes(b'corrupt')
        task=self.fresh_task()
        packet=self.owner.prepare(task['id'],old['id'],'corrupt-context')
        self.assertFalse(self.owner.check_receipt(packet['id'])['valid'])

    def test_new_correction_to_a_recalled_source_still_requires_prepare_again(self):
        _,old=self.material(changed=False)
        task=self.fresh_task()
        packet=self.owner.prepare(task['id'],old['id'],'before-challenge')
        case=self.worker.challenge(old['id'],'出现新反例',[],'reviewer',1,'核查一次','new-challenge')
        self.reviewer.assess_case(case['id'],True,'明确反例需要检查','new-assess')
        self.assertFalse(self.owner.check_receipt(packet['id'])['valid'])

    def test_legacy_receipt_keeps_its_original_strict_reference_semantics(self):
        _,old=self.material()
        task=self.fresh_task()
        packet=self.owner.prepare(task['id'],old['id'],'new-context')
        self.assertTrue(self.owner.check_receipt(packet['id'])['valid'])
        data=dict(self.owner.store.get(packet['id'])['data'],receipt_version=2)
        data.pop('live_refs',None);data.pop('working_items',None)
        legacy=self.owner.store.create('receipt',data,'synthetic-legacy-receipt')
        self.assertFalse(self.owner.check_receipt(legacy['id'])['valid'])

    def test_target_selection_is_part_of_request_identity(self):
        _,old=self.material(changed=False)
        self.worker.prepare(self.task['id'],'context','selection-request',working_items=[old['id']])
        with self.assertRaises(ConflictError):
            self.worker.prepare(self.task['id'],'context','selection-request',working_items=[])

    def test_empty_selection_cannot_be_used_to_adopt_a_submitted_item(self):
        _,item=self.material(changed=False)
        packet=self.worker.prepare(self.task['id'],item['id'],'deliver-context')
        self.worker.submit(self.task['id'],[item['id']],packet['id'],'submit-item')
        review=self.reviewer.review(item['id'],'pass','核对文件',['字节一致'],'review-item',expected_revision=1)
        empty=self.owner.prepare(self.task['id'],item['id'],'empty-adopt-context',working_items=[])
        with self.assertRaises(StaleInput):
            self.owner.adopt(item['id'],review['id'],empty['id'],'unbound-adopt')

    def test_partial_adoption_does_not_require_a_rejected_sibling_file_to_stay_current(self):
        folder=self.root/'outputs';folder.mkdir(exist_ok=True)
        (folder/'good.txt').write_text('good',encoding='utf-8');(folder/'bad.txt').write_text('bad-v1',encoding='utf-8')
        delivered=Workflow(self.worker).deliver(self.task['id'],[
            {'key':'good','path':'outputs/good.txt','title':'可采纳部分'},
            {'key':'bad','path':'outputs/bad.txt','title':'不通过部分'}],[],'two-delivery')['data']['result']['items']
        good=delivered[0];bad=delivered[1]
        review=self.reviewer.review(good['id'],'pass','核对可用部分',['good'],'good-review',expected_revision=1)
        self.reviewer.review(bad['id'],'fail','反例验证',['bad'],'bad-review',expected_revision=1)
        (folder/'bad.txt').write_text('bad-v2',encoding='utf-8')
        Workflow(self.owner).finalize(self.task['id'],[{'item_id':good['id'],'review_id':review['id']}],
                                      '仅采纳明确通过部分。','partial-finalize')
        self.assertEqual('adopted',self.owner.status(good['id'])['status'])
        self.assertNotEqual('adopted',self.owner.status(bad['id'])['status'])

    def test_live_support_chain_limit_rejects_without_truncating_or_recording_a_receipt(self):
        from aireos.recall import reference
        def build(tx):
            previous=None
            for index in range(201):
                previous=tx.create('item',dict(type='claim',title='node',text=str(index),classification='unknown',
                    scope='合成',task=self.task['id'],producer='worker',supports=[reference(previous)] if previous else []))
            return previous
        leaf=self.owner.store.mutate('synthetic.chain',{},'build-chain','write',build,corpus=True)
        count=len(self.owner.store.list('receipt'))
        with self.assertRaises(FrameworkError):
            self.worker.prepare(self.task['id'],'node','too-long-prepare',working_items=[leaf['id']])
        self.assertEqual(count,len(self.owner.store.list('receipt')))

    def test_old_prepare_request_replays_old_receipt_without_reinterpreting_it(self):
        _,old=self.material()
        task=self.fresh_task()
        current=self.owner.prepare(task['id'],old['id'],'current-for-old')
        data=dict(self.owner.store.get(current['id'])['data'],receipt_version=2)
        data.pop('live_refs');data.pop('working_items')
        payload=dict(task=task['id'],query=old['id'],limit=12,max_chars=10000,view='brief')
        legacy=self.owner.store.mutate('context.prepare',payload,'old-request','write',lambda tx:tx.create('receipt',data))
        replay=self.owner.prepare(task['id'],old['id'],'old-request')
        self.assertEqual(legacy['id'],replay['id'])
        self.assertEqual(2,replay['data']['receipt_version'])
        self.assertFalse(self.owner.check_receipt(replay['id'])['valid'])

    def test_small_packet_stays_bounded_and_full_source_directory_is_recoverable(self):
        item=self.worker.add_item('needle','needle '+'padding '*300,'unknown','合成',[],self.task['id'],'small-item')
        packet=self.worker.prepare(self.task['id'],'needle','small-context',max_chars=1000)
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),1000)
        self.assertIn(item['id'],[entry['id'] for entry in packet['context']])
        self.assertTrue(self.owner.store.get(packet['id'])['data']['live_refs'])

    def test_small_history_packet_reserves_space_for_reading_measurement(self):
        from aireos.history import HistoryArchive
        (self.root/'history.yaml').write_text('- id: E-001\n  statement: needle '+('padding '*100)+'\n',encoding='utf-8')
        HistoryArchive(self.owner.store).configure([{'path':'history.yaml','format':'yaml-records'}],'history-source')
        packet=self.worker.prepare(self.task['id'],'needle','small-history',max_chars=1000)
        self.assertIn('reading',packet)
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),1000)

    def test_live_reference_directory_does_not_blow_up_display_packet(self):
        (self.root/'outputs').mkdir(exist_ok=True)
        ids=[]
        for index in range(35):
            path=self.root/'outputs'/('material'+str(index)+'.txt');path.write_text(str(index),encoding='utf-8')
            ids.append(self.worker.add_artifact(path.relative_to(self.root).as_posix(),'材料'+str(index),
                self.task['id'],'many-'+str(index))['id'])
        packet=self.worker.prepare(self.task['id'],'no-word-hit','many-context',max_chars=2500,working_items=ids)
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),2500)
        self.assertEqual(35,len(self.owner.store.get(packet['id'])['data']['live_refs']))


if __name__=='__main__':
    unittest.main()
