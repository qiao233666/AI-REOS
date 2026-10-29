"""任务接续只返回一张有界卡，不能绕过版本、权限或正式成果要求。"""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_workflow as workflow
from aireos.errors import ConflictError, FrameworkError, PermissionDenied
from aireos.history import session_context
from aireos.project import Project
from aireos.storage import canonical


class TaskCheckpointTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def save(self, revision=0, request='checkpoint', **changes):
        self.assertTrue(callable(getattr(self.worker, 'checkpoint', None)), '尚无任务接续卡写入口')
        values = dict(task_id=self.task['id'], progress='已检查输入格式',
                      next_step='补异常样例并运行检查', pending='参数缺失的处理待验证',
                      references=[], expected_revision=revision, request_id=request)
        values.update(changes)
        return self.worker.checkpoint(**values)

    def test_fresh_process_recovers_pinned_materials_and_actual_next_step(self):
        (self.root/'outputs').mkdir()
        material = self.root/'outputs/check.txt'
        material.write_text('checked-v1', encoding='utf-8')
        saved = self.save(references=[{'type':'file','path':'outputs/check.txt'}])
        card = saved['checkpoint']
        self.assertLessEqual(len(canonical(card)), 1500)
        material.write_text('changed-after-checkpoint', encoding='utf-8')
        path = self.root/'checkpoint-read.json'
        path.write_text(json.dumps({'task_id':self.task['id']}), encoding='utf-8')
        result = subprocess.run([sys.executable,'-B','-m','aireos','--project',str(self.root),
                                 'task-checkpoint','--input',str(path)],
                                capture_output=True,encoding='utf-8',timeout=30,
                                env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONUTF8':'1'})
        self.assertEqual(0, result.returncode, result.stderr)
        recovered = json.loads(result.stdout)
        self.assertEqual('补异常样例并运行检查', recovered['checkpoint']['next_step'])
        self.assertEqual('changed', recovered['reference_state']['state'])
        self.assertEqual(hashlib.sha256(b'checked-v1').hexdigest(), card['references'][0]['sha256'])

    def test_valid_checkpoint_pins_identity_without_copying_working_material(self):
        (self.root/'outputs').mkdir()
        (self.root/'outputs/working.txt').write_text('mutable working material',encoding='utf-8')
        before={path.name for path in (self.root/'.aireos/objects').iterdir()}
        self.save(references=['outputs/working.txt'])
        self.assertEqual(before,{path.name for path in (self.root/'.aireos/objects').iterdir()})

    def test_many_updates_keep_one_current_card_and_preserve_old_task_version(self):
        first = self.save(progress='obsolete-checkpoint')
        task_revision = self.owner.store.get(self.task['id'])['rev']
        for index in range(1, 31):
            self.save(index, 'checkpoint-'+str(index), progress='current-'+str(index))
        packet = session_context(self.owner.store)
        self.assertEqual('current-30', packet['tasks'][0]['checkpoint']['progress'])
        self.assertNotIn('obsolete-checkpoint', json.dumps(packet))
        self.assertEqual(1, len(self.owner.store.list('task')))
        old = self.owner.store.get(self.task['id'], task_revision)
        self.assertEqual('obsolete-checkpoint', old['data']['checkpoint']['progress'])
        self.assertEqual(1, first['checkpoint']['revision'])

    def test_stale_writer_rejected_and_same_request_is_idempotent(self):
        first = self.save()
        event_count = len(self.owner.store.events())
        replay = self.save()
        self.assertEqual(first['checkpoint'], replay['checkpoint'])
        self.assertEqual(event_count, len(self.owner.store.events()))
        with self.assertRaises(ConflictError):
            self.save(0, 'late-write', progress='cannot-overwrite')
        self.assertEqual(event_count, len(self.owner.store.events()))
        self.assertEqual(first['checkpoint'], Project(self.root).task_checkpoint(self.task['id'])['checkpoint'])

    def test_unchanged_card_does_not_create_another_task_version(self):
        first = self.save()
        revision = self.owner.store.get(self.task['id'])['rev']
        repeated = self.save(1, 'same-content')
        self.assertEqual(first['checkpoint'], repeated['checkpoint'])
        self.assertEqual(revision, self.owner.store.get(self.task['id'])['rev'])

    def test_cli_write_and_query_recover_only_selected_latest_card(self):
        key = self.root/'.aireos/owner.key'
        data = dict(task_id=self.task['id'], progress='CLI写入的真实进展', next_step='从新进程继续', expected_revision=0)
        path = self.root/'write-checkpoint.json'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        environment = {**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONUTF8':'1'}
        command = [sys.executable,'-B','-m','aireos','--project',str(self.root)]
        result = subprocess.run(command+['--credential-file',str(key),'checkpoint','--input',str(path),
                                          '--request-id','cli-checkpoint'], env=environment,
                                capture_output=True,encoding='utf-8',timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual(1,json.loads(result.stdout)['checkpoint']['revision'])
        path.write_text(json.dumps({'task_id':self.task['id'],'query':'未命中的精确合成问题'}),encoding='utf-8')
        result = subprocess.run(command+['session-context','--input',str(path)],env=environment,
                                capture_output=True,encoding='utf-8',timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        packet=json.loads(result.stdout)
        self.assertEqual('CLI写入的真实进展',packet['tasks'][0]['checkpoint']['progress'])
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),10000)

    def test_two_process_writers_cannot_both_replace_the_same_card_version(self):
        self.save()
        environment={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONUTF8':'1'}
        processes=[]
        for number in (1,2):
            path=self.root/('race-'+str(number)+'.json')
            path.write_text(json.dumps(dict(task_id=self.task['id'],expected_revision=1,
                progress='writer-'+str(number),next_step='continue')),encoding='utf-8')
            processes.append(subprocess.Popen([sys.executable,'-B','-m','aireos','--project',str(self.root),
                '--credential-file',str(self.root/'.aireos/owner.key'),'checkpoint','--input',str(path),
                '--request-id','race-'+str(number)],env=environment,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,text=True,encoding='utf-8'))
        results=[]
        for process in processes:
            output,error=process.communicate(timeout=30)
            results.append((process.returncode,output,error))
        self.assertEqual([0,2],sorted(result[0] for result in results),str(results))
        card=Project(self.root).task_checkpoint(self.task['id'])['checkpoint']
        self.assertEqual(2,card['revision'])
        self.assertIn(card['progress'],['writer-1','writer-2'])

    def test_an_authorized_successor_can_update_but_reviewer_cannot(self):
        self.save()
        grant = self.owner.store.grant('successor', ['execute'], ['outputs'], 'grant-successor')
        successor = Project(self.root, Path(grant['credential_file']).read_text().strip())
        successor.checkpoint(self.task['id'], '已接续已有材料', '检查遗漏项', 1, 'successor-card')
        self.assertEqual('successor', Project(self.root).task_checkpoint(self.task['id'])['checkpoint']['author'])
        with self.assertRaises(PermissionDenied):
            self.reviewer.checkpoint(self.task['id'], '越权', '不能写入', 2, 'reviewer-write')
        grant = self.owner.store.grant('outside', ['execute'], ['elsewhere'], 'grant-outside')
        outside = Project(self.root, Path(grant['credential_file']).read_text().strip())
        with self.assertRaises(PermissionDenied):
            outside.checkpoint(self.task['id'], '越界', '不能写入', 2, 'outside-write')

    def test_overlong_or_invalid_card_rolls_back_without_partial_records(self):
        self.save()
        revision = self.owner.store.get(self.task['id'])['rev']
        events = len(self.owner.store.events())
        invalid = [dict(progress='字'*1500), dict(next_step=''), dict(expected_revision=True),
                   dict(expected_revision=-1), dict(references=['x']*9), dict(pending=[])]
        for index, values in enumerate(invalid):
            with self.subTest(values=list(values)):
                with self.assertRaises(FrameworkError):
                    self.save(1, 'invalid-'+str(index), **values)
                self.assertEqual(revision, self.owner.store.get(self.task['id'])['rev'])
                self.assertEqual(events, len(self.owner.store.events()))

    def test_rejected_card_does_not_leave_unreferenced_material_snapshot(self):
        (self.root/'outputs').mkdir()
        (self.root/'outputs/new-material.txt').write_text('not-saved-if-rejected',encoding='utf-8')
        before={path.name for path in (self.root/'.aireos/objects').iterdir()}
        with self.assertRaises(FrameworkError):
            self.save(progress='字'*1400,references=['outputs/new-material.txt'])
        self.assertEqual(before,{path.name for path in (self.root/'.aireos/objects').iterdir()})

    def test_saving_progress_does_not_change_contract_or_validate_a_claim(self):
        packet = self.worker.prepare(self.task['id'], '不存在的合成关键词', 'before-checkpoint')
        before = self.owner.store.get(self.task['id'])['data']['contract']
        epoch = self.owner.store.epoch()
        self.save()
        self.assertEqual(epoch, self.owner.store.epoch())
        self.assertTrue(self.worker.check_receipt(packet['id'])['valid'])
        self.assertEqual(before, self.owner.store.get(self.task['id'])['data']['contract'])
        self.assertEqual([], self.owner.store.list('item'))
        self.assertEqual([], self.owner.store.list('review'))

    def test_checkpoint_progress_is_not_indexed_as_formal_knowledge(self):
        from aireos.semantic import MemoryService
        self.save(progress='unreviewedcheckpointuniqueneedle')
        result=MemoryService(self.owner.store).query('unreviewedcheckpointuniqueneedle')
        self.assertEqual([],result['matches'])

    def test_item_reference_reports_changes_to_its_actual_file(self):
        (self.root/'outputs').mkdir()
        path=self.root/'outputs/reference.txt'
        path.write_text('original',encoding='utf-8')
        artifact=self.worker.add_artifact('outputs/reference.txt','引用文件',self.task['id'],'artifact-ref')
        self.save(references=[{'type':'item','id':artifact['id'],'rev':artifact['rev']}])
        path.write_text('changed',encoding='utf-8')
        self.assertEqual('changed',Project(self.root).task_checkpoint(self.task['id'])['reference_state']['state'])

    def test_multiple_tasks_show_only_selected_card_within_complete_packet_budget(self):
        self.save(progress='first-task-card')
        other = self.owner.create_task('另一个任务', '另一个目标', ['明确限制'], ['other'], 'other-task')
        self.owner.checkpoint(other['id'], 'other-task-card', '继续验证', 0, 'other-card')
        packet = session_context(self.owner.store)
        self.assertFalse(any('checkpoint' in entry for entry in packet['tasks']))
        selected = session_context(self.owner.store, task_id=other['id'])
        cards = [entry['checkpoint'] for entry in selected['tasks'] if 'checkpoint' in entry]
        self.assertEqual(['other-task-card'], [entry['progress'] for entry in cards])
        self.assertLessEqual(len(json.dumps(selected, ensure_ascii=False)), 10000)

    def test_small_packet_omits_whole_card_with_explicit_locator(self):
        self.save(progress='p'*900, pending='b'*150, next_step='n'*150)
        packet = session_context(self.owner.store, task_id=self.task['id'], max_chars=2000)
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False)), 2000)
        entry = packet['tasks'][0]
        if 'checkpoint' in entry:
            self.assertEqual('p'*900, entry['checkpoint']['progress'])
        else:
            self.assertTrue(entry['checkpoint_omitted'])
            self.assertTrue(packet['truncated'])

    def test_closed_and_stopped_tasks_leave_navigation_without_losing_card(self):
        self.save()
        self.owner.stop_task(self.task['id'], '停止本轮，保留未决。', 'stop')
        self.assertEqual([], session_context(self.owner.store)['tasks'])
        result = Project(self.root).task_checkpoint(self.task['id'])
        self.assertFalse(result['active'])
        self.assertEqual('已检查输入格式', result['checkpoint']['progress'])
        with self.assertRaises(FrameworkError):
            self.save(1, 'after-stop')

    def test_read_without_card_does_not_fabricate_progress_and_closed_task_preserves_it(self):
        self.assertTrue(callable(getattr(self.owner, 'task_checkpoint', None)), '尚无任务接续卡读入口')
        self.assertIsNone(Project(self.root).task_checkpoint(self.task['id'])['checkpoint'])
        self.save()
        item = self.worker.add_item('未知项', '还不足以判断', 'unknown', '合成样例', [], self.task['id'], 'item')
        packet = self.worker.prepare(self.task['id'], '未知项', 'submit-context')
        self.worker.submit(self.task['id'], [item['id']], packet['id'], 'submit')
        self.reviewer.review(item['id'], 'unknown', '检查材料', ['证据不足'], 'review', expected_revision=1)
        self.owner.close_task(self.task['id'], '本轮结束，保留未知。', 'close')
        self.assertEqual([], session_context(self.owner.store)['tasks'])
        self.assertFalse(Project(self.root).task_checkpoint(self.task['id'])['active'])
        with self.assertRaises(FrameworkError):
            self.save(1, 'after-close')


if __name__ == '__main__':
    unittest.main()
