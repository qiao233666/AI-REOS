"""只调用框架明确列出的受管方法的 JSON 命令行入口。"""

import argparse
import json
import sys
from pathlib import Path

from .errors import FrameworkError
from .project import Project
from .storage import Store


READ_ONLY = {"get", "list", "events", "status", "check-receipt", "check-finalize", "trace", "views-status", "memory-query", "run-evidence", "task-checkpoint",
    "history-query", "history-get", "history-read", "session-context", "reading-usage", "task-attention", "event-feed",
    "client-context", "client-guard-status", "client-adapter-check", "execution-status", "execution-review-packet"}
EXECUTION_COMMANDS = {"execution-configure": "configure", "execution-begin": "begin",
    "execution-report": "report", "execution-decide": "decide"}
PROJECT_COMMANDS = {
    "request-decision": "request_decision",
    "resolve-decision": "resolve_decision",
    "checkpoint": "checkpoint",
    "create-task": "create_task",
    "prepare": "prepare",
    "start-task": "start_task",
    "add-artifact": "add_artifact",
    "add-item": "add_item",
    "revise-item": "revise_item",
    "submit": "submit",
    "review": "review",
    "begin-case-review": "begin_case_review",
    "adopt": "adopt",
    "challenge": "challenge",
    "assess-case": "assess_case",
    "resolve-case": "resolve_case",
    "pause-case": "pause_case",
    "propose-case-continuation": "propose_case_continuation",
    "assess-case-continuation": "assess_case_continuation",
    "authorize-case-continuation": "authorize_case_continuation",
    "close-task": "close_task",
    "annotate-stage": "annotate_stage",
    "stop-task": "stop_task",
}
COMMANDS = READ_ONLY | set(PROJECT_COMMANDS) | set(EXECUTION_COMMANDS) | {
    "execution-admit", "execution-invalidate", "execution-review",
    "init", "grant", "claim-output", "takeover-output", "release-output",
    "register-resource", "reserve-resource", "finish-reservation",
    "prepare-external-review", "run-external-review",
    "register-tool", "set-tool-enabled", "run-tool", "publish",
    "memory-configure", "deliver-batch", "record-reviews", "finalize-task", "restore-run",
    "history-configure", "import-history", "add-memory-note", "add-history-link", "prework-configure",
}


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)


