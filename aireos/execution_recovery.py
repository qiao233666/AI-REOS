"""从正式记录派生有限恢复文件；不保存另一套任务或许可状态。"""
import hashlib
import json
from pathlib import Path
import re
import stat

from . import inputs, recall
from .errors import FrameworkError
from .execution_control import ExecutionControl
from .project import Project, typed
from .storage import Store, Transaction, within


MAX_BYTES = 64 * 1024
SCHEMAS = {
    'execution-materials': ({'segment_id'}, {'max_bytes'}),
    'execution-status': ({'task_id', 'session_id'}, set()),
    'execution-configure': ({'task_id', 'original_item_id', 'evidence_rules', 'max_tools', 'max_seconds'}, set()),
    'execution-begin': ({'task_id', 'session_id', 'criteria', 'purpose', 'expected'}, set()),
    'execution-report': ({'segment_id', 'artifact_id'}, set()),
    'execution-decide': ({'segment_id', 'review_id', 'decision', 'reason'}, set()),
    'add-artifact': ({'task_id', 'path', 'title'}, set()),
    'review': ({'item_id', 'verdict', 'method', 'checks', 'expected_revision'}, {'attempt_id','provenance'}),
    'checkpoint': ({'task_id', 'progress', 'next_step', 'expected_revision'}, {'pending', 'references'}),
    'stop-task': ({'task_id','summary'}, set()),
    'prepare-external-review': ({'task_id','materials','question','model','effort','authorization'}, {'case_id'}),
    'run-external-review': ({'package_id','executable'}, {'timeout','attempt_id'}),
    'request-decision': ({'task_id', 'key', 'question'}, {'context'}),
    'resolve-decision': ({'decision_id', 'expected_revision', 'answer'}, {'resolution'}),
    'task-attention': ({'task_id'}, {'max_chars', 'offset', 'expected_snapshot'}),
    'get': ({'identifier'}, {'revision'}),
}


def input_path(root, session_id, command):
    if command not in SCHEMAS or not isinstance(session_id, str) or not session_id.strip():
        raise FrameworkError('恢复路径须有准确会话和明确治理命令')
    key = hashlib.sha256(session_id.encode('utf-8')).hexdigest()
    return Path(root).resolve() / '.aireos/cache' / ('execution-input-' + key + '-' + command + '.json')


def plain_target(root, path):
    """在解析前检查各层链接，避免受限JSON写入替换治理库或科学文件。"""
    root, path = Path(root).resolve(), Path(path).absolute()
    if '..' in path.parts or (path != root and root not in path.parents):
        raise FrameworkError('恢复文件必须位于准确项目根内')
    for part in [*reversed(path.parents), path]:
        if part != root and root not in part.parents:
            continue
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise FrameworkError('恢复路径不能包含链接或重解析点')
        if part == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise FrameworkError('恢复目标须为普通单链接文件')
    return path.resolve()


