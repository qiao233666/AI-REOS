"""只读核对既有审核渠道的实际材料和运行记录，不定义审核意见格式。"""
import hashlib
import json
from .errors import FrameworkError
from .storage import canonical


def check(store, provenance, segment, policy, historical=False):
    try:
        return _check(store,provenance,segment,policy,historical)
    except (KeyError,TypeError,ValueError,OSError) as exc:
        raise FrameworkError('实际审核轨迹缺失、损坏或格式不符') from exc


def _check(store, provenance, segment, policy, historical=False):
    if not isinstance(provenance,dict):
        raise FrameworkError('执行阶段审核须引用项目既有渠道的实际派发记录')
    source=provenance.get('type')
    if source=='external_run':
        run=store.get(provenance['id'])
        data=run['data']
        package=store.get(data['package'])
        packet=package['data'].get('execution')
        if (run['kind']!='external_run' or data.get('status')!='completed'
                or data.get('delivery_status')!='completed' or not packet):
            raise FrameworkError('既有外审未完成或未携带正式执行材料')
        original=store.blob(packet['blob'])
        report=store.blob(data['report_blob'])
        if not report.strip():
            raise FrameworkError('审核报告为空')
        directory=store.root/data['directory']
        prompt=store.blob(package['data']['prompt_blob'])
        stdin=(directory/'stdin.txt').read_bytes()
        if (data.get('channel')=='project-dispatch' and canonical(json.loads(original)) not in stdin.decode('utf-8')) or (
                data.get('channel')!='project-dispatch' and stdin!=prompt):
            raise FrameworkError('外审实际stdin与固定派发材料不符')
        packet=json.loads(original)
        identity=canonical({'run':run,'package':package})
    elif source=='project_dispatch':
        raise FrameworkError('独立目录日志不能代替正式运行身份；须经已有run-external-review衔接项目渠道')
    else:
        raise FrameworkError('未接入的审核轨迹类型不能给执行段放行')
    if (packet.get('segment',{}).get('id')!=segment['id']
            or packet['segment']['data'].get('report')!=segment['data']['report']
            or packet['policy']!=policy
            or packet['original']['ref']!=policy['data']['original']):
        raise FrameworkError('实际审核材料属于另一任务、原件或报告版本')
    if historical:
        current_contract=store.get(segment['data']['task'])['data']['contract']
        if packet['task']['data']['contract']!=current_contract:
            raise FrameworkError('历史审核合同已变化')
        return dict(type=source,sha256=hashlib.sha256(identity.encode()+stdin+report+canonical(packet).encode()).hexdigest())
    from .execution_materials import ExecutionMaterials
    # 对准确原件、合同、报告和证据重新生成；旧材料不能混用新成果。
    current=ExecutionMaterials(store).packet(segment['id'])
    for key in ('original','report','evidence','requires_read'):
        if not historical and packet[key]!=current[key]:
            raise FrameworkError('实际审核材料与当前正式材料不一致: '+key)
    if packet['task']['data']['contract']!=current['task']['data']['contract']:
        raise FrameworkError('审核合同已变化')
    return dict(type=source,sha256=hashlib.sha256(identity.encode()+stdin+report+canonical(packet).encode()).hexdigest())
