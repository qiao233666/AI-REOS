"""原登记工具、原生一次许可和既有Codex Windows隔离后端的薄衔接。"""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from . import recall
from .errors import FrameworkError,PermissionDenied,StaleInput
from .execution_control import ExecutionControl
from .execution_recovery import plain_target
from .project import Project,typed
from .storage import Store,Transaction,digest
from .tools import ToolRegistry


def configuration(root,task_id):
    root=Path(root).resolve();path=root/'aiops/aireos/retrieval-policy.json'
    plain_target(root,path);raw=path.read_bytes()
    if len(raw)>65536:raise FrameworkError('脚本运行策略过大')
    policy=json.loads(raw)
    execution=policy.get('execution',{})
    if policy.get('enabled') is not True or not isinstance(execution,dict) or execution.get('enabled') is not True:
        raise PermissionDenied('受管执行未启用，不能独立放开脚本通道')
    config=execution.get('script_runtime',{})
    if not isinstance(config,dict) or config.get('enabled') is not True:
        raise PermissionDenied('项目尚未显式启用登记脚本隔离运行')
    if config.get('backend')!='elevated' or type(config.get('max_seconds')) is not int or not 1<=config['max_seconds']<=600:
        raise FrameworkError('本机脚本只使用已验证后端，墙钟不超过600秒')
    executable=Path(config.get('executable',''))
    if not executable.is_absolute() or not executable.is_file() or executable.name.casefold()!='codex.exe':
        raise FrameworkError('隔离后端必须为受保护策略固定的Codex绝对路径')
    if hashlib.sha256(executable.read_bytes()).hexdigest()!=config.get('sha256'):
        raise StaleInput('隔离后端字节版本变化，不能静默更新')
    binding=config.get('task_bindings',{}).get(task_id)
    if not isinstance(binding,dict) or set(binding)!={'tools','receipt_id','credential_file'} or not isinstance(binding['tools'],list):
        raise FrameworkError('登记脚本须有正式任务、准确工具与执行凭据绑定')
    credential=root/binding['credential_file']
    plain_target(root,credential)
    if credential.resolve().parent!=root/'.aireos/keys':
        raise PermissionDenied('脚本凭据只能来自原受管keys目录，不能借用owner')
    return config,binding,hashlib.sha256(raw).hexdigest()


def script_scope(tx,task,values):
    """由原PermissionBroker在once之前调用；模型不能选择后端、密钥或输出根。"""
    config,binding,checksum=configuration(tx.store.root,task['id'])
    metadata=values.get('metadata',{})
    if not isinstance(metadata,dict) or set(metadata)!={'registered_tool_id','arguments','runtime_sha256'}:
        raise FrameworkError('登记脚本许可缺少准确受管参数')
    identifier=metadata['registered_tool_id'];arguments=metadata['arguments']
    if (values.get('resources')!=[identifier] or not isinstance(arguments,list)
            or not all(isinstance(v,str) and '\x00' not in v for v in arguments)
            or len(json.dumps(arguments,ensure_ascii=False).encode('utf-8'))>32768):
        raise FrameworkError('登记脚本资源或参数不符')
    if metadata['runtime_sha256']!=checksum:
        raise StaleInput('脚本运行策略在原生工具创建后变化')
    tool=ToolRegistry(tx.store)._validate(tx,identifier)
    if recall.reference(tool) not in binding['tools']:
        raise PermissionDenied('脚本不在该正式任务准确获准工具集合')
    Project._fresh(tx,binding['receipt_id'],task['id'])
    for output in task['data']['contract']['outputs']:
        target=tx.store.root/output;plain_target(tx.store.root,target,directory=True)
        if not target.is_dir():raise PermissionDenied('隔离脚本输出须为已存在的正式目录')
    return config,binding,checksum


def _toml(value):
    if isinstance(value,dict):return '{'+', '.join(json.dumps(k)+' = '+_toml(v) for k,v in value.items())+'}'
    return json.dumps(value)


