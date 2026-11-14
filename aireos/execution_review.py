"""原任务驱动的独立续行核查；审核事实仍由既有Project.review保存。"""
import hashlib
import json

from . import recall
from .errors import FrameworkError
from .project import Project, typed
from .storage import Transaction, canonical


SCHEMA='aireos.execution-review/1'
INSTRUCTIONS=(
    '先读原任务原件及正式合同，再读实际证据；执行者摘要仅作导航。',
    '逐项核对全部原验收要求、报告状态和证据类别；合成、桩与真实求值不得互换。',
    '核对变量、验证对象、容差、目标和一般性是否被未经批准地收窄。',
    '未知和局部失败可诚实记录；只有证据支持当前陈述且下一步推进原验收缺口才可继续。',
    '范围变化或证据夸大进入hold；不得为过关改写原任务。未读取完整材料时不录入pass。',
)


def validate(opinion,segment,task,policy,body,decision):
    """拒绝空泛pass；逐项语义结论由独立核查者承担，程序不猜测物理正确。"""
    checks=opinion.get('data',{}).get('checks')
    try:
        assessment=json.loads(checks[0]) if isinstance(checks,list) and len(checks)==1 else None
    except (ValueError,TypeError):
        assessment=None
    if not isinstance(assessment,dict) or assessment.get('schema')!=SCHEMA:
        raise FrameworkError('续行须提交原件驱动的逐项独立execution-review，通用pass不能替代')
    required={'schema','segment_id','contract_hash','original','report','decision',
              'scope_preserved','criteria','next_step','reason'}
    if set(assessment)!=required or any(assessment.get(k)!=v for k,v in {
            'segment_id':segment['id'],'contract_hash':policy['data']['contract_hash'],
            'original':policy['data']['original'],'report':segment['data'].get('report'),'decision':decision}.items()):
        raise FrameworkError('独立核查未准确绑定原件、合同、本段报告及裁定')
    if type(assessment['scope_preserved']) is not bool:
        raise FrameworkError('独立核查须明确范围是否保持')
    if not isinstance(assessment['reason'],str) or not assessment['reason'].strip():
        raise FrameworkError('独立核查须明确裁定依据')
    entries=assessment['criteria']
    expected={c['criterion']:c for c in body['criteria']}
    if (not isinstance(entries,list) or len(entries)!=len(expected)
            or any(not isinstance(c,dict) or not isinstance(c.get('criterion'),str) for c in entries)
            or {c.get('criterion') for c in entries}!=set(expected)):
        raise FrameworkError('独立核查不得省略或重复原验收项')
    for entry in entries:
        if (set(entry)!={'criterion','judgment','evidence','finding'}
                or entry['judgment'] not in {'accurate','overclaimed','unknown'}
                or entry['evidence']!=expected[entry['criterion']]['evidence']
                or not isinstance(entry['finding'],str) or not entry['finding'].strip()):
            raise FrameworkError('独立核查须逐项给出判断、准确证据版本及内容依据')
    if decision not in {'continue','hold','completed'}:
        raise FrameworkError('独立裁定仅允许continue/hold/completed')
    if decision!='hold' and (assessment['scope_preserved'] is not True
                            or any(c['judgment']!='accurate' for c in entries)):
        raise FrameworkError('范围缩减、夸大或未核清不得继续或完成')
    if decision=='completed' and any(c['state']!='satisfied' for c in body['criteria']):
        raise FrameworkError('未覆盖全部原验收要求，不能裁定完整成功')
    next_step=assessment['next_step']
    if decision=='continue':
        if (not isinstance(next_step,dict) or set(next_step)!={'criteria','purpose','expected'}
                or not isinstance(next_step['criteria'],list) or not next_step['criteria']
                or not all(isinstance(c,str) for c in next_step['criteria'])
                or len(set(next_step['criteria']))!=len(next_step['criteria'])
                or not set(next_step['criteria'])<=set(expected)
                or any(not isinstance(next_step[k],str) or not 0<len(next_step[k].strip())<=4000
                       for k in ('purpose','expected'))):
            raise FrameworkError('继续须独立批准下一段对应原要求的目的和预期')
    elif next_step is not None:
        raise FrameworkError('hold或completed不发放下一段计划')
    return assessment


class ExecutionReview:
    def __init__(self,store):
        self.store=store

    def packet(self,segment_id,max_bytes=262144):
        """原件完整提供；大型或非文本证据保留准确定位，绝不静默截断成已读。"""
        from .execution_control import ExecutionControl
        if type(max_bytes) is not int or not 1024<=max_bytes<=4194304:
            raise FrameworkError('核查包正文预算须为1024至4194304字节')
        control=ExecutionControl(self.store)
        with self.store.connection() as connection:
            tx=Transaction(self.store,connection,{'scopes':['.']})
            segment=typed(tx,segment_id,'execution_segment')
            policy=control._policy(tx,segment['data']['task'])
            task=control._base(tx,segment['data']['task'],policy)
            if segment['data']['status']!='submitted':
                raise FrameworkError('须先提交本段准确报告，再构造独立核查包')
            body,_=control._report_body(tx,segment,task,policy)
            original=control._ref(tx,policy['data']['original'],artifact=True)
            raw=self.store.blob(original['data']['blob'])
            try:
                text=raw.decode('utf-8')
            except UnicodeError:
                raise FrameworkError('原任务原件须有完整UTF-8文本，不能用摘要代替') from None
            result={'schema':'aireos.execution-review-packet/1','task':task,'policy':policy,
                    'segment':segment,'original':{'ref':recall.reference(original),'path':original['data']['origin'],
                       'sha256':hashlib.sha256(raw).hexdigest(),'text':text},'report':body,
                    'instructions':list(INSTRUCTIONS),'evidence':[],'requires_read':[],
                    'meaning':'核查材料包不代表完成审核；逐项结论须由独立身份实际读取证据后录入'}
            if len(canonical(result).encode('utf-8'))>max_bytes:
                raise FrameworkError('原任务、合同及报告不能完整装入核查包；提高max_bytes，不允许静默省略原件')
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
                raise FrameworkError('证据清单超过核查包预算；提高max_bytes，不能省略证据')
            return result

    def record(self,segment_id,assessment,request_id):
        """适配既有独立审核入口；不调用模型，不另存一套审核事实。"""
        if not isinstance(assessment,dict):
            raise FrameworkError('独立核查须为结构化对象')
        from .execution_control import ExecutionControl
        control=ExecutionControl(self.store)
        with self.store.connection() as connection:
            tx=Transaction(self.store,connection,{'scopes':['.']})
            segment=typed(tx,segment_id,'execution_segment')
            policy=control._policy(tx,segment['data']['task'])
            task=control._base(tx,segment['data']['task'],policy,allow_closed=True)
            body,_=control._report_body(tx,segment,task,policy)
        opinion={'data':{'checks':[canonical(assessment)]}}
        validate(opinion,segment,task,policy,body,assessment.get('decision'))
        target=segment['data']['report']
        return Project(self.store.root,self.store.token).review(target['id'],
            'limit' if assessment['decision']=='hold' else 'pass',
            '原任务原件及全部验收要求驱动的独立证据核查',opinion['data']['checks'],request_id,
            expected_revision=target['rev'])
