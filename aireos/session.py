"""新会话短包与统一检索；默认只取导航，查询时核对来源和更正。"""
from .errors import FrameworkError
from .history import HistoryArchive, encoded_size
from .storage import Transaction, normalize_scope, within
from .memory_catalog import applicable_constraints, latest
from . import prework


def context(store,output_prefix=None,query=None,max_chars=10000):
    if isinstance(max_chars,bool) or not isinstance(max_chars,int) or not 2000<=max_chars<=50000:
        raise FrameworkError('启动导航预算须为2000至50000字符')
    prefix=normalize_scope(output_prefix) if output_prefix else None
    budget=max_chars-180
    with store.connection() as connection:
        tx=Transaction(store,connection,{})
        policy=latest(tx,'prework_config')
        config=policy['data'] if policy else {}
        result=dict(tasks=[],history=dict(configured=bool(latest(tx,'history_config'))),truncated=False,
            constraints=applicable_constraints(config,[prefix or '.']),
            guidance='按问题查新记录和旧原文，展开条件与更正；旧状态不自动授信。新工作经prepare、核查及采纳。')
        if config.get('handoff'):
            result['handoff_baseline']={**config['handoff'],
                'state':'historical_baseline_not_revalidated',
                'warning':'这是切换时的历史导航，默认接手未重读原文；决定路线前须按问题检索当前来源和更正。'}
        for task in tx.list('task'):
            data=task['data']
            if data['status'] not in {'planned','running','delivered'} or (prefix and not any(within(output,prefix) for output in data['contract']['outputs'])):
                continue
            result['tasks'].append(dict(id=task['id'],title=data['contract']['title'][:120],status=data['status']))
            if encoded_size(result)>budget//2:
                result['tasks'].pop(); result['truncated']=True; break
    if query:
        from .semantic import MemoryService
        channel_budget=max(1000,(budget-encoded_size(result)-160)//2)
        native=MemoryService(store).query(query,limit=6,max_chars=channel_budget)
        result['memory']={key:native[key] for key in
            ('matches','historical','reuse_status','truncated','display_truncated','retrieval','coverage')}
        with store.connection() as connection:
            tx=Transaction(store,connection,{})
            seed_refs=[]
            for ref in native['references']:
                obj=tx.get(ref['id'],ref['rev'])
                if obj['kind']=='memory_note':
                    seed_refs.extend(obj['data']['sources'])
            captured=prework.capture(store,tx,query,12,[prefix or '.'],seed_refs)
        result['history']=prework.packet(captured,query,channel_budget)
        result['truncated']=result['truncated'] or native['truncated'] or native['display_truncated'] or result['history']['candidate_truncated'] or result['history']['display_truncated']
    while encoded_size(result)>budget:
        memory=result.get('memory',{})
        legacy=result.get('history',{})
        arrays=[memory.get('matches',[]),memory.get('historical',[]),legacy.get('matches',[])]
        nonempty=[array for array in arrays if array]
        if nonempty:
            max(nonempty,key=encoded_size).pop()
            visible={row['id'] for row in memory.get('matches',[])+memory.get('historical',[])}
            memory['reuse_status']={key:state for key,state in memory.get('reuse_status',{}).items() if key in visible}
            memory['display_truncated']=True; legacy['display_truncated']=True
        elif result['tasks']:
            result['tasks'].pop()
        elif 'handoff_baseline' in result:
            result.pop('handoff_baseline'); result['handoff_omitted']=True
        elif memory.get('retrieval'):
            memory.pop('retrieval')
        elif legacy.get('relations'):
            legacy['relations']=[]; legacy['relations_omitted']=True
        else:
            raise FrameworkError('明确约束和导航元信息超出预算，请提高预算或缩小任务')
        result['truncated']=True
    from .reading import record
    result=record(store,'session',result)
    if encoded_size(result)>max_chars:
        raise FrameworkError('计量元信息超出输出预算')
    return result
