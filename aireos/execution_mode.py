"""用户显式受管模式的宿主操作状态；不推断任务大小，不授予科研权限。"""
import json
from pathlib import Path

from .errors import FrameworkError
from .storage import Store


ON='开启受管长任务'
OFF='退出受管长任务'


def opt_in(root):
    """核心绑定只读模式字段，不要求独立脚本运行配置也安装客户端钩子。"""
    path=Path(root)/'aiops/aireos/retrieval-policy.json'
    if not path.is_file():return False
    try:
        execution=json.loads(path.read_text(encoding='utf-8')).get('execution',{})
        scope=execution.get('scope','all_sessions')
        if scope not in {'all_sessions','bound_sessions','opt_in'}:raise ValueError()
        return scope=='opt_in'
    except (OSError,ValueError,TypeError,AttributeError):
        raise FrameworkError('无法读取显式模式策略，拒绝自动绑定') from None


def prompt_request(prompt):
    if not isinstance(prompt,str) or not prompt.strip():return None
    first=prompt.strip().splitlines()[0].strip().rstrip('。！!')
    if first in {ON,'进入长任务模式','开启长任务模式'}:return True
    if first in {OFF,'退出长任务模式'}:return False
    return None


def mode(root,session_id):
    from .client_guard import _db,_session
    with _db(root) as db:
        row=db.execute('SELECT enabled,released,note FROM execution_modes WHERE session=?',(_session(session_id),)).fetchone()
    return dict(enabled=bool(row['enabled']),released=json.loads(row['released']) if row['released'] else None,
                note=row['note']) if row else dict(enabled=False,released=None,note='默认普通工作模式')


def binding(root,session_id):
    store=Store(root)
    bindings=[obj for obj in store.list('execution_binding') if obj['data']['session_id']==session_id]
    if len(bindings)>1:raise FrameworkError('会话有多个正式绑定，须诊断，不能自动降级')
    stages=[obj for obj in store.list('execution_segment') if obj['data']['session_id']==session_id]
    if bindings:
        stages=[obj for obj in stages if obj['data']['task']==bindings[0]['data']['task']]
        if not stages:raise FrameworkError('正式绑定缺少执行段，须诊断，不能自动降级')
    return max(stages,key=lambda obj:obj['sequence']) if stages else None


def managed(root,session_id):
    stage=binding(root,session_id)
    if stage is None:return False
    released=mode(root,session_id)['released']
    return released!={'id':stage['id'],'rev':stage['rev']}


def set_mode(root,session_id,enabled,authorization):
    """已接入hook由真实用户事件调用；无hook客户端须保留用户准确授权文字。"""
    from .client_guard import _db,_session,policy
    if type(enabled) is not bool or authorization!=(ON if enabled else OFF):
        raise FrameworkError('模式设置须有明确布尔值及用户准确开启/退出口令')
    if policy(root).get('execution',{}).get('scope')!='opt_in':
        raise FrameworkError('本项目尚未选择显式受管模式，不能静默更改项目策略')
    released=None;note='用户已显式开启；主控办理准确任务启动，无新增计算、外审或子代理授权'
    if not enabled:
        stage=binding(root,session_id)
        if stage and managed(root,session_id):
            from .execution_control import ExecutionControl
            control=ExecutionControl(Store(root))
            current=control.status(session_id=session_id)
            with control._cache() as cache:
                inflight=cache.execute("SELECT 1 FROM inflight WHERE session=? AND outcome='running'",(session_id,)).fetchone()
            if inflight or not any(current.get(field) for field in ('completion_ready','handoff_ready','waiting_ready')):
                enabled=True
                note='退出待交接：已有绑定必须按原流程完成或hold且无在途工具；未解除约束，也未自动派发外审'
            else:
                released={'id':stage['id'],'rev':stage['rev']}
                note='用户在准确交接后退出；原任务、未完成项及科学未知保留'
        else:note='用户已退出，恢复普通工作模式'
    with _db(root) as db:
        db.execute('INSERT OR REPLACE INTO execution_modes(session,enabled,released,note) VALUES(?,?,?,?)',
                   (_session(session_id),int(enabled),json.dumps(released) if released else None,note))
    return mode(root,session_id)


def workflow(root,session_id):
    from .execution_recovery import input_path,SCHEMAS
    current=mode(root,session_id)
    is_managed=managed(root,session_id)
    return dict(session_id=session_id,state='managed' if is_managed else ('requested' if current['enabled'] else 'normal'),
        **current,activation=ON,deactivation=OFF,
        startup={name:{'input_path':str(input_path(root,session_id,name)),
                      'required':sorted(SCHEMAS[name][0]),'optional':sorted(SCHEMAS[name][1])}
                 for name in ('client-workflow','create-task','execution-start')},
        guidance='默认普通工作；仅用户显式开启受管长任务后由主控登记并启动，无需用户编写JSON或指定内部编号。'
                 '已有准确任务先核对，不按同题猜测。保留全部原要求；启用不授予新增科研、外审或super-agent权限。')