def _json_out(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _load_input(path):
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入 JSON 须为参数对象")
    if "request_id" in value:
        raise ValueError("request_id 必须通过 CLI 参数提供，不能放在输入 JSON 中")
    return value


def _call(method, params, request_id=None):
    arguments = dict(params)
    if request_id is not None:
        arguments["request_id"] = request_id
    return method(**arguments)


def _dispatch(args, params):
    command = args.command
    if command == "init":
        if params:
            raise ValueError("init 不接受输入参数")
        return Store.initialize(args.project)

    if command in {"execution-admit", "execution-invalidate"} and (args.credential_file is not None or args.request_id is not None):
        raise ValueError("宿主执行缓存命令不接受正式写凭据或request-id")
    token = None
    if args.credential_file is not None:
        token = args.credential_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("凭据文件为空")
    store = Store(args.project, token)
    if command in {"execution-admit", "execution-invalidate"}:
        from .execution_control import ExecutionControl
        method = ExecutionControl(store).admit if command == "execution-admit" else ExecutionControl(store).invalidate
        return _call(method, params)
    if command == "grant":
        return _call(store.grant, params, args.request_id)

    if command in READ_ONLY:
        if args.request_id is not None:
            raise ValueError("只读命令不接受 --request-id")
        if command == "execution-status":
            from .execution_control import ExecutionControl
            return _call(ExecutionControl(store).status, params)
        if command == 'execution-review-packet':
            from .execution_review import ExecutionReview
            return _call(ExecutionReview(store).packet,params)
        if command in {'client-context','client-guard-status','client-adapter-check'}:
            from . import client_guard
            if command == 'client-context':
                return _call(lambda **values: client_guard.retrieve(args.project, **values), params)
            if command == 'client-guard-status':
                return _call(lambda **values: client_guard.status(args.project, **values), params)
            if params:
                raise ValueError('client-adapter-check不接受输入参数')
            return client_guard.check_adapter(args.project)
        if command == "get":
            return _call(store.get, params)
        if command == 'event-feed':
            return _call(store.event_feed, params)
        if command == "list":
            return _call(store.list, params)
        if command == "events":
            if params:
                raise ValueError("events 不接受输入参数")
            return store.events()
        if command=='reading-usage':
            from .reading import usage
            return usage(store,**params)
        if command in {"history-query", "history-get", "history-read", "session-context"}:
            from .history import HistoryArchive, session_context
            archive = HistoryArchive(store)
            method = {"history-query": archive.query, "history-get": archive.get, "history-read":archive.read,
                "session-context": lambda **values: session_context(store, **values)}[command]
            if command=='history-read':
                from .reading import record
                arguments=dict(params)
                arguments['max_chars']=max(1000,arguments.get('max_chars',10000)-180)
                return record(store,'session',_call(method,arguments))
            return _call(method, params)
        project = Project(args.project, token)
        if command == 'task-attention':
            return _call(project.task_attention, params)
        if command == 'task-checkpoint':
            return _call(project.task_checkpoint, params)
        if command == "status":
            return _call(project.status, params)
        if command == "check-receipt":
            return _call(project.check_receipt, params)
        if command == "check-finalize":
            return _call(project.check_finalize, params)
        if command == "memory-query":
            from .semantic import MemoryService
            return _call(MemoryService(store).query, params)
        if command == "run-evidence":
            from .run_evidence import RunEvidence
            return _call(RunEvidence(store).inspect, params)
        from .views import Views
        views = Views(store)
        if command == "trace":
            return _call(views.trace, params)
        return _call(views.status, params)

    if not args.request_id:
        raise ValueError("写操作必须通过 CLI 明确提供 --request-id")
    if command=='execution-review':
        from .execution_review import ExecutionReview
        return _call(ExecutionReview(store).record,params,args.request_id)
    if command in EXECUTION_COMMANDS:
        from .execution_control import ExecutionControl
        return _call(getattr(ExecutionControl(store), EXECUTION_COMMANDS[command]), params, args.request_id)
    project = Project(args.project, token)
    if command in {'import-history','add-memory-note','add-history-link','prework-configure'}:
        from .memory_catalog import MemoryCatalog
        catalog=MemoryCatalog(store)
        method={'import-history':catalog.import_history,'add-memory-note':catalog.add_note,
            'add-history-link':catalog.add_link,'prework-configure':catalog.configure}[command]
        return _call(method,params,args.request_id)
    if command == "history-configure":
        from .history import HistoryArchive
        return _call(HistoryArchive(store).configure, params, args.request_id)
    if command == "memory-configure":
        from .semantic import MemoryService
        return _call(MemoryService(store).configure, params, args.request_id)
    if command in {"deliver-batch", "record-reviews", "finalize-task"}:
        from .workflow import Workflow
        flow = Workflow(project)
        method = {"deliver-batch": flow.deliver, "record-reviews": flow.record_reviews, "finalize-task": flow.finalize}[command]
        return _call(method, params, args.request_id)
    if command in PROJECT_COMMANDS:
        project_methods = {
            "request-decision": project.request_decision,
            "resolve-decision": project.resolve_decision,
            "checkpoint": project.checkpoint,
            "create-task": project.create_task,
            "prepare": project.prepare,
            "start-task": project.start_task,
            "add-artifact": project.add_artifact,
            "add-item": project.add_item,
            "revise-item": project.revise_item,
            "submit": project.submit,
            "review": project.review,
            "begin-case-review": project.begin_case_review,
            "adopt": project.adopt,
            "challenge": project.challenge,
            "assess-case": project.assess_case,
            "resolve-case": project.resolve_case,
            "pause-case": project.pause_case,
            "propose-case-continuation": project.propose_case_continuation,
            "assess-case-continuation": project.assess_case_continuation,
            "authorize-case-continuation": project.authorize_case_continuation,
            "close-task": project.close_task,
            "annotate-stage": project.annotate_stage,
            "stop-task": project.stop_task,
        }
        return _call(project_methods[command], params, args.request_id)

    from .coordination import Coordinator
    if command in {"claim-output", "takeover-output", "release-output", "register-resource",
                   "reserve-resource", "finish-reservation"}:
        coordinator = Coordinator(store)
        method = {
            "claim-output": coordinator.claim,
            "takeover-output": coordinator.takeover,
            "release-output": coordinator.release,
            "register-resource": coordinator.register_resource,
            "reserve-resource": coordinator.reserve,
            "finish-reservation": coordinator.finish_reservation,
        }[command]
        return _call(method, params, args.request_id)

    if command in {"prepare-external-review", "run-external-review"}:
        if "runner" in params:
            raise ValueError("runner是内部测试钩子，不能通过CLI指定")
        from .external_review import ExternalReview
        review = ExternalReview(store)
        method = {"prepare-external-review": review.prepare,
                  "run-external-review": review.run}[command]
        return _call(method, params, args.request_id)

    if command in {"register-tool", "set-tool-enabled", "run-tool", "restore-run"}:
        from .tools import ToolRegistry
        registry = ToolRegistry(store)
        method = {"register-tool": registry.register, "set-tool-enabled": registry.set_enabled,
                  "run-tool": registry.run, "restore-run": registry.restore}[command]
        return _call(method, params, args.request_id)

    if command == "publish":
        from .views import Views
        return _call(Views(store).publish, params, args.request_id)
    raise ValueError("未知命令")


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--request-id")
    args = parser.parse_args(argv)
    try:
        params = _load_input(args.input)
        if args.command in READ_ONLY and "request_id" in params:
            raise ValueError("只读命令不接受 request_id")
        _json_out(_dispatch(args, params))
        return 0
    except (FrameworkError, ValueError, TypeError, OSError, ImportError) as exc:
        _json_out_error(str(exc))
        return 2


def _json_out_error(message):
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)