class SandboxRunner:
    def __init__(self,store,control,formal,native,event,config,checksum,outputs):
        self.store,self.control=store,control
        self.formal,self.native,self.event=formal,native,event
        self.config,self.outputs=config,outputs
        self.description={'backend':'codex-windows-elevated','executable_sha256':config['sha256'],
            'configuration_sha256':checksum,'formal':formal,'native':native,'permission_event_id':event,
            'network':False,'outputs':outputs,
            'read_limit':'根目录可读，治理及已知鉴权目录显式拒绝；非严格输入读取白名单'}

    def __call__(self,command,*,cwd,stdout,stderr,timeout,shell):
        root=self.store.root
        runs=[r for r in self.store.list('run') if root/r['data']['directory']==Path(cwd)]
        if len(runs)!=1:raise FrameworkError('隔离进程必须对应已登记的唯一run')
        run=runs[0];event='child:script:'+run['id']
        deadline=self.store.get(self.formal['segment_id'])['data']['deadline']
        remaining=(datetime.fromisoformat(deadline)-datetime.now(timezone.utc)).total_seconds()
        if remaining<=0:raise PermissionDenied('执行段在实际脚本启动前已到期')
        actual_timeout=min(timeout,remaining)
        source=Path(command[2]).read_bytes();checksum=hashlib.sha256(source).hexdigest()
        staging=root/'.aireos-script-inputs';plain_target(root,staging,directory=True);staging.mkdir(exist_ok=True)
        entry=staging/(checksum+'.py');plain_target(root,entry)
        if entry.exists():
            if entry.read_bytes()!=source:raise StaleInput('隔离入口缓存字节变化')
        else:
            with entry.open('xb') as stream:stream.write(source)
        permissions={':root':'read',str(staging):'read'}
        for output in self.outputs:permissions[str(root/output)]='write'
        # 控制目录deny优先于任何更宽输出；脚本缓存无凭据且强制只读。
        for name in ['.aireos','.git','.zcode','.codex','.claude','.opencode','vendor','aiops/aireos']:
            target=root/name
            if target.exists():permissions[str(target)]='deny'
        permissions[str(staging)]='read'
        for name in ['.codex','.zcode','.ssh','.aws','.azure','.config/opencode']:
            target=Path.home()/name
            if target.exists():
                if root==target or target in root.parents:raise PermissionDenied('项目不能置于被拒绝的用户鉴权目录内')
                permissions[str(target)]='deny'
        profile={'filesystem':permissions,'network':{'enabled':False}}
        argv=[self.config['executable'],'sandbox','--include-managed-config','-P','aireos-registered-script',
            '-c','windows.sandbox="elevated"','-c','permissions.aireos-registered-script='+_toml(profile),
            '-C',str(root/self.outputs[0]),'--',sys.executable,'-I','-B',str(entry),*command[3:]]
        env={k:v for k,v in os.environ.items() if k.upper() in
            {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','SYSTEMDRIVE','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES'}}
        env.update(CODEX_HOME=str(Path.home()/'.codex'),PYTHONDONTWRITEBYTECODE='1',
            OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
        self.control.start_work(self.formal['session_id'],self.formal['segment_id'],event,admitted_event_id=self.event)
        (Path(cwd)/'isolated-command.json').write_text(json.dumps({'argv':argv,'runtime':self.description,
            'actual_timeout_seconds':actual_timeout},ensure_ascii=False,indent=2),encoding='utf-8')
        # 超时只知主进程被终止；既有run记unknown，物理子工作继续保留在途，不猜测清理。
        result=subprocess.run(argv,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=actual_timeout,shell=False)
        self.control.finish(self.formal['session_id'],self.formal['segment_id'],event,
                            'completed' if result.returncode==0 else 'failed')
        return result


def execute_registered(root,payload):
    if not isinstance(payload,dict) or set(payload)!={'formal','native','tool_id','arguments'}:
        raise FrameworkError('脚本桥只接受固定受管输入，不接受路径或凭据覆盖')
    formal,native=payload['formal'],payload['native']
    if not isinstance(formal,dict) or set(formal)!={'task_id','session_id','segment_id'} or not isinstance(native,dict) or set(native)!={'session_id','tool_id','message_id'}:
        raise FrameworkError('脚本桥缺少正式及真实原生身份')
    if not all(isinstance(v,str) and v.strip() and len(v)<=200 for v in [*formal.values(),*native.values()]):
        raise FrameworkError('脚本桥身份无效')
    control=ExecutionControl(Store(root));config,binding,checksum=configuration(root,formal['task_id'])
    values={'resources':[payload['tool_id']], 'metadata':{'registered_tool_id':payload['tool_id'],
        'arguments':payload['arguments'],'runtime_sha256':checksum},
        'source':{'type':'tool','id':native['tool_id'],'messageID':native['message_id']}}
    expected=digest(dict(session_id=formal['session_id'],segment_id=formal['segment_id'],
                         tool_name='aireos_run_tool',tool_input=values))
    with control._cache() as cache:
        rows=cache.execute("SELECT i.event FROM inflight i JOIN events e ON e.event=i.event WHERE i.session=? AND i.segment=? AND i.outcome='running' AND e.fingerprint=?",
            (formal['session_id'],formal['segment_id'],expected)).fetchall()
        matches=[r['event'] for r in rows if r['event'].startswith(native['session_id']+':')]
    if len(matches)!=1:raise PermissionDenied('没有与真实原生调用匹配的在途一次许可')
    event=matches[0]
    proof=control.admit(formal['session_id'],'aireos_run_tool',values,event,segment_id=formal['segment_id'])
    if not proof.get('allowed') or not proof.get('replayed'):raise PermissionDenied('原生脚本许可已失效')
    raw_key=(Path(root)/binding['credential_file']).read_bytes()
    if len(raw_key)>4096:raise PermissionDenied('执行凭据体积异常')
    token=raw_key.decode('utf-8').strip()
    store=Store(root,token)
    with store.connection() as connection:
        principal=store._principal(connection,'execute')
        if set(principal['capabilities'])!={'execute'}:raise PermissionDenied('脚本桥只持有execute能力，不借用owner或review')
        task=Project._scope_task(Transaction(store,connection,principal),formal['task_id'])
    segment=store.get(formal['segment_id'])
    if segment['data']['task']!=task['id'] or segment['data']['session_id']!=formal['session_id']:
        raise PermissionDenied('脚本段与正式任务/会话不符')
    # 请求参数固定；实际剩余墙钟在runner内缩减，重放不因时钟变化变成新请求。
    timeout=config['max_seconds']
    runner=SandboxRunner(store,control,formal,native,event,config,checksum,task['data']['contract']['outputs'])
    identifier='native-script-'+digest([native['session_id'],native['tool_id']])
    result=ToolRegistry(store,runner=runner).run(payload['tool_id'],task['id'],binding['receipt_id'],
        payload['arguments'],identifier,timeout=timeout)
    return {'id':result['id'],'status':result['data']['status'],'exit_code':result['data'].get('exit_code'),
            'replayed':result.get('_replayed',False),'native':native}
