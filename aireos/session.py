"""新会话短包与统一检索；默认只取导航，查询时核对来源和更正。"""
from .errors import FrameworkError
from .history import HistoryArchive, encoded_size
from .storage import Transaction, normalize_scope, within
from .memory_catalog import applicable_constraints, latest
from . import prework


def context(store,output_prefix=None,query=None,max_chars=10000,task_id=None):
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
            guidance='按问题查新记录和旧原文，展开条件与更正；旧状态不自动授信。新工作经prepare、核查及采纳。长任务交接前及关键变化时用checkpoint更新接续卡；多任务接手提供task_id。')
        if config.get('handoff'):
            result['handoff_baseline']={**config['handoff'],
                'state':'historical_baseline_not_revalidated',
                'warning':'这是切换时的历史导航，默认接手未重读原文；决定路线前须按问题检索当前来源和更正。'}
        tasks=tx.list('task')
        if task_id is not None:
            from .project import typed
            selected=typed(tx,task_id,'task')
            if selected['data']['status'] in {'closed','stopped'}:
                raise FrameworkError('指定任务已经结束；用task-checkpoint追溯，不作为当前工作接手')
            if prefix and not any(within(output,prefix) for output in selected['data']['contract']['outputs']):
                raise FrameworkError('指定任务不在本次输出范围内')
            tasks.sort(key=lambda task:task['id']!=task_id)
        visible=[]
        for task in tasks:
            data=task['data']
            if data['status'] not in {'planned','running','delivered'} or (prefix and not any(within(output,prefix) for output in data['contract']['outputs'])):
                continue
            entry=dict(id=task['id'],title=data['contract']['title'][:120],status=data['status'])
            if data.get('checkpoint'):
                entry['checkpoint_revision']=data['checkpoint']['revision']
            result['tasks'].append(entry)
            if encoded_size(result)>budget//2:
                result['tasks'].pop(); result['truncated']=True; break
            visible.append(task)
        active=[task for task in tasks if task['data']['status'] in {'planned','running','delivered'}
                and (not prefix or any(within(output,prefix) for output in task['data']['contract']['outputs']))]
        chosen=task_id or (active[0]['id'] if len(active)==1 else None)
        for task,entry in zip(visible,result['tasks']):
            if task['id']==chosen and task['data'].get('checkpoint'):
                from .checkpoints import describe
                detail=describe(tx,task)
                entry.update(checkpoint=detail['checkpoint'],checkpoint_reference_state=detail['reference_state'],
                             checkpoint_notice=detail['notice'])
        if len(active)>1 and task_id is None and any(task['data'].get('checkpoint') for task in active):
            result['checkpoint_selection']='多个活跃任务；提供task_id只展开本次接手的卡，或用task-checkpoint准确读取。'
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
        elif any('checkpoint' in task for task in result['tasks']):
            for task in result['tasks']:
                if 'checkpoint' in task:
                    for key in ('checkpoint','checkpoint_reference_state','checkpoint_notice'):
                        task.pop(key,None)
                    task['checkpoint_omitted']=True
            result['checkpoint_read']='完整接续卡未放入本包；用task-checkpoint及任务id读取，不可把省略当成没有进度。'
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
