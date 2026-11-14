"""待决请求单独留痕；答复不授予权限、不改合同，也不自动恢复任务。"""
import json

from .errors import ConflictError, FrameworkError, PermissionDenied
from .storage import Transaction, digest, now


MAX_PENDING = 8


def _text(value, label, maximum, optional=False):
    if (not isinstance(value, str) or len(value) > maximum
            or (not optional and not value.strip())):
        raise FrameworkError(label + '须为' + ('可空的' if optional else '非空的')
                             + str(maximum) + '字符以内字符串')


def pending(tx, task_id):
    """同一事务内列出准确的当前未决版本，原顺序用于分页。"""
    return [obj for obj in tx.list('decision_request')
            if obj['data']['task'] == task_id and obj['data']['status'] == 'pending']


def request(project, task_id, key, question, request_id, context=''):
    _text(task_id, '任务id', 100)
    _text(key, '待决键', 100)
    _text(question, '待决问题', 2000)
    _text(context, '待决背景', 4000, optional=True)
    payload = dict(task=task_id, key=key, question=question, context=context)

    def apply(tx):
        capabilities = set(tx.principal['capabilities'])
        if not capabilities.intersection({'*', 'plan', 'execute', 'review', 'decide'}):
            raise PermissionDenied('提出待决请求需要plan、execute、review或decide能力')
        task = project._scope_task(tx, task_id)
        for obj in tx.list('decision_request'):
            data = obj['data']
            if data['task'] == task_id and data['key'] == key:
                if data['question'] != question or data['context'] != context:
                    raise ConflictError('同任务同待决键对应不同内容；请明确新的问题键')
                return obj
        if task['data']['status'] not in {'planned', 'running', 'delivered'}:
            raise FrameworkError('已经结束的任务不能新增待决请求')
        if len(pending(tx, task_id)) >= MAX_PENDING:
            raise FrameworkError('每任务最多8项待决请求；请先答复或撤销既有问题')
        return tx.create('decision_request', {**payload, 'status': 'pending',
                                             'author': tx.principal['actor']})

    return project.store.mutate('decision.request', payload, request_id, None, apply)


def resolve(project, decision_id, expected_revision, answer, request_id, resolution='answered'):
    from .project import typed
    _text(decision_id, '待决id', 100)
    if (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
            or expected_revision < 1):
        raise FrameworkError('待决版本必须为正整数')
    _text(answer, '答复或撤销理由', 4000)
    if not isinstance(resolution, str) or resolution not in {'answered', 'withdrawn'}:
        raise FrameworkError('待决处理方式须为answered或withdrawn')
    payload = dict(decision=decision_id, rev=expected_revision, answer=answer, resolution=resolution)

    def apply(tx):
        obj = typed(tx, decision_id, 'decision_request')
        project._scope_task(tx, obj['data']['task'])
        if obj['rev'] != expected_revision:
            raise ConflictError('待决请求版本已经变化')
        if obj['data']['status'] != 'pending':
            raise ConflictError('待决请求已经处理；原请求重放不会重新开启问题')
        return tx.replace(decision_id, expected_revision, {**obj['data'], 'status': resolution,
                          'answer': answer, 'resolved_by': tx.principal['actor'], 'resolved_at': now()})

    return project.store.mutate('decision.resolve', payload, request_id, 'decide', apply)


def read(project, task_id, max_chars=10000, offset=0, expected_snapshot=None):
    from .project import typed
    _text(task_id, '任务id', 100)
    if (isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1000 <= max_chars <= 50000
            or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
        raise FrameworkError('待决显示预算须为1000至50000字符，offset须为非负整数')
    with project.store.connection() as connection:
        tx = Transaction(project.store, connection, {})
        task = typed(tx, task_id, 'task')
        requests = pending(tx, task_id)
        count = len(requests)
        snapshot = digest([[obj['id'], obj['rev']] for obj in requests])
        if offset and expected_snapshot is None:
            raise FrameworkError('后续页须提供首包snapshot作为expected_snapshot；否则请从0重新读取')
        if expected_snapshot is not None and expected_snapshot != snapshot:
            raise ConflictError('待决集合或版本已经变化；请从offset=0重新读取，不得沿用旧页偏移')
        if offset > count:
            raise FrameworkError('offset超出当前待决条目范围；请从0重新读取')
        response = dict(task_id=task_id, task_revision=task['rev'], task_status=task['data']['status'],
                        pending_count=count, snapshot=snapshot, requests=[], offset=offset, next_offset=None,
                        note='只读待决记录；用准确id/rev答复。答复不改合同、不授予权限、不自动恢复任务。')
        for index, obj in enumerate(requests[offset:], offset):
            response['requests'].append(obj)
            response['next_offset'] = index + 1 if index + 1 < count else None
            if len(json.dumps(response, ensure_ascii=False)) > max_chars:
                response['requests'].pop()
                response['next_offset'] = index
                if not response['requests']:
                    raise FrameworkError('单项待决请求超出预算，请提高max_chars；不得把省略当成没有待决')
                break
        if len(json.dumps(response, ensure_ascii=False)) > max_chars:
            raise FrameworkError('待决导航元信息超出预算，请提高max_chars')
        return response
