"""结构化待决与事件增量只协调工作，不代替权限、审核及采纳。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_workflow as workflow
from aireos import cli
from aireos.errors import ConflictError, FrameworkError, PermissionDenied
from aireos.history import session_context
from aireos.project import Project
from aireos.storage import Store


class DecisionRequestTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    deliver = workflow.WorkflowTests.deliver
    pass_review = workflow.WorkflowTests.pass_review

    def ask(self, key='geometry', question='请选择边界条件', request='ask', **extra):
        return self.worker.request_decision(self.task['id'], key, question, request, **extra)

    def principal(self, actor, caps, scopes):
        grant = self.owner.store.grant(actor, caps, scopes, 'grant-'+actor)
        return Project(self.root, Path(grant['credential_file']).read_text().strip())

    def test_dedup_replay_conflict_and_restart(self):
        first = self.ask(context='现有合同范围内的选择')
        self.assertEqual('decision_request', first['kind'])
        events = len(self.owner.store.events())
        self.assertTrue(self.ask(context='现有合同范围内的选择')['_replayed'])
        self.assertEqual(events, len(self.owner.store.events()))
        same = self.ask(request='another-request', context='现有合同范围内的选择')
        self.assertEqual(first['id'], same['id'])
        self.assertEqual(1, len(self.owner.store.list('decision_request')))
        with self.assertRaises(ConflictError):
            self.ask(question='不同的问题', request='conflicting-content')
        with self.assertRaises(ConflictError):
            self.ask(question='不同的问题')
        packet = Project(self.root).task_attention(self.task['id'])
        self.assertEqual(1, packet['pending_count'])
        self.assertEqual(first['id'], packet['requests'][0]['id'])
        self.assertEqual(1, packet['requests'][0]['rev'])

    def test_capabilities_scopes_and_anonymous_mutations(self):
        for actor, caps in [('planner',['plan']), ('review-only',['review']), ('decider',['decide'])]:
            principal = self.principal(actor, caps, ['outputs'])
            principal.request_decision(self.task['id'], actor, '范围内问题', 'ask-'+actor)
        denied = [self.principal('tools-only',['tools'],['.']),
                  self.principal('outside',['execute'],['elsewhere']), Project(self.root)]
        for index, principal in enumerate(denied):
            with self.assertRaises(PermissionDenied):
                principal.request_decision(self.task['id'], 'denied', '问题', 'denied-'+str(index))
        decision = self.ask()
        for index, principal in enumerate([self.worker, self.reviewer, denied[1], Project(self.root)]):
            with self.assertRaises(PermissionDenied):
                principal.resolve_decision(decision['id'],1,'答复','resolve-denied-'+str(index))
        outside_decider = self.principal('outside-decider',['decide'],['elsewhere'])
        with self.assertRaises(PermissionDenied):
            outside_decider.resolve_decision(decision['id'],1,'答复','outside-answer')

    def test_answer_cas_history_replay_and_no_reopen(self):
        first = self.ask()
        answer = self.owner.resolve_decision(first['id'],1,'采用原合同范围内方案','answer')
        self.assertEqual(2, answer['rev'])
        self.assertEqual('answered', answer['data']['status'])
        self.assertEqual('pending', self.owner.store.get(first['id'],1)['data']['status'])
        count = len(self.owner.store.events())
        self.assertTrue(self.owner.resolve_decision(first['id'],1,'采用原合同范围内方案','answer')['_replayed'])
        self.assertEqual(count, len(self.owner.store.events()))
        with self.assertRaises(ConflictError):
            self.owner.resolve_decision(first['id'],1,'迟到答复','late')
        with self.assertRaises(ConflictError):
            self.owner.resolve_decision(first['id'],2,'另一个答复','overwrite')
        self.assertEqual(first['id'], self.ask(request='retry-key')['id'])
        self.assertEqual(0, self.owner.task_attention(self.task['id'])['pending_count'])

    def test_stop_preserves_pending_and_answer_does_not_restart(self):
        first = self.ask()
        self.worker.checkpoint(self.task['id'],'已做范围内工作','等待选择',0,'checkpoint')
        self.assertEqual(1,self.owner.task_attention(self.task['id'])['pending_count'])
        self.owner.stop_task(self.task['id'],'暂时停止，保留未决','stop')
        self.assertEqual('stopped',Project(self.root).task_attention(self.task['id'])['task_status'])
        self.owner.resolve_decision(first['id'],1,'终止该问题的原因','withdraw',resolution='withdrawn')
        self.assertEqual('withdrawn',self.owner.store.get(first['id'])['data']['status'])
        self.assertEqual('stopped',self.owner.store.get(self.task['id'])['data']['status'])
        with self.assertRaises(FrameworkError):
            self.ask(key='after-stop',request='after-stop')

    def test_pending_blocks_preflight_and_close_without_blocking_work(self):
        first = self.ask()
        item = self.worker.add_item('仍能工作','范围内进度','unknown','合成',[],self.task['id'],'item')
        self.deliver([item])
        self.pass_review(item)
        diagnostic = self.owner.check_finalize(self.task['id'],[])
        self.assertIn('decision_pending',[issue['code'] for issue in diagnostic['issues']])
        with self.assertRaisesRegex(FrameworkError,'待决'):
            self.owner.close_task(self.task['id'],'不能过早结案','close-pending')
        self.owner.resolve_decision(first['id'],1,'问题已处理','answer')
        self.assertTrue(self.owner.check_finalize(self.task['id'],[])['ready'])
        self.owner.close_task(self.task['id'],'已如实结案','close')

    def test_input_bounds_pending_cap_and_rollback(self):
        invalid = [dict(key=''),dict(key='x'*101),dict(question='x'*2001),
                   dict(context='x'*4001),dict(context=[]),dict(question=True)]
        for index, values in enumerate(invalid):
            parameters=dict(task_id=self.task['id'],key='test',question='问题',request_id='bad-'+str(index))
            parameters.update(values)
            with self.assertRaises(FrameworkError):
                self.worker.request_decision(**parameters)
        self.assertEqual([],self.owner.store.list('decision_request'))
        first = self.ask()
        for index in range(1,8):
            self.ask(key='key-'+str(index),request='request-'+str(index))
        with self.assertRaises(FrameworkError):
            self.ask(key='ninth',request='ninth')
        self.assertEqual(first['id'],self.ask(request='duplicate-at-cap')['id'])
        for index, change in enumerate([dict(expected_revision=True),dict(expected_revision=0),
                                      dict(answer=''),dict(answer='x'*4001),dict(resolution='other')]):
            params=dict(decision_id=first['id'],expected_revision=1,answer='答复',request_id='invalid-answer-'+str(index))
            params.update(change)
            with self.assertRaises(FrameworkError):
                self.owner.resolve_decision(**params)
        self.assertEqual(1,self.owner.store.get(first['id'])['rev'])

    def test_malformed_public_parameters_are_controlled_and_transaction_free(self):
        first=self.ask()
        events=self.owner.store.events()
        for bad in [[],{},True,17,None]:
            for name in ['task_id','key','question','context']:
                values=dict(task_id=self.task['id'],key='bad',question='问题',context='',request_id='bad-type')
                values[name]=bad
                with self.subTest(action='request',name=name,bad=bad):
                    with self.assertRaises(FrameworkError):
                        self.worker.request_decision(**values)
            for name in ['decision_id','answer','resolution']:
                values=dict(decision_id=first['id'],expected_revision=1,answer='答复',resolution='answered',request_id='bad-type-answer')
                values[name]=bad
                with self.subTest(action='answer',name=name,bad=bad):
                    with self.assertRaises(FrameworkError):
                        self.owner.resolve_decision(**values)
            with self.assertRaises(FrameworkError):
                Project(self.root).task_attention(bad)
        self.assertEqual(events,self.owner.store.events())

    def test_attention_budget_pagination_and_session_navigation(self):
        identifiers = [self.ask(key=str(index),question='问'*600,request='ask-'+str(index))['id'] for index in range(5)]
        offset, seen, snapshot = 0, [], None
        while True:
            packet = Project(self.root).task_attention(self.task['id'],max_chars=2200,offset=offset,expected_snapshot=snapshot)
            snapshot = packet['snapshot']
            self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),2200)
            self.assertEqual(5,packet['pending_count'])
            seen.extend(row['id'] for row in packet['requests'])
            if packet['next_offset'] is None:
                break
            self.assertGreater(packet['next_offset'],offset)
            offset = packet['next_offset']
        self.assertEqual(identifiers,seen)
        session = session_context(self.owner.store,max_chars=2000,task_id=self.task['id'])
        self.assertLessEqual(len(json.dumps(session,ensure_ascii=False)),2000)
        self.assertEqual(5,session['tasks'][0]['pending_decisions'])
        self.assertIn('task-attention',session['tasks'][0]['attention_read'])
        for values in [dict(offset=True),dict(offset=-1),dict(offset=6),dict(max_chars=True),dict(max_chars=999)]:
            with self.assertRaises(FrameworkError):
                Project(self.root).task_attention(self.task['id'],**values)
        self.ask(key='large',question='问'*2000,context='文'*4000,request='large')
        current = Project(self.root).task_attention(self.task['id'])
        with self.assertRaisesRegex(FrameworkError,'预算'):
            Project(self.root).task_attention(self.task['id'],max_chars=1000,offset=5,expected_snapshot=current['snapshot'])

    def test_changed_pending_set_refuses_stale_offset_instead_of_skipping(self):
        identifiers = [self.ask(key=str(index),question='问'*600,request='ask-'+str(index))['id'] for index in range(5)]
        first = Project(self.root).task_attention(self.task['id'],max_chars=2200)
        offset = first['next_offset']
        self.assertIsNotNone(offset)
        with self.assertRaises(FrameworkError):
            Project(self.root).task_attention(self.task['id'],max_chars=2200,offset=offset)
        self.owner.resolve_decision(identifiers[0],1,'并发处理','answer-first')
        with self.assertRaises(ConflictError):
            Project(self.root).task_attention(self.task['id'],max_chars=2200,offset=offset,expected_snapshot=first['snapshot'])
        refreshed = Project(self.root).task_attention(self.task['id'])
        self.assertEqual(identifiers[1:],[request['id'] for request in refreshed['requests']])
        self.assertNotEqual(first['snapshot'],refreshed['snapshot'])

    def test_requests_and_answers_preserve_contract_epoch_reviews_and_decisions(self):
        task = self.owner.store.get(self.task['id'])
        epoch = self.owner.store.epoch()
        first = self.ask()
        self.owner.resolve_decision(first['id'],1,'答复不授予权限','answer')
        events = len(self.owner.store.events())
        self.assertTrue(self.ask()['_replayed'])
        self.assertTrue(self.owner.resolve_decision(first['id'],1,'答复不授予权限','answer')['_replayed'])
        self.assertEqual(events,len(self.owner.store.events()))
        self.assertEqual(task,self.owner.store.get(self.task['id']))
        self.assertEqual(epoch,self.owner.store.epoch())
        self.assertEqual([],self.owner.store.list('review'))
        self.assertEqual([],self.owner.store.list('decision'))

    def test_cli_fresh_process_and_read_request_id_rejection(self):
        environment={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONUTF8':'1'}
        base=[sys.executable,'-B','-m','aireos','--project',str(self.root)]
        def invoke(command,params,request=None):
            path=self.root/'cli-input.json'
            path.write_text(json.dumps(params,ensure_ascii=False),encoding='utf-8')
            args=base+(['--credential-file',str(self.root/'.aireos/owner.key')] if command in {'request-decision','resolve-decision'} else [])
            args += [command,'--input',str(path)]
            if request:
                args += ['--request-id',request]
            result=subprocess.run(args,capture_output=True,encoding='utf-8',env=environment,timeout=30)
            return result
        asked=invoke('request-decision',dict(task_id=self.task['id'],key='cli-real',question='合成问题'),'cli-real-ask')
        self.assertEqual(0,asked.returncode,asked.stderr)
        decision=json.loads(asked.stdout)
        packet=invoke('task-attention',dict(task_id=self.task['id']))
        self.assertEqual(0,packet.returncode,packet.stderr)
        self.assertEqual(1,json.loads(packet.stdout)['pending_count'])
        answer=invoke('resolve-decision',dict(decision_id=decision['id'],expected_revision=1,answer='答复'),'cli-real-answer')
        self.assertEqual(0,answer.returncode,answer.stderr)
        self.assertEqual('answered',json.loads(answer.stdout)['data']['status'])
        feed=invoke('event-feed',dict(limit=2))
        self.assertEqual(0,feed.returncode,feed.stderr)
        self.assertEqual(2,len(json.loads(feed.stdout)['events']))
        for command,params in [('event-feed',{}),('task-attention',dict(task_id=self.task['id']))]:
            denied=invoke(command,params,'forbidden')
            self.assertEqual(2,denied.returncode)

    def test_cli_whitelist_mutations_and_credential_free_reads(self):
        def invoke(command,params,request=None,token=True):
            args=argparse.Namespace(command=command,project=self.root,request_id=request,
                credential_file=self.root/'.aireos/owner.key' if token else None)
            return cli._dispatch(args,params)
        first=invoke('request-decision',dict(task_id=self.task['id'],key='cli',question='选择'),'cli-ask')
        self.assertEqual(1,invoke('task-attention',dict(task_id=self.task['id']),token=False)['pending_count'])
        invoke('resolve-decision',dict(decision_id=first['id'],expected_revision=1,answer='答复'),'cli-answer')
        self.assertEqual(0,invoke('task-attention',dict(task_id=self.task['id']),token=False)['pending_count'])
        with self.assertRaises(ValueError):
            invoke('task-attention',dict(task_id=self.task['id']),'forbidden',token=False)
        for command in ['request-decision','resolve-decision','task-attention','event-feed']:
            self.assertIn(command,cli.COMMANDS)


class EventFeedTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def test_paging_complete_empty_readonly_and_old_events_compatible(self):
        original = self.owner.store.events()
        self.assertIn('input',original[0]['payload'])
        cursor, sequences = 0, []
        while True:
            result = Store(self.root).event_feed(cursor,limit=2,max_chars=1000)
            self.assertEqual(self.owner.store.project_id(),result['project_id'])
            self.assertEqual(original[-1]['seq'],result['high_watermark'])
            self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)),1000)
            for event in result['events']:
                self.assertEqual({'seq','action','actor','created','changes','changed_count','changes_truncated'},set(event))
                source = next(row for row in original if row['seq']==event['seq'])
                self.assertEqual(source['payload']['changes'][:len(event['changes'])],event['changes'])
                self.assertEqual(len(source['payload']['changes']),event['changed_count'])
            sequences.extend(event['seq'] for event in result['events'])
            if not result['has_more']:
                break
            self.assertGreater(result['next_after_seq'],cursor)
            cursor=result['next_after_seq']
        self.assertEqual([event['seq'] for event in original],sequences)
        empty = Store(self.root).event_feed(result['next_after_seq'])
        self.assertEqual([],empty['events'])
        self.assertEqual(result['next_after_seq'],empty['next_after_seq'])
        self.assertFalse(empty['has_more'])
        self.assertEqual(original,self.owner.store.events())

    def test_large_event_changes_are_explicitly_truncated_without_payload(self):
        before = self.owner.store.events()[-1]['seq']
        def many(tx):
            for number in range(50):
                tx.create('synthetic',dict(secret='不应出现在摘要'+str(number)))
            return dict(done=True)
        self.owner.store.mutate('synthetic.batch',dict(secret='禁止泄露的输入'),'many','write',many)
        result = Store(self.root).event_feed(before,max_chars=1000)
        self.assertEqual(1,len(result['events']))
        event = result['events'][0]
        self.assertTrue(event['changes_truncated'])
        self.assertEqual(50,event['changed_count'])
        self.assertLess(len(event['changes']),50)
        self.assertNotIn('不应出现在摘要',json.dumps(result,ensure_ascii=False))
        self.assertNotIn('禁止泄露',json.dumps(result,ensure_ascii=False))
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)),1000)
        self.assertFalse(result['has_more'])
        self.assertEqual(event['seq'],result['next_after_seq'])

    def test_validation_and_cli_readonly(self):
        invalid=[dict(after_seq=True),dict(after_seq=-1),dict(after_seq=10**10),dict(limit=True),
                 dict(limit=0),dict(limit=101),dict(max_chars=True),dict(max_chars=999),dict(max_chars=50001)]
        before=self.owner.store.events()
        for values in invalid:
            with self.assertRaises(FrameworkError):
                Store(self.root).event_feed(**values)
        args=argparse.Namespace(command='event-feed',project=self.root,request_id=None,credential_file=None)
        self.assertEqual(before[-1]['seq'],cli._dispatch(args,{})['high_watermark'])
        args.request_id='invalid-read'
        with self.assertRaises(ValueError):
            cli._dispatch(args,{})
        self.assertEqual(before,self.owner.store.events())

    def test_snapshot_high_watermark_defers_concurrent_append_without_losing_it(self):
        reader=Store(self.root)
        high=self.owner.store.events()[-1]['seq']
        original_project_id=reader.project_id
        def append_after_snapshot(connection=None):
            self.owner.store.create('synthetic',dict(text='并发事件'),'concurrent-event')
            return original_project_id(connection)
        reader.project_id=append_after_snapshot
        result=reader.event_feed()
        self.assertEqual(high,result['high_watermark'])
        self.assertEqual(high,result['next_after_seq'])
        self.assertFalse(result['has_more'])
        later=Store(self.root).event_feed(result['next_after_seq'])
        self.assertEqual(high+1,later['high_watermark'])
        self.assertEqual([high+1],[event['seq'] for event in later['events']])


if __name__ == '__main__':
    unittest.main()
