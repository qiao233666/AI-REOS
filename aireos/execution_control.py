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

    def _base(self, tx, task_id, policy, allow_closed=False, allow_stopped=False):
        task = Project._scope_task(tx, task_id) if allow_closed or allow_stopped else Project._task_open(tx, task_id)
        allowed = {'running', 'delivered'} | ({'closed'} if allow_closed else set()) | ({'stopped'} if allow_stopped else set())
        if task['data']['status'] not in allowed:
            raise FrameworkError('任务不在可确认正式完成的状态')
        if recall.contract_hash(task) != policy['data']['contract_hash']:
            raise StaleInput('原任务合同已经变化，禁止沿用执行策略')
        failures = inputs.validate(tx, task['data']['contract'].get('inputs', []))
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
                # 只固定证据来源；类别真实性由独立审核承担，文字标签本身不证明物理正确。
                refs.append({**exact, 'class': ref['class']})
                classes.add(ref['class'])
            if entry['state'] == 'satisfied' and not rules[entry['criterion']] <= classes:
                raise FrameworkError('satisfied缺少规定证据类别: ' + entry['criterion'])
        return body, refs

    def report(self, segment_id, artifact_id, request_id):
        def apply(tx):
            segment = typed(tx, segment_id, 'execution_segment')
            data = segment['data']
            policy = self._policy(tx, data['task'])
            task = self._base(tx, data['task'], policy)
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
            return tx.replace(segment_id, segment['rev'], {**data, 'status': 'submitted',
                'report': recall.reference(artifact), 'evidence': refs,
                'states': {entry['criterion']: entry['state'] for entry in body['criteria']},
                'reported_at': now(), 'review': None, 'decision': None})

        return self.store.mutate('execution.report', dict(segment=segment_id, artifact=artifact_id),
                                 request_id, 'execute', apply)

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
        # 质量意见沿用项目既有review语义、职责及渠道；不解析另一套审核JSON。
        return opinion

    def decide(self, segment_id, review_id, decision, reason, request_id):
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
            if decision != 'hold':
                self._no_pending(tx, data['task'])
            if decision == 'completed' and any(entry['state'] != 'satisfied' for entry in body['criteria']):
                raise FrameworkError('原验收项尚未全部satisfied，不能把局部进展当完成')
            return tx.replace(segment_id, segment['rev'], {**data,
                'status': 'continued' if decision == 'continue' else decision,
                'decision': decision, 'reason': reason, 'review': recall.reference(opinion),
                'decided_by': tx.principal['actor'], 'decided_at': now()})

        return self.store.mutate('execution.decide', payload, request_id, 'decide', apply)

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
            return {**result, 'task_id': task['id'], 'session_id': session_id,
                'policy': policy, 'segment': segment, 'original': policy['data']['original'],
                'recovery': describe_paths(self.store.root, task, segment, session_id),
                'criteria': [dict(criterion=key, acceptance=text) for key, text in
                             zip(self._criteria(task), task['data']['contract']['acceptance'])]}

    def admit(self, session_id, tool_name, tool_input, event_id, segment_id=None):
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
                    result['replayed'] = False
                    cache.execute('INSERT INTO events VALUES(?,?,?)', (event_id, fingerprint, canonical(result)))
                    return result
        except (FrameworkError, OSError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            return dict(allowed=False, reasons=['执行控制不可放行: ' + str(exc)], segment_id=segment_id)

    def invalidate(self, session_id):
        _text(session_id, '会话id')
        with self._cache() as cache:
            with self.store.connection() as connection:
                tx = Transaction(self.store, connection, {'scopes': ['.']})
                segments = [obj for obj in tx.list('execution_segment') if obj['data']['session_id'] == session_id]
                for segment in segments:
                    cache.execute('INSERT OR IGNORE INTO revoked VALUES(?,?)', (session_id, segment['id']))
                return dict(session_id=session_id, invalidated=[obj['id'] for obj in segments])
