"""本地权威存储：对象、版本、事件和请求结果共同提交。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import sqlite3
import tempfile
import uuid

from .errors import ConflictError, FrameworkError, PermissionDenied


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normalize_scope(value):
    if not isinstance(value, str):
        raise FrameworkError("路径必须为字符串")
    path = Path(value)
    windows = PureWindowsPath(value)
    if path.anchor or windows.drive or windows.root or ".." in path.parts or ".." in windows.parts or not value.strip():
        raise FrameworkError("范围必须是项目内的相对路径")
    if os.name == "nt" and any(part.rstrip(" .") != part for part in path.parts):
        raise FrameworkError("Windows路径不能带末尾空格或点")
    return path.as_posix().rstrip("/") or "."


def within(path, scope):
    if os.name == "nt":
        path, scope = path.casefold(), scope.casefold()
    return scope == "." or path == scope or path.startswith(scope + "/")


class Transaction:
    """只在已开启的权威写事务中使用。"""

    def __init__(self, store, connection, principal):
        self.store = store
        self.connection = connection
        self.principal = principal
        self.changes = []

    def get(self, identifier, revision=None):
        return self.store._get(self.connection, identifier, revision)

    def list(self, kind=None):
        return self.store._list(self.connection, kind)

    def require_scope(self, path):
        path = normalize_scope(path)
        if not any(within(path, scope) for scope in self.principal["scopes"]):
            raise PermissionDenied("写入范围未获准: " + path)

    def create(self, kind, data):
        if not re.fullmatch(r"[a-z][a-z_-]{0,30}", kind):
            raise FrameworkError("记录类型不合法")
        identifier = kind.upper() + "-" + uuid.uuid4().hex[:16]
        created = now()
        payload = canonical(data)
        self.connection.execute("INSERT INTO objects VALUES(?,?,?,?,?)", (identifier, kind, 1, payload, created))
        self.connection.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (identifier, 1, kind, payload, created))
        self.changes.append({"id": identifier, "rev": 1, "digest": digest(data)})
        return self.get(identifier)

    def replace(self, identifier, expected_revision, data):
        old = self.get(identifier)
        if old["rev"] != expected_revision:
            raise ConflictError("对象版本已经变化")
        revision = expected_revision + 1
        payload, created = canonical(data), now()
        self.connection.execute("UPDATE objects SET rev=?,payload=?,created=? WHERE id=? AND rev=?", (revision, payload, created, identifier, expected_revision))
        self.connection.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (identifier, revision, old["kind"], payload, created))
        self.changes.append({"id": identifier, "rev": revision, "digest": digest(data)})
        return self.get(identifier)


class Store:
    """单项目、单权威目录；支持多个本地进程访问。"""

    def __init__(self, root, token=None):
        self.root = Path(root).resolve()
        self.home = self.root / ".aireos"
        self.database = self.home / "state.sqlite3"
        self.token = token
        if not self.database.is_file():
            raise FrameworkError("项目尚未初始化")

    @classmethod
    def initialize(cls, root):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        home = root / ".aireos"
        try:
            home.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise ConflictError("项目记录已存在，拒绝覆盖初始化") from exc
        for directory in ("objects", "keys", "runs", "views"):
            (home / directory).mkdir()
        token = secrets.token_urlsafe(32)
        keyfile = home / "owner.key"
        with keyfile.open("x", encoding="utf-8") as stream:
            stream.write(token + "\n")
        os.chmod(keyfile, 0o600)
        connection = sqlite3.connect(home / "state.sqlite3")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE objects(id TEXT PRIMARY KEY,kind TEXT NOT NULL,rev INTEGER NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
                CREATE TABLE versions(id TEXT NOT NULL,rev INTEGER NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(id,rev));
                CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT NOT NULL,actor TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
                CREATE TABLE requests(request_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,result TEXT NOT NULL,event_seq INTEGER NOT NULL);
                CREATE TABLE principals(actor TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,capabilities TEXT NOT NULL,scopes TEXT NOT NULL);
            """)
            connection.executemany("INSERT INTO meta VALUES(?,?)", [("schema_version", "1"), ("project_id", uuid.uuid4().hex), ("corpus_epoch", "0")])
            connection.execute("INSERT INTO principals VALUES(?,?,?,?)", ("owner", hashlib.sha256(token.encode()).hexdigest(), canonical(["*"]), canonical(["."])))
            connection.commit()
        finally:
            connection.close()
        return {"project": str(root), "credential_file": str(keyfile), "schema_version": 1}

    @contextmanager
    def connection(self, write=False):
        connection = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if not version or version[0] != "1":
                raise FrameworkError("不支持的存储版本")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _principal(self, connection, capability):
        token_hash = hashlib.sha256((self.token or "").encode()).hexdigest()
        row = connection.execute("SELECT * FROM principals WHERE token_hash=?", (token_hash,)).fetchone()
        if row is None:
            raise PermissionDenied("缺少有效作用域凭据；角色名称不是凭据")
        caps = json.loads(row["capabilities"])
        if capability is not None and "*" not in caps and capability not in caps:
            raise PermissionDenied("没有动作能力: " + capability)
        return {"actor": row["actor"], "capabilities": caps, "scopes": json.loads(row["scopes"])}

    @staticmethod
    def _row(row):
        if row is None:
            raise FrameworkError("记录或版本不存在")
        return {"id": row["id"], "kind": row["kind"], "rev": row["rev"], "data": json.loads(row["payload"]), "created": row["created"],
                **({"sequence": row["ordinal"]} if "ordinal" in row.keys() else {})}

    def _get(self, connection, identifier, revision=None):
        if revision is None:
            row = connection.execute("SELECT v.*,v.rowid AS ordinal FROM versions v JOIN objects o ON o.id=v.id AND o.rev=v.rev WHERE o.id=?", (identifier,)).fetchone()
        else:
            row = connection.execute("SELECT *,rowid AS ordinal FROM versions WHERE id=? AND rev=?", (identifier, revision)).fetchone()
        return self._row(row)

    def _list(self, connection, kind=None):
        if kind is None:
            rows = connection.execute("SELECT v.*,v.rowid AS ordinal FROM versions v JOIN objects o ON o.id=v.id AND o.rev=v.rev ORDER BY v.rowid").fetchall()
        else:
            rows = connection.execute("SELECT v.*,v.rowid AS ordinal FROM versions v JOIN objects o ON o.id=v.id AND o.rev=v.rev WHERE v.kind=? ORDER BY v.rowid", (kind,)).fetchall()
        return [self._row(row) for row in rows]

    def get(self, identifier, revision=None):
        with self.connection() as connection:
            return self._get(connection, identifier, revision)

    def list(self, kind=None):
        with self.connection() as connection:
            return self._list(connection, kind)

    def events(self):
        with self.connection() as connection:
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in connection.execute("SELECT * FROM events ORDER BY seq")]

    def epoch(self, connection=None):
        if connection is None:
            with self.connection() as current:
                return self.epoch(current)
        return int(connection.execute("SELECT value FROM meta WHERE key='corpus_epoch'").fetchone()[0])

    def project_id(self, connection=None):
        if connection is None:
            with self.connection() as current:
                return self.project_id(current)
        return connection.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()[0]

    def mutate(self, operation, payload, request_id, capability, function, corpus=False):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise FrameworkError("写入必须有明确且有限长度的 request_id")
        with self.connection(write=True) as connection:
            principal = self._principal(connection, capability)
            fingerprint = digest({"operation": operation, "actor": principal["actor"], "payload": payload})
            previous = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["payload_hash"] != fingerprint:
                    raise ConflictError("相同请求身份对应不同内容或执行者")
                return {**json.loads(previous["result"]), "_replayed": True}
            tx = Transaction(self, connection, principal)
            result = function(tx)
            if not isinstance(result, dict):
                raise FrameworkError("受管动作必须返回对象")
            cursor = connection.execute("INSERT INTO events(action,actor,payload,created) VALUES(?,?,?,?)", (operation, principal["actor"], canonical({"input": payload, "changes": tx.changes}), now()))
            if corpus:
                connection.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='corpus_epoch'")
            connection.execute("INSERT INTO requests VALUES(?,?,?,?)", (request_id, fingerprint, canonical(result), cursor.lastrowid))
            return {**result, "_replayed": False}

    def create(self, kind, data, request_id):
        return self.mutate("create", {"kind": kind, "data": data}, request_id, "write", lambda tx: tx.create(kind, data), corpus=True)

    def replace(self, identifier, expected_revision, data, request_id):
        return self.mutate("replace", {"id": identifier, "rev": expected_revision, "data": data}, request_id, "write", lambda tx: tx.replace(identifier, expected_revision, data), corpus=True)

    def grant(self, actor, capabilities, scopes, request_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", actor):
            raise FrameworkError("执行者名称不合法")
        allowed = {"*", "plan", "execute", "review", "decide", "publish", "tools", "write", "coordinate"}
        if not capabilities or not set(capabilities) <= allowed:
            raise FrameworkError("动作能力不合法")
        scopes = [normalize_scope(scope) for scope in scopes]
        if not scopes:
            raise FrameworkError("必须明确输出范围")
        payload = {"actor": actor, "capabilities": capabilities, "scopes": scopes}
        def apply(tx):
            own_caps = tx.principal["capabilities"]
            if "*" not in own_caps and not set(capabilities) <= set(own_caps):
                raise PermissionDenied("不能授予自己没有的能力")
            for scope in scopes:
                tx.require_scope(scope)
            if tx.connection.execute("SELECT 1 FROM principals WHERE actor=?", (actor,)).fetchone():
                raise ConflictError("执行者已登记")
            keyfile = self.home / "keys" / (actor + "-" + digest([request_id, payload])[:16] + ".key")
            if keyfile.is_symlink():
                raise PermissionDenied("凭据文件不能是链接")
            if keyfile.exists():
                token = keyfile.read_text(encoding="utf-8").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                    raise FrameworkError("未完成授予所留凭据格式异常，请归档后调查")
            else:
                token = secrets.token_urlsafe(32)
                with keyfile.open("x", encoding="utf-8") as stream:
                    stream.write(token + "\n")
            os.chmod(keyfile, 0o600)
            tx.connection.execute("INSERT INTO principals VALUES(?,?,?,?)", (actor, hashlib.sha256(token.encode()).hexdigest(), canonical(capabilities), canonical(scopes)))
            return {"actor": actor, "capabilities": capabilities, "scopes": scopes, "credential_file": str(keyfile)}
        return self.mutate("grant", payload, request_id, "plan", apply)

    def snapshot(self, data):
        checksum = hashlib.sha256(data).hexdigest()
        destination = self.home / "objects" / checksum
        if destination.exists():
            if destination.read_bytes() != data:
                raise FrameworkError("快照损坏")
            return checksum
        with tempfile.NamedTemporaryFile(dir=self.home / "objects", delete=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, destination)
        return checksum

    def blob(self, checksum):
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise FrameworkError("快照摘要不合法")
        try:
            data = (self.home / "objects" / checksum).read_bytes()
        except OSError as exc:
            raise FrameworkError("快照不可取得") from exc
        if hashlib.sha256(data).hexdigest() != checksum:
            raise FrameworkError("快照摘要不符")
        return data
