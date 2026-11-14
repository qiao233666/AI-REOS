"""可选客户端检索前置；只证明本轮查询完成，不授予执行或科学采纳资格。"""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import sys
import uuid

from .errors import FrameworkError


EVENTS = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'Stop')
POLICY = 'aiops/aireos/retrieval-policy.json'


def policy(root):
    path = Path(root) / POLICY
    if not path.is_file():
        return {'enabled': False}
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('schema') != 1
            or type(value.get('enabled')) is not bool):
        raise FrameworkError('客户端检索策略须为schema=1及明确enabled布尔值')
    for field in ('entry', 'hook'):
        if value.get('enabled'):
            _inside(root, value.get(field))
    if not isinstance(value.get('bootstrap_files', []), list) or not isinstance(value.get('readonly_files', {}), dict):
        raise FrameworkError('客户端启动文件及只读文件配置格式错误')
    for name in list(value.get('bootstrap_files', [])) + list(value.get('readonly_files', {})):
        _inside(root, name)
    execution=value.get('execution',{})
    if not isinstance(execution,dict) or type(execution.get('enabled',False)) is not bool:
        raise FrameworkError('执行段策略须明确enabled布尔值')
    return value


def _inside(root, name):
    if not isinstance(name, str) or not name.strip():
        raise FrameworkError('客户端配置路径不能为空')
    base = Path(root).resolve()
    target = (base / name).resolve()
    if target != base and base not in target.parents:
        raise FrameworkError('客户端配置路径越出项目根')
    return target


def _session(session_id):
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 200:
        raise FrameworkError('客户端须提供真实session_id，不能跨会话复用完成记录')
    return hashlib.sha256(session_id.encode('utf-8')).hexdigest()


