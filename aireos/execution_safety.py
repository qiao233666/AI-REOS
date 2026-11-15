"""执行控制复用既有质量状态和任务范围；不把同用户shell当沙箱。"""
from pathlib import Path
import os
from .errors import FrameworkError, PermissionDenied
from .project import Project
from .storage import within


CONTROL = {'.aireos', '.git', '.zcode', '.codex', '.claude', '.opencode', 'vendor'}
CONTROL_FILES = {'agents.md', 'aiops/charter.md', 'aiops/policy.yaml'}


def _relative(base,target):
    try:
        relative=target.resolve().relative_to(base).as_posix()
    except ValueError:
        raise PermissionDenied('工具路径越出正式项目根') from None
    parts=[p.casefold() for p in Path(relative).parts]
    if any(p in CONTROL for p in parts) or relative.casefold() in CONTROL_FILES or relative.casefold().startswith('aiops/aireos/'):
        raise PermissionDenied('普通执行不能读写控制目录、运行配置或凭据')
    return relative


def _search_tree(base,target):
    """宿主未提供受信排除器时，搜索范围不能覆盖控制后代；只查名称，不读正文。"""
    pending=[target];seen=set();count=0
    while pending:
        current=pending.pop();resolved=current.resolve()
        if resolved in seen:continue
        seen.add(resolved);_relative(base,current)
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    count+=1
                    if count>10000:
                        raise PermissionDenied('递归范围无法在有限检查内排除控制路径；请指定更小目录或准确文件')
                    child=Path(entry.path);_relative(base,child)
                    if entry.is_dir(follow_symlinks=True):pending.append(child)
        except OSError:
            raise PermissionDenied('递归范围无法完整核对；请指定可读子目录或准确文件') from None


def tool_scope(root, task, name, values):
    """只有宿主直接执行的文件工具可检查路径；任意代码执行须真实隔离。"""
    action = name.lower()
    if action in {'bash', 'shell', 'powershell', 'terminal', 'exec', 'execute', 'python',
                  'applypatch', 'apply_patch', 'computer', 'computer_use'}:
        raise PermissionDenied('受管执行拒绝未隔离的shell、代码或无法准确定位的写入；须使用已验证隔离的原运行渠道')
    writes = {'write', 'edit', 'multiedit', 'delete', 'move', 'rename'}
    reads = {'read', 'glob', 'grep', 'list'}
    if action not in writes | reads:
        raise PermissionDenied('工具尚未接入准确范围检查，不能静默按普通文件工具放行')
    paths = []
    for key in ('file_path', 'path'):
        if isinstance(values.get(key), str):
            paths.append(values[key])
    if isinstance(values.get('resources'), list):
        paths.extend(values['resources'])
    base = Path(root).resolve()
    if not paths:
        if action in {'grep','glob','list'}:paths=[str(base)]
        else:raise PermissionDenied('工具必须提供宿主实际使用的准确路径')
    for raw in paths:
        target = Path(raw)
        target = target if target.is_absolute() else base / target
        relative=_relative(base,target)
        if action in {'grep','glob','list'} and target.is_dir():_search_tree(base,target)
        if action in writes and not any(within(relative, out) for out in task['data']['contract']['outputs']):
            raise PermissionDenied('写入不在正式任务输出范围')


def negative_basis(tx, item, visited=None):
    """未审候选可检查；已明确否定、撤回、争议或上游失效不能作完成依据。"""
    visited = set() if visited is None else visited
    if item['id'] in visited or len(visited) >= 200:
        raise FrameworkError('证据依赖循环或超出核对范围')
    visited.add(item['id'])
    state = Project._status(tx, item['id'])
    if state['status'] in {'needs_review', 'withdrawn'}:
        raise FrameworkError('证据已有否定、撤回、争议或来源失效: ' + item['id'])
    for ref in item['data'].get('supports', []):
        negative_basis(tx, tx.get(ref['id']), set(visited))