def decode(content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_BYTES:
        raise FrameworkError('恢复JSON须为不超过64KiB的UTF-8文本')
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise FrameworkError('恢复JSON不得含重复字段')
            result[key] = value
        return result
    body = json.loads(content, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(FrameworkError('JSON常量不合法')))
    if not isinstance(body, dict):
        raise FrameworkError('恢复JSON必须为明确参数对象')
    return body


def _fields(body, required, optional=()):
    if not isinstance(body, dict) or not required <= set(body) or not set(body) <= required | set(optional):
        raise FrameworkError('恢复JSON缺少必需字段或含未允许字段')


def _text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise FrameworkError('恢复参数须为有限的非空文本')


def _number(value, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise FrameworkError('恢复参数版本或预算须为正确的整数')


def _task_for(tx, body):
    if 'task_id' in body:
        return typed(tx, body['task_id'], 'task'), None
    if 'segment_id' in body:
        segment = typed(tx, body['segment_id'], 'execution_segment')
        return typed(tx, segment['data']['task'], 'task'), segment
    if 'package_id' in body:
        package=typed(tx,body['package_id'],'external_package')
        return typed(tx,package['data']['task'],'task'),None
    key = 'item_id' if 'item_id' in body else ('decision_id' if 'decision_id' in body else 'identifier')
    obj = tx.get(body[key])
    if obj['kind'] == 'task':
        return obj, None
    if obj['kind'] == 'review':
        obj = typed(tx, obj['data']['target']['id'], 'item')
    if obj['kind'] not in {'item', 'decision_request', 'execution_policy', 'execution_segment'}:
        raise FrameworkError('恢复读取只允许任务及其明确关联的治理记录')
    return typed(tx, obj['data']['task'], 'task'), obj if obj['kind'] == 'execution_segment' else None


def validate_input(root, session_id, command, body):
    """仅验证恢复文件，不借用模型凭据执行任何正式写动作。"""
    if command not in SCHEMAS:
        raise FrameworkError('命令不在有限恢复白名单')
    _fields(body, *SCHEMAS[command])
    store = Store(root)
    control = ExecutionControl(store)
    with store.connection() as connection:
        tx = Transaction(store, connection, {'scopes': ['.']})
        task, segment = _task_for(tx, body)
        Project._scope_task(tx, task['id'])
        stages = control._segments(tx, task['id'])
        latest = stages[-1] if stages else None
        review_commands = {'execution-materials', 'execution-decide', 'review',
                           'get', 'task-attention', 'resolve-decision','prepare-external-review','run-external-review'}
        if segment and command != 'get' and (not latest or latest['id'] != segment['id'] or
                        (segment['data']['session_id'] != session_id and command not in review_commands)):
            raise FrameworkError('恢复输入不属于当前会话的最新执行段')
        readonly = command in {'execution-status', 'task-attention', 'get', 'execution-materials'}
        if not readonly and task['data']['status'] not in {'running', 'delivered'}:
            raise FrameworkError('恢复写入须对应已有可交付的正式任务')
        if not readonly and latest and latest['data']['session_id'] != session_id and command not in review_commands:
            if command != 'execution-begin' or latest['data']['status'] != 'continued':
                raise FrameworkError('不能通过恢复通道操作其他会话的任务')
        if command in {'execution-status', 'execution-begin'} and body['session_id'] != session_id:
            raise FrameworkError('恢复输入的session_id与宿主当前会话不符')
        if command == 'execution-materials':
            if 'max_bytes' in body and (type(body['max_bytes']) is not int or not 1024 <= body['max_bytes'] <= 4194304):
                raise FrameworkError('材料包显示预算不合法')
        elif command == 'execution-configure':
            if stages or any(p['data']['task'] == task['id'] for p in tx.list('execution_policy')):
                raise FrameworkError('已有执行策略，不能经恢复通道重设原件或预算')
            control._ref(tx, recall.reference(typed(tx, body['original_item_id'], 'item')), artifact=True)
            _number(body['max_tools']); _number(body['max_seconds'])
            names = control._criteria(task)
            rules = body['evidence_rules']
            if not isinstance(rules, list) or len(rules) != len(names):
                raise FrameworkError('策略必须完整覆盖全部原验收项')
            keys = []
            for rule in rules:
                _fields(rule, {'criterion', 'required_classes'})
                _text(rule['criterion']); keys.append(rule['criterion'])
                classes = rule['required_classes']
                if not isinstance(classes, list) or not classes or len(classes) > 16:
                    raise FrameworkError('证据类别须为有限非空数组')
                for value in classes: _text(value, 100)
                if len(set(classes)) != len(classes): raise FrameworkError('证据类别不能重复')
            if set(keys) != set(names) or len(set(keys)) != len(keys):
                raise FrameworkError('不能删除或增加原验收编号')
        elif command == 'execution-begin':
            control._base(tx, task['id'], control._policy(tx, task['id']))
            control._no_pending(tx, task['id'])
            if latest and latest['data']['status'] != 'continued':
                raise FrameworkError('上一段尚未独立放行，恢复输入不能续期')
            criteria = body['criteria']
            if not isinstance(criteria, list) or not criteria or not all(isinstance(c, str) for c in criteria):
                raise FrameworkError('阶段须明确有限验收范围')
            if len(set(criteria)) != len(criteria) or not set(criteria) <= set(control._criteria(task)):
                raise FrameworkError('阶段验收范围不符原合同')
            _text(body['purpose']); _text(body['expected'])
        elif command == 'execution-report':
            policy = control._policy(tx, task['id'])
            control._base(tx, task['id'], policy,archival=True)
            if segment['data']['status'] not in {'active', 'submitted', 'hold'}:
                raise FrameworkError('该段不能重报')
            control._report_body(tx, segment, task, policy, typed(tx, body['artifact_id'], 'item'))
        elif command == 'execution-decide':
            _text(body['reason'])
            if body['decision'] not in {'continue', 'hold', 'completed'}:
                raise FrameworkError('裁定值不合法')
            if segment['data']['status'] != 'submitted': raise FrameworkError('须先提交报告')
            control._review(tx, segment, body['review_id'], body['decision'])
        elif command == 'add-artifact':
            _text(body['title'])
            relative = inputs.canonical_path(store.root, body['path'])
            if any(part.startswith('.') for part in Path(relative).parts) or not any(
                    within(relative, output) for output in task['data']['contract']['outputs']):
                raise FrameworkError('只能登记正式任务输出内的普通产物，cache不能作为artifact')
            plain_target(root, store.root / relative)
            if not (store.root / relative).is_file(): raise FrameworkError('待登记产物不存在')
        elif command == 'review':
            _number(body['expected_revision']); _text(body['method'])
            if body['verdict'] not in {'pass', 'fail', 'limit', 'unknown'}: raise FrameworkError('审核值不合法')
            if not isinstance(body['checks'], list) or not 1 <= len(body['checks']) <= 64:
                raise FrameworkError('审核须有有限检查记录')
            for entry in body['checks']: _text(entry)
            item = typed(tx, body['item_id'], 'item')
            if item['rev'] != body['expected_revision']: raise FrameworkError('审核版本不符')
        elif command == 'checkpoint':
            _text(body['progress'], 1500); _text(body['next_step'], 1500)
            _number(body['expected_revision'], zero=True)
            if 'pending' in body and (not isinstance(body['pending'], str) or len(body['pending']) > 1500):
                raise FrameworkError('未决说明须为有限文本')
            if 'references' in body and (not isinstance(body['references'], list) or len(body['references']) > 8):
                raise FrameworkError('接续引用须为最多8项的数组')
        elif command == 'stop-task':
            _text(body['summary'],500)
        elif command == 'prepare-external-review':
            for field in ('question','model','effort','authorization'):_text(body[field])
            if not isinstance(body['materials'],list) or not 1<=len(body['materials'])<=128:
                raise FrameworkError('审核材料须为有限清单')
            for path in body['materials']:
                relative=inputs.canonical_path(store.root,path)
                if any(part.startswith('.') for part in Path(relative).parts):
                    raise FrameworkError('审核材料不能读取隐藏控制目录')
        elif command == 'run-external-review':
            _text(body['executable'])
            if 'timeout' in body and (type(body['timeout']) not in {int,float} or not 0<body['timeout']<=3600):
                raise FrameworkError('兼容外审执行参数不合法')
        elif command == 'request-decision':
            _text(body['key'], 100); _text(body['question'], 2000)
            if 'context' in body and (not isinstance(body['context'], str) or len(body['context']) > 4000):
                raise FrameworkError('待决背景须为有限文本')
        elif command == 'resolve-decision':
            _number(body['expected_revision']); _text(body['answer'])
            decision = typed(tx, body['decision_id'], 'decision_request')
            if decision['rev'] != body['expected_revision']: raise FrameworkError('待决版本不符')
            if body.get('resolution', 'answered') not in {'answered', 'withdrawn'}:
                raise FrameworkError('待决处理值不合法')
        elif command == 'get' and 'revision' in body:
            _number(body['revision'])
        elif command == 'task-attention':
            if 'max_chars' in body and (type(body['max_chars']) is not int or not 1000 <= body['max_chars'] <= 50000):
                raise FrameworkError('显示预算不合法')
            if 'offset' in body: _number(body['offset'], zero=True)
        return dict(task_id=task['id'], segment_id=segment['id'] if segment else None)


def _report_directory(root, task, segment, session_id):
    root = Path(root).resolve()
    key = hashlib.sha256(session_id.encode('utf-8')).hexdigest()
    for output in task['data']['contract']['outputs']:
        directory = root / output
        if (directory.is_dir() or (not directory.exists() and not directory.suffix)) and not any(
                part.startswith('.') and part != '.' for part in Path(output).parts):
            return directory / 'execution-reports' / key / segment['id']
    raise FrameworkError('原任务须有可容纳报告的输出目录；不能占用计划中的科学文件')


def describe_paths(root, task, segment, session_id):
    result = dict(input_paths={command: str(input_path(root, session_id, command)) for command in SCHEMAS},
                  note='这些路径只允许有限治理JSON，不授予执行、独立审核或科学采纳资格。')
    if segment and segment['data']['session_id'] == session_id:
        try:
            directory = _report_directory(root, task, segment, session_id)
            result['report_path'] = str(directory / ('report-r' + str(segment['rev']) + '-0.json'))
            result['report_drafts'] = '同目录report-r' + str(segment['rev']) + '-0.json至-63.json；只可新建'
        except FrameworkError as exc:
            result['report_unavailable'] = str(exc)
    return result


def report_path(root, session_id, body):
    """从当前正式段和原合同目录派生唯一报告路径，同时验证全部原验收。"""
    _fields(body, {'schema', 'segment_id', 'contract_hash', 'criteria'})
    store = Store(root)
    control = ExecutionControl(store)
    with store.connection() as connection:
        tx = Transaction(store, connection, {'scopes': ['.']})
        segment = typed(tx, body['segment_id'], 'execution_segment')
        data = segment['data']
        if data['session_id'] != session_id or data['status'] not in {'active', 'submitted', 'hold'}:
            raise FrameworkError('恢复报告须对应本会话可报告的当前段')
        stages = control._segments(tx, data['task'])
        if stages[-1]['id'] != segment['id']: raise FrameworkError('旧段不能写恢复报告')
        policy = control._policy(tx, data['task'])
        task = control._base(tx, data['task'], policy,archival=True)
        for entry in body.get('criteria', []):
            _fields(entry, {'criterion', 'state', 'evidence', 'remaining'})
            if not isinstance(entry['remaining'], str) or len(entry['remaining']) > 4000:
                raise FrameworkError('报告剩余缺口须为有限文本')
            if not isinstance(entry['evidence'], list) or len(entry['evidence']) > 128:
                raise FrameworkError('报告证据须为有限数组')
            for ref in entry['evidence']: _fields(ref, {'id', 'rev', 'class'})
        control._report_content(tx, segment, task, policy, body)
        path = _report_directory(root, task, segment, session_id) / ('report-r' + str(segment['rev']) + '-0.json')
        return plain_target(root, path)


def write_allowed(root, session_id, path, content):
    """只允许有限治理参数或新报告，不能修改已登记报告及任意科学输出。"""
    path = plain_target(root, path)
    body = decode(content)
    for command in SCHEMAS:
        if path == input_path(root, session_id, command):
            validate_input(root, session_id, command, body)
            return True
    expected = report_path(root, session_id, body)
    pattern = re.escape(expected.stem[:-1]) + r'(?:[0-9]|[1-5][0-9]|6[0-3])\.json'
    if path.parent == expected.parent and re.fullmatch(pattern, path.name):
        if path.exists(): raise FrameworkError('已有报告不可覆写；修订须新建另一个有限草稿路径')
        return True
    return False


def command_input(root, session_id, command, path):
    path = plain_target(root, path)
    if path != input_path(root, session_id, command) or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise FrameworkError('治理豁免须读取当前会话的派生恢复JSON')
    return validate_input(root, session_id, command, decode(path.read_text(encoding='utf-8')))
