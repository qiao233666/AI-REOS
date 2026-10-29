"""会话与开工共用的历史检查；凭据证明来源/版本，不证明模型理解或历史穷尽。"""
import copy
import json

from .errors import FrameworkError
from .history import COVERAGE, encoded_size
from .memory_catalog import snapshot, identity, reference, latest, applicable_constraints
from .memory_packet import window
from .storage import digest


def capture(store,tx,query,limit=12,outputs=None,seed_refs=None):
    found,entries=snapshot(store,query,limit)
    history=latest(tx,'history_config')
    policy=latest(tx,'prework_config')
    config=policy['data'] if policy else {}
    configured={source['path'] for source in found['sources']}
    missing=set(config.get('required_sources',[]))-configured
    if missing:
        raise FrameworkError('必查历史来源尚未登记: '+', '.join(sorted(missing)))
    selected={identity(row):row for row in found['matches']}
    for ref in seed_refs or []:
        key=identity(ref)
        if key not in entries:
            raise FrameworkError('命中摘要的原始来源缺失: '+key)
        selected[key]=entries[key]
    if len(selected)>100:
        raise FrameworkError('命中摘要来源超过100项，请细化任务')
    required=[]
    for ref in config.get('required_records',[]):
        key=identity(ref)
        if key not in entries:
            raise FrameworkError('明确必需历史条目缺失: '+key)
        selected[key]=entries[key]
        required.append(key)
    related=[]
    links=tx.list('history_link')
    changed=True
    while changed:
        changed=False
        for link in links:
            data=link['data']
            left,right=identity(data['source']),identity(data['target'])
            if left not in selected and right not in selected:
                continue
            if not any(x['id']==link['id'] for x in related):
                current=all(key in entries and entries[key]['entry_sha256']==data[field]['entry_sha256']
                    for key,field in ((left,'source'),(right,'target')))
                related.append(dict(id=link['id'],rev=link['rev'],source=left,target=right,
                    relation=data['relation'],source_state='current' if current else 'changed'))
            for key in (left,right):
                if key in entries and key not in selected:
                    if len(selected)>=100:
                        raise FrameworkError('明确历史关联超过本次100项范围，请细化任务')
                    selected[key]=entries[key]; changed=True
    # 更正与风险先显示，普通条目仍保留精确定位及来源。
    correction={item['source'] for item in related if item['relation'] in {'corrects','supersedes','qualifies'}}
    ordered=sorted(selected.values(),key=lambda row:(
        0 if identity(row) in correction else 1 if identity(row) in required else
        2 if 'risks' in row['categories'] else 3, list(selected).index(identity(row))))
    checked=dict(api=1,configuration=digest(history['data']['sources'] if history else []),
        policy=digest(config),query=query,limit=limit,
        sources=found['sources'],references=[reference(row) for row in ordered],links=related,
        required=required,coverage=COVERAGE)
    constraints=applicable_constraints(config,outputs or ['.'])
    return dict(check=checked,entries=ordered,constraints=constraints,
        configured=found['configured'],candidate_truncated=found['candidate_truncated'])


def packet(captured,query,max_chars=6000):
    result=dict(configured=captured['configured'],matches=[],
        source_coverage={'checked':len(captured['check']['sources'])},
        candidate_truncated=captured['candidate_truncated'],display_truncated=False,
        coverage=COVERAGE,relations=[dict(source=x['source'].rsplit('#',1)[-1],
            target=x['target'].rsplit('#',1)[-1],relation=x['relation']) for x in captured['check']['links']],
        relation_details='完整来源路径与版本保存在开工收据；普通查看须展开对应原文核对')
    for row in captured['entries']:
        body,start,end=window(row['text'],query,160 if captured['check']['links'] else 400)
        card=dict(legacy_id=row['legacy_id'],authority='historical_reference',
            legacy_status=row['legacy_status'],categories=row['categories'],body=body,
            partial=start>0 or end<len(row['text']),
            source=dict(path=row['path'],sha256=row['source_sha256'],entry_sha256=row['entry_sha256'],
                format=row.get('source_format',next((s['format'] for s in captured['check']['sources'] if s['path']==row['path']),None)),
                start_line=row['start_line'],end_line=row['end_line'],fragment_start=start,fragment_end=end))
        result['matches'].append(card)
        if encoded_size(result)>max_chars:
            result['matches'].pop(); result['display_truncated']=True
            break
        result['display_truncated']=result['display_truncated'] or card['partial']
    if encoded_size(result)>max_chars:
        result['relations_omitted']=len(result['relations'])
        result['relations']=[]
        result['display_truncated']=True
    if encoded_size(result)>max_chars:
        raise FrameworkError('历史元信息超出显示预算')
    return result


