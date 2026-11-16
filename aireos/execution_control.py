"""复用版本化任务、文件快照和独立审核的分段执行控制。"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3

from . import inputs, recall
from .attention import pending
from .errors import ConflictError, FrameworkError, PermissionDenied, StaleInput
from .project import Project, explicit_review, typed
from .storage import Transaction, canonical, digest, now


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise FrameworkError(label + '须为非空且不超过4000字符的文本')


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FrameworkError(label + '须为正整数')


class ExecutionControl:
    """正式事实仅写Store；工具计数和会话撤销仅写可重建操作缓存。"""

    def __init__(self, store):
        self.store = store

    def validate_start(self,task_id,session_id,original_text,evidence_rules,max_tools,max_seconds,criteria,purpose,expected):
        """启动参数的无写入预检；不猜任务、原文、证据规则或预算。"""
        from .client_guard import policy
        from .execution_mode import mode
        _text(session_id,'会话id');_text(purpose,'段目的');_text(expected,'预期')
        _positive(max_tools,'工具预算');_positive(max_seconds,'时间预算')
        if not isinstance(original_text,str) or not original_text.strip() or len(original_text.encode('utf-8'))>65536:
            raise FrameworkError('原文须为不超过64KiB的完整非空UTF-8文本')
        if policy(self.store.root).get('execution',{}).get('scope')=='opt_in' and not mode(self.store.root,session_id)['enabled']:
            raise PermissionDenied('用户尚未显式开启受管长任务；不能自动升级普通任务')
        with self.store.connection() as connection:
            tx=Transaction(self.store,connection,{'scopes':['.']})
            task=typed(tx,task_id,'task')
            if task['data']['status'] not in {'planned','running'}:raise FrameworkError('启动须有准确待执行或执行中任务')
            names=self._criteria(task)
            if (not isinstance(criteria,list) or not criteria or not all(isinstance(c,str) for c in criteria)
                    or len(set(criteria))!=len(criteria) or not set(criteria)<=set(names)):
                raise FrameworkError('启动验收范围须为原合同非空A编号子集')
            if not isinstance(evidence_rules,list) or len(evidence_rules)!=len(names):
                raise FrameworkError('启动规则须覆盖全部原验收项')
            keys=[]
            for rule in evidence_rules:
                if not isinstance(rule,dict) or set(rule)!={'criterion','required_classes'}:raise FrameworkError('证据规则字段不符')
                key=rule['criterion'];classes=rule['required_classes']
                if not isinstance(key,str) or not isinstance(classes,list) or not classes or len(classes)>16:
                    raise FrameworkError('证据规则须有准确编号及有限类别数组')
                if any(not isinstance(c,str) or not c.strip() or len(c)>100 for c in classes) or len(set(classes))!=len(classes):
                    raise FrameworkError('证据类别不能为空或重复')
                keys.append(key)
            if len(set(keys))!=len(keys) or set(keys)!=set(names):raise FrameworkError('启动规则不能缩减或增加原验收')
            self._no_pending(tx,task_id)
        from pathlib import Path
        from .execution_recovery import plain_target
        for output in task['data']['contract']['outputs']:
            candidate=self.store.root/output
            if not any(p.startswith('.') for p in Path(output).parts) and (candidate.is_dir() or (not candidate.exists() and not candidate.suffix)):
                plain_target(self.store.root,candidate,directory=True)
                return task,candidate/'execution-original'/(task_id+'.txt')
        raise FrameworkError('长任务须声明可容纳原文及阶段报告的输出目录；不能占用单一科学文件')

    def start(self,task_id,session_id,original_text,evidence_rules,max_tools,max_seconds,criteria,purpose,expected,request_id):
        """组合既有服务；失败可原请求重试，重放不补发预算。"""
        from .execution_recovery import plain_target
        payload=dict(task_id=task_id,session_id=session_id,original_text=original_text,evidence_rules=evidence_rules,
                     max_tools=max_tools,max_seconds=max_seconds,criteria=criteria,purpose=purpose,expected=expected)
        task,path=self.validate_start(**payload)
        if not isinstance(request_id,str) or not request_id.strip() or len(request_id)>200:raise FrameworkError('启动须有有限请求身份')
        import hashlib
        key='execution-start-'+hashlib.sha256(request_id.encode()).hexdigest()
        with self.store.connection() as connection:
            principal=self.store._principal(connection,'plan');self.store._principal(connection,'execute')
            tx=Transaction(self.store,connection,principal)
            tx.require_scope(path.relative_to(self.store.root).as_posix())
            existing=[p for p in tx.list('execution_policy') if p['data']['task']==task_id]
            replay=connection.execute('SELECT 1 FROM requests WHERE request_id=?',(key+':intent',)).fetchone()
            if existing and not replay:raise ConflictError('已有固定策略；须沿报告、独立审核和续行路径，不能重新启动')
        self.store.mutate('execution.start.intent',payload,key+':intent','plan',lambda tx:{'task_id':task_id})
        project=Project(self.store.root,self.store.token)
        receipt=project.prepare(task_id,purpose,key+':prepare',max_chars=3000)
        if task['data']['status']=='planned':project.start_task(task_id,receipt['id'],key+':task')
        plain_target(self.store.root,path.parent,directory=True)
        path.parent.mkdir(parents=True,exist_ok=True)
        plain_target(self.store.root,path)
        raw=original_text.encode('utf-8')
        try:
            with path.open('xb') as handle:handle.write(raw)
        except FileExistsError:
            if path.read_bytes()!=raw:raise ConflictError('原文已存在且字节不同，不能覆盖准确任务原件')
        original=project.add_artifact(path.relative_to(self.store.root).as_posix(),'受管长任务准确原文',task_id,key+':original')
        policy=self.configure(task_id,original['id'],evidence_rules,max_tools,max_seconds,key+':policy')
        segment=self.begin(task_id,session_id,criteria,purpose,expected,key+':segment')
        return dict(task_id=task_id,receipt_id=receipt['id'],original=original,policy=policy,segment=segment,
                    execution=dict(task_id=task_id,session_id=session_id,segment_id=segment['id']))

    @staticmethod
    def _criteria(task):
        return ['A' + str(i + 1) for i in range(len(task['data']['contract']['acceptance']))]

    @staticmethod
    def _policy(tx, task_id):
        policies = [obj for obj in tx.list('execution_policy') if obj['data']['task'] == task_id]
        if len(policies) != 1:
            raise FrameworkError('任务须有且仅有一个明确的执行策略')
        return policies[0]

    @staticmethod
    def _segments(tx, task_id):
        return sorted([obj for obj in tx.list('execution_segment') if obj['data']['task'] == task_id],
                      key=lambda obj: obj['data']['index'])

    @staticmethod
    def _ref(tx, ref, artifact=False):
        """固定版本、当前版本、摘要和现场字节必须同时成立。"""
        item = typed(tx, ref['id'], 'item')
        if recall.reference(item) != ref:
            raise StaleInput('引用对象的准确版本已经变化: ' + ref['id'])
        if artifact and item['data'].get('type') != 'artifact':
            raise FrameworkError('证据和原件必须是可定位的文件产物快照')
        if recall.source_state(tx.store, item) is False:
            raise StaleInput('引用文件或快照已变化: ' + ref['id'])
        return item

    def _base(self, tx, task_id, policy, allow_closed=False, allow_stopped=False, archival=False):
        task = Project._scope_task(tx, task_id) if allow_closed or allow_stopped else Project._task_open(tx, task_id)
        allowed = {'running', 'delivered'} | ({'closed'} if allow_closed else set()) | ({'stopped'} if allow_stopped else set())
        if task['data']['status'] not in allowed:
            raise FrameworkError('任务不在可确认正式完成的状态')
        if recall.contract_hash(task) != policy['data']['contract_hash']:
            raise StaleInput('原任务合同已经变化，禁止沿用执行策略')
        failures = inputs.validate(tx, task['data']['contract'].get('inputs', []))
        if archival:
            original=tx.get(policy['data']['original']['id'],policy['data']['original']['rev'])
            tx.store.blob(original['data']['blob'])
            return task
        if failures:
            raise StaleInput('；'.join(failures))
        self._ref(tx, policy['data']['original'], artifact=True)
        return task

    @staticmethod
    def _no_pending(tx, task_id):
        if pending(tx, task_id):
            raise FrameworkError('任务存在未决请求，不能继续执行')

    def configure(self, task_id, original_item_id, evidence_rules, max_tools, max_seconds, request_id):
        _positive(max_tools, '工具预算')
        _positive(max_seconds, '时间预算')
        payload = dict(task=task_id, original=original_item_id, evidence_rules=evidence_rules,
                       max_tools=max_tools, max_seconds=max_seconds)

        def apply(tx):
            task = Project._task_open(tx, task_id)
            if any(obj['data']['task'] == task_id for obj in tx.list('execution_policy')):
                raise ConflictError('本任务已有固定执行策略，不能静默改写原件、验收或预算')
            names = self._criteria(task)
            if (not isinstance(evidence_rules, list) or len(evidence_rules) != len(names)
                    or any(not isinstance(rule, dict) for rule in evidence_rules)):
                raise FrameworkError('证据规则必须逐项覆盖全部原验收项')
            keys = [rule.get('criterion') for rule in evidence_rules]
            if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys) or set(keys) != set(names):
                raise FrameworkError('证据规则不得删除、增加或重复原验收项')
            rules = []
            for key in names:
                rule = evidence_rules[keys.index(key)]
                classes = rule.get('required_classes')
                if (not isinstance(classes, list) or not classes
                        or not all(isinstance(value, str) and value.strip() for value in classes)
                        or len(set(classes)) != len(classes)):
                    raise FrameworkError('每项验收须声明不重复的非空证据类别数组')
                rules.append(dict(criterion=key, required_classes=classes))
            original = typed(tx, original_item_id, 'item')
            self._ref(tx, recall.reference(original), artifact=True)
            return tx.create('execution_policy', dict(task=task_id, contract_hash=recall.contract_hash(task),
                original=recall.reference(original), evidence_rules=rules,
                max_tools=max_tools, max_seconds=max_seconds, configured_by=tx.principal['actor']))

        return self.store.mutate('execution.configure', payload, request_id, 'plan', apply)

    def begin(self, task_id, session_id, criteria, purpose, expected, request_id):
        from .execution_mode import opt_in
        if opt_in(self.store.root):
            from .execution_mode import mode,managed
            if not mode(self.store.root,session_id)['enabled'] and not managed(self.store.root,session_id):
                raise PermissionDenied('用户尚未显式开启受管长任务；正式登记任务不等于授权绑定')
        for value, label in ((session_id, '会话id'), (purpose, '阶段目的'), (expected, '阶段预期')):
            _text(value, label)
        payload = dict(task=task_id, session_id=session_id, criteria=criteria, purpose=purpose, expected=expected)

        def apply(tx):
            policy = self._policy(tx, task_id)
            task = self._base(tx, task_id, policy)
            self._no_pending(tx, task_id)
            if (not isinstance(criteria, list) or not criteria or not all(isinstance(c, str) for c in criteria)
                    or len(set(criteria)) != len(criteria) or not set(criteria) <= set(self._criteria(task))):
                raise FrameworkError('阶段验收范围须为非空且不重复的原A编号子集')
            # 同一会话不能同时指向多个任务，避免宿主猜测目标。
            if any(obj['data']['session_id'] == session_id and obj['data']['status'] == 'active'
                   for obj in tx.list('execution_segment')):
                raise ConflictError('该会话已有活动段，不能并行绑定其他任务')
            previous = self._segments(tx, task_id)
            if previous:
                last = previous[-1]
                if last['data']['status'] != 'continued':
                    raise ConflictError('上一段须经独立核查并明确continue；换会话不能绕过')
                self._report_body(tx, last, task, policy)
                self._review(tx, last, last['data']['review']['id'], 'continue')
            bindings = [obj for obj in tx.list('execution_binding') if obj['data']['session_id'] == session_id]
            binding = dict(session_id=session_id, task=task_id)
            if bindings:
                tx.replace(bindings[0]['id'], bindings[0]['rev'], binding)
            else:
                tx.create('execution_binding', binding)
            started = now()
            deadline = (datetime.fromisoformat(started) + timedelta(seconds=policy['data']['max_seconds'])).isoformat()
            return tx.create('execution_segment', dict(task=task_id, policy=recall.reference(policy),
                contract_hash=recall.contract_hash(task), original=policy['data']['original'],
                session_id=session_id, criteria=criteria, purpose=purpose, expected=expected,
                executor=tx.principal['actor'], index=len(previous) + 1, status='active',
                started_at=started, deadline=deadline, max_tools=policy['data']['max_tools'],
                max_seconds=policy['data']['max_seconds']))

        result = self.store.mutate('execution.begin', payload, request_id, 'execute', apply)
        # 仅首次正式创建时发放操作计数槽。缓存丢失后重放begin不能补发预算。
        if not result['_replayed']:
            with self._cache() as cache:
                cache.execute('INSERT INTO counters(segment,used) VALUES(?,0)', (result['id'],))
        return result

    def _report_body(self, tx, segment, task, policy, artifact=None, historical_evidence=False):
        data = segment['data']
        if (data['policy'] != recall.reference(policy) or data['contract_hash'] != recall.contract_hash(task)
                or data['original'] != policy['data']['original']):
            raise StaleInput('执行段固定的策略、合同或原件版本不符')
        artifact = artifact or self._ref(tx, data['report'], artifact=True)
        self._ref(tx, recall.reference(artifact), artifact=True)
        if artifact['data'].get('task') != task['id'] or artifact['data'].get('producer') != data['executor']:
            raise PermissionDenied('阶段报告须由本段执行者登记在本任务内')
        try:
            body = json.loads(tx.store.blob(artifact['data']['blob']).decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise FrameworkError('阶段报告须为有效UTF-8 JSON文件') from exc
        body, refs = self._report_content(tx, segment, task, policy, body,
                                          historical_evidence=historical_evidence)
        if data.get('evidence') is not None and data.get('report') == recall.reference(artifact):
            if refs != data['evidence']:
                raise StaleInput('报告证据与正式记录不符')
        return body, refs

    def _report_content(self, tx, segment, task, policy, body, historical_evidence=False):
        """正式报告和受限恢复写入共用同一验收及证据校验。"""
        data = segment['data']
        if (data['policy'] != recall.reference(policy) or data['contract_hash'] != recall.contract_hash(task)
                or data['original'] != policy['data']['original']):
            raise StaleInput('执行段固定的策略、合同或原件版本不符')
        if (not isinstance(body, dict) or body.get('schema') != 'aireos.execution-report/1'
                or body.get('segment_id') != segment['id'] or body.get('contract_hash') != recall.contract_hash(task)):
            raise FrameworkError('阶段报告格式、段ID或原合同摘要不匹配')
        entries = body.get('criteria')
        names = self._criteria(task)
        if (not isinstance(entries, list) or len(entries) != len(names)
                or any(not isinstance(entry, dict) for entry in entries)):
            raise FrameworkError('报告必须保留全部原验收项，包括未知或尚待完成项')
        keys = [entry.get('criterion') for entry in entries]
        if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys) or set(keys) != set(names):
            raise FrameworkError('报告不得删除、增加或重复原A验收项')
        rules = {rule['criterion']: set(rule['required_classes']) for rule in policy['data']['evidence_rules']}
        refs = []
        for entry in entries:
            if entry.get('state') not in {'progress', 'satisfied', 'unknown', 'pending'}:
                raise FrameworkError('验收状态必须为progress/satisfied/unknown/pending')
            if not isinstance(entry.get('remaining'), str):
                raise FrameworkError('每项须有remaining文本，明确剩余缺口')
            evidence = entry.get('evidence')
            if not isinstance(evidence, list):
                raise FrameworkError('每项须明确证据列表，未知时可为空')
            classes = set()
            for ref in evidence:
                if not isinstance(ref, dict):
                    raise FrameworkError('证据必须包含准确id、rev及class')
                _positive(ref.get('rev'), '证据版本')
                _text(ref.get('class'), '证据类别')
                item = (tx.get(ref.get('id'), ref['rev']) if historical_evidence else
                        typed(tx, ref.get('id'), 'item'))
                if item['kind'] != 'item' or item['data'].get('type') != 'artifact':
                    raise FrameworkError('证据须为准确版本的文件产物快照')
                if item['rev'] != ref['rev']:
                    raise StaleInput('证据准确版本不符')
                exact = recall.reference(item)
                if historical_evidence:
                    # 已核查前段仅复核准确快照；获准的新段可正常迭代同一输出文件。
                    tx.store.blob(item['data']['blob'])
                else:
                    self._ref(tx, exact, artifact=True)
                    from .execution_safety import negative_basis
                    if entry['state']=='satisfied':
                        negative_basis(tx, item)
                # 只固定证据来源；类别真实性由独立审核承担，文字标签本身不证明物理正确。
                refs.append({**exact, 'class': ref['class']})
                classes.add(ref['class'])
            if entry['state'] == 'satisfied' and not rules[entry['criterion']] <= classes:
                raise FrameworkError('satisfied缺少规定证据类别: ' + entry['criterion'])
        return body, refs

    def report(self, segment_id, artifact_id, request_id):
        self._quiescent(segment_id)
        def apply(tx):
            segment = typed(tx, segment_id, 'execution_segment')
            data = segment['data']
            policy = self._policy(tx, data['task'])
            stale=False
            try:
                task = self._base(tx, data['task'], policy)
            except StaleInput:
                task = self._base(tx,data['task'],policy,archival=True)
                stale=True
            if data['executor'] != tx.principal['actor']:
                raise PermissionDenied('只有本段执行者可以提交或修订阶段报告')
            if data['status'] not in {'active', 'submitted', 'hold'}:
                raise ConflictError('本段已经裁定，不能覆盖报告')
            artifact = typed(tx, artifact_id, 'item')
            if data.get('report') == recall.reference(artifact):
                raise ConflictError('重新报告须登记修订后的准确新快照，不得重用旧报告解除hold')
            if data.get('report'):
                previous = tx.get(data['report']['id'], data['report']['rev'])
                if previous['data'].get('blob') == artifact['data'].get('blob'):
                    raise ConflictError('换新产物id但报告字节未修订，不能解除原段的核查要求')
            body, refs = self._report_body(tx, segment, task, policy, artifact)
            if stale and any(entry['state']=='satisfied' for entry in body['criteria']):
                raise StaleInput('来源失效只能归档缺口，不能登记满足验收')
            return tx.replace(segment_id, segment['rev'], {**data, 'status': 'submitted',
                'report': recall.reference(artifact), 'evidence': refs,
                'states': {entry['criterion']: entry['state'] for entry in body['criteria']},
                'reported_at': now(), 'review': None, 'decision': None,'review_trace':None})

        return self._stable_mutate(segment_id,'execution.report',dict(segment=segment_id,artifact=artifact_id),
                                   request_id,'execute',apply)

    def _review(self, tx, segment, review_id, decision, historical_evidence=False):
        opinion = typed(tx, review_id, 'review')
        data = opinion['data']
        if segment['data'].get('review') and recall.reference(opinion) != segment['data']['review']:
            raise StaleInput('前段固定的独立审核准确版本已经变化')
        if (not explicit_review(opinion) or data['target'] != segment['data']['report']
                or data['reviewer'] == segment['data']['executor']):
            raise PermissionDenied('须有针对本段报告准确版本的独立明确审核')
        opinions = [obj for obj in tx.list('review') if obj['data']['target'] == data['target']]
        if opinions[-1]['id'] != review_id:
            raise StaleInput('须使用该报告准确版本的最新独立质量意见')
        if decision != 'hold' and data['verdict'] != 'pass':
            raise FrameworkError('继续或完成须有明确pass；拒绝意见只能进入hold')
        if decision != 'hold':
            # 报告本身也是续行/完成依据；既有已确认争议和撤回不能被旧pass遮蔽。
            from .execution_safety import negative_basis
            negative_basis(tx,typed(tx,data['target']['id'],'item'))
        from .review_trace import check
        trace=check(tx.store,data.get('provenance'),segment,self._policy(tx,segment['data']['task']),historical=historical_evidence)
        if segment['data'].get('review_trace') and trace!=segment['data']['review_trace']:
            raise StaleInput('实际审核轨迹与裁定时固定记录不符')
        # 质量意见沿用项目既有review语义、职责及渠道；不解析另一套审核JSON。
        return opinion

    def decide(self, segment_id, review_id, decision, reason, request_id):
        self._quiescent(segment_id)
        _text(reason, '裁定理由')
        if decision not in {'continue', 'hold', 'completed'}:
            raise FrameworkError('裁定须为continue/hold/completed')
        payload = dict(segment=segment_id, review=review_id, decision=decision, reason=reason)

        def apply(tx):
            segment = typed(tx, segment_id, 'execution_segment')
            data = segment['data']
            policy = self._policy(tx, data['task'])
            task = self._base(tx, data['task'], policy)
            if data['status'] != 'submitted':
                raise ConflictError('必须先提交当前报告；hold恢复须先登记修订报告再独立核查')
            body, _ = self._report_body(tx, segment, task, policy)
            opinion = self._review(tx, segment, review_id, decision)
            from .review_trace import check
            trace=check(tx.store,opinion['data'].get('provenance'),segment,policy)
            if decision != 'hold':
                self._no_pending(tx, data['task'])
            if decision == 'completed' and any(entry['state'] != 'satisfied' for entry in body['criteria']):
                raise FrameworkError('原验收项尚未全部satisfied，不能把局部进展当完成')
            return tx.replace(segment_id, segment['rev'], {**data,
                'status': 'continued' if decision == 'continue' else decision,
                'decision': decision, 'reason': reason, 'review': recall.reference(opinion),
                'decided_by': tx.principal['actor'], 'decided_at': now(),'review_trace':trace})


        return self._stable_mutate(segment_id,'execution.decide',payload,request_id,'decide',apply)

    def _stable_mutate(self,segment_id,operation,payload,request_id,capability,apply):
        # 与admit相同的cache→正式库锁序，封闭检查无在途到提交记录之间的竞争窗口。
        with self._cache() as cache:
            if cache.execute("SELECT 1 FROM inflight WHERE segment=? AND outcome='running'",(segment_id,)).fetchone():
                raise FrameworkError('本段仍有在途工具，不能冻结报告或推进审核')
            return self.store.mutate(operation,payload,request_id,capability,apply)

    @contextmanager
    def _cache(self):
        folder = self.store.home / 'cache'
        database = folder / 'execution-control.sqlite3'
        if folder.is_symlink() or database.is_symlink():
            raise FrameworkError('执行缓存目录或数据库不能是链接')
        folder.mkdir(exist_ok=True)
        if folder.resolve().parent != self.store.home.resolve():
            raise FrameworkError('执行缓存路径脱离治理目录')
        connection = sqlite3.connect(database, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA busy_timeout=15000')
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS counters(segment TEXT PRIMARY KEY, used INTEGER NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS revoked(session TEXT NOT NULL, segment TEXT NOT NULL, PRIMARY KEY(session,segment))')
            connection.execute('CREATE TABLE IF NOT EXISTS events(event TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS inflight(event TEXT PRIMARY KEY, session TEXT NOT NULL, segment TEXT NOT NULL, outcome TEXT NOT NULL)')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _usage(cache, segment):
        row = cache.execute('SELECT used FROM counters WHERE segment=?', (segment['id'],)).fetchone()
        if row is None:
            raise FrameworkError('执行段操作计数缺失，不能重建或重置预算；须报告后独立裁定')
        return row['used']

    def _locate(self, tx, task_id, session_id):
        if task_id is None:
            bindings = [obj for obj in tx.list('execution_binding') if obj['data']['session_id'] == session_id]
            if len(bindings) == 1:
                task_id = bindings[0]['data']['task']
        if task_id is None:
            matches = {obj['data']['task'] for obj in tx.list('execution_segment')
                       if obj['data']['session_id'] == session_id}
            if len(matches) != 1:
                raise FrameworkError('会话未唯一绑定任务，请提供准确task_id；不得猜测')
            task_id = matches.pop()
        task = typed(tx, task_id, 'task')
        policy = self._policy(tx, task_id)
        segments = self._segments(tx, task_id)
        return task, policy, segments[-1] if segments else None

    def _inspect(self, tx, cache, task, policy, segment, session_id):
        reasons = []
        try:
            self._base(tx, task['id'], policy)
            self._no_pending(tx, task['id'])
            if segment:
                if segment['data']['policy'] != recall.reference(policy):
                    raise StaleInput('执行策略准确版本已经变化')
                if segment['data'].get('report'):
                    self._report_body(tx, segment, task, policy)
        except FrameworkError as exc:
            reasons.append(str(exc))
        used = 0
        if segment is None:
            reasons.append('尚未开始执行段')
        else:
            data = segment['data']
            try:
                used = self._usage(cache, segment)
            except FrameworkError as exc:
                reasons.append(str(exc))
            if data['session_id'] != session_id:
                reasons.append('当前段属于其他会话')
            if data['status'] != 'active':
                reasons.append('当前段状态不允许工具执行: ' + data['status'])
            if cache.execute('SELECT 1 FROM revoked WHERE session=? AND segment=?', (session_id, segment['id'])).fetchone():
                reasons.append('本会话旧执行段已经撤销')
            if datetime.now(timezone.utc) >= datetime.fromisoformat(data['deadline']):
                reasons.append('执行段时间预算已到期')
            if used >= data['max_tools']:
                reasons.append('执行段工具预算已耗尽')
            # 前段报告及独立意见仍精确绑定；其证据按历史快照保留，允许已获准迭代。
            previous = self._segments(tx, task['id'])
            for old in previous[:-1]:
                try:
                    self._report_body(tx, old, task, policy, historical_evidence=True)
                    self._review(tx, old, old['data']['review']['id'], 'continue', historical_evidence=True)
                except FrameworkError as exc:
                    reasons.append('前段核查基础变化: ' + str(exc))
                    break
        return dict(allowed=not reasons, reasons=reasons, segment_id=segment['id'] if segment else None,
                    used=used, max_tools=segment['data']['max_tools'] if segment else policy['data']['max_tools'],
                    deadline=segment['data']['deadline'] if segment else None)

    def _completion(self, tx, cache, task, policy, segment, session_id):
        """完成依据独立于工具预算；旧会话授权不能自动完成新请求。"""
        reasons = []
        if segment is None:
            return dict(completion_ready=False, completion_reasons=['尚无正式执行段'])
        data = segment['data']
        if data['session_id'] != session_id:
            reasons.append('完成段属于其他会话')
        if data['status'] != 'completed' or data.get('decision') != 'completed':
            reasons.append('最新段尚无正式completed裁定')
            return dict(completion_ready=False, completion_reasons=reasons)
        try:
            self._base(tx, task['id'], policy, allow_closed=True)
            self._no_pending(tx, task['id'])
            # 缓存丢失不能被解释为从未撤销，计数槽仅用于确认操作状态仍完整。
            self._usage(cache, segment)
            if cache.execute('SELECT 1 FROM revoked WHERE session=? AND segment=?',
                             (session_id, segment['id'])).fetchone():
                reasons.append('本会话完成段已因新输入或重置撤销')
            for stage in self._segments(tx, task['id']):
                historical = stage['id'] != segment['id']
                body, _ = self._report_body(tx, stage, task, policy, historical_evidence=historical)
                stage_data = stage['data']
                expected = 'completed' if stage['id'] == segment['id'] else 'continue'
                expected_status = 'completed' if expected == 'completed' else 'continued'
                if stage_data['status'] != expected_status or stage_data.get('decision') != expected:
                    raise FrameworkError('执行链缺少准确的独立继续或完成裁定')
                review = stage_data.get('review')
                if not review:
                    raise FrameworkError('执行链缺少独立审核引用')
                self._review(tx, stage, review['id'], expected, historical_evidence=historical)
                if stage['id'] == segment['id'] and any(
                        entry['state'] != 'satisfied' for entry in body['criteria']):
                    raise FrameworkError('原验收项尚未全部satisfied')
        except (FrameworkError, KeyError, TypeError, ValueError) as exc:
            reasons.append(str(exc))
        return dict(completion_ready=not reasons, completion_reasons=reasons)

    def _handoff(self, tx, cache, task, policy, segment, session_id):
        """诚实hold可交接结束本轮，不将未知、停止或未决升级为科学完成。"""
        reasons = []
        if segment is None:
            return dict(handoff_ready=False, handoff_reasons=['尚无可交接的正式段'])
        data = segment['data']
        if data['session_id'] != session_id:
            reasons.append('交接段属于其他会话')
        if data['status'] != 'hold' or data.get('decision') != 'hold':
            return dict(handoff_ready=False, handoff_reasons=reasons + ['最新段没有正式hold裁定'])
        try:
            self._base(tx, task['id'], policy, allow_closed=True, allow_stopped=True)
            self._usage(cache, segment)
            if cache.execute('SELECT 1 FROM revoked WHERE session=? AND segment=?',
                             (session_id, segment['id'])).fetchone():
                reasons.append('旧交接段已因新输入或重置撤销')
            if task['data']['status'] == 'stopped':
                stage = typed(tx, task['data'].get('stage'), 'stage')
                previous_task = tx.get(stage['data']['task']['id'], stage['data']['task']['rev'])
                if (not task['data']['contract'].get('stop_conditions')
                        or stage['data'].get('outcome') != 'stopped_without_acceptance'
                        or previous_task['id'] != task['id']
                        or recall.reference(previous_task) != stage['data']['task']
                        or recall.contract_hash(previous_task) != recall.contract_hash(task)):
                    raise FrameworkError('停止记录未准确绑定原合同和停止条件，不能自动交接')
            for stage in self._segments(tx, task['id']):
                historical = stage['id'] != segment['id']
                self._report_body(tx, stage, task, policy, historical_evidence=historical)
                expected = 'continue' if historical else 'hold'
                expected_status = 'continued' if historical else 'hold'
                if stage['data']['status'] != expected_status or stage['data'].get('decision') != expected:
                    raise FrameworkError('交接链缺少准确的继续或hold裁定')
                self._review(tx, stage, stage['data']['review']['id'], expected,
                             historical_evidence=historical)
        except (FrameworkError, KeyError, TypeError, ValueError) as exc:
            reasons.append(str(exc))
        return dict(handoff_ready=not reasons, handoff_reasons=reasons)

    def status(self, task_id=None, session_id=None):
        _text(session_id, '会话id')
        # 只读状态不创建缓存；已有缓存只用mode=ro打开。
        with self.store.connection() as connection:
            tx = Transaction(self.store, connection, {'scopes': ['.']})
            task, policy, segment = self._locate(tx, task_id, session_id)
            database = self.store.home / 'cache' / 'execution-control.sqlite3'
            cache = None
            try:
                if database.is_file():
                    if database.is_symlink() or database.parent.is_symlink():
                        raise FrameworkError('执行缓存目录或数据库不能是链接')
                    cache = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
                    cache.row_factory = sqlite3.Row
                else:
                    cache = sqlite3.connect(':memory:')
                    cache.row_factory = sqlite3.Row
                    cache.execute('CREATE TABLE counters(segment TEXT PRIMARY KEY, used INTEGER)')
                    cache.execute('CREATE TABLE revoked(session TEXT, segment TEXT)')
                result = self._inspect(tx, cache, task, policy, segment, session_id)
                result.update(self._completion(tx, cache, task, policy, segment, session_id))
                result.update(self._handoff(tx, cache, task, policy, segment, session_id))
            except (OSError, sqlite3.Error, FrameworkError) as exc:
                result = dict(allowed=False, reasons=['执行缓存不可用: ' + str(exc)],
                              segment_id=segment['id'] if segment else None, used=None,
                              max_tools=policy['data']['max_tools'], deadline=segment['data']['deadline'] if segment else None,
                              completion_ready=False, completion_reasons=['无法确认操作缓存及完成依据: ' + str(exc)],
                              handoff_ready=False, handoff_reasons=['无法确认操作缓存及交接依据: ' + str(exc)])
            finally:
                if cache is not None:
                    cache.close()
            from .execution_recovery import describe_paths
            try:
                waiting_ready=self.waiting(task['id'],session_id)
            except (FrameworkError,OSError,sqlite3.Error):
                waiting_ready=False
            return {**result, 'task_id': task['id'], 'session_id': session_id,
                'waiting_ready': waiting_ready,
                'policy': policy, 'segment': segment, 'original': policy['data']['original'],
                'recovery': describe_paths(self.store.root, task, segment, session_id),
                'criteria': [dict(criterion=key, acceptance=text) for key, text in
                             zip(self._criteria(task), task['data']['contract']['acceptance'])]}

    def admit(self, session_id, tool_name, tool_input, event_id, segment_id=None, track=True):
        """只供受信宿主调用；明确段ID防止迟到事件串入下一段。"""
        try:
            for value, label in ((session_id, '会话id'), (tool_name, '工具名'),
                                 (event_id, '事件id'), (segment_id, '执行段id')):
                _text(value, label)
            fingerprint = digest(dict(session_id=session_id, segment_id=segment_id,
                                      tool_name=tool_name, tool_input=tool_input))
            # 先锁操作缓存再读正式记录；所有宿主计数在同一个原子事务串行化。
            with self._cache() as cache:
                previous = cache.execute('SELECT * FROM events WHERE event=?', (event_id,)).fetchone()
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        return dict(allowed=False, reasons=['相同事件id对应不同会话、段或工具内容'], segment_id=segment_id)
                    original = json.loads(previous['result'])
                    if not original['allowed']:
                        return {**original, 'replayed': True}
                with self.store.connection() as connection:
                    tx = Transaction(self.store, connection, {'scopes': ['.']})
                    segment = typed(tx, segment_id, 'execution_segment')
                    task, policy, latest = self._locate(tx, segment['data']['task'], session_id)
                    from .execution_safety import tool_scope
                    if tool_name=='aireos_run_tool':
                        from .isolated_tools import script_scope
                        script_scope(tx,task,tool_input)
                    else:
                        tool_scope(self.store.root, task, tool_name, tool_input)
                    result = self._inspect(tx, cache, task, policy, latest, session_id)
                    if latest['id'] != segment_id:
                        result['allowed'] = False
                        result['reasons'].append('旧执行段事件不能计入当前新段')
                    if previous:
                        # 重放不再次消耗预算，也不能使失效、过期或已提交段重新获得权限。
                        result['reasons'] = [reason for reason in result['reasons'] if reason != '执行段工具预算已耗尽']
                        result['allowed'] = not result['reasons']
                        return {**result, 'replayed': True}
                    if result['allowed']:
                        cache.execute('INSERT INTO counters(segment,used) VALUES(?,1) ON CONFLICT(segment) DO UPDATE SET used=used+1', (segment_id,))
                        result['used'] += 1
                        if track:
                            cache.execute('INSERT INTO inflight VALUES(?,?,?,?)', (event_id,session_id,segment_id,'running'))
                    result['replayed'] = False
                    cache.execute('INSERT INTO events VALUES(?,?,?)', (event_id, fingerprint, canonical(result)))
                    return result
        except (FrameworkError, OSError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            return dict(allowed=False, reasons=['执行控制不可放行: ' + str(exc)], segment_id=segment_id)

    def finish(self, session_id, segment_id, event_id, outcome):
        """由真实宿主结束事件登记，不接受模型自报或用新事件清理旧任务。"""
        if outcome not in {'completed', 'failed'}:
            raise FrameworkError('未确认结束不能清除在途工具')
        with self._cache() as cache:
            row = cache.execute('SELECT * FROM inflight WHERE event=?', (event_id,)).fetchone()
            if row is None or row['session'] != session_id or row['segment'] != segment_id:
                raise FrameworkError('结束事件与已获准工具不匹配')
            if row['outcome'] not in {'running', outcome}:
                raise FrameworkError('工具终态冲突')
            cache.execute('UPDATE inflight SET outcome=? WHERE event=?', (outcome,event_id))
        return dict(event_id=event_id,outcome=outcome)

    def start_work(self,session_id,segment_id,event_id,admitted_event_id=None):
        """宿主既有异步队列的在途登记；不放行shell、不重置工具预算。"""
        with self._cache() as cache:
            with self.store.connection() as connection:
                tx=Transaction(self.store,connection,{'scopes':['.']})
                segment=typed(tx,segment_id,'execution_segment')
                task,policy,latest=self._locate(tx,segment['data']['task'],session_id)
                state=self._inspect(tx,cache,task,policy,latest,session_id)
                if admitted_event_id is not None:
                    accepted=cache.execute("SELECT 1 FROM inflight WHERE event=? AND session=? AND segment=? AND outcome='running'",
                        (admitted_event_id,session_id,segment_id)).fetchone()
                    if not accepted:raise FrameworkError('子工作须绑定仍在途的原宿主许可')
                    # 最后一次已准入工具可启动自己的子进程，不发新预算或放行新工具。
                    state['reasons']=[r for r in state['reasons'] if r!='执行段工具预算已耗尽']
                    state['allowed']=not state['reasons']
                if not state['allowed'] or latest['id']!=segment_id:
                    raise FrameworkError('异步工作不能在失效或非当前执行段启动')
                cache.execute('INSERT INTO inflight VALUES(?,?,?,?)',(event_id,session_id,segment_id,'running'))
        return dict(event_id=event_id,segment_id=segment_id)

    def _quiescent(self, segment_id):
        with self._cache() as cache:
            if cache.execute("SELECT 1 FROM inflight WHERE segment=? AND outcome='running'", (segment_id,)).fetchone():
                raise FrameworkError('本段仍有在途工具，不能冻结报告或推进审核')

    def waiting(self, task_id, session_id):
        """复用原待决、checkpoint和stop记录交还用户；不授予审核或执行资格。"""
        with self.store.connection() as connection:
            tx = Transaction(self.store, connection, {'scopes':['.']})
            task, policy, segment = self._locate(tx,task_id,session_id)
            if not segment or segment['data']['session_id'] != session_id:
                return False
            database=self.store.home/'cache/execution-control.sqlite3'
            if not database.is_file() or database.is_symlink() or database.parent.is_symlink():
                return False
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as cache:
                cache.row_factory=sqlite3.Row
                if cache.execute("SELECT 1 FROM inflight WHERE segment=? AND outcome='running'",(segment['id'],)).fetchone():
                    return False
                self._usage(cache,segment)
                if cache.execute('SELECT 1 FROM revoked WHERE session=? AND segment=?',(session_id,segment['id'])).fetchone():
                    return False
            if pending(tx,task['id']) or task['data']['status']=='stopped':
                return True
            try:
                self._base(tx,task['id'],policy)
            except StaleInput:
                card=task['data'].get('checkpoint')
                return bool((card and card['updated'] > segment['data']['started_at']) or (
                    segment['data'].get('report') and segment['data']['status']=='submitted'
                    and all(value!='satisfied' for value in segment['data'].get('states',{}).values())))
            return False

    def invalidate(self, session_id):
        _text(session_id, '会话id')
        with self._cache() as cache:
            with self.store.connection() as connection:
                tx = Transaction(self.store, connection, {'scopes': ['.']})
                segments = [obj for obj in tx.list('execution_segment') if obj['data']['session_id'] == session_id]
                for segment in segments:
                    cache.execute('INSERT OR IGNORE INTO revoked VALUES(?,?)', (session_id, segment['id']))
                return dict(session_id=session_id, invalidated=[obj['id'] for obj in segments])
