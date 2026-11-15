"""只读核对ZCode已有工具终态；覆盖权限拒绝未触发后置hook的原生分支。"""
from pathlib import Path
from datetime import datetime,timezone
import math
import sqlite3
import stat

from .errors import FrameworkError


def native_path():
    """固定宿主路径，不接受模型提示、hook输入或项目输出指定另一份数据库。"""
    return Path.home()/'.zcode/cli/db/db.sqlite'


def _connection(path):
    path=Path(path).absolute()
    for parent in [*reversed(path.parents),path]:
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise FrameworkError('原生工具状态路径不能包含链接或重解析点')
    if not path.is_file():
        raise FrameworkError('原生工具状态库不是普通文件')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def reconcile(control,session_id,path=None):
    """只有原生准确身份、明确终态及完成时间同时存在才结束已跟踪工具。"""
    with control._cache() as cache:
        rows=cache.execute("SELECT event,segment FROM inflight WHERE session=? AND outcome='running'",
                           (session_id,)).fetchall()
        # 异步子代理的生命周期由原队列负责，不能拿同名文件或原生表行清理。
        pending=[dict(row) for row in rows if not row['event'].startswith('child:')]
    if not pending:
        return {'source':'zcode-native-tool-usage','finished':0,'unresolved':0}
    observations=[]
    try:
        db=_connection(path if path is not None else native_path())
        try:
            columns={row['name'] for row in db.execute('PRAGMA table_info(tool_usage)')}
            required={'session_id','tool_call_id','status','completed_at','approval_status'}
            if not required<=columns:
                raise FrameworkError('原生工具表缺少已核验的身份或终态字段')
            for work in pending:
                records=db.execute('SELECT status,completed_at,approval_status FROM tool_usage '
                    'WHERE session_id=? AND tool_call_id=? LIMIT 2',(session_id,work['event'])).fetchall()
                if len(records)!=1:
                    continue
                row=records[0];stamp=row['completed_at']
                if (row['status'] not in {'completed','error','cancelled'}
                        or not isinstance(stamp,(int,float)) or isinstance(stamp,bool)
                        or not math.isfinite(stamp) or stamp<=0):
                    continue
                segment=control.store.get(work['segment'])
                if (segment['kind']!='execution_segment'
                        or segment['data']['session_id']!=session_id):
                    raise FrameworkError('在途工具的正式执行段或会话不匹配')
                start=datetime.fromisoformat(segment['data']['started_at'])
                if start.tzinfo is None:
                    raise FrameworkError('正式执行段缺少带时区的开始时间')
                # JS Date.getTime是整数毫秒；秒级、旧段、未来或小数不能冒充本段终态。
                floor=int(start.timestamp()*1000)
                ceiling=int(datetime.now(timezone.utc).timestamp()*1000)+1000
                if not float(stamp).is_integer() or not floor<=stamp<=ceiling:
                    continue
                if row['status']=='completed' and row['approval_status']=='denied':
                    raise FrameworkError('原生工具完成状态与拒绝状态矛盾，保留在途等待核查')
                observations.append((work,'completed' if row['status']=='completed' else 'failed'))
        finally:
            db.close()
    except (OSError,sqlite3.Error) as exc:
        raise FrameworkError('无法只读核对原生工具终态；保留在途，不猜测完成: '+type(exc).__name__) from None
    # 先读完同一快照；不边读取不兼容数据边清理其他工具。
    for work,outcome in observations:
        control.finish(session_id,work['segment'],work['event'],outcome)
    return {'source':'zcode-native-tool-usage','finished':len(observations),
            'unresolved':len(pending)-len(observations)}


def check_native_store():
    """只读检查安装接口，不扫描或输出真实会话、工具参数和凭据。"""
    db=None
    try:
        db=_connection(native_path())
        columns={row['name'] for row in db.execute('PRAGMA table_info(tool_usage)')}
        required={'session_id','tool_call_id','status','completed_at','approval_status'}
        if not required<=columns:
            raise FrameworkError('原生工具表字段不兼容')
        return {'available':True,'source':'zcode-native-tool-usage',
                'coverage':'原生工具表接口可读；实际会话加载、时序和模型整链仍须验收'}
    except (OSError,sqlite3.Error,FrameworkError) as exc:
        return {'available':False,'reason':type(exc).__name__+': '+str(exc)}
    finally:
        if db is not None:db.close()
