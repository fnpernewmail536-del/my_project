"""Use Turso libSQL as durable storage, with SQLite for local development.

All application stores share the cloud database. Local paths remain unchanged
when Turso is not configured. A failed cloud connection never opens a local DB.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from urllib.parse import urlsplit


# Gunicorn uses one process with request/job threads. Serialize cloud write
# transactions within that process, without blocking unrelated read connections.
_CLOUD_WRITE_LOCK = threading.RLock()
_POOL_LOCK = threading.Lock()
_IDLE_CONNECTIONS = []
_POOL_PID = os.getpid()
_POOL_MAX_IDLE = 4
_POOL_IDLE_SECONDS = 30
_INHERITED_POOLS = []


def _reset_after_fork():
    global _CLOUD_WRITE_LOCK, _POOL_LOCK, _IDLE_CONNECTIONS, _POOL_PID
    _CLOUD_WRITE_LOCK = threading.RLock()
    _POOL_LOCK = threading.Lock()
    # Never operate on or destroy a parent's native driver state in the child.
    _INHERITED_POOLS.append(_IDLE_CONNECTIONS)
    _IDLE_CONNECTIONS = []
    _POOL_PID = os.getpid()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def _quiet_close(raw):
    try:
        raw.close()
    except Exception:
        pass


def clear_connection_pool():
    """Discard idle connections, e.g. after credentials or a test primary change."""
    with _POOL_LOCK:
        idle = list(_IDLE_CONNECTIONS)
        _IDLE_CONNECTIONS.clear()
    for _, _, raw, _ in idle:
        _quiet_close(raw)


def _take_connection(key):
    expired = []
    found = None
    now = time.monotonic()
    with _POOL_LOCK:
        for item in list(_IDLE_CONNECTIONS):
            if now - item[1] >= _POOL_IDLE_SECONDS or item[0][:2] != key[:2]:
                _IDLE_CONNECTIONS.remove(item)
                expired.append(item[2])
        for item in reversed(_IDLE_CONNECTIONS):
            if item[0] == key:
                _IDLE_CONNECTIONS.remove(item)
                found = (item[2], item[3])
                break
    for raw in expired:
        _quiet_close(raw)
    return found


def _return_connection(key, raw, foreign_keys, pid):
    with _POOL_LOCK:
        if pid == _POOL_PID == os.getpid() and len(_IDLE_CONNECTIONS) < _POOL_MAX_IDLE:
            _IDLE_CONNECTIONS.append((key, time.monotonic(), raw, foreign_keys))
            return
    _quiet_close(raw)


def storage_config() -> tuple[str, str]:
    url = os.getenv("TURSO_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    if bool(url) != bool(token):
        raise RuntimeError("TURSO_DATABASE_URL と TURSO_AUTH_TOKEN を両方設定してください。")
    if url:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"libsql", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RuntimeError("TURSO_DATABASE_URL には libSQL DB の接続URLを設定してください。")
        if not os.getenv("FLASK_SECRET_KEY", "").strip():
            raise RuntimeError("外部DBでは固定の FLASK_SECRET_KEY が必要です。")
    elif os.getenv("REQUIRE_PERSISTENT_DB", "0") == "1":
        raise RuntimeError("永続DBが未設定です。Tursoの接続情報を設定してください。")
    return url, token


def remote_enabled() -> bool:
    return bool(storage_config()[0])


def _call(method, *args, **kwargs):
    """Keep existing sqlite3 error handling, without leaking remote credentials."""
    try:
        return method(*args, **kwargs)
    except Exception as exc:
        message = str(exc).lower()
        if "constraint failed" in message or "sqlite_constraint" in message:
            raise sqlite3.IntegrityError("database constraint failed") from None
        if "duplicate column name" in message:
            raise sqlite3.OperationalError("duplicate column name") from None
        raise sqlite3.OperationalError(
            "外部DBの処理に失敗しました。Tursoの接続・認証・利用枠を確認してください。"
        ) from None


class _NamedRow:
    """The index/name access and keys() used by sqlite3.Row callers."""

    def __init__(self, description, values):
        self._values = tuple(values)
        self._names = tuple(column[0] for column in description)

    def keys(self):
        return list(self._names)

    def __getitem__(self, key):
        if isinstance(key, str):
            for index, name in enumerate(self._names):
                if name.casefold() == key.casefold():
                    return self._values[index]
            raise IndexError("No item with that key")
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class _Cursor:
    def __init__(self, connection, raw):
        self.connection = connection
        self._raw = raw
        self._description = None
        self._rowcount = -1
        self._lastrowid = None
        self._closed = False

    def _check_open(self):
        self.connection._check_open()
        if self._closed:
            raise sqlite3.ProgrammingError("database cursor is closed")

    def _capture_metadata(self):
        self._description = self.connection._driver_call(lambda: self._raw.description)
        self._rowcount = self.connection._driver_call(lambda: self._raw.rowcount)
        self._lastrowid = self.connection._driver_call(lambda: self._raw.lastrowid)

    @property
    def description(self):
        return self._description

    @property
    def rowcount(self):
        return self._rowcount

    @property
    def lastrowid(self):
        return self._lastrowid

    def _row(self, values):
        if values is None:
            return None
        factory = self.connection.row_factory
        if factory is sqlite3.Row:
            return _NamedRow(self.description, values)
        return factory(self, values) if factory else values

    def execute(self, sql, parameters=()):
        self._check_open()
        self.connection._execute(self._raw.execute, sql, parameters)
        self._capture_metadata()
        return self

    def executemany(self, sql, parameters):
        self._check_open()
        self.connection._execute(self._raw.executemany, sql, parameters)
        self._capture_metadata()
        return self

    def executescript(self, sql):
        self._check_open()
        self.connection._execute(self._raw.executescript, sql, script=True)
        self._capture_metadata()
        return self

    def fetchone(self):
        self._check_open()
        return self._row(self.connection._driver_call(self._raw.fetchone))

    def fetchall(self):
        self._check_open()
        return [self._row(row) for row in (self.connection._driver_call(self._raw.fetchall) or [])]

    def fetchmany(self, size=1):
        self._check_open()
        return [self._row(row) for row in (self.connection._driver_call(self._raw.fetchmany, size) or [])]

    def close(self):
        if not self._closed and not self.connection._closed:
            self.connection._driver_call(self._raw.close)
            self._closed = True

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class _CloudConnection:
    is_remote = True

    def __init__(self, raw, pool_key=None, foreign_keys=None):
        self._raw = raw
        self.row_factory = None
        self._write_locked = False
        self._pool_key = pool_key
        self._foreign_keys = foreign_keys
        self._closed = False
        self._broken = False
        self._pid = os.getpid()
        self._cursors = []

    def _check_open(self):
        if self._closed or self._pid != os.getpid():
            raise sqlite3.ProgrammingError("database connection is closed")

    def _driver_call(self, method, *args, **kwargs):
        try:
            return _call(method, *args, **kwargs)
        except sqlite3.OperationalError:
            self._broken = True
            raise

    def _release_write_lock(self):
        if self._write_locked:
            self._write_locked = False
            _CLOUD_WRITE_LOCK.release()

    def _execute(self, method, sql, *args, script=False):
        self._check_open()
        command = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        writes = script or command in {
            "BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "VACUUM",
        }
        if writes and not self._write_locked:
            if not _CLOUD_WRITE_LOCK.acquire(timeout=10):
                raise sqlite3.OperationalError("外部DBの書き込みが混雑しています。少し待って再試行してください。")
            self._write_locked = True
        try:
            result = self._driver_call(method, sql, *args)
            normalized = sql.strip().rstrip(";").replace(" ", "").upper()
            if normalized == "PRAGMAFOREIGN_KEYS=ON":
                self._foreign_keys = True
            elif normalized == "PRAGMAFOREIGN_KEYS=OFF":
                self._foreign_keys = False
            elif script:
                self._foreign_keys = None
            return result
        except sqlite3.OperationalError:
            self._broken = True
            raise
        finally:
            if self._write_locked and not self.in_transaction:
                self._release_write_lock()

    @property
    def in_transaction(self):
        self._check_open()
        return self._driver_call(lambda: self._raw.in_transaction)

    def cursor(self):
        self._check_open()
        cursor = _Cursor(self, self._driver_call(self._raw.cursor))
        self._cursors.append(cursor)
        return cursor

    def execute(self, sql, parameters=()):
        if sql.strip().rstrip(";").replace(" ", "").upper() == "PRAGMAFOREIGN_KEYS=ON" and self._foreign_keys is True:
            self._check_open()
            return self.cursor()
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql):
        return self.cursor().executescript(sql)

    def commit(self):
        self._check_open()
        try:
            _call(self._raw.commit)
        except sqlite3.OperationalError:
            self._broken = True
            raise
        self._release_write_lock()

    def rollback(self):
        self._check_open()
        try:
            _call(self._raw.rollback)
        except sqlite3.OperationalError:
            self._broken = True
            raise
        finally:
            self._release_write_lock()

    def close(self):
        if self._closed:
            return
        if self._pid != os.getpid():
            self._closed = True
            self._write_locked = False
            _INHERITED_POOLS.append(self)
            return
        try:
            if self._broken:
                _quiet_close(self._raw)
                return
            if self.in_transaction:
                self.rollback()
            for cursor in self._cursors:
                cursor.close()
            if self._pool_key is not None and not self._broken and self._foreign_keys is not False:
                _return_connection(self._pool_key, self._raw, self._foreign_keys, self._pid)
            else:
                _quiet_close(self._raw)
        except Exception:
            _quiet_close(self._raw)
            raise
        finally:
            self._closed = True
            self._cursors.clear()
            self._release_write_lock()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()


def connect(database, *, timeout=10, isolation_level="", check_same_thread=True):
    url, token = storage_config()
    if not url:
        return sqlite3.connect(
            database, timeout=timeout, isolation_level=isolation_level,
            check_same_thread=check_same_thread,
        )
    try:
        import libsql
    except ImportError:
        raise RuntimeError("libsql が必要です。requirements.txt をインストールしてください。") from None
    key = (url, token, isolation_level, max(timeout, 10))
    pooling = os.getenv("TURSO_POOL_ENABLED", "1") != "0"
    if not pooling:
        clear_connection_pool()
    pooled = _take_connection(key) if pooling else None
    if pooled is not None:
        return _CloudConnection(pooled[0], key, pooled[1])
    raw = _call(
        libsql.connect, database=url, auth_token=token, timeout=max(timeout, 10),
        isolation_level=isolation_level, _check_same_thread=False,
    )
    return _CloudConnection(raw, key if pooling else None)
