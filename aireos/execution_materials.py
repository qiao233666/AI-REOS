"""汇集原任务和准确证据，交由项目既有审核机制消费；只读不裁定。"""
import hashlib

from . import recall
from .errors import FrameworkError
from .project import typed
from .storage import Transaction, canonical


class ExecutionMaterials:
    def __init__(self,store):
        self.store=store

    def packet(self,segment_id,max_bytes=262144):
        """原件完整提供；大型或非文本证据保留准确定位，绝不静默截断成已读。"""
        from .execution_control import ExecutionControl
        if type(max_bytes) is not int or not 1024<=max_bytes<=4194304:
            raise FrameworkError('材料包正文预算须为1024至4194304字节')
        control=ExecutionControl(self.store)
        with self.store.connection() as connection:
            tx=Transaction(self.store,connection,{'scopes':['.']})
            segment=typed(tx,segment_id,'execution_segment')
            policy=control._policy(tx,segment['data']['task'])
            task=control._base(tx,segment['data']['task'],policy)
            if segment['data']['status']!='submitted':
                raise FrameworkError('须先提交本段准确报告，再构造执行材料包')
            body,_=control._report_body(tx,segment,task,policy)
            original=control._ref(tx,policy['data']['original'],artifact=True)
            raw=self.store.blob(original['data']['blob'])
            try:
                text=raw.decode('utf-8')
            except UnicodeError:
                raise FrameworkError('原任务原件须有完整UTF-8文本，不能用摘要代替') from None
            result={'schema':'aireos.execution-materials/1','task':task,'policy':policy,
                    'segment':segment,'original':{'ref':recall.reference(original),'path':original['data']['origin'],
                       'sha256':hashlib.sha256(raw).hexdigest(),'text':text},'report':body,
                    'evidence':[],'requires_read':[],
                    'meaning':'材料包供执行复核及项目既有审核渠道使用，不代表审核通过或任务完成'}
            if len(canonical(result).encode('utf-8'))>max_bytes:
                raise FrameworkError('原任务、合同及报告不能完整装入材料包；提高max_bytes，不允许静默省略原件')
            seen=set()
            for criterion in body['criteria']:
                for ref in criterion['evidence']:
                    key=(ref['id'],ref['rev'])
                    if key in seen:continue
                    seen.add(key)
                    item=typed(tx,ref['id'],'item')
                    control._ref(tx,recall.reference(item),artifact=True)
                    raw=self.store.blob(item['data']['blob'])
                    entry={'ref':recall.reference(item),'path':item['data']['origin'],
                           'snapshot':str(self.store.home/'objects'/item['data']['blob']),
                           'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
                    try:entry['text']=raw.decode('utf-8')
                    except UnicodeError:pass
                    result['evidence'].append(entry)
                    if 'text' not in entry or len(canonical(result).encode('utf-8'))>max_bytes:
                        entry.pop('text',None)
                        result['requires_read'].append(entry['ref'])
            if len(canonical(result).encode('utf-8'))>max_bytes:
                raise FrameworkError('证据清单超过材料包预算；提高max_bytes，不能省略证据')
            return result