def validate(tx,old,query):
    """相关条目变化硬失效；新增候选只提示，避免无关更新使全部任务失效。"""
    history=latest(tx,'history_config')
    policy=latest(tx,'prework_config')
    if old is None:
        return (['旧收据未覆盖已登记历史或开工策略，请重新prepare'] if history or policy else []),[]
    reasons=[]; warnings=[]
    if old.get('configuration')!=digest(history['data']['sources'] if history else []):
        reasons.append('历史来源配置变化，请重新prepare')
    if old.get('policy')!=digest(policy['data'] if policy else {}):
        reasons.append('开工约束配置变化，请重新prepare')
    try:
        current=capture(tx.store,tx,query,old.get('limit',12),seed_refs=old.get('references',[]))
        entries={identity(row):row for row in current['entries']}
        # 排名变化不能使先前已经绑定的条目免于检查。
        from .history import records
        for source in current['check']['sources']:
            for row in records(tx.store.blob(source['sha256']).decode('utf-8-sig'),source['format']):
                row['path']=source['path']; entries[identity(row)]=row
        selected={identity(ref) for ref in old.get('references',[])}
        for ref in old.get('references',[]):
            actual=entries.get(identity(ref))
            if actual is None or actual['entry_sha256']!=ref['entry_sha256']:
                reasons.append('所用历史条目变化或消失: '+identity(ref))
            # 老快照仍须可恢复；不能只认当前内容。
            tx.store.blob(ref['source_sha256'])
        previous_links={(item['id'],item['rev']) for item in old.get('links',[])}
        for item in current['check']['links']:
            if (item['id'],item['rev']) not in previous_links and (item['source'] in selected or item['target'] in selected):
                reasons.append('所用历史新增或变更明确关联: '+item['id'])
        for item in old.get('links',[]):
            actual=tx.get(item['id'])
            if actual['rev']!=item['rev']:
                reasons.append('所用历史关联版本变化: '+item['id'])
        if old.get('sources')!=current['check']['sources']:
            for ref in current['check']['references']:
                if identity(ref) not in selected:
                    warnings.append(dict(legacy_id=ref['legacy_id'],path=ref['path'],
                        note='来源更新后有新的相关候选，须核对适用性；不自动判定冲突'))
    except (FrameworkError,OSError,UnicodeError) as exc:
        reasons.append('历史来源无法重新核对: '+str(exc))
    return reasons,warnings


def fit_prepare(value,max_chars):
    """历史检查的长凭据存在本地；给模型的完整JSON另按硬预算裁切。"""
    result=copy.deepcopy(value)
    metadata_compacted=False
    # 完整收据可由get恢复；其内部来源目录不重复注入模型。
    if 'history_check' in result.get('data',{}):
        data=result['data']
        check=data.pop('history_check')
        data['history_check_summary']=dict(sources=len(check['sources']),references=len(check['references']),
            detail='get '+result['id'])
    result['display_truncated']=result.get('display_truncated',False)
    while encoded_size({**result,'_replayed':False})>max_chars:
        result['display_truncated']=True
        # 先缩短片段，尽可能保留不同类别的指针。
        candidates=[(entry,'body') for entry in result.get('context',[])+result.get('historical',[])+result.get('history',{}).get('matches',[])
            if len(entry.get('body',''))>80]
        if candidates:
            entry,field=max(candidates,key=lambda pair:len(pair[0][pair[1]]))
            entry[field]=entry[field][:max(80,len(entry[field])//2)]
            entry['partial']=True
            source=entry.get('source',{})
            if 'fragment_start' in source:
                source['fragment_end']=source['fragment_start']+len(entry[field])
            elif 'start' in source:
                source['end']=source['start']+len(entry[field])
            continue
        data=result.get('data',{})
        if len(data.get('refs',[]))>1:
            data['refs_omitted']=data.get('refs_omitted',0)+len(data['refs'])
            data['refs']=[]
            result['receipt_details']='get '+result['id']
            continue
        if data.get('historical'):
            data['historical']=[]; data['historical_omitted']=True
            continue
        directory=next((key for key in ('live_refs','working_items') if data.get(key)),None)
        if directory:
            data[directory+'_omitted']=len(data[directory])
            data[directory]=[]
            result['receipt_details']='get '+result['id']
            continue
        freshness=result.get('freshness',{})
        if freshness.get('warnings') or freshness.get('reasons'):
            freshness['warnings']=[]; freshness['reasons']=[]
            freshness['details_omitted']=True
            continue
        if 'retrieval' in result:
            result.pop('retrieval'); result['retrieval_details']='get '+result['id']
            continue
        if not metadata_compacted:
            for key in ('project','contract','watermark','epoch','query','coverage','history_check_summary'):
                data.pop(key,None)
            for key in ('retrieval_note','source_policy','created','sequence','view','retrieval_details','kind'):
                result.pop(key,None)
            for key in ('historical','live_refs','working_items','live_refs_omitted','working_items_omitted'):
                data.pop(key,None)
            metadata_compacted=True
            result['receipt_details']='get '+result['id']
            continue
        if any(not state.get('details_omitted') for state in result.get('reuse_status',{}).values()):
            result['reuse_status']={identifier:dict(status=state['status'],details_omitted=True)
                                    for identifier,state in result['reuse_status'].items()}
            continue
        if freshness and not freshness.get('compact'):
            result['freshness']=dict(valid=freshness['valid'],blockers=freshness['blockers'],
                details_omitted=True,compact=True)
            continue
        history=result.get('history',{})
        arrays=[result.get('context',[]),result.get('historical',[]),history.get('matches',[])]
        nonempty=[items for items in arrays if items]
        if nonempty:
            items=max(nonempty,key=lambda rows:encoded_size(rows))
            removed=items.pop()
            if removed.get('id'):
                visible={row['id'] for row in result.get('context',[])+result.get('historical',[])}
                result['reuse_status']={key:state for key,state in result.get('reuse_status',{}).items() if key in visible}
            history['display_truncated']=True
            continue
        if data.get('refs'):
            data['refs_omitted']=len(data['refs']);data['refs']=[]
            continue
        raise FrameworkError('开工元信息超出预算，请提高预算或缩小明确约束')
    from .storage import canonical
    states=result.get('reuse_status',{})
    result['display_chars']=sum(len(canonical(entry))+(len(canonical({entry['id']:states[entry['id']]}))
        if entry['id'] in states else 0) for entry in result.get('context',[])+result.get('historical',[]))
    return result
