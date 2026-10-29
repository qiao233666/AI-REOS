"""任务内的一张有界接续卡；旧版沿用任务版本历史，不进入知识索引。"""
import json

from .errors import ConflictError, FrameworkError
from .inputs import bind, validate
from .project import typed
from .storage import Transaction, now


def describe(tx, task):
    card = task['data'].get('checkpoint')
    reasons = validate(tx, card['references'], verify_snapshots=False) if card else []
    if card:
        from .recall import source_state
        for ref in card['references']:
            if ref['type'] == 'item' and source_state(tx.store, tx.get(ref['id'], ref['rev'])) is False:
                reasons.append('引用交付项的实际文件已变化或快照不可读取: ' + ref['id'])
    return dict(task_id=task['id'], task_status=task['data']['status'],
                active=task['data']['status'] not in {'closed', 'stopped'}, checkpoint=card,
                reference_state=dict(state='changed' if reasons else 'current', reasons=reasons),
                notice='接续卡是未审核执行进度；材料当前可定位不等于已核查或获采纳。')


def read(project, task_id):
    with project.store.connection() as connection:
        tx = Transaction(project.store, connection, {})
        return describe(tx, typed(tx, task_id, 'task'))


def save(project, task_id, progress, next_step, expected_revision, request_id,
         pending='', references=None):
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise FrameworkError('须提供接续卡当前版本，首次为0')
    for value, required in ((progress, True), (next_step, True), (pending, False)):
        if not isinstance(value, str) or len(value) > 1500 or (required and not value.strip()):
            raise FrameworkError('接续信息须为有界文本，进度和下一步不能为空')
    references = [] if references is None else references
    if not isinstance(references, list) or len(references) > 8:
        raise FrameworkError('接续卡最多8个材料引用，详细内容放实际产物')
    entries = []
    for entry in references:
        entry = {'type':'file', 'path':entry} if isinstance(entry, str) else entry
        if not isinstance(entry, dict) or entry.get('type') not in {'file', 'item'}:
            raise FrameworkError('接续材料须为项目文件或交付项引用')
        entries.append({**entry, 'usage':'reference'})
    payload = dict(task_id=task_id, progress=progress, next_step=next_step,
                   expected_revision=expected_revision, pending=pending, references=entries)

    def apply(tx):
        task = project._scope_task(tx, task_id)
        if task['data']['status'] in {'closed', 'stopped'}:
            raise FrameworkError('已结束任务不能更新接续卡；历史仍可读取')
        previous = task['data'].get('checkpoint')
        if (previous['revision'] if previous else 0) != expected_revision:
            raise ConflictError('接续卡已由其他执行者更新，请先读取最新版')
        content = dict(progress=progress, references=bind(tx, entries, save_snapshots=False), pending=pending, next_step=next_step)
        if previous and all(previous[key] == value for key, value in content.items()):
            return describe(tx, task)
        card = dict(revision=expected_revision+1, **content, author=tx.principal['actor'], updated=now())
        if len(json.dumps(card, ensure_ascii=False)) > 1500:
            raise FrameworkError('整张接续卡连同材料定位最多1500字符，请提炼当前进度')
        task = tx.replace(task_id, task['rev'], {**task['data'], 'checkpoint':card})
        return describe(tx, task)

    return project.store.mutate('task.checkpoint', payload, request_id, 'execute', apply)
