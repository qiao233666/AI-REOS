"""复用OpenCode原生许可请求；父进程核对后仅放行当前一次动作。"""
import copy
import json
from pathlib import Path
import re
from urllib.parse import quote

from .errors import FrameworkError


def guarded_permissions(permissions):
    """保留原拒绝边界，把允许改为逐次询问，不授予always权限。"""
    result=copy.deepcopy(permissions)
    for action,value in result.items():
        if isinstance(value,dict):
            for resource,effect in value.items():
                if effect not in {'allow','deny','ask'}:
                    raise FrameworkError('宿主许可规则格式不支持')
                value[resource]='ask' if effect=='allow' else effect
        elif value in {'allow','deny','ask'}:
            result[action]='ask' if value=='allow' else value
        else:
            raise FrameworkError('宿主许可规则格式不支持')
    return result


class PermissionBroker:
    """只处理绑定原生会话的待决许可；不会自动审核报告或续期。"""
    def __init__(self, api, admit, session_id, segment_id, native_session_id, log_path, defer_interrupt=False):
        for value in (session_id,segment_id,native_session_id):
            if not isinstance(value,str) or not value.strip():
                raise FrameworkError('许可代理须绑定正式会话、准确执行段和原生会话')
        self.api,self.admit=api,admit
        self.session_id,self.segment_id=session_id,segment_id
        self.native=native_session_id
        self.path='/api/session/'+quote(native_session_id,safe='')
        self.log_path=Path(log_path)
        self.handled=set()
        self.approved_tools=set()
        self.defer_interrupt=defer_interrupt
        self.blocked=False
        self.reason=[]

    def _record(self, value):
        with self.log_path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')

    def poll(self):
        if self.blocked:
            return False
        response=self.api('GET',self.path+'/permission')
        requests=response.get('data') if isinstance(response,dict) else None
        if not isinstance(requests,list):
            raise FrameworkError('宿主未返回明确的待决许可集合')
        for request in requests:
            if not isinstance(request,dict) or not isinstance(request.get('id'),str):
                raise FrameworkError('宿主待决许可缺少准确身份')
            key=request['id']
            if key in self.handled:
                continue
            action=request.get('action')
            resources=request.get('resources')
            source=request.get('source')
            if (request.get('sessionID')!=self.native or not isinstance(action,str) or not action.strip()
                    or not isinstance(resources,list) or not all(isinstance(x,str) for x in resources)
                    or not isinstance(source,dict) or source.get('type')!='tool'
                    or not all(isinstance(source.get(k),str) and source[k].strip() for k in ('id','messageID'))):
                check={'allowed':False,'reasons':['宿主许可的会话或动作格式不符']}
            else:
                # 绑定原生请求ID，不接受执行者在提示里自报完成或工具身份。
                check=self.admit(session_id=self.session_id,segment_id=self.segment_id,
                    tool_name=action,tool_input={'resources':resources,'metadata':request.get('metadata',{}),
                    'source':request.get('source')},event_id=self.native+':'+key)
            allow=check.get('allowed') is True and not check.get('replayed',False)
            decision='once' if allow else 'reject'
            self._record({'native_session_id':self.native,'request_id':key,'segment_id':self.segment_id,
                          'action':action,'resources':resources,'decision':decision,'check':check})
            # 在回复前标为已处理。网络结果不明不能重发一次许可导致重复副作用。
            self.handled.add(key)
            if allow and isinstance(source,dict) and source.get('type')=='tool' and isinstance(source.get('id'),str):
                self.approved_tools.add(source['id'])
            self.api('POST',self.path+'/permission/'+quote(key,safe='')+'/reply',
                     {'decision':decision,'message':'AI-REOS执行段核查：'+(';'.join(check.get('reasons',[])) or '仅允许当前一次动作')})
            if not allow:
                self.blocked=True
                self.reason=check.get('reasons',[]) or ['旧事件重放不得再次执行动作']
                if not self.defer_interrupt:
                    self.api('POST',self.path+'/interrupt')
                return False
        return True
