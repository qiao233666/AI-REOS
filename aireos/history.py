"""旧账本只读投影：保留原文快照，程序管理可重建的条目检索缓存。"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from .errors import FrameworkError
from .memory_index import phrases
from .memory_packet import window
from .run_evidence import file_signature, read_regular
from .storage import normalize_scope, within

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_ENTRY_BYTES = 1024 * 1024
MAX_RECORDS = 10000
IDENTIFIER = r'[A-Z][A-Z0-9_]{0,12}-\d{1,9}'
COVERAGE = '旧账本原文的有限候选检索；仅历史参考，未命中为UNKNOWN，不证明从未做过或当前有效。'


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False))


def records(text, format):
    """只识别显式编号边界，不执行YAML标签，也不推断条目内容的真假。"""
    lines = text.splitlines(keepends=True)
    if format == 'document':
        if len(text.encode('utf-8')) > MAX_ENTRY_BYTES:
            raise FrameworkError('历史单条正文超过预算，须先明确拆分边界')
        return [dict(legacy_id='DOC-1', text=text, start_line=1, end_line=len(lines),
            legacy_status='unknown', entry_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())]
    if format == 'yaml-records':
        anchors = [(index, len(match[1])) for index, line in enumerate(lines)
            if (match := re.match(r'^( *)- (?:id|schema_version):\s*', line))]
        if not anchors:
            raise FrameworkError('无法识别YAML编号列表；须提供支持的账本格式')
        depth = min(indent for _, indent in anchors)
        starts = [index for index, indent in anchors if indent == depth]
        id_pattern = re.compile(r'^' + ' ' * depth + r'(?:- |  )id:\s*[\'"]?(' + IDENTIFIER + r')[\'"]?(?:\s|$)')
    elif format == 'markdown':
        starts, previous = [], None
        for index,line in enumerate(lines):
            match=re.match(r'^#{2,3}\s+(' + IDENTIFIER + r')\b(.*)',line)
            if not match:
                continue
            correction=re.match(r'^\s*(?:[—:：-]\s*)?(?:追加|修订|更正|补充|续)',match[2])
            if previous==match[1] and correction:
                continue
            starts.append(index)
            previous=match[1]
        id_pattern = re.compile(r'^#{2,3}\s+(' + IDENTIFIER + r')\b')
    else:
        raise FrameworkError('历史来源格式须为yaml-records或markdown')
    if not starts or len(starts) > MAX_RECORDS:
        raise FrameworkError('未识别到编号条目或条目数量超限')
    found, identifiers = [], set()
    for start, end in zip(starts, starts[1:] + [len(lines)]):
        raw = ''.join(lines[start:end])
        if len(raw.encode('utf-8')) > MAX_ENTRY_BYTES:
            raise FrameworkError('历史单条正文超过预算，须先明确拆分边界')
        ids = [match[1] for line in lines[start:end] if (match := id_pattern.match(line))]
        if format=='markdown':
            ids=list(dict.fromkeys(ids))
        if len(ids) != 1 or ids[0] in identifiers:
            raise FrameworkError('旧账本条目缺少唯一编号或同文件编号重复')
        identifier = ids[0]
        identifiers.add(identifier)
        status = re.search(r'^\s*(?:- )?status:\s*([^\r\n]+)', raw, re.I | re.M)
        found.append(dict(legacy_id=identifier, text=raw, start_line=start+1, end_line=end,
            legacy_status=status[1].strip().strip('\'"') if status else 'unknown',
            entry_sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest()))
    return found


class HistoryArchive:
    def __init__(self, store):
        self.store = store

    def _path(self, value):
        relative = normalize_scope(value)
        if any(part.startswith('.') for part in Path(relative).parts):
            raise FrameworkError('历史来源不能位于私有或隐藏目录')
        path = self.store.root / relative
        try:
            path.resolve().relative_to(self.store.root)
        except ValueError as exc:
            raise FrameworkError('历史来源不能越出项目目录') from exc
        for parent in (path, *path.parents):
            if parent == self.store.root:
                break
            if parent.is_symlink():
                raise FrameworkError('历史来源路径不能经过链接')
        return relative, path

    def configure(self, sources, request_id):
        def apply(tx):
            if not isinstance(sources, list) or not 1 <= len(sources) <= 16:
                raise FrameworkError('须明确登记1至16个历史来源，不能全仓自动扫描')
            result, seen = [], set()
            for source in sources:
                if not isinstance(source, dict) or not {'path', 'format'} <= set(source) or set(source) - {'path', 'format', 'categories'}:
                    raise FrameworkError('每个历史来源须含path、format及可选categories')
                path, _ = self._path(source['path'])
                tx.require_scope(path)
                if path in seen or source['format'] not in {'yaml-records', 'markdown', 'document'}:
                    raise FrameworkError('重复来源或未知历史格式')
                seen.add(path)
                categories = self._categories(source)
                result.append(dict(path=path, format=source['format'], categories=categories))
            return tx.create('history_config', dict(sources=result, authority='historical_reference', api=1))
        return self.store.mutate('history.configure', dict(sources=sources), request_id, 'plan', apply)

    def _sources(self):
        configs = self.store.list('history_config')
        return configs[-1]['data']['sources'] if configs else []

    @staticmethod
    def _categories(source):
        categories = source.get('categories')
        if 'categories' not in source:
            name = Path(source['path']).stem.upper()
            categories = ['risks' if name == 'FAILURES' else 'decisions' if name in {'DECISIONS', 'ASSUMPTIONS'} else 'implementations']
        if not isinstance(categories, list) or not categories or any(not isinstance(c, str) or c not in {'risks', 'decisions', 'implementations'} for c in categories):
            raise FrameworkError('历史分类须为非空的已知分类列表')
        return list(dict.fromkeys(categories))

    def _read(self, source):
        relative, path = self._path(source['path'])
        try:
            before = file_signature(path.lstat())
            content = read_regular(path, MAX_SOURCE_BYTES, time.monotonic()+5)
            if file_signature(path.lstat()) != before:
                raise OSError('读取期间历史文件变化')
            text = content.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError('含二进制空字节')
        except (OSError, UnicodeError, ValueError) as exc:
            raise FrameworkError('历史来源未通过有界UTF-8读取: '+relative) from exc
        return content, text

    def _database(self):
        folder = self.store.home / 'cache'
        folder.mkdir(exist_ok=True)
        connection = sqlite3.connect(folder/'history.sqlite3', timeout=15)
        connection.row_factory = sqlite3.Row
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS history_sources(path TEXT PRIMARY KEY,checksum TEXT,format TEXT);
            CREATE TABLE IF NOT EXISTS history_snapshots(path TEXT,checksum TEXT,format TEXT,PRIMARY KEY(path,checksum));
            CREATE TABLE IF NOT EXISTS history_snapshot_formats(path TEXT,checksum TEXT,format TEXT,PRIMARY KEY(path,checksum,format));
            CREATE TABLE IF NOT EXISTS history_entries(path TEXT,legacy_id TEXT,text TEXT,start_line INTEGER,end_line INTEGER,legacy_status TEXT,entry_sha256 TEXT,source_sha256 TEXT,UNIQUE(path,legacy_id));
            CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(legacy_id,text,tokenize='trigram');
        ''')
        return connection

    def _refresh(self, connection, sources):
        total, count = 0, 0
        connection.execute('BEGIN IMMEDIATE')
        # 只删除派生缓存；正式账本、内容快照和已见快照映射均保留。
        active = {source['path'] for source in sources}
        for row in connection.execute('SELECT path FROM history_sources').fetchall():
            if row['path'] not in active:
                self._clear(connection, row['path'])
        for source in sources:
            content, text = self._read(source)
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise FrameworkError('历史来源总字节数超过预算')
            checksum = hashlib.sha256(content).hexdigest()
            previous = connection.execute('SELECT checksum,format FROM history_sources WHERE path=?', (source['path'],)).fetchone()
            if previous and previous['checksum'] == checksum and previous['format'] == source['format']:
                count += connection.execute('SELECT COUNT(*) FROM history_entries WHERE path=?', (source['path'],)).fetchone()[0]
                continue
            items = records(text, source['format'])
            count += len(items)
            if count > MAX_RECORDS:
                raise FrameworkError('历史来源总条目数超过预算')
            self.store.snapshot(content)
            self._clear(connection, source['path'])
            for item in items:
                cursor = connection.execute('INSERT INTO history_entries VALUES(?,?,?,?,?,?,?,?)',
                    (source['path'], item['legacy_id'], item['text'], item['start_line'], item['end_line'], item['legacy_status'], item['entry_sha256'], checksum))
                connection.execute('INSERT INTO history_fts(rowid,legacy_id,text) VALUES(?,?,?)', (cursor.lastrowid,item['legacy_id'],item['text']))
            connection.execute('INSERT INTO history_sources VALUES(?,?,?)', (source['path'],checksum,source['format']))
            connection.execute('INSERT OR IGNORE INTO history_snapshots VALUES(?,?,?)', (source['path'],checksum,source['format']))
            connection.execute('INSERT OR IGNORE INTO history_snapshot_formats VALUES(?,?,?)', (source['path'],checksum,source['format']))
        if count > MAX_RECORDS:
            raise FrameworkError('历史来源总条目数超过预算')
        connection.commit()
        return count

    @staticmethod
    def _clear(connection, path):
        connection.execute('DELETE FROM history_fts WHERE rowid IN (SELECT rowid FROM history_entries WHERE path=?)', (path,))
        connection.execute('DELETE FROM history_entries WHERE path=?', (path,))
        connection.execute('DELETE FROM history_sources WHERE path=?', (path,))

    def collect(self, query, limit=12, paths=None):
        if not isinstance(query, str) or not query.strip():
            raise FrameworkError('历史检索问题不能为空')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise FrameworkError('历史候选条数须为1至100')
        sources = self._sources()
        configured = {source['path']: source for source in sources}
        if paths is not None and (not isinstance(paths, list) or not paths or any(not isinstance(p, str) or p not in configured for p in paths)):
            raise FrameworkError('历史来源筛选须为已登记路径的非空列表')
        selected = set(paths) if paths is not None else set(configured)
        result = dict(configured=bool(sources), matches=[], sources=[], coverage=COVERAGE, candidate_truncated=False)
        if not sources:
            return result
        connection = self._database()
        try:
            self._refresh(connection, sources)
            for source in sources:
                info = connection.execute('SELECT checksum FROM history_sources WHERE path=?', (source['path'],)).fetchone()
                count = connection.execute('SELECT COUNT(*) FROM history_entries WHERE path=?', (source['path'],)).fetchone()[0]
                result['sources'].append(dict(path=source['path'], format=source['format'], categories=self._categories(source), sha256=info['checksum'], records=count))
            exact = connection.execute('SELECT * FROM history_entries WHERE legacy_id=? ORDER BY path', (query.strip(),)).fetchall()
            tokens, short = phrases(query)
            rows = []
            for path in sorted(selected):
                if tokens:
                    expression = ' OR '.join('"'+token.replace('"','""')+'"' for token in tokens)
                    found = connection.execute('SELECT e.*, rank AS _rank FROM history_fts f JOIN history_entries e ON e.rowid=f.rowid WHERE history_fts MATCH ? AND e.path=? ORDER BY rank,e.legacy_id LIMIT ?', (expression,path,limit+1)).fetchall()
                    result['candidate_truncated'] |= len(found) > limit
                    rows.extend(found)
                if short:
                    needles = ['%'+part.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%' for part in short]
                    clause = ' OR '.join("text LIKE ? ESCAPE '\\'" for _ in needles)
                    found = connection.execute('SELECT *, NULL AS _rank FROM history_entries WHERE path=? AND ('+clause+') ORDER BY legacy_id LIMIT ?', [path,*needles,limit+1]).fetchall()
                    result['candidate_truncated'] |= len(found) > limit
                    rows.extend(found)
            rows.sort(key=lambda row: (row['_rank'] is None, row['_rank'] or 0, row['path'], row['legacy_id']))
            chosen, seen = [], set()
            def add(row):
                key = row['path'], row['legacy_id']
                if row['path'] in selected and key not in seen and len(chosen) < limit:
                    chosen.append(row)
                    seen.add(key)
            for row in exact:
                add(row)
            for category in ('risks', 'decisions', 'implementations'):
                if any(category in self._categories(configured[r['path']]) for r in chosen):
                    continue
                row = next((r for r in rows if category in self._categories(configured[r['path']]) and (r['path'],r['legacy_id']) not in seen), None)
                if row is not None:
                    add(row)
            for row in rows:
                add(row)
            all_keys = {(row['path'], row['legacy_id']) for row in list(exact)+rows if row['path'] in selected}
            result['candidate_truncated'] |= len(all_keys) > len(chosen)
            result['matches'] = [{**{key:row[key] for key in row.keys() if key != '_rank'}, 'categories':self._categories(configured[row['path']]), 'source_format':configured[row['path']]['format']} for row in chosen]
            return result
        finally:
            connection.close()

    def query(self, query, limit=12, max_chars=10000, paths=None):
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1000 <= max_chars <= 50000:
            raise FrameworkError('历史显示预算须为1000至50000字符')
        collected = self.collect(query, limit, paths)
        result = dict(configured=collected['configured'], matches=[], coverage=collected['coverage'],
            candidate_truncated=collected['candidate_truncated'], display_truncated=False,
            indexed_records=sum(s['records'] for s in collected['sources']),
            source_coverage=dict(configured=len(collected['sources']), selected=len(set(paths)) if paths is not None else len(collected['sources'])))
        for row in collected['matches']:
            body, start, end = window(row['text'],query,600)
            card = dict(legacy_id=row['legacy_id'],authority='historical_reference',legacy_status=row['legacy_status'], categories=row['categories'],
                body=body,partial=start>0 or end<len(row['text']), source=dict(path=row['path'],sha256=row['source_sha256'],
                    format=row['source_format'],entry_sha256=row['entry_sha256'],start_line=row['start_line'],end_line=row['end_line'],fragment_start=start,fragment_end=end))
            result['matches'].append(card)
            if encoded_size(result)>max_chars:
                result['matches'].pop()
                result['display_truncated']=True
                break
            result['display_truncated'] = result['display_truncated'] or card['partial']
        if encoded_size(result)>max_chars:
            raise FrameworkError('历史检索元信息超出预算')
        return result

    def read(self, path, legacy_id, source_sha256, offset=0, max_chars=10000, source_format=None):
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise FrameworkError('分页偏移须为非负整数')
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1000 <= max_chars <= 50000:
            raise FrameworkError('历史显示预算须为1000至50000字符')
        if not isinstance(source_sha256, str) or not source_sha256:
            raise FrameworkError('分页须指定已记录来源快照')
        item = self.get(path, legacy_id, source_sha256, source_format=source_format)
        content = item.pop('text')
        if offset > len(content):
            raise FrameworkError('分页偏移超出正文')
        result = {**item, 'text':'', 'offset':offset, 'next_offset':None, 'partial':offset > 0}
        if encoded_size(result) > max_chars:
            raise FrameworkError('历史分页元信息超出预算')
        low, high = 0, min(len(content)-offset, max_chars)
        while low < high:
            size = (low+high+1)//2
            end = offset+size
            result.update(text=content[offset:end], next_offset=end if end < len(content) else None, partial=offset>0 or end<len(content))
            if encoded_size(result) <= max_chars:
                low = size
            else:
                high = size-1
        end = offset+low
        if end == offset and end < len(content):
            raise FrameworkError('历史分页预算不足以容纳正文')
        result.update(text=content[offset:end], next_offset=end if end<len(content) else None, partial=offset>0 or end<len(content))
        if encoded_size(result)>max_chars:
            raise FrameworkError('历史分页元信息超出预算')
        return result

    def get(self, path, legacy_id, source_sha256=None, source_format=None):
        sources = {source['path']:source for source in self._sources()}
        if path not in sources:
            raise FrameworkError('未登记该历史来源')
        if source_sha256 is None:
            content,text = self._read(sources[path])
            checksum=hashlib.sha256(content).hexdigest()
            formats=[sources[path]['format']]
            if source_format is not None and source_format != formats[0]:
                raise FrameworkError('当前来源未使用指定格式')
        else:
            connection=self._database()
            try:
                rows=connection.execute('SELECT format FROM history_snapshots WHERE path=? AND checksum=? UNION SELECT format FROM history_snapshot_formats WHERE path=? AND checksum=?', (path,source_sha256,path,source_sha256)).fetchall()
                formats=[row['format'] for row in rows]
                if not formats:
                    raise FrameworkError('未记录该来源的指定快照，不能猜测关联')
                if source_format is not None:
                    if source_format not in formats:
                        raise FrameworkError('该快照未记录指定来源格式')
                    formats=[source_format]
                content=self.store.blob(source_sha256)
                text=content.decode('utf-8-sig')
                checksum=source_sha256
            finally:
                connection.close()
        matches=[]
        for format in formats:
            match=next((item for item in records(text,format) if item['legacy_id']==legacy_id),None)
            if match is not None:
                matches.append((format,match))
        if not matches:
            raise FrameworkError('指定快照中未找到旧编号')
        if any(match != matches[0][1] for _,match in matches[1:]):
            raise FrameworkError('同一编号存在不同格式解释，请明确指定source_format')
        format,match=matches[0]
        return {**match,'path':path,'source_sha256':checksum,'source_format':format,'authority':'historical_reference'}


def session_context(store, output_prefix=None, query=None, max_chars=10000, task_id=None):
    """共同接手入口，保留原参数与调用位置。"""
    from .session import context
    return context(store,output_prefix=output_prefix,query=query,max_chars=max_chars,task_id=task_id)
