"""Use Turso libSQL as durable storage, with SQLite for local development.

All application stores share the cloud database. Local paths remain unchanged
when Turso is not configured. A failed cloud connection never opens a local DB.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from urllib.parse import urlsplit


# Gunicorn uses one process with request/job threads. Serialize cloud write
# transactions within that process, without blocking unrelated read connections.
_CLOUD_WRITE_LOCK = threading.RLock()


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

    @property
    def description(self):
        return _call(lambda: self._raw.description)

    @property
    def rowcount(self):
        return _call(lambda: self._raw.rowcount)

    @property
    def lastrowid(self):
        return _call(lambda: self._raw.lastrowid)

    def _row(self, values):
        if values is None:
            return None
        factory = self.connection.row_factory
        if factory is sqlite3.Row:
            return _NamedRow(self.description, values)
        return factory(self, values) if factory else values

    def execute(self, sql, parameters=()):
        self.connection._execute(self._raw.execute, sql, parameters)
        return self

    def executemany(self, sql, parameters):
        self.connection._execute(self._raw.executemany, sql, parameters)
        return self

    def executescript(self, sql):
        self.connection._execute(self._raw.executescript, sql, script=True)
        return self

    def fetchone(self):
        return self._row(_call(self._raw.fetchone))

    def fetchall(self):
        return [self._row(row) for row in (_call(self._raw.fetchall) or [])]

    def fetchmany(self, size=1):
        return [self._row(row) for row in (_call(self._raw.fetchmany, size) or [])]

    def close(self):
        _call(self._raw.close)

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class _CloudConnection:
    is_remote = True

    def __init__(self, raw):
        self._raw = raw
        self.row_factory = None
        self._write_locked = False

    def _release_write_lock(self):
        if self._write_locked:
            self._write_locked = False
            _CLOUD_WRITE_LOCK.release()

    def _execute(self, method, sql, *args, script=False):
        command = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        writes = script or command in {
            "BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "VACUUM",
        }
        if writes and not self._write_locked:
            if not _CLOUD_WRITE_LOCK.acquire(timeout=10):
                raise sqlite3.OperationalError("外部DBの書き込みが混雑しています。少し待って再試行してください。")
            self._write_locked = True
        try:
            return _call(method, sql, *args)
        finally:
            if self._write_locked and not self.in_transaction:
                self._release_write_lock()

    @property
    def in_transaction(self):
        return _call(lambda: self._raw.in_transaction)

    def cursor(self):
        return _Cursor(self, _call(self._raw.cursor))

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql):
        return self.cursor().executescript(sql)

    def commit(self):
        _call(self._raw.commit)
        self._release_write_lock()

    def rollback(self):
        try:
            _call(self._raw.rollback)
        finally:
            self._release_write_lock()

    def close(self):
        try:
            _call(self._raw.close)
        finally:
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
    raw = _call(
        libsql.connect, database=url, auth_token=token, timeout=max(timeout, 10),
        isolation_level=isolation_level, _check_same_thread=check_same_thread,
    )
    return _CloudConnection(raw)
