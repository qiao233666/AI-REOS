"""只沿显式输入及实际工作目标的supports绑定现场来源，不把检索关联当依据。"""
from .errors import FrameworkError, StaleInput
from .inputs import objects as input_objects
from .recall import reference


def live_sources(tx, task, working_items=None):
    if working_items is not None and (not isinstance(working_items,list) or len(working_items)>64
            or any(not isinstance(value,str) or not value for value in working_items)
            or len(set(working_items))!=len(working_items)):
        raise FrameworkError('working_items须为本任务最多64个不同交付项身份')
    if working_items is None:
        selected=[obj for obj in tx.list('item') if obj['data']['task']==task['id']]
    else:
        selected=[tx.get(identifier) for identifier in working_items]
    if any(obj['kind']!='item' or obj['data']['task']!=task['id'] for obj in selected):
        raise FrameworkError('实际工作目标必须属于本任务')
    seeds=input_objects(tx,task['data']['contract'].get('inputs',[]))+selected
    visited={}
    active=set()

    def visit(obj):
        key=(obj['id'],obj['rev'])
        if key in active:
            raise FrameworkError('实际依据存在循环，不能作为有效开工来源')
        if key in visited:
            return
        if len(visited)>=200:
            raise FrameworkError('实际依据链超过200项，须缩小任务而非截断校验')
        active.add(key)
        visited[key]=reference(obj)
        for ref in obj['data'].get('supports',[]):
            support=tx.get(ref['id'],ref['rev'])
            if support['kind']!='item' or reference(support)!=ref:
                raise FrameworkError('实际依据引用缺失或摘要不符')
            visit(support)
        active.remove(key)

    for obj in seeds:
        visit(obj)
    return dict(live_refs=list(visited.values()),working_items=[reference(obj) for obj in selected])


def require_coverage(tx, receipt_id, task, items):
    data=tx.get(receipt_id)['data']
    if data.get('receipt_version',1)<3:
        return
    required=live_sources(tx,task,items)['live_refs']
    available=data['live_refs']
    if any(ref not in available for ref in required):
        raise StaleInput('本次交付或采纳目标及其依据未被收据绑定；请按working_items重新prepare')
