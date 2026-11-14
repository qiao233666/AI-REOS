"""安装到项目aiops/checks/hooks/；通过项目固定入口加载经过锁校验的框架。"""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys


def main():
    root = Path(__file__).resolve().parents[3]
    # 新轮先用标准库撤销运行缓存，再加载框架；锁/入口坏掉也不能复活上一轮。
    raw = sys.stdin.read()
    payload = None
    try:
        payload = json.loads(raw)
    finally:
        event = sys.argv[sys.argv.index('--event')+1] if '--event' in sys.argv else None
        session = (payload.get('session_id') if isinstance(payload,dict) else None) or os.environ.get('CLAUDE_SESSION_ID')
        cache = root/'.aireos/cache/client-guard.sqlite3'
        if event in {'UserPromptSubmit','SessionStart'} and isinstance(session,str) and session and cache.is_file():
            connection=sqlite3.connect(cache,timeout=10)
            try:
                with connection:
                    connection.execute('DELETE FROM sessions WHERE session=?',(hashlib.sha256(session.encode('utf-8')).hexdigest(),))
            finally:
                connection.close()
    sys.stdin = io.StringIO(raw)
    path = root/'aiops/aireos/run.py'
    spec = importlib.util.spec_from_file_location('project_aireos_entry', path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    entry.load_framework()
    from aireos.client_guard import main as guard_main
    return guard_main(root)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('检索守卫加载失败；请核对版本锁及固定入口：'+str(exc), file=sys.stderr)
        raise SystemExit(2)
