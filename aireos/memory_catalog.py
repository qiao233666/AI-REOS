"""有来源的历史导入、提炼及关联；不会替旧材料补造科学审核或采纳。"""
import re

from .errors import FrameworkError
from .history import HistoryArchive, records
from .storage import digest, normalize_scope, within


def identity(entry):
    return entry['path']+'#'+entry['legacy_id']


def reference(entry):
    return {key:entry[key] for key in ('path','legacy_id','entry_sha256','source_sha256')}


def snapshot(store, query='__inventory_without_semantic_claim__', limit=12):
    """一次读取并保存所有登记来源；后续解析同一批已保存字节。"""
    found=HistoryArchive(store).collect(query,limit=limit)
    entries={}
    for source in found['sources']:
        text=store.blob(source['sha256']).decode('utf-8-sig')
        for entry in records(text,source['format']):
            entry.update(path=source['path'],source_sha256=source['sha256'],categories=source['categories'])
            entries[identity(entry)]=entry
    return found,entries


def latest(tx,kind):
    values=tx.list(kind)
    return values[-1] if values else None


def related_links(links,refs):
    keys={identity(ref) for ref in refs}
    selected={}
    changed=True
    while changed:
        changed=False
        for link in links:
            data=link['data']
            adjacent={identity(data['source']),identity(data['target'])}
            if adjacent & keys and link['id'] not in selected:
                selected[link['id']]=dict(id=link['id'],rev=link['rev'])
                if len(selected)>100:
                    raise FrameworkError('短记录关联超过100项，请缩小来源范围')
                keys.update(adjacent); changed=True
    return sorted(selected.values(),key=lambda item:item['id'])


def pin(entries,values):
    if not isinstance(values,list) or not 1<=len(values)<=32:
        raise FrameworkError('须明确1至32条历史来源')
    found=[]
    for value in values:
        if not isinstance(value,dict) or not {'path','legacy_id'}<=set(value):
            raise FrameworkError('历史来源须含path与legacy_id')
        path=normalize_scope(value['path'])
        key=path+'#'+str(value['legacy_id'])
        if key not in entries:
            raise FrameworkError('未登记或未找到历史来源: '+key)
        entry=entries[key]
        for field in ('entry_sha256','source_sha256'):
            if field in value and value[field]!=entry[field]:
                raise FrameworkError('所声明历史来源版本不一致: '+key)
        ref=reference(entry)
        if ref not in found:
            found.append(ref)
    return found