@contextmanager
def _db(root):
    folder = Path(root).resolve() / '.aireos/cache'
    folder.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(folder / 'client-guard.sqlite3', timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS sessions (session TEXT PRIMARY KEY, turn TEXT NOT NULL, query TEXT NOT NULL, packet TEXT, error TEXT, attempt TEXT)')
        if 'attempt' not in [row[1] for row in connection.execute('PRAGMA table_info(sessions)')]:
            connection.execute('ALTER TABLE sessions ADD COLUMN attempt TEXT')
        with connection:
            yield connection
    finally:
        connection.close()


def begin(root, session_id, query):
    key = _session(session_id)
    if not isinstance(query, str) or not query.strip() or len(query) > 16000:
        raise FrameworkError('本轮检索问题须为1至16000字符的非空字符串')
    turn = uuid.uuid4().hex
    with _db(root) as connection:
        connection.execute('INSERT OR REPLACE INTO sessions (session,turn,query,packet,error,attempt) VALUES (?,?,?,NULL,NULL,NULL)', (key, turn, query))
    return turn


def status(root, session_id):
    with _db(root) as connection:
        row = connection.execute('SELECT * FROM sessions WHERE session=?', (_session(session_id),)).fetchone()
    return dict(row) if row else None


def invalidate(root, session_id):
    with _db(root) as connection:
        connection.execute('DELETE FROM sessions WHERE session=?', (_session(session_id),))


def retrieve(root, session_id=None, query=None, turn_id=None, max_chars=10000):
    """由真实查询结果写完成记录；失败或迟到结果不能解锁当前轮。"""
    from .history import session_context
    from .storage import Store
    session_id = session_id or os.environ.get('CLAUDE_SESSION_ID')
    key = _session(session_id)
    before = status(root, session_id)
    if before is None or (turn_id is not None and before['turn'] != turn_id):
        raise FrameworkError('本轮状态不存在或已被新一轮替代；须重新提交当前请求')
    turn_id = before['turn']
    attempt = uuid.uuid4().hex
    # 重新查询前先撤销旧完成记录；查询异常不能留下旧的放行状态。
    with _db(root) as connection:
        changed = connection.execute('UPDATE sessions SET packet=NULL,error=NULL,attempt=? WHERE session=? AND turn=?', (attempt, key, turn_id)).rowcount
    if changed != 1:
        raise FrameworkError('本轮已变化，不能重用旧查询状态')
    try:
        if not isinstance(query, str) or not query.strip() or len(query) > 16000:
            raise FrameworkError('带具体问题的检索才可生成完成记录')
        packet = session_context(Store(root), query=query, max_chars=max_chars)
        if 'memory' not in packet or 'history' not in packet:
            raise FrameworkError('查询没有同时返回新版记录与历史通道状态')
        encoded = json.dumps(packet, ensure_ascii=False, allow_nan=False)
    except Exception as exc:
        with _db(root) as connection:
            connection.execute('UPDATE sessions SET error=? WHERE session=? AND turn=? AND attempt=?', (str(exc)[:1500], key, turn_id, attempt))
        raise
    with _db(root) as connection:
        changed = connection.execute('UPDATE sessions SET packet=?,query=?,error=NULL WHERE session=? AND turn=? AND attempt=?',
                                     (encoded, query, key, turn_id, attempt)).rowcount
    if changed != 1:
        raise FrameworkError('查询期间已进入新一轮；迟到结果未解锁新请求')
    return {'context': packet, 'completion': {'turn': turn_id, 'query_sha256': hashlib.sha256(query.encode('utf-8')).hexdigest(),
            'packet_sha256': hashlib.sha256(encoded.encode('utf-8')).hexdigest(), 'complete': True,
            'meaning': '只证明本轮真实查询完成；截断、原文核对、正式prepare及科学审核仍须分别处理'}}


def context_text(root):
    entry = str(_inside(root, policy(root).get('entry', 'aiops/aireos/run.py'))).replace('\\', '/')
    return ('[AI-REOS] 新工作以本项目.aireos为正式记录。查项目记录一律先治理检索，当前状态同样适用。'
            '启用ZCode严格模式时，每轮UserPromptSubmit自动带问题联查新版记录与历史，成功后才开放普通项目工具。'
            'session-context无query只给导航，不能算完成检索。失败须修复后重新查询；不得退回grep替代。'
            '检索片段或已采纳状态不代替原文、完整覆盖或科学正确。新任务仍须prepare。'
            '旧账本只读，不追加新正式事实；新教训通过新版受管入口登记。'
            '恢复入口："' + str(Path(sys.executable).resolve()).replace('\\', '/') + '" -X utf8 "' + entry + '" client-context --input <含query和session_id的JSON>。'
            '其他客户端只有明确接入并验证相同事件后才有程序保障。')


def _bootstrap(root, config, payload):
    if payload.get('tool_name') != 'Read':
        return False
    name = (payload.get('tool_input') or {}).get('file_path')
    if not isinstance(name, str):
        return False
    try:
        return _tool_path(root, payload, name) in [_inside(root, p) for p in config.get('bootstrap_files', [])]
    except FrameworkError:
        return False


def _tool_path(root, payload, name):
    base = Path(payload.get('cwd') or root)
    if not base.is_absolute():
        raise FrameworkError('客户端cwd须为绝对路径')
    return (base / name).resolve()


def recovery_input(root, session_id):
    return Path(root).resolve()/'.aireos/cache'/('client-query-'+_session(session_id)+'.json')


def _query_write(root, payload, session_id):
    if payload.get('tool_name') != 'Write':
        return False
    values=payload.get('tool_input') or {}
    name=values.get('file_path')
    if not isinstance(name,str) or _tool_path(root,payload,name)!=recovery_input(root,session_id):
        return False
    try:
        from .execution_recovery import plain_target, decode
        plain_target(root,Path(payload.get('cwd') or root)/name)
        body=decode(values.get('content',''))
        return (isinstance(body,dict) and set(body)<={'query','session_id','max_chars'}
                and body.get('session_id')==session_id and isinstance(body.get('query'),str)
                and 0<len(body['query'].strip())<=16000)
    except (OSError,FrameworkError,ValueError,TypeError):
        return False


def _literal_tokens(command):
    """只接受纯字面参数，不能让shell在固定入口之前展开或执行内容。"""
    if (not isinstance(command, str) or not command.strip()
            or any(char in command for char in ';|&\r\n`<>$%!*?[]{}()^~#\x00')):
        raise FrameworkError('治理恢复命令仅允许纯字面参数，拒绝变量、替换、通配及重定向')
    return shlex.split(command.replace('\\', '/'))


def _recovery(root, config, payload):
    """仅开放单一恢复调用；echo、帮助、任意脚本与命令串不能解锁工具。"""
    if payload.get('tool_name') != 'Bash':
        return False
    command = (payload.get('tool_input') or {}).get('command', '')
    try:
        tokens = _literal_tokens(command)
        raw_executable=tokens.pop(0)
        executable = Path(raw_executable).name.lower()
        if executable not in {'python', 'python.exe', 'python3', 'python3.exe'}:
            return False
        if not _trusted_python(raw_executable):
            return False
        while tokens and tokens[0] in {'-B', '-X'}:
            flag = tokens.pop(0)
            if flag == '-X' and (not tokens or tokens.pop(0) != 'utf8'):
                return False
        if len(tokens) != 4 or tokens[1:3] != ['client-context', '--input']:
            return False
        # 相对路径在cwd漂移后不作为恢复豁免；要求可核对的绝对固定入口。
        if not (Path(tokens[0]).is_absolute() and Path(tokens[0]).resolve() == _inside(root, config['entry'])):
            return False
        if config.get('execution',{}).get('enabled',False):
            from .execution_recovery import plain_target, decode
            session = payload.get('session_id') or os.environ.get('CLAUDE_SESSION_ID')
            path = plain_target(root, _tool_path(root, payload, tokens[3]))
            if path != recovery_input(root, session) or not path.is_file() or path.stat().st_size > 65536:
                return False
            body = decode(path.read_text(encoding='utf-8'))
            return (set(body) <= {'query','session_id','max_chars'} and body.get('session_id') == session
                    and isinstance(body.get('query'), str) and 0 < len(body['query'].strip()) <= 16000)
        return True
    except (ValueError, IndexError, FrameworkError):
        return False


def _trusted_python(value):
    # 不接受PATH别名或shell函数；实际启动的必须是此守卫的绝对解释器。
    return Path(value).is_absolute() and Path(value).resolve()==Path(sys.executable).resolve()


def _control_command(root, config, payload):
    """执行段被阻断后只开放固定治理入口，不开放任意脚本/命令串。"""
    if payload.get('tool_name')!='Bash':
        return False
    command=(payload.get('tool_input') or {}).get('command','')
    try:
        tokens=_literal_tokens(command)
        if not _trusted_python(tokens.pop(0)):
            return False
        while tokens and tokens[0] in {'-B','-X'}:
            flag=tokens.pop(0)
            if flag=='-X' and (not tokens or tokens.pop(0)!='utf8'):
                return False
        entry=Path(tokens.pop(0))
        if not entry.is_absolute() or entry.resolve()!=_inside(root,config['entry']):
            return False
        from .execution_recovery import SCHEMAS, command_input
        command=tokens.pop(0)
        if command not in SCHEMAS:
            return False
        # 参数必须为明确的成对参数；权限仍由正式CLI的作用域凭据核对。
        valid = (len(tokens)%2==0 and all(tokens[i] in {'--input','--credential-file','--request-id'}
                                      and tokens[i+1] and not tokens[i+1].startswith('-')
                                      for i in range(0,len(tokens),2)))
        if not valid or len(set(tokens[::2])) != len(tokens[::2]):
            return False
        options=dict(zip(tokens[::2],tokens[1::2]))
        if '--input' not in options:
            return False
        session=payload.get('session_id') or os.environ.get('CLAUDE_SESSION_ID')
        command_input(root,session,command,_tool_path(root,payload,options['--input']))
        return True
    except (OSError,ValueError,TypeError,IndexError,FrameworkError):
        return False


def _execution(root,event,payload,config,session_id):
    if not config.get('execution',{}).get('enabled',False):
        return 0,None,''
    from .execution_control import ExecutionControl
    from .storage import Store
    control=ExecutionControl(Store(root))
    if event=='PreToolUse' and payload.get('tool_name')=='Write':
        values=payload.get('tool_input') or {}
        name=values.get('file_path')
        if isinstance(name,str):
            raw=Path(payload.get('cwd') or root)/name
            # 仅此命名空间可申请恢复豁免；科研输出仍须消耗普通工具预算。
            candidate=('execution-reports' in raw.parts or raw.name.startswith('execution-input-'))
            if candidate:
                from .execution_recovery import write_allowed
                if write_allowed(root,session_id,raw,values.get('content')):
                    return 0,None,''
                return 2,None,'恢复写入不在当前会话、正式任务和准确报告的派生路径内。'
    if event=='PreToolUse' and _control_command(root,config,payload):
        return 0,None,''
    try:
        current=control.status(session_id=session_id)
    except FrameworkError as exc:
        current={'allowed':False,'reasons':[str(exc)]}
    reason='执行段不能继续：'+'；'.join(current.get('reasons',[]))
    if event=='Stop':
        if current.get('completion_ready') is True:
            return 0,None,''
        if current.get('handoff_ready') is True:
            message='正式hold交接：原任务尚未完成，当前未知、失败或待决仍保留；本轮结束不授予续行或科学成功。'
            return 0,{'systemMessage':message},''
        detail='；'.join(current.get('completion_reasons',[]))
        handoff='；'.join(current.get('handoff_reasons',[]))
        return 0,{'decision':'block','reason':reason+'；'+detail+'；'+handoff+'。请登记当前证据与缺口，并由独立核查决定hold交接、续行或完成。'},''
    if event=='PreToolUse':
        event_id=payload.get('tool_use_id') or payload.get('tool_call_id')
        if not event_id:
            return 2,None,'真实宿主未提供tool_use_id/tool_call_id；无法安全计数，不放行。'
        result=control.admit(session_id=session_id,segment_id=current.get('segment_id'),
            tool_name=payload.get('tool_name'),tool_input=payload.get('tool_input') or {},event_id=event_id)
        if not result.get('allowed') or result.get('replayed'):
            from .execution_recovery import input_path
            example=input_path(root,session_id,'execution-status')
            return 2,None,('执行段未放行：'+'；'.join(result.get('reasons') or ['已处理工具事件不能再次执行'])
                +'。可Write有限治理JSON到 '+str(example)+'（将末尾命令名替换为白名单命令）；'
                '报告路径由execution-status的recovery.report_path给出，只能新建有限JSON草稿；'
                '需保留全部原验收项，之后用add-artifact、execution-report和独立逐项审核恢复。')
    return 0,None,''


def handle(root, event, payload):
    """返回退出码、客户端JSON及诊断；未知/损坏的已启用守卫不静默放行。"""
    try:
        if event == 'UserPromptSubmit':
            session = (payload.get('session_id') if isinstance(payload,dict) else None) or os.environ.get('CLAUDE_SESSION_ID')
            if session:
                invalidate(root,session)
                # 即使策略损坏，本轮新输入也不能复活旧执行资格。
                from .execution_control import ExecutionControl
                from .storage import Store
                ExecutionControl(Store(root)).invalidate(session)
        config = policy(root)
        if not config.get('enabled'):
            return 0, None, ''
        if not isinstance(payload, dict) or event not in EVENTS:
            raise FrameworkError('客户端事件或输入格式不支持')
        if event == 'PreToolUse' and _bootstrap(root, config, payload):
            return 0, None, ''
        session_id = payload.get('session_id') or os.environ.get('CLAUDE_SESSION_ID')
        _session(session_id)
        if event == 'PreToolUse' and _query_write(root,payload,session_id):
            return 0,None,''
        if event == 'SessionStart':
            with _db(root) as connection:
                connection.execute('DELETE FROM sessions WHERE session=?', (_session(session_id),))
            if config.get('execution',{}).get('enabled',False):
                from .execution_control import ExecutionControl
                from .storage import Store
                ExecutionControl(Store(root)).invalidate(session_id)
            return 0, {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': context_text(root)}}, ''
        if event == 'UserPromptSubmit':
            # 长提示明确裁剪首尾；这不承诺覆盖整份输入或穷尽项目历史。
            prompt = payload.get('prompt')
            if not isinstance(prompt, str) or not prompt.strip():
                with _db(root) as connection:
                    connection.execute('DELETE FROM sessions WHERE session=?', (_session(session_id),))
                raise FrameworkError('本轮没有有效检索问题；旧完成记录已撤销')
            truncated = len(prompt) > 12000
            query = prompt if not truncated else prompt[:6000] + '\n[长请求中段省略]\n' + prompt[-6000:]
            turn = begin(root, session_id, query)
            found = retrieve(root, session_id, query, turn_id=turn)
            text = context_text(root) + '\n' + json.dumps({'prompt_truncated': truncated, **found}, ensure_ascii=False)
            return 0, {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}, ''
        if event == 'PreToolUse':
            tool = payload.get('tool_name')
            name = (payload.get('tool_input') or {}).get('file_path')
            if tool in {'Write', 'Edit', 'ApplyPatch'} and isinstance(name, str):
                if _tool_path(root, payload, name) in [_inside(root, p) for p in config.get('readonly_files', {})]:
                    return 2, None, '旧账本只读；新事实及教训须走新版受管入口。'
            if _recovery(root, config, payload):
                return 0, None, ''
        current = status(root, session_id)
        if current and current['packet'] and not current['error']:
            return _execution(root,event,payload,config,session_id)
        reason = ('本轮治理检索尚未成功完成，不能用旧轮次、旧STATE或grep替代。请用固定入口client-context恢复。'
                  '允许Write创建仅含query/session_id的恢复JSON：'+str(recovery_input(root,session_id)))
        if current and current.get('error'):
            reason += ' 查询诊断：' + current['error']
        if event == 'Stop':
            return 0, {'decision': 'block', 'reason': reason}, ''
        return 2, None, reason
    except Exception as exc:
        return 2, None, '治理检索前置未通过：' + str(exc)


def zcode_config(root, python, script, existing=None):
    """返回合并配置供审核；不自动安装、不覆盖其他命令和计算守卫。"""
    config = copy.deepcopy(existing or {})
    hooks = config.setdefault('hooks', {})
    hooks['enabled'] = True
    events = hooks.setdefault('events', {})
    script = str(Path(script).resolve()).replace('\\', '/')
    interpreter = shutil.which(str(python))
    if interpreter is None:
        raise FrameworkError('无法定位用于客户端守卫的Python解释器')
    for event in EVENTS:
        groups = events.setdefault(event, [])
        # 只替换本守卫的同事件实例，其他hook保持原顺序与内容。
        for group in groups:
            group['hooks'] = [hook for hook in group.get('hooks', []) if script not in hook.get('args', [])]
        events[event] = groups = [group for group in groups if group.get('hooks')]
        groups.append({'hooks': [{'type': 'process', 'command': str(Path(interpreter).resolve()),
                     'args': ['-B', '-X', 'utf8', script, '--event', event], 'timeoutMs': 60000,
                     'statusMessage': 'AI-REOS: 本轮治理检索前置'}]})
    return config


def check_adapter(root):
    config = policy(root)
    result = {'enabled': config.get('enabled', False), 'errors': [],
              'coverage': '仅ZCode配置检查；运行时仍须新会话验证；未接入客户端无此保障'}
    if not result['enabled']:
        return result
    try:
        path = Path(root)/'.zcode/config.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        hooks = data.get('hooks', {})
        if not _inside(root, config['entry']).is_file():
            result['errors'].append('固定项目入口缺失')
        if hooks.get('enabled') is not True:
            result['errors'].append('ZCode hooks未启用')
        script = _inside(root, config['hook'])
        if not script.is_file():
            result['errors'].append('检索守卫脚本缺失')
        for event in EVENTS:
            found = False
            for group in hooks.get('events', {}).get(event, []):
                # 带matcher的条目不能覆盖所有工具/用户提示。
                if group.get('matcher'):
                    continue
                for hook in group.get('hooks', []):
                    args = hook.get('args', [])
                    command = Path(hook.get('command', ''))
                    python = (command.is_absolute() and command.is_file()
                              and re.fullmatch(r'python(?:\d+(?:\.\d+)*)?(?:\.exe)?', command.name, re.IGNORECASE))
                    if (hook.get('enabled', True) is not False and hook.get('type') == 'process'
                            and python
                            and str(script).replace('\\', '/') in args and args[-2:] == ['--event', event]
                            and hook.get('timeoutMs', 0) >= 10000):
                        found = True
            if not found:
                result['errors'].append('检索守卫未完整挂载事件：' + event)
        for name, expected in config.get('readonly_files', {}).items():
            if hashlib.sha256(_inside(root, name).read_bytes()).hexdigest() != expected:
                result['errors'].append('旧账本保护基线变化，须归档核对：' + name)
    except (OSError, ValueError, TypeError) as exc:
        result['errors'].append('客户端适配无法核对：' + str(exc))
    return result


def main(root=None, argv=None):
    """供项目薄包装调用；只读取本次事件，不加载写凭据。"""
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    if root is None:
        parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--event', required=True, choices=EVENTS)
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        code, output, error = handle(root if root is not None else args.project, args.event, payload)
    except (ValueError, OSError) as exc:
        if args.event == 'UserPromptSubmit' and os.environ.get('CLAUDE_SESSION_ID'):
            invalidate(root if root is not None else args.project, os.environ['CLAUDE_SESSION_ID'])
        code, output, error = 2, None, '客户端输入无法解析：' + str(exc)
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    if error:
        print(error, file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
