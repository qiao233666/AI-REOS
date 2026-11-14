"""原生会话许可必须在动作前核对，永远只授予一次。"""
import json
from pathlib import Path
import tempfile
import unittest

from aireos.permission_broker import PermissionBroker, guarded_permissions


class PermissionBrokerTests(unittest.TestCase):
    def test_native_permission_is_checked_before_once_reply_and_denial_interrupts(self):
        calls=[]; requests=[{'id':'perm-one','sessionID':'native-session','action':'bash','resources':['python work.py'],
                           'source':{'type':'tool','id':'tool1','messageID':'msg1'},'metadata':{'input':{'command':'python work.py'}}}]
        def api(method,path,body=None):
            calls.append((method,path,body))
            return {'data':requests} if method=='GET' else {}
        def admit(**kw):
            calls.append(('CHECK',kw,None))
            return {'allowed':False,'reasons':['完整二阶段缺少真实联算证据']}
        with tempfile.TemporaryDirectory() as tmp:
            broker=PermissionBroker(api,admit,'formal-session','SEGMENT-one','native-session',Path(tmp)/'permit.jsonl')
            self.assertFalse(broker.poll())
            self.assertEqual('CHECK',calls[1][0])
            self.assertEqual('reject',calls[2][2]['decision'])
            self.assertTrue(calls[-1][1].endswith('/interrupt'))
            self.assertEqual('SEGMENT-one',calls[1][1]['segment_id'])

    def test_allow_is_once_and_repeated_pending_request_never_reissues(self):
        calls=[]
        def api(method,path,body=None):
            calls.append((method,path,body));return {'data':[{'id':'p1','sessionID':'native','action':'read','resources':['a.py'],
                 'source':{'type':'tool','id':'tool1','messageID':'msg1'},'metadata':{}}]} if method=='GET' else {}
        with tempfile.TemporaryDirectory() as tmp:
            broker=PermissionBroker(api,lambda **kw:{'allowed':True},'formal','segment','native',Path(tmp)/'log')
            self.assertTrue(broker.poll());self.assertTrue(broker.poll())
            replies=[x for x in calls if x[0]=='POST']
            self.assertEqual(1,len(replies));self.assertEqual('once',replies[0][2]['decision'])

    def test_missing_session_is_rejected_without_formal_admission(self):
        calls=[]
        def api(method,path,body=None):
            calls.append((method,path,body))
            return {'data':[{'id':'p1','action':'bash','resources':['work']}]} if method=='GET' else {}
        def admit(**kw):
            self.fail('缺会话身份不能计数或授权')
        with tempfile.TemporaryDirectory() as tmp:
            broker=PermissionBroker(api,admit,'formal','segment','native',Path(tmp)/'log',defer_interrupt=True)
            self.assertFalse(broker.poll())
            self.assertEqual('reject',calls[1][2]['decision'])
            self.assertFalse(any(x[1].endswith('/interrupt') for x in calls))

    def test_guarded_permissions_retain_denies_and_convert_allows_to_ask(self):
        p=guarded_permissions({'*':'deny','bash':'allow','write':{'*':'deny','out.py':'allow'},'read':{'*':'allow','../*':'deny'}})
        self.assertEqual('ask',p['bash']);self.assertEqual('ask',p['write']['out.py'])
        self.assertEqual('deny',p['write']['*']);self.assertEqual('deny',p['read']['../*'])