class MemoryCatalog:
    def __init__(self,store):
        self.store=store

    def configure(self,required_sources,constraints,request_id,required_records=None,handoff=None):
        if not isinstance(required_sources,list) or len(required_sources)>16:
            raise FrameworkError('必查来源须为不超过16个路径的数组')
        paths=sorted(set(normalize_scope(path) for path in required_sources))
        if not isinstance(constraints,list) or len(constraints)>12:
            raise FrameworkError('常驻任务约束最多12项，详细条款按需展开')
        checked=[]
        for item in constraints:
            if not isinstance(item,dict) or not isinstance(item.get('key'),str) or not item['key'].strip():
                raise FrameworkError('约束须有明确key')
            if not isinstance(item.get('text'),str) or not 1<=len(item['text'])<=800:
                raise FrameworkError('单条约束正文须为1至800字符')
            outputs=item.get('outputs',['.'])
            if not isinstance(outputs,list) or not outputs or len(outputs)>16:
                raise FrameworkError('约束须明确适用输出范围')
            checked.append(dict(key=item['key'],text=item['text'],outputs=[normalize_scope(x) for x in outputs]))
        if len({x['key'] for x in checked})!=len(checked):
            raise FrameworkError('约束key不得重复')
        required_records=required_records or []
        if not isinstance(required_records,list) or len(required_records)>24:
            raise FrameworkError('明确必需历史条目最多24项')
        required=[]
        for item in required_records:
            if not isinstance(item,dict) or set(item)!={'path','legacy_id'} or not isinstance(item['legacy_id'],str):
                raise FrameworkError('必需条目须明确path和legacy_id')
            required.append(dict(path=normalize_scope(item['path']),legacy_id=item['legacy_id']))
        payload=dict(api=1,required_sources=paths,constraints=checked,required_records=required)
        if handoff is not None:
            if not isinstance(handoff,dict) or not isinstance(handoff.get('text'),str) or not 1<=len(handoff['text'])<=1600:
                raise FrameworkError('接手基线须有不超过1600字符的正文及来源')
            payload['handoff']=dict(text=handoff['text'],sources=handoff.get('sources',[]),
                meaning='切换/整理时的基线，后续进展以正式任务与阶段记录为准')
        def apply(tx):
            for path in paths+[x['path'] for x in required]:
                tx.require_scope(path)
            for rule in checked:
                for output in rule['outputs']:
                    tx.require_scope(output)
            if handoff is not None:
                _,entries=snapshot(self.store)
                payload['handoff']['sources']=pin(entries,handoff.get('sources',[]))
                for ref in payload['handoff']['sources']:
                    tx.require_scope(ref['path'])
            return tx.create('prework_config',payload)
        return self.store.mutate('prework.configure',payload,request_id,'plan',apply)

    def import_history(self,request_id):
        def apply(tx):
            found,entries=snapshot(self.store)
            if not found['configured']:
                raise FrameworkError('请先登记要转换的历史来源')
            previous={obj['data']['key']:obj for obj in tx.list('legacy_record')}
            mapping={}
            created=updated=unchanged=0
            for key,entry in entries.items():
                tx.require_scope(entry['path'])
                old=previous.get(key)
                if old and old['data']['source']['entry_sha256']==entry['entry_sha256'] and old['data']['categories']==entry['categories']:
                    obj=old
                    unchanged+=1
                else:
                    data=dict(key=key,authority='historical_reference',source=reference(entry),
                        blob=self.store.snapshot(entry['text'].encode('utf-8')),
                        legacy_status=entry['legacy_status'],categories=entry['categories'],
                        note='迁移保留当时原文；未生成科学审核、采纳或执行完成')
                    obj=tx.replace(old['id'],old['rev'],data) if old else tx.create('legacy_record',data)
                    updated+=int(bool(old)); created+=int(not old)
                mapping[key]={'id':obj['id'],'rev':obj['rev'],'entry_sha256':entry['entry_sha256']}
            return dict(mapping=mapping,created=created,updated=updated,unchanged=unchanged,
                missing_from_current_sources=sorted(set(previous)-set(entries)),sources=found['sources'])
        return self.store.mutate('history.import',{},request_id,'plan',apply)

    def add_note(self,key,title,text,scope,sources,request_id):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',key):
            raise FrameworkError('短记录key须为有限字母数字或下划线/连字符')
        for value,maximum in ((title,200),(text,12000),(scope,2000)):
            if not isinstance(value,str) or not 1<=len(value)<=maximum:
                raise FrameworkError('短记录标题、正文或适用范围为空/超限')
        payload=dict(key=key,title=title,text=text,scope=scope,sources=sources)
        def apply(tx):
            _,entries=snapshot(self.store)
            refs=pin(entries,sources)
            for ref in refs:
                tx.require_scope(ref['path'])
            data={**payload,'sources':refs,'authority':'derived_reference','classification':'unknown',
                'review_scope':'源文摘要，科学正确性及适用性仍须独立核查',
                'related_links':related_links(tx.list('history_link'),refs)}
            old=next((x for x in tx.list('memory_note') if x['data']['key']==key),None)
            return tx.replace(old['id'],old['rev'],data) if old else tx.create('memory_note',data)
        return self.store.mutate('memory.note',payload,request_id,'plan',apply,corpus=True)

    def add_link(self,source,target,relation,reason,request_id):
        if relation not in {'corrects','supersedes','qualifies','related','implementation'}:
            raise FrameworkError('未知历史关联类型')
        if not isinstance(reason,str) or not 1<=len(reason)<=2000:
            raise FrameworkError('关联须有有限长度的来源解释')
        payload=dict(source=source,target=target,relation=relation,reason=reason)
        def apply(tx):
            _,entries=snapshot(self.store)
            left=pin(entries,[source])[0]; right=pin(entries,[target])[0]
            if identity(left)==identity(right):
                raise FrameworkError('历史关联不能指向自己')
            for ref in (left,right):
                tx.require_scope(ref['path'])
            key=identity(left)+'|'+relation+'|'+identity(right)
            data=dict(key=key,source=left,target=right,relation=relation,reason=reason,
                authority='annotated_relation',note='原文关系注记，不自动裁定科学真假')
            old=next((x for x in tx.list('history_link') if x['data']['key']==key),None)
            return tx.replace(old['id'],old['rev'],data) if old else tx.create('history_link',data)
        return self.store.mutate('history.link',payload,request_id,'plan',apply,corpus=True)

    def note_state(self,obj,entries=None):
        if entries is None:
            try:
                _,entries=snapshot(self.store)
            except FrameworkError as exc:
                return dict(state='source_unavailable',reasons=[str(exc)],authority='derived_reference')
        reasons=[]
        for ref in obj['data']['sources']:
            current=entries.get(identity(ref))
            if current is None or current['entry_sha256']!=ref['entry_sha256']:
                reasons.append('原条目已变化或不可定位: '+identity(ref))
        try:
            if obj['data'].get('related_links',[])!=related_links(self.store.list('history_link'),obj['data']['sources']):
                reasons.append('来源已有新增或修订的明确关联，摘要须重新核对')
        except FrameworkError as exc:
            reasons.append(str(exc))
        return dict(state='source_changed' if reasons else 'source_current',reasons=reasons,
            authority='derived_reference',scientific_validation='not_granted_by_summary')


def applicable_constraints(config,outputs):
    return [dict(key=item['key'],text=item['text']) for item in config.get('constraints',[])
        if any(within(output,scope) or within(scope,output) for output in outputs for scope in item['outputs'])]
