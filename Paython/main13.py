from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_from_directory
import os
import re
import copy
import base64
import gzip
import json
import csv
import io
import tempfile
import unicodedata
import uuid
import secrets
import hashlib
import threading
import traceback
import time
import requests
from datetime import timedelta
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from job_timing import TimingStore, advance_timing, timing_profile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ACCOUNT.access_guard import register_access_guard
from ACCOUNT.account_routes import register_account_routes
from ACCOUNT.account_store import AccountPermissionError, AccountStore
from DISCORD.discord_log import send_usage_log
from free_usage import (
    FreeUsageError,
    FreeUsageManager,
    IdentityConflict,
    InvitationError,
    QuotaExhausted,
)

load_dotenv()

# =====================
# プロキシ設定（全通信共通）
# =====================
PROXY_URL = os.getenv("PROXY_URL", "").strip()
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else {}
if PROXY_URL:
    os.environ["HTTP_PROXY"] = PROXY_URL
    os.environ["HTTPS_PROXY"] = PROXY_URL
    os.environ["http_proxy"] = PROXY_URL
    os.environ["https_proxy"] = PROXY_URL

# ── requests.Session を継承してプロキシを強制注入 ──
import functools as _ft
_orig_requests_Session = requests.Session
class _ProxiedRequestsSession(_orig_requests_Session):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.proxies.update(PROXIES)
requests.Session = _ProxiedRequestsSession

# ── requests モジュールレベル関数にもプロキシを強制付与 ──
for _mn in ("get", "post", "put", "patch", "delete", "head", "options", "request"):
    _orig_fn = getattr(requests, _mn)
    def _make_proxied(fn):
        @_ft.wraps(fn)
        def _wrapped(*a, **kw):
            kw.setdefault("proxies", PROXIES)
            return fn(*a, **kw)
        return _wrapped
    setattr(requests, _mn, _make_proxied(_orig_fn))

# ── urllib.request グローバル opener にプロキシを強制設定 ──
import urllib.request as _urllib_req
_urllib_proxy_handler = _urllib_req.ProxyHandler(PROXIES)
_urllib_req.install_opener(_urllib_req.build_opener(_urllib_proxy_handler))

# プロキシ疎通確認（外部通信を伴うため明示有効時のみ）
if PROXY_URL and os.getenv("PROXY_CHECK_ON_STARTUP", "0") == "1":
    try:
        _r = requests.get("https://httpbin.org/ip", proxies=PROXIES, timeout=10)
        if _r.status_code == 200:
            print(f"[PROXY CHECK] OK - 外部IP: {_r.json()['origin']}")
        else:
            print(f"[PROXY CHECK] FAILED - HTTPステータス: {_r.status_code}")
    except Exception as _e:
        print(f"[PROXY CHECK] FAILED - {type(_e).__name__}")
elif not PROXY_URL:
    print("[PROXY CHECK] SKIPPED - PROXY_URLが未設定です")

# =====================
# 設定値
# =====================
MAX_API_KEYS = 5000          # api_keys 辞書の上限
API_KEY_TTL = 600            # APIキーの有効期間(秒)
MAX_WORKERS = 10              # ジョブ実行ワーカー数
MAX_INFLIGHT_JOBS = 30       # 同時に受け付ける未完了ジョブ数
JOB_TIMEOUT = 300            # 進捗が更新されないジョブのタイムアウト(秒)
JOB_ABSOLUTE_TIMEOUT = 600   # 進捗が続いていても待つ絶対上限(秒)
CLONE_JOB_TIMEOUT = 600      # 複製中の連続した認証・保存通信は最大10分待つ
CLONE_JOB_ABSOLUTE_TIMEOUT = 1800  # 複数コピーの連続通信を考慮して30分
MAX_CHAR_LIST = 300          # char_list の最大要素数
MAX_CHARACTER_SETTINGS = 1000  # レアリティ単位・最新版キャラの名前選択に対応
MAX_COUNT = 5                # 作成/複製の最大個数
MAX_LEGEND_SELECTIONS = 5000 # レジェンド系の章/星/ステージ選択数上限

# にゃんこ大戦争 JP 15.7.1を最低版とし、公式公開版を稼働中に自動確認。
# 同梱の定義データの版数とは分離し、取得できていない新版定義を推測しない。
TARGET_GAME_VERSION_NUMBER = 150701
BUNDLED_RANK_GIFT_VERSION_NUMBER = 150600
AUTO_GAME_UPDATE_ENABLED = os.getenv("AUTO_GAME_UPDATE_ENABLED", "1") != "0"
GAME_UPDATE_INTERVAL = 3600
GAME_UPDATE_RETRY_INTERVAL = 300
GAME_UPDATE_MAX_BYTES = 8 * 1024 * 1024
GAME_UPDATE_CACHE_PATH = os.getenv("GAME_UPDATE_CACHE_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "BACKUP", "game_updates.json")
_game_update_state = None
_game_update_state_lock = threading.RLock()
_game_update_refresh_lock = threading.Lock()
_game_update_start_lock = threading.Lock()
_game_update_stop = threading.Event()
_game_update_thread = None

# 通常ページから非表示にするだけでなく、APIへ直接送られてもVIP確認なしでは適用しない。
VIP_ONLY_SYSTEM_ACTIONS = {
    "hide_character_new",
    "hide_medal_new",
    "user_rank_rewards_claimed",
    "user_rank_rewards_unclaimed",
    "catguide_rewards_claimed",
    "catguide_rewards_unclaimed",
    "cat_scratcher_reset",
    "all_missions_clear",
    "labyrinth_medals",
    "ototo_detailed",
    "dojo_score_detailed",
    "future_score_detailed",
}

# =====================
# Discord OAuth2（チャット・管理者認証用）
# =====================
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

def _positive_env_int(name, fallback):
    try:
        value = int(os.getenv(name, str(fallback)))
        return value if 1 <= value <= 1_000_000 else fallback
    except (TypeError, ValueError):
        return fallback


VIP_PLAN_PRICES = {
    30: _positive_env_int("VIP_PRICE_30", 300),
    60: _positive_env_int("VIP_PRICE_60", 600),
    90: _positive_env_int("VIP_PRICE_90", 900),
}
VIP_PRICE_LABEL = f"30日{VIP_PLAN_PRICES[30]:,}円から"
VIP_PLAN_LABEL = os.getenv("VIP_PLAN_LABEL", "VIPプラン").strip() or "VIPプラン"

DISCORD_API = "https://discord.com/api"
OAUTH_SCOPES = "identify"

# =====================
# 入力フォーマット定義
# =====================
TRANSFER_CODE_RE = re.compile(r'^[0-9a-fA-F]{9}$')
AUTH_CODE_RE = re.compile(r'^\d{4}$')
# 「時間:分」形式のみ許可。時間は最大4桁(9999まで)、分は0〜59のみ。
PLAYTIME_RE = re.compile(r'^(\d{1,4}):([0-5]?\d)$')
OPERATION_ID_RE = re.compile(r'^[A-Za-z0-9]{30}$')
ADMIN_USER = (os.getenv("ADMIN_USER") or os.getenv("Admin_USER", "")).strip()
ADMIN_SNAPSHOT_KEY = os.getenv("ADMIN_SNAPSHOT_KEY", "").strip()


def validate_transfer_auth_codes(data):
    """引き継ぎコード(9桁数字)・認証番号(4桁数字)を厳密に検証。問題なければ None。"""
    tc = str(data.get("transfer_code", "")).strip()
    ac = str(data.get("auth_code", "")).strip()
    if not TRANSFER_CODE_RE.match(tc):
        return jsonify({"error": "引き継ぎコードは9桁の16進数（0-9,a-f）で入力してください"}), 400
    if not AUTH_CODE_RE.match(ac):
        return jsonify({"error": "認証番号は4桁の数字（0-9）で入力してください"}), 400
    return None


def validate_playtime_str(value):
    """'時間:分' 形式のみ許可。不正なら None、OKなら (hours, minutes) のタプルを返す。"""
    if not isinstance(value, str):
        return None
    m = PLAYTIME_RE.match(value.strip())
    if not m:
        return None
    hours, minutes = int(m.group(1)), int(m.group(2))
    if hours > 9999 or minutes > 59:
        return None
    return hours, minutes


def safe_custom_playtime(data):
    """custom_playtime を検証済みの文字列として返す。不正・未指定なら空文字。"""
    raw = data.get("custom_playtime", "")
    if raw in (None, ""):
        return ""
    raw_str = str(raw).strip()
    return raw_str if validate_playtime_str(raw_str) else ""


# =====================
# APIキー管理
# =====================
def generate_api_key():
    key = str(uuid.uuid4())
    with _job_db_connect() as conn:
        conn.execute(
            "INSERT INTO api_request_keys(api_key,created_at,used) VALUES(?,?,0)",
            (key, time.time()),
        )
        conn.execute(
            "DELETE FROM api_request_keys WHERE api_key IN "
            "(SELECT api_key FROM api_request_keys ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
            (MAX_API_KEYS,),
        )
    return key


def validate_and_consume_api_key(key: str) -> bool:
    with _job_db_connect() as conn:
        cur = conn.execute(
            "UPDATE api_request_keys SET used=1 "
            "WHERE api_key=? AND used=0 AND created_at>=?",
            (str(key), time.time() - API_KEY_TTL),
        )
    return cur.rowcount == 1


def cleanup_api_keys():
    while True:
        time.sleep(60)
        with _job_db_connect() as conn:
            conn.execute(
                "DELETE FROM api_request_keys WHERE created_at < ? OR used=1",
                (time.time() - API_KEY_TTL,),
            )


try:
    from bcsfe import core
    from bcsfe.core.server.server_handler import ServerHandler
    from bcsfe.core.game.catbase.cat import Talent
    from bcsfe.core.game.catbase.user_rank_rewards import Reward
    from bcsfe.core.game.catbase.playtime import PlayTime
    from bcsfe.core.game.map.outbreaks import (
        Outbreak as ZombieOutbreak,
        Chapter as ZombieChapter,
    )
except ImportError:
    import core
    from core.server.server_handler import ServerHandler

# BCSFE 3.6.0は保存ファイルのアップロードだけタイムアウトなしで、その他は
# 既定30秒。複製では保存回数が増えるため、接続15秒・通常45秒・保存90秒の
# 上限を明示する。TimeoutもConnectionErrorと同様にNoneへ変換し、BCSFE側の
# 安全な段階別失敗処理へ戻す。
def _bounded_timeout_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


BCSFE_CONNECT_TIMEOUT = _bounded_timeout_env("BCSFE_CONNECT_TIMEOUT", 15, 5, 60)
BCSFE_API_TIMEOUT = _bounded_timeout_env("BCSFE_API_TIMEOUT", 45, 15, 120)
BCSFE_UPLOAD_TIMEOUT = _bounded_timeout_env("BCSFE_UPLOAD_TIMEOUT", 90, 30, 300)


def _bounded_bcsfe_request_post(self, no_timeout=False):
    read_timeout = BCSFE_UPLOAD_TIMEOUT if self.form is not None else BCSFE_API_TIMEOUT
    try:
        return requests.post(
            self.url,
            headers=self.headers,
            data=self.data.data,
            timeout=(BCSFE_CONNECT_TIMEOUT, read_timeout),
            files=None if self.form is None else self.form.into_files(),
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        print(f"[BCSFE NETWORK] POST failed: {type(exc).__name__}")
        return None


core.RequestHandler.post = _bounded_bcsfe_request_post

core.core_data.init_data()

BCSFE_EDIT_LOCK = threading.RLock()

import os
from flask import Flask

# プロジェクトの根元（my_project）のパスを基準にする
base_dir = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(base_dir, 'static'),
    template_folder=os.path.join(base_dir, 'HTML')
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(32))
# VIPのイベント詳細は、全選択時に数千ステージ分の指定を送る。
# 64KBでは正常な操作でも413になるため、既存機能が収まる上限へ更新する。
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Cookie の Secure 属性を自動判定する。
# - 127.0.0.1 / localhost の HTTP 開発環境: Secure=False
# - ngrok / catps.com など HTTPS 公開環境: Secure=True
# SESSION_COOKIE_SECURE=0 / 1 を設定した場合は、その値を明示指定として優先する。
from flask.sessions import SecureCookieSessionInterface

_SESSION_COOKIE_SECURE_MODE = os.getenv("SESSION_COOKIE_SECURE", "auto").strip().lower()

class _AdaptiveSecureCookieSessionInterface(SecureCookieSessionInterface):
    def get_cookie_secure(self, flask_app):
        if _SESSION_COOKIE_SECURE_MODE in {"0", "false", "no", "off"}:
            return False
        if _SESSION_COOKIE_SECURE_MODE in {"1", "true", "yes", "on"}:
            return True
        host = (request.host or "").split(":", 1)[0].strip("[]").lower()
        if host in {"127.0.0.1", "localhost", "::1"}:
            return False
        forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        return forwarded_proto == "https" or request.scheme == "https"

app.session_interface = _AdaptiveSecureCookieSessionInterface()
# Flask本体の既定値。実際の応答時は上の session_interface が環境に応じて上書きする。
app.config["SESSION_COOKIE_SECURE"] = True
# 同一ドメイン上の別Flaskアプリが既定の ``session`` Cookieを上書きしても、
# Discord認証・サイトアカウントのセッションが分断されないよう専用名にする。
app.config["SESSION_COOKIE_NAME"] = "catps_session"
app.config["SESSION_COOKIE_PATH"] = "/"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://autocat.jp").strip().rstrip("/")


# =====================
# 実IP取得（Cloudflare 前提）
# =====================
def real_ip():
    # オリジンを Cloudflare 経由のみに絞っている前提。
    # そうでない環境では CF-Connecting-IP は信頼できない点に注意。
    return request.headers.get("CF-Connecting-IP") or get_remote_address()


limiter = Limiter(
    key_func=real_ip,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# =====================
# ジョブ実行プール
# =====================
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

jobs: dict = {}
jobs_lock = threading.Lock()
JOBS_MAX = 200
JOBS_TTL = 60 * 30
OPERATION_RETENTION = 2 * 24 * 60 * 60  # 受付IDと失敗セーブは48時間で失効

# =====================
# 使用回数カウンター（代行・作成・複製の成功回数を全種合算。count.dbに永続化し再起動後も保持）
# =====================
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, "BACKUP")
os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
try:
    os.chmod(BACKUP_DIR, 0o700)
except OSError:
    pass
USAGE_DB_PATH = os.path.join(BASE_DIR, "count.db")
job_timing_store = TimingStore(USAGE_DB_PATH)
try:
    if os.path.exists(USAGE_DB_PATH):
        os.chmod(USAGE_DB_PATH, 0o600)
except OSError:
    pass
OPERATION_DB_PATH = os.path.join(BACKUP_DIR, "admin_recovery.db")
BONUS_DB_PATH = os.path.join(BASE_DIR, "Login", "Bonus.db")
INVITATION_DB_PATH = os.path.join(BASE_DIR, "invitation", "invitation.db")
ACCOUNT_DB_PATH = os.path.join(BASE_DIR, "ACCOUNT", "account.db")
usage_count_lock = threading.Lock()

_identity_secret = os.getenv("IDENTITY_HASH_KEY") or os.getenv("FLASK_SECRET_KEY")
if not _identity_secret:
    # FLASK_SECRET_KEY未設定時の開発用フォールバック。本番では再起動をまたいで
    # 同じ利用者を識別できるよう、必ずいずれかの環境変数を固定する。
    _identity_secret = app.secret_key if isinstance(app.secret_key, bytes) else str(app.secret_key)
    print("[WARNING] IDENTITY_HASH_KEY/FLASK_SECRET_KEY is not configured; identity hashes may change after restart.")
free_usage_manager = FreeUsageManager(
    BONUS_DB_PATH,
    INVITATION_DB_PATH,
    _identity_secret,
)
account_store = AccountStore(
    ACCOUNT_DB_PATH,
    os.getenv("ACCOUNT_IDENTITY_HASH_KEY") or _identity_secret,
    vip_plan_prices=VIP_PLAN_PRICES,
)


def _process_invitation_vip_trials() -> None:
    """Idempotently turn newly rewarded invitations into one-use VIP trials."""
    try:
        events = free_usage_manager.pending_vip_trial_events(20)
    except Exception as exc:
        print(f"[VIP TRIAL] event lookup failed: {type(exc).__name__}")
        return
    for event in events:
        try:
            account_store.grant_invitation_trial(
                event["inviter_account_id"], event["redemption_id"], event["rewarded_at"]
            )
            free_usage_manager.mark_vip_trial_processed(event["redemption_id"])
        except Exception as exc:
            # Leave the event unprocessed so a later status/job request retries it.
            print(f"[VIP TRIAL] grant failed: {type(exc).__name__}")


def _usage_db_connect():
    conn = sqlite3.connect(USAGE_DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_count (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("INSERT OR IGNORE INTO usage_count (id, count) VALUES (1, 0)")
    conn.commit()
    return conn


_usage_db_conn = _usage_db_connect()
try:
    os.chmod(USAGE_DB_PATH, 0o600)
except OSError:
    pass


# ジョブ状態はメモリだけだと、Gunicorn等の別ワーカーに
# /api/job が割り当てられた時やワーカー再起動後に404になる。
# count.db内にJSONとして保存し、プロセス間で共有する。
@contextmanager
def _job_db_connect():
    conn = sqlite3.connect(USAGE_DB_PATH, timeout=10, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS background_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_request_keys (
                api_key TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


with _job_db_connect() as _job_init_conn:
    pass


@contextmanager
def _operation_db_connect():
    conn = sqlite3.connect(OPERATION_DB_PATH, timeout=10, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operation_records (
                operation_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE,
                operation_type TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                recovery_status TEXT NOT NULL DEFAULT 'not_needed',
                snapshot BLOB,
                reissue_result BLOB,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_records)")}
        if "reissue_result" not in columns:
            conn.execute("ALTER TABLE operation_records ADD COLUMN reissue_result BLOB")
        conn.commit()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


with _operation_db_connect() as _operation_init_conn:
    pass
try:
    os.chmod(OPERATION_DB_PATH, 0o600)
except OSError:
    pass

threading.Thread(target=cleanup_api_keys, daemon=True).start()


def _persist_job(job_id: str, job: dict):
    payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
    created_at = float(job.get("created_at", time.time()))
    updated_at = float(job.get("updated_at", created_at))
    with _job_db_connect() as conn:
        conn.execute(
            """INSERT INTO background_jobs(job_id,status,payload,created_at,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 status=excluded.status,payload=excluded.payload,
                 created_at=excluded.created_at,updated_at=excluded.updated_at""",
            (job_id, str(job.get("status", "pending")), payload, created_at, updated_at),
        )

    event = {"pending": "job_started", "done": "job_done", "error": "job_error"}.get(job.get("status"))
    if event and job.get("site_account_id"):
        try:
            kind = {"daiko": "代行", "create": "新規作成", "clone": "複製"}.get(job.get("operation_type"), "処理")
            account_store.record_activity(event, job["site_account_id"], actor="システム", detail=kind,
                                          reference=job_id, event_key=f"{event}:{job_id}")
        except sqlite3.Error:
            print("[サイトログ] 処理履歴を保存できませんでした")


def _load_job(job_id: str):
    with _job_db_connect() as conn:
        row = conn.execute(
            "SELECT payload FROM background_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0])
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        return None


def _create_job(job_id: str, job: dict):
    with jobs_lock:
        jobs[job_id] = job
    _persist_job(job_id, job)


def _create_operation(operation_id: str, job_id: str, operation_type: str, now: float):
    with _operation_db_connect() as conn:
        conn.execute(
            """INSERT INTO operation_records(
                   operation_id,job_id,operation_type,status,error,recovery_status,
                   snapshot,reissue_result,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (operation_id, job_id, operation_type, "pending", None, "not_needed", None, None, now, now),
        )


def _update_operation(operation_id: str, **changes):
    allowed = {"status", "error", "recovery_status", "snapshot", "reissue_result", "updated_at"}
    values = {key: value for key, value in changes.items() if key in allowed}
    if not values:
        return
    values.setdefault("updated_at", time.time())
    assignments = ",".join(f"{key}=?" for key in values)
    with _operation_db_connect() as conn:
        conn.execute(
            f"UPDATE operation_records SET {assignments} WHERE operation_id=?",
            (*values.values(), operation_id),
        )


def _load_operation(operation_id: str):
    with _operation_db_connect() as conn:
        row = conn.execute(
            """SELECT operation_id,job_id,operation_type,status,error,recovery_status,
                      snapshot,reissue_result,created_at,updated_at
               FROM operation_records WHERE operation_id=?""",
            (operation_id,),
        ).fetchone()
    if not row:
        return None
    keys = ("operation_id", "job_id", "operation_type", "status", "error",
            "recovery_status", "snapshot", "reissue_result", "created_at", "updated_at")
    record = dict(zip(keys, row))
    if time.time() >= float(record["created_at"]) + OPERATION_RETENTION:
        with _operation_db_connect() as conn:
            conn.execute("DELETE FROM operation_records WHERE operation_id=?", (operation_id,))
        return None
    return record


def _close_operation(
    operation_id: str,
    *,
    status: str,
    recovery_status: str,
    error: str | None = None,
    issued_codes: tuple[str, str] | None = None,
):
    """Close an operation without making an already-issued ID disappear.

    Closed records contain no save snapshot. When a code was already issued, an
    encrypted copy is retained for the admin panel until the normal 48-hour
    cleanup. Keeping the row distinguishes an expired/unknown ID from an
    operation that never reached the recoverable stage.
    """
    if not operation_id:
        return
    encrypted_codes = None
    if issued_codes:
        try:
            encrypted_codes = _encrypt_snapshot(
                json.dumps(
                    {"tc": issued_codes[0], "ac": issued_codes[1]},
                    separators=(",", ":"),
                ).encode(),
                operation_id,
            )
        except Exception as exc:
            # Do not discard an existing save snapshot when the fallback code
            # itself cannot be stored. The admin panel can still reissue it.
            combined = _safe_error_message(
                f"{error or ''}\n発行済みコードの保管に失敗: {exc}"
            )
            _update_operation(
                operation_id,
                status="error",
                error=combined,
                recovery_status="reissue_failed",
            )
            return
    _update_operation(
        operation_id,
        status=status,
        error=_safe_error_message(error) if error else None,
        recovery_status=recovery_status,
        snapshot=None,
        reissue_result=encrypted_codes,
    )


def _operation_public(record: dict):
    status_labels = {
        "pending": "受付済み",
        "running": "処理中",
        "done": "完了",
        "error": "エラー",
    }
    recovery_labels = {
        "not_needed": "復旧不要",
        "not_available": "セーブ未取得のため再発行不可",
        "snapshot_saved": "管理者による再発行が可能",
        "reissue_failed": "再発行待ち",
        "issuing": "再発行中",
        "client_code_issued": "引き継ぎコード発行済み",
        "admin_reissued": "引き継ぎコード再発行済み",
    }
    return {
        "operation_id": record["operation_id"],
        "operation_type": record["operation_type"],
        "status": record["status"],
        "error": record.get("error"),
        "recovery_status": record.get("recovery_status", "not_needed"),
        "status_label": status_labels.get(record["status"], record["status"]),
        "recovery_status_label": recovery_labels.get(
            record.get("recovery_status", "not_needed"),
            record.get("recovery_status", "not_needed"),
        ),
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "can_reissue": bool(
            record.get("snapshot")
            and record.get("status") == "error"
            and record.get("recovery_status") in ("snapshot_saved", "reissue_failed")
        ),
        "expires_at": record["created_at"] + OPERATION_RETENTION,
    }


def _snapshot_cipher_key() -> bytes:
    # 専用鍵を優先。未設定時は複数ワーカーで同じFLASK_SECRET_KEYから導出する。
    source = ADMIN_SNAPSHOT_KEY or os.getenv("FLASK_SECRET_KEY", "")
    if not source:
        raise RuntimeError("ADMIN_SNAPSHOT_KEY または FLASK_SECRET_KEY が必要です")
    return hashlib.sha256((source + "|autocat-admin-snapshot-v1").encode()).digest()


def _encrypt_snapshot(data: bytes, operation_id: str) -> bytes:
    key = _snapshot_cipher_key()
    nonce = secrets.token_bytes(12)
    aad = ("autocat-admin-snapshot-v2|" + operation_id).encode()
    return b"ACS2" + nonce + AESGCM(key).encrypt(nonce, data, aad)


def _decrypt_snapshot(blob: bytes, operation_id: str) -> bytes:
    if not blob or not blob.startswith(b"ACS2") or len(blob) < 32:
        raise ValueError("保存データが不正です")
    key = _snapshot_cipher_key()
    nonce, ciphertext = blob[4:16], blob[16:]
    aad = ("autocat-admin-snapshot-v2|" + operation_id).encode()
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except Exception as exc:
        raise ValueError("保存データの検証に失敗しました") from exc


def _save_operation_snapshot(operation_id: str, save_file):
    raw = save_file.to_data().to_bytes()
    snapshot = _encrypt_snapshot(gzip.compress(raw, compresslevel=6), operation_id)
    _update_operation(operation_id, snapshot=snapshot, recovery_status="snapshot_saved")


def _generate_operation_id() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(30))


def _safe_operation_id(data: dict) -> str:
    value = str(data.get("operation_id", "")).strip()
    if not value:
        return _generate_operation_id()
    return value if OPERATION_ID_RE.fullmatch(value) else ""


def _safe_error_message(value) -> str:
    text = str(value or "処理に失敗しました。")[:2000]
    text = re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1***:***@", text)
    text = re.sub(r"(?i)(authorization|token|secret|password)\s*[:=]\s*[^\s,;]+", r"\1=***", text)
    return text


def _update_job(job_id: str, changes: dict):
    """Update the local cache and durable shared job record together."""
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            job = _load_job(job_id)
            if job is None:
                return
            jobs[job_id] = job
        advance_timing(job, changes, float(changes.get("updated_at", time.time())))
        snapshot = copy.deepcopy(job)
    _persist_job(job_id, snapshot)
    _settle_job_quota(snapshot)
    if snapshot.get("status") == "done":
        job_timing_store.remember(job_id, snapshot)


def _settle_job_quota(job: dict | None) -> None:
    """無料回数とVIP体験1回を、成功時確定・失敗時返却する。"""
    if not isinstance(job, dict):
        return
    status = job.get("status")
    if status not in {"done", "error"}:
        return
    success = status == "done"
    reservation_id = job.get("quota_reservation_id")
    if reservation_id:
        try:
            free_usage_manager.settle(str(reservation_id), success=success)
            _process_invitation_vip_trials()
        except Exception as exc:
            # ジョブ結果は失わせず、次回の状態確認でも冪等に再試行する。
            print(f"[ERROR] free quota settlement {reservation_id}: {type(exc).__name__}")
    trial_reservation_id = job.get("trial_vip_reservation_id")
    if trial_reservation_id:
        try:
            account_store.settle_vip_job(str(trial_reservation_id), success=success)
        except Exception as exc:
            print(f"[ERROR] VIP trial settlement {trial_reservation_id}: {type(exc).__name__}")


def get_usage_count() -> int:
    with usage_count_lock:
        row = _usage_db_conn.execute("SELECT count FROM usage_count WHERE id = 1").fetchone()
        return row[0] if row else 0


def increment_usage_count() -> int:
    with usage_count_lock:
        _usage_db_conn.execute("UPDATE usage_count SET count = count + 1 WHERE id = 1")
        _usage_db_conn.commit()
        row = _usage_db_conn.execute("SELECT count FROM usage_count WHERE id = 1").fetchone()
        return row[0] if row else 0


def count_inflight_jobs() -> int:
    with _job_db_connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM background_jobs WHERE status IN ('pending','running')"
        ).fetchone()
    return int(row[0]) if row else 0


def cleanup_jobs():
    while True:
        time.sleep(30)
        now = time.time()
        with _job_db_connect() as conn:
            rows = conn.execute(
                "SELECT job_id,payload,updated_at FROM background_jobs "
                "WHERE status IN ('pending','running')"
            ).fetchall()
        for jid, raw_payload, stored_updated_at in rows:
            try:
                job = json.loads(raw_payload)
            except (TypeError, ValueError):
                continue
            created = float(job.get("created_at", now))
            last_activity = float(job.get("updated_at") or job.get("started_at") or created)
            absolute_timeout = (
                CLONE_JOB_ABSOLUTE_TIMEOUT
                if job.get("operation_type") == "clone"
                else JOB_ABSOLUTE_TIMEOUT
            )
            inactivity_timeout = (
                CLONE_JOB_TIMEOUT
                if job.get("operation_type") == "clone"
                else JOB_TIMEOUT
            )
            if now - last_activity > inactivity_timeout or now - created > absolute_timeout:
                job.update({"status": "error", "updated_at": now})
                if job.get("transfer_received"):
                    job["error"] = (
                        "引き継ぎ取得後に処理がタイムアウトしました。"
                        "同じ操作を再実行せず、管理者へ確認してください。"
                    )
                else:
                    job["error"] = "処理がタイムアウトしました。もう一度実行してください。"
                # 読み取り後にワーカーが進捗更新した場合はタイムアウトで上書きしない。
                with _job_db_connect() as conn:
                    cur = conn.execute(
                        "UPDATE background_jobs SET status=?,payload=?,updated_at=? "
                        "WHERE job_id=? AND updated_at=?",
                        ("error", json.dumps(job, ensure_ascii=False, separators=(",", ":")),
                         now, jid, stored_updated_at),
                    )
                if cur.rowcount:
                    with jobs_lock:
                        jobs[jid] = job
                    _settle_job_quota(job)
                    operation_id = job.get("operation_id")
                    if operation_id:
                        if job.get("return_pending"):
                            job["admin_recovery_required"] = True
                            _update_job(jid, {
                                "admin_recovery_required": True,
                                "return_pending": False,
                            })
                            _update_operation(
                                operation_id, status="error", error=job["error"],
                                recovery_status="reissue_failed",
                            )
                        elif job.get("recovery_transfer_code"):
                            # A usable return code wins over a stale timeout flag.
                            # Clear the flag and retain a closed record so history
                            # never points at an immediately deleted operation.
                            job["admin_recovery_required"] = False
                            _update_job(jid, {"admin_recovery_required": False})
                            _close_operation(
                                operation_id,
                                status="error",
                                recovery_status="client_code_issued",
                                error=job["error"],
                                issued_codes=(
                                    job["recovery_transfer_code"],
                                    job.get("recovery_auth_code") or "",
                                ),
                            )
                        else:
                            job["admin_recovery_required"] = True
                            _update_job(jid, {"admin_recovery_required": True})
                            operation = _load_operation(operation_id)
                            recovery_state = (
                                operation.get("recovery_status", "reissue_failed")
                                if operation and operation.get("snapshot") else "reissue_failed"
                            )
                            _update_operation(
                                operation_id, status="error", error=job["error"],
                                recovery_status=recovery_state,
                            )

        cutoff = now - JOBS_TTL
        with _job_db_connect() as conn:
            conn.execute(
                "DELETE FROM background_jobs WHERE status IN ('done','error') AND created_at < ?",
                (cutoff,),
            )
            keep_ids = {
                row[0] for row in conn.execute(
                    "SELECT job_id FROM background_jobs ORDER BY created_at DESC LIMIT ?", (JOBS_MAX,)
                ).fetchall()
            }
            if keep_ids:
                placeholders = ",".join("?" for _ in keep_ids)
                conn.execute(
                    f"DELETE FROM background_jobs WHERE job_id NOT IN ({placeholders})",
                    tuple(keep_ids),
                )
        with jobs_lock:
            for jid in list(jobs):
                if jid not in keep_ids:
                    jobs.pop(jid, None)
        # 返却コード発行まで失敗した操作だけを残し、作成から48時間で削除。
        with _operation_db_connect() as conn:
            conn.execute(
                "DELETE FROM operation_records WHERE created_at < ?",
                (now - OPERATION_RETENTION,),
            )


threading.Thread(target=cleanup_jobs, daemon=True).start()


def register_job_to_session(job_id: str):
    # FlaskのCookieセッションを肥大化させるとブラウザがCookieを破棄し、
    # 以後の/api/jobが全て403になる。新規UIはjob tokenを使い、ここは
    # 旧UI互換用として直近20件だけ保持する。
    existing = [value for value in session.get("job_ids", []) if isinstance(value, str)]
    session["job_ids"] = (existing + [job_id])[-20:]


def _new_job_access_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def session_owns_job(job_id: str, job: dict | None = None) -> bool:
    # 署名Cookie内のjob_idsは複数タブの同時レスポンスで片方が欠落し得る。
    # ジョブごとのCapability tokenを優先し、旧ジョブだけ従来方式へ戻す。
    if job is None:
        with jobs_lock:
            job = copy.deepcopy(jobs.get(job_id))
        if job is None:
            job = _load_job(job_id)
    expected = str((job or {}).get("job_access_hash") or "")
    supplied = request.headers.get("X-Job-Token", "")
    if expected and supplied:
        actual = hashlib.sha256(supplied.encode()).hexdigest()
        return secrets.compare_digest(actual, expected)
    return job_id in session.get("job_ids", [])


# =====================
# Discordログイン（チャット・管理者）/ サイトアカウント（VIP）
# =====================
def current_user():
    """Discordログイン中のチャット用ユーザー。VIP判定には使用しない。"""
    return session.get("discord_user")


def current_site_user():
    """サイトアカウント。VIP権限はACCOUNT/account.dbの契約状態から毎回取得する。"""
    user = account_store.session_account(session.get("site_account_id"), session.get("site_auth_version", 0))
    if user and user.get("status") == "active":
        return user
    session.pop("site_account_id", None)
    return None


def site_vip_confirmed() -> bool:
    user = current_site_user()
    return bool(user and user.get("is_vip"))


def is_admin_user() -> bool:
    user = current_user()
    configured = ADMIN_USER
    if not re.fullmatch(r"\d{15,22}", configured) or not user or not user.get("id"):
        return False
    return secrets.compare_digest(str(user["id"]), configured)


def admin_api_required(view):
    @_ft.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin_user():
            return jsonify({"error": "forbidden"}), 403
        return view(*args, **kwargs)
    return wrapped


def _admin_csrf_token() -> str:
    token = session.get("admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["admin_csrf_token"] = token
    return token


def _valid_admin_csrf() -> bool:
    expected = session.get("admin_csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


@app.route("/auth/login")
@limiter.limit("20 per minute")
def auth_login():
    if not DISCORD_CLIENT_ID or not DISCORD_REDIRECT_URI:
        print("[ERROR] Discord OAuth設定が不足しています")
        return redirect("/?login_error=config")
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    oauth_next = request.args.get("next", "/")
    allowed_next = {"/", "/vip-guide", "/admin/panel", "/admin/vip", "/admin/logs"}
    session["oauth_next"] = oauth_next if oauth_next in allowed_next else "/"
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
    }
    return redirect(f"{DISCORD_API}/oauth2/authorize?{urlencode(params)}")


@app.route("/auth/callback")
@limiter.limit("20 per minute")
def auth_callback():
    state = request.args.get("state", "")
    expected_state = session.pop("oauth_state", "")
    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        print("[ERROR] Discord OAuth stateが一致しません")
        return redirect("/?login_error=state")

    oauth_error = request.args.get("error")
    if oauth_error:
        print(f"[ERROR] Discord OAuth denied: {oauth_error}")
        return redirect("/?login_error=denied")

    code = request.args.get("code")
    if not code:
        return redirect("/?login_error=code")

    try:
        token_res = requests.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
            proxies=PROXIES,
        )
        if token_res.status_code != 200:
            print(f"[ERROR] Discord token exchange: HTTP {token_res.status_code}")
            return redirect("/?login_error=token")

        access_token = token_res.json().get("access_token")
        if not access_token:
            print("[ERROR] Discord token exchange: access_tokenがありません")
            return redirect("/?login_error=token")

        user_res = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
            proxies=PROXIES,
        )
        if user_res.status_code != 200:
            print(f"[ERROR] Discord user fetch: HTTP {user_res.status_code}")
            return redirect("/?login_error=user")

        u = user_res.json()
        discord_id = u["id"]

        session.permanent = True
        session["discord_user"] = {
            "id": discord_id,
            "username": u.get("username"),
            "avatar": u.get("avatar"),
            # DiscordロールはVIP判定に使用しない。チャット互換用に常にFalseを保持する。
            "is_vip": False,
        }
    except Exception as e:
        print(f"[ERROR] auth_callback: {e}")
        return redirect("/?login_error=network")

    return redirect(session.pop("oauth_next", "/"))


@app.route("/auth/logout")
@limiter.limit("20 per minute")
def auth_logout():
    session.pop("discord_user", None)
    return redirect("/")


@app.route("/auth/status")
@limiter.limit("60 per minute")
def auth_status():
    """現在のブラウザでDiscordセッションが共有されているか確認する。"""
    user = current_user()
    user_id = str((user or {}).get("id") or "")
    response = jsonify({
        "logged_in": bool(user_id),
        "discord_id": user_id or None,
        "username": (user or {}).get("username"),
        "is_admin": is_admin_user(),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


# =====================
# 入力バリデーション
# =====================
def validate_common_input(data):
    """selected / char_list / custom_playtime の型・サイズを検証。問題なければ None、あればエラーレスポンス。"""
    if not isinstance(data, dict):
        return jsonify({"error": "入力が不正です"}), 400
    selected = data.get("selected", {})
    if not isinstance(selected, dict):
        return jsonify({"error": "selected が不正です"}), 400
    for v in selected.values():
        if not isinstance(v, list) or len(v) > 100:
            return jsonify({"error": "selected が不正です"}), 400
    char_list = data.get("char_list", [])
    if not isinstance(char_list, list) or len(char_list) > MAX_CHAR_LIST:
        return jsonify({"error": "char_list が不正です"}), 400
    character_settings = data.get("character_settings", [])
    if not isinstance(character_settings, list) or len(character_settings) > MAX_CHARACTER_SETTINGS:
        return jsonify({"error": "character_settings が不正です"}), 400
    for setting in character_settings:
        if not isinstance(setting, dict):
            return jsonify({"error": "character_settings が不正です"}), 400
        talents = setting.get("talents", {})
        if not isinstance(talents, dict) or len(talents) > 32:
            return jsonify({"error": "キャラ本能設定が不正です"}), 400

    ototo_settings = data.get("ototo_settings")
    if ototo_settings is not None:
        if not isinstance(ototo_settings, dict) or len(ototo_settings) > 64:
            return jsonify({"error": "オトート詳細設定が不正です"}), 400
        for cannon_id, setting in ototo_settings.items():
            if not str(cannon_id).isdigit() or not isinstance(setting, dict):
                return jsonify({"error": "オトート詳細設定が不正です"}), 400
            levels = setting.get("levels", {})
            if not isinstance(levels, dict) or len(levels) > 3:
                return jsonify({"error": "オトート詳細設定が不正です"}), 400
            if any(not str(part_id).isdigit() for part_id in levels):
                return jsonify({"error": "オトート詳細設定が不正です"}), 400

    vip_items = data.get("vip_items", {})
    if not isinstance(vip_items, dict) or len(vip_items) > len(VIP_ITEM_GROUPS):
        return jsonify({"error": "vip_items が不正です"}), 400
    for group_key, values in vip_items.items():
        spec = VIP_ITEM_GROUPS.get(group_key)
        if spec is None or not isinstance(values, dict) or len(values) > spec["length"]:
            return jsonify({"error": "vip_items が不正です"}), 400

    if not validate_legend_stages_shape(data):
        return jsonify({"error": "legend_stages が不正です"}), 400
    if not validate_detailed_stage_settings_shape(data):
        return jsonify({"error": "ステージ詳細指定が不正です"}), 400
    if not validate_labyrinth_character_settings_shape(data):
        return jsonify({"error": "地底迷宮のキャラ指定が不正です"}), 400
    if not validate_lineup_settings_shape(data):
        return jsonify({"error": "編成キャラ指定が不正です"}), 400
    if not validate_score_settings_shape(data):
        return jsonify({"error": "道場・未来編スコア指定が不正です"}), 400

    vip_facilities = data.get("vip_facilities", {})
    if not isinstance(vip_facilities, dict) or len(vip_facilities) > len(FACILITY_NAMES):
        return jsonify({"error": "vip_facilities が不正です"}), 400
    vip_talent_orbs = data.get("vip_talent_orbs", {})
    if not isinstance(vip_talent_orbs, dict) or len(vip_talent_orbs) > 1000:
        return jsonify({"error": "vip_talent_orbs が不正です"}), 400

    # プレイ時間(絶対値指定): 値が入っている場合は「時間:分」形式のみ許可
    playtime_raw = data.get("custom_playtime")
    if playtime_raw not in (None, ""):
        if validate_playtime_str(str(playtime_raw)) is None:
            return jsonify({"error": "プレイ時間は「時間:分」の形式で入力してください（例: 1200:30）"}), 400

    return None


def safe_count(data, maximum=MAX_COUNT):
    maximum = max(1, min(MAX_COUNT, int(maximum)))
    try:
        return max(1, min(maximum, int(data.get("count", 1))))
    except (TypeError, ValueError):
        return 1


def safe_character_settings(data):
    """名前選択UIから届いたキャラ・形態・レベル設定を安全な範囲へ丸める。IDは画面表示と同じ1始まり。"""
    out = []
    seen = set()
    raw_settings = data.get("character_settings", [])
    if not isinstance(raw_settings, list) or not raw_settings:
        return out
    try:
        form_counts = {
            character["id"]: character["form_count"]
            for character in get_character_metadata().get("characters", [])
            if character.get("selectable", True)
        }
    except Exception:
        form_counts = {}
    for raw in raw_settings[:MAX_CHARACTER_SETTINGS]:
        try:
            cat_id = max(1, min(9999, int(raw.get("id"))))
            if form_counts and cat_id not in form_counts:
                continue
            max_form = form_counts.get(cat_id, 4)
            form = max(1, min(max_form, int(raw.get("form", 1))))
            base_level = max(1, min(60, int(raw.get("base_level", 60))))
            plus_level = max(0, min(90, int(raw.get("plus_level", 0))))
            level_mode = raw.get("level_mode", "individual")
            if level_mode not in {"same", "individual", "random"}:
                level_mode = "individual"
            base_min = max(1, min(60, int(raw.get("base_level_min", 1))))
            base_max = max(1, min(60, int(raw.get("base_level_max", 60))))
            plus_min = max(0, min(90, int(raw.get("plus_level_min", 0))))
            plus_max = max(0, min(90, int(raw.get("plus_level_max", 90))))
        except (TypeError, ValueError, AttributeError):
            continue
        base_min, base_max = sorted((base_min, base_max))
        plus_min, plus_max = sorted((plus_min, plus_max))
        if cat_id in seen:
            continue
        seen.add(cat_id)
        out.append({
            "id": cat_id,
            "form": form,
            "base_level": base_level,
            "plus_level": plus_level,
            "level_mode": level_mode,
            "base_level_min": base_min,
            "base_level_max": base_max,
            "plus_level_min": plus_min,
            "plus_level_max": plus_max,
            "talent_action": (
                raw.get("talent_action")
                if raw.get("talent_action") in {"set", "disable_selected", "disable_all"}
                else "set"
            ),
            # 0は無効化。自然上限では丸めず、限界突破指定を保持する。
            "talents": {
                str(max(1, min(99999, int(talent_id)))): max(0, min(32767, int(level)))
                for talent_id, level in (raw.get("talents") or {}).items()
                if str(talent_id).lstrip("-").isdigit()
                and str(level).lstrip("-").isdigit()
            } if isinstance(raw.get("talents"), dict) else {},
        })
    return out


def safe_ototo_settings(data):
    """画面の表示レベルを、最新版で実在する城・部品・上限へ丸める。"""
    raw_settings = data.get("ototo_settings")
    if not isinstance(raw_settings, dict) or not raw_settings:
        return None
    try:
        cannons = get_ototo_metadata().get("cannons", [])
    except Exception:
        cannons = _bundled_ototo_metadata()["cannons"]
    definitions = {
        int(cannon["id"]): {
            int(part["id"]): (
                int(part.get("min_level", 0)),
                int(part.get("max_level", 0)),
            )
            for part in cannon.get("parts", [])
        }
        for cannon in cannons
    }
    output = {}
    for raw_cannon_id, raw_setting in list(raw_settings.items())[:64]:
        try:
            cannon_id = int(raw_cannon_id)
        except (TypeError, ValueError):
            continue
        if cannon_id not in definitions or not isinstance(raw_setting, dict):
            continue
        raw_levels = raw_setting.get("levels", {})
        if not isinstance(raw_levels, dict):
            continue
        levels = {}
        for raw_part_id, raw_level in list(raw_levels.items())[:3]:
            try:
                part_id = int(raw_part_id)
                minimum, maximum = definitions[cannon_id][part_id]
                level = max(minimum, min(maximum, int(raw_level)))
            except (TypeError, ValueError, KeyError):
                continue
            levels[str(part_id)] = level
        if levels:
            output[str(cannon_id)] = {"levels": levels}
    return output or None


# アカウント種別 → セーブファイル名のマッピング（ホワイトリスト方式。任意の文字列をパスに使わせない）
ACCOUNT_TYPE_FILES = {
    "new": "Nyanko_new",
    "beginner": "Nyanko_beginner",
    "intermediate": "Nyanko_Intermediate",
    "advanced": "Nyanko_advanced",
}


def safe_account_type(data):
    """account_type を検証済みのキーとして返す。不正・未指定なら 'new'（初期垢）。"""
    raw = str(data.get("account_type", "new")).strip()
    return raw if raw in ACCOUNT_TYPE_FILES else "new"


# アカウント種別ごとの「素の中身」。ユーザーが画面で追加指定した内容は、
# このプリセット適用後に上書きされるため、従来の詳細編集機能はそのまま使える。
ACCOUNT_TYPE_PRESET_LABELS = {
    "new": "初期垢",
    "beginner": "初心者垢",
    "intermediate": "中級者垢",
    "advanced": "上級者垢",
}


def _preset_randint(minimum, maximum):
    """secrets を使って両端を含む整数乱数を返す。"""
    minimum, maximum = sorted((int(minimum), int(maximum)))
    return minimum + secrets.randbelow(maximum - minimum + 1)


def clear_created_account_new_marks(save):
    """所持キャラの新規取得通知を解除し、図鑑報酬を受取済みにする。"""
    changed = 0
    for cat in save.cats.cats:
        if not getattr(cat, "unlocked", False):
            continue
        cat_id = int(cat.id)
        if cat.gatya_seen != 0 or not cat.catguide_collected or save.cats.chara_new_flags.get(cat_id, 0):
            changed += 1
        # BCSFEのCat.unlock()も1を設定する。1は解放直後の通知状態。
        # 図鑑の受取フラグとは独立して、パワーアップ側の通知を0へ戻す。
        cat.gatya_seen = 0
        cat.catguide_collected = True
        save.cats.chara_new_flags[cat_id] = 0
    return changed


def clear_owned_medal_new_marks(save):
    """獲得済みメダルの通知エントリだけを解除する。獲得・進捗は維持。"""
    medals = save.medals
    changed = 0
    # add_medal()は獲得リストとは別にmedal_data_2へ通知を追加する。
    # 既読の上級者テンプレート同様、通知は削除する（1への書換えではない）。
    for medal_id in medals.medal_data_1:
        if medal_id in medals.medal_data_2:
            del medals.medal_data_2[medal_id]
            changed += 1
    return changed


def disable_rank_up_sale(save):
    """ランクアップセールの案内を無効化する。UR・資源・ランク報酬は維持する。"""
    before = save.rank_up_sale_value
    save.rank_up_sale_value = 0x7FFFFFFF
    return before != save.rank_up_sale_value


def _preset_randomize_cats(save, account_type_key):
    """初心者・中級者・上級者用に、所持キャラとレベルへ自然なばらつきを付ける。

    初期垢は対象外。既にテンプレート側で所持しているキャラは消さず、
    各レアリティからランダムに追加解放してから所持キャラのLv/+値を散らす。
    画面から個別指定されたキャラ設定は、この処理の後に上書きされる。
    """
    if account_type_key == "new":
        return {"unlocked": 0, "leveled": 0, "rarities": {}}

    # (最低追加数, 最大追加数)。中級者から「レア」を明確に多めにする。
    unlock_ranges = {
        "beginner": {
            1: (4, 10),    # EX
            2: (12, 24),   # レア
            3: (3, 8),     # 激レア
            4: (0, 2),     # 超激レア
            5: (0, 0),     # 伝説レア
        },
        "intermediate": {
            1: (12, 22),
            2: (55, 85),   # レア多め
            3: (20, 35),
            4: (4, 10),
            5: (0, 1),
        },
        "advanced": {
            1: (25, 40),
            2: (90, 130),  # レアはかなり多め
            3: (45, 70),
            4: (20, 35),
            5: (1, 3),
        },
    }

    # rarity -> (base_min, base_max, plus_min, plus_max)
    level_profiles = {
        "beginner": {
            0: (10, 20, 0, 6),
            1: (10, 22, 0, 2),
            2: (12, 25, 0, 3),
            3: (15, 25, 0, 2),
            4: (15, 25, 0, 1),
            5: (20, 25, 0, 0),
        },
        "intermediate": {
            0: (20, 30, 5, 20),
            1: (20, 30, 0, 4),
            2: (24, 35, 1, 9),
            3: (25, 35, 0, 6),
            4: (25, 35, 0, 3),
            5: (30, 35, 0, 1),
        },
        "advanced": {
            0: (30, 40, 20, 60),
            1: (30, 42, 2, 12),
            2: (35, 45, 6, 28),
            3: (35, 45, 3, 18),
            4: (35, 45, 0, 7),
            5: (40, 50, 0, 3),
        },
    }

    ranges = unlock_ranges.get(account_type_key)
    profile = level_profiles.get(account_type_key)
    if not ranges or not profile:
        return {"unlocked": 0, "leveled": 0, "rarities": {}}

    try:
        metadata = get_character_metadata()
        by_rarity = {rarity: [] for rarity in range(6)}
        rarity_by_id = {}
        for info in metadata.get("characters", []):
            try:
                display_id = int(info.get("id"))
                rarity = int(info.get("rarity", -1))
            except (TypeError, ValueError):
                continue
            if rarity not in by_rarity or not info.get("selectable", True):
                continue
            if not (1 <= display_id <= len(save.cats.cats)):
                continue
            by_rarity[rarity].append(display_id)
            rarity_by_id[display_id] = rarity

        rng = secrets.SystemRandom()
        unlocked_added = 0
        rarity_counts = {}
        for rarity, (minimum, maximum) in ranges.items():
            candidates = [
                display_id for display_id in by_rarity.get(rarity, [])
                if not getattr(save.cats.cats[display_id - 1], "unlocked", False)
            ]
            if not candidates:
                continue
            wanted = min(len(candidates), _preset_randint(minimum, maximum))
            for display_id in rng.sample(candidates, wanted):
                try:
                    save.cats.cats[display_id - 1].unlock(save)
                    unlocked_added += 1
                    rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
                except Exception as e:
                    print(f"[PRESET] cat unlock {display_id}: {e}")

        leveled = 0
        for display_id, rarity in rarity_by_id.items():
            cat = save.cats.cats[display_id - 1]
            if not getattr(cat, "unlocked", False):
                continue
            spec = profile.get(rarity, profile.get(2))
            if not spec:
                continue
            base_min, base_max, plus_min, plus_max = spec
            cat.upgrade.base = max(0, _preset_randint(base_min, base_max) - 1)
            cat.upgrade.plus = max(0, _preset_randint(plus_min, plus_max))
            leveled += 1

        return {"unlocked": unlocked_added, "leveled": leveled, "rarities": rarity_counts}
    except Exception as e:
        print(f"[PRESET] random cats {account_type_key}: {e}")
        return {"unlocked": 0, "leveled": 0, "rarities": {}}


def _preset_clear_manics(save):
    """イベント名に「大狂乱」を含むマップをクリア。取得失敗時は元テンプレート状態を保持。"""
    cleared = 0
    try:
        metadata = get_legend_metadata().get("event_stage_families", {})
        selections = {}
        for family_key, family in metadata.items():
            if family_key not in EVENT_STAGE_FAMILIES:
                continue
            maps = {}
            for map_info in family.get("maps", []):
                if "大狂乱" not in str(map_info.get("name", "")):
                    continue
                stages = map_info.get("stages", [])
                if not stages:
                    continue
                maps[int(map_info["id"])] = {0: {i: 1 for i in range(len(stages))}}
                cleared += len(stages)
            if maps:
                selections[family_key] = maps
        if selections:
            apply_event_stage_settings(save, selections)
    except Exception as e:
        print(f"[PRESET] manics: {e}")
    return cleared


ACCOUNT_PRESET_RANGES = {
    "beginner": {
        "scalars": {"catfood": (800, 2500), "xp": (800_000, 4_000_000), "np": (0, 50),
                    "normal_tickets": (5, 25), "rare_tickets": (1, 6), "platinum_tickets": (0, 1),
                    "legend_tickets": (0, 0), "platinum_shards": (0, 3), "leadership": (5, 25)},
        "arrays": {"catseyes": (0, 6), "catamins": (1, 8), "catfruit": (0, 5)},
        "battle_items": (3, 20), "materials": (0, 15), "event_tickets": (0, 4), "orbs": (0, 1),
        "facilities": (12, 20, 0, 5), "cannon_charge": (6, 10),
        "cannon_level": (1, 5), "cannon_parts": (0, 2), "cannon_count": (0, 2),
        "gamatoto_xp": (60_000, 250_000), "helpers": (3, 4),
        "shrine_xp": (0, 1_000_000), "play_hours": (30, 90),
        "completed_chapters": 3, "partial_stages": (0, 8), "clear_times": (1, 4),
        "treasure_chance": (70, 90), "future_score": (500, 3000),
        "legend_maps": (0, 6), "true_legend_maps": (0, 0), "extra_crowns": (0, 0),
        "zombies": (3, 12), "aku_stages": (0, 0), "dojo_score": (0, 50_000),
    },
    "intermediate": {
        "scalars": {"catfood": (3000, 7000), "xp": (8_000_000, 25_000_000), "np": (80, 250),
                    "normal_tickets": (20, 70), "rare_tickets": (4, 15), "platinum_tickets": (0, 2),
                    "legend_tickets": (0, 1), "platinum_shards": (0, 8), "leadership": (25, 80)},
        "arrays": {"catseyes": (10, 40), "catamins": (5, 25), "catfruit": (4, 20)},
        "battle_items": (20, 80), "materials": (15, 70), "event_tickets": (2, 12), "orbs": (0, 4),
        "facilities": (18, 20, 2, 10), "cannon_charge": (8, 10),
        "cannon_level": (3, 15), "cannon_parts": (0, 6), "cannon_count": (2, 5),
        "gamatoto_xp": (2_000_000, 8_000_000), "helpers": (6, 10),
        "shrine_xp": (3_000_000, 15_000_000), "play_hours": (150, 350),
        "completed_chapters": 6, "max_treasure_chapters": 5, "partial_stages": (8, 20), "clear_times": (1, 8),
        "treasure_chance": (80, 95), "future_score": (3000, 7500),
        "legend_maps": (12, 24), "true_legend_maps": (0, 0), "extra_crowns": (0, 3),
        "zombies": (15, 35), "aku_stages": (0, 8), "dojo_score": (50_000, 250_000),
    },
    "advanced": {
        "scalars": {"catfood": (8000, 16000), "xp": (35_000_000, 75_000_000), "np": (300, 900),
                    "normal_tickets": (70, 180), "rare_tickets": (15, 40), "platinum_tickets": (1, 4),
                    "legend_tickets": (0, 2), "platinum_shards": (3, 9), "leadership": (80, 200)},
        "arrays": {"catseyes": (40, 120), "catamins": (20, 60), "catfruit": (15, 50)},
        "battle_items": (60, 180), "materials": (60, 200), "event_tickets": (5, 30), "orbs": (2, 12),
        "facilities": (20, 20, 5, 10), "cannon_charge": (10, 10),
        "cannon_level": (15, 30), "cannon_parts": (5, 20), "cannon_count": (5, 7),
        "gamatoto_xp": (25_000_000, 110_000_000), "helpers": (8, 10),
        "shrine_xp": (100_000_000, 600_000_000), "play_hours": (650, 1600),
        "completed_chapters": 9, "max_treasure_chapters": 5, "partial_stages": (0, 0), "clear_times": (2, 15),
        "treasure_chance": (90, 100), "future_score": (6500, 9999),
        "legend_maps": (49, 49), "true_legend_maps": (8, 16), "extra_crowns": (3, 12),
        "zombies": (30, 48), "aku_stages": (24, 48), "dojo_score": (250_000, 900_000),
    },
}


def _preset_randomize_resources(save, profile, metadata):
    """既存の保存枠と実装済みIDだけに、進行度別の資源を割り当てる。"""
    for name, bounds in profile["scalars"].items():
        setattr(save, name, _preset_randint(*bounds))
    for name, bounds in profile["arrays"].items():
        target = getattr(save, name, [])
        for index in range(len(target)):
            target[index] = _preset_randint(*bounds)
    for item in save.battle_items.items:
        item.amount = _preset_randint(*profile["battle_items"])
    for material in save.ototo.base_materials.materials:
        material.amount = _preset_randint(*profile["materials"])

    ticket_arrays = {1: save.event_capsules, 8: save.lucky_tickets, 10: save.event_capsules_2}
    # 廃止イベントの保存枠は0にして、現行の有効なチケットだけ抽選する。
    for target in ticket_arrays.values():
        for index in range(len(target)):
            target[index] = 0
    for ticket in metadata.get("event_tickets", []):
        target = ticket_arrays.get(int(ticket.get("category", -1)), [])
        index = int(ticket.get("index", -1))
        if 0 <= index < len(target):
            target[index] = _preset_randint(*profile["event_tickets"])
    # データ取得不能時も、テンプレートの有効な本能玉枠は抽選できる。
    orb_ids = set(save.talent_orbs.orbs)
    for group in metadata.get("talent_orbs", []):
        orb_ids.update(int(rank["id"]) for rank in group.get("ranks", []))
    for orb_id in sorted(orb_ids):
        save.talent_orbs.set_orb(orb_id, _preset_randint(*profile["orbs"]))


def _preset_randomize_facilities(save, profile):
    base_min, base_max, plus_min, plus_max = profile["facilities"]
    levels = []
    for facility_id, skill in enumerate(save.special_skills.get_valid_skills()[:len(FACILITY_NAMES)]):
        if facility_id == 1:  # にゃんこ砲チャージの通常上限はLv10、+値なし。
            base, plus = _preset_randint(*profile["cannon_charge"]), 0
        else:
            base, plus = _preset_randint(base_min, base_max), _preset_randint(plus_min, plus_max)
        skill.upgrade.base, skill.upgrade.plus = base - 1, plus
        skill.max_upgrade_level.base = max(skill.max_upgrade_level.base, base - 1)
        skill.max_upgrade_level.plus = max(skill.max_upgrade_level.plus, plus)
        levels.append((base, plus))
        if facility_id == 0 and len(save.special_skills.skills) > 1:
            mirror = save.special_skills.skills[1]
            mirror.upgrade.base, mirror.upgrade.plus = base - 1, plus
    return levels


def _preset_randomize_main_story(save, profile):
    """後続章の古い進行を消し、クリア済み章と次章の序盤を一貫した状態にする。"""
    complete = profile["completed_chapters"]
    partial = _preset_randint(*profile["partial_stages"])
    treasure_chance = _preset_randint(*profile["treasure_chance"])
    for position, real_id in enumerate(MAIN_STORY_REAL_INDEX):
        if real_id >= len(save.story.chapters):
            continue
        chapter = save.story.chapters[real_id]
        count = min(48, len(chapter.stages))
        target = count if position < complete else min(partial, count) if position == complete else 0
        chapter.progress = 0
        chapter.selected_stage = min(target, max(0, count - 1))
        for stage in chapter.stages[:count]:
            stage.clear_times = 0
            stage.treasure = 0
            stage.itf_timed_score = 0
        for stage_id in range(target):
            chapter.clear_stage(stage_id, _preset_randint(*profile["clear_times"]))
            stage = chapter.stages[stage_id]
            if position < profile.get("max_treasure_chapters", 0):
                stage.treasure = 3
            else:
                stage.treasure = 3 if _preset_randint(1, 100) <= treasure_chance else _preset_randint(1, 2)
            if position in {3, 4, 5}:
                stage.itf_timed_score = _preset_randint(*profile["future_score"])
    return partial


def _preset_randomize_legend_group(groups, map_ids, full_maps, profile, extra_crowns=0):
    """指定したシリーズの章だけを更新する。イベントの別シリーズには触れない。"""
    cleared = 0
    for order, map_id in enumerate(map_ids):
        if not 0 <= map_id < len(groups):
            continue
        group = groups[map_id]
        for star, chapter in enumerate(group.chapters):
            chapter.clear_progress = 0
            chapter.selected_stage = 0
            chapter.chapter_unlock_state = 0
            for stage in chapter.stages:
                stage.unclear_stage()
            if order < full_maps and (star == 0 or (star == 1 and order < extra_crowns)):
                for stage_id in range(len(chapter.stages)):
                    chapter.clear_stage(stage_id, _preset_randint(*profile["clear_times"]))
                    cleared += 1
            elif order == full_maps and star == 0:
                # 次の章を選択可能にするが、クリア扱いにはしない。
                chapter.chapter_unlock_state = 1
    return cleared


def _preset_randomize_progress(save, profile, metadata):
    legend_maps = _preset_randint(*profile["legend_maps"])
    true_maps = _preset_randint(*profile["true_legend_maps"])
    extra = min(legend_maps, _preset_randint(*profile["extra_crowns"]))
    groups = save.event_stages.chapters[0].chapters
    legend_ids = sorted({int(m["id"]) for m in metadata.get("legend", {}).get("maps", [])})
    if not legend_ids:
        # 同梱テンプレートの0〜48だけが通常レジェンド。以降は他イベントの保存枠。
        legend_ids = list(range(min(49, len(groups))))
    _preset_randomize_legend_group(groups, legend_ids, legend_maps, profile, extra)
    true_groups = save.uncanny.chapters.chapters
    _preset_randomize_legend_group(true_groups, list(range(len(true_groups))), true_maps, profile)

    rng = secrets.SystemRandom()
    save.outbreaks.current_outbreaks = {}
    for position, real_id in enumerate(MAIN_STORY_REAL_INDEX):
        chapter = save.outbreaks.chapters.get(real_id)
        if chapter is None:
            chapter = ZombieChapter(real_id, {})
            save.outbreaks.chapters[real_id] = chapter
        chapter.outbreaks = {}
        if position < profile["completed_chapters"]:
            count = _preset_randint(*profile["zombies"])
            for stage_id in rng.sample(range(48), min(48, count)):
                chapter.outbreaks[stage_id] = ZombieOutbreak(True)
    aku_count = _preset_randint(*profile["aku_stages"])
    for group in getattr(getattr(save, "aku", None), "chapters", []):
        for chapter in group.chapters:
            chapter.current_stage = min(aku_count, max(0, len(chapter.stages) - 1))
            for stage_id, stage in enumerate(chapter.stages):
                stage.clear_times = _preset_randint(*profile["clear_times"]) if stage_id < aku_count else 0
    save.dojo.chapters.get_stage(0, 0).score = _preset_randint(*profile["dojo_score"])
    return legend_maps, true_maps


def _preset_randomize_extras(save, profile):
    save.gamatoto.xp = _preset_randint(*profile["gamatoto_xp"])
    helpers = save.gamatoto.helpers.helpers
    # 元テンプレートの実在隊員から抽選し、人数・並びにも変化を付ける。
    pool = list({helper.id: helper for helper in helpers if helper.is_valid()}.values())
    count = min(len(pool), _preset_randint(*profile["helpers"]))
    chosen = secrets.SystemRandom().sample(pool, count)
    empty = core.game.gamoto.gamatoto.Helper
    save.gamatoto.helpers.helpers = chosen + [empty(-1) for _ in range(max(0, len(helpers) - count))]
    save.cat_shrine.xp_offering = _preset_randint(*profile["shrine_xp"])
    hours = _preset_randint(*profile["play_hours"])
    minutes = _preset_randint(0, 59)
    save.officer_pass.play_time = PlayTime.from_hours_mins_secs(hours, minutes, 0).frames
    cannons = save.ototo.cannons.cannons
    optional = [cannon_id for cannon_id in cannons if cannon_id != 0]
    selected = set(secrets.SystemRandom().sample(optional, min(len(optional), _preset_randint(*profile["cannon_count"]))))
    for cannon_id, cannon in cannons.items():
        if cannon_id == 0 or cannon_id in selected:
            cannon.development = 3
            level = _preset_randint(*profile["cannon_level"])
            cannon.levels = [level - 1] if cannon_id == 0 else [level - 1, _preset_randint(*profile["cannon_parts"]), _preset_randint(*profile["cannon_parts"])]
        else:
            cannon.development = 0
            cannon.levels = [0, 0, 0]
    # 未開発に戻した砲種を編成に残さない。
    for parts in save.ototo.cannons.selected_parts:
        for index, cannon_id in enumerate(parts):
            if cannon_id != 0 and cannon_id not in selected:
                parts[index] = 0
    return hours, minutes


def apply_account_type_preset(save, account_type_key):
    """作成1個ごとに抽選する。初期垢と、後から適用される個別指定は維持する。"""
    profile = ACCOUNT_PRESET_RANGES.get(account_type_key)
    if profile is None:
        return []
    logs = []
    try:
        try:
            metadata = get_legend_metadata()
        except Exception:
            metadata = {}
        _preset_randomize_resources(save, profile, metadata)
        levels = _preset_randomize_facilities(save, profile)
        partial = _preset_randomize_main_story(save, profile)
        legend_maps, true_maps = _preset_randomize_progress(save, profile, metadata)
        hours, minutes = _preset_randomize_extras(save, profile)
        cat_stats = _preset_randomize_cats(save, account_type_key)
        stage_label = {"beginner": f"日本編クリア・未来編1章{partial}ステージ",
                       "intermediate": f"日本・未来編クリア・宇宙編1章{partial}ステージ",
                       "advanced": "宇宙編3章までクリア"}[account_type_key]
        logs.append(f"{ACCOUNT_TYPE_PRESET_LABELS[account_type_key]}ランダムプリセット({stage_label})")
        logs.append(f"猫缶{save.catfood:,} / XP{save.xp:,} / NP{save.np} / 銀チケ{save.normal_tickets} / 金チケ{save.rare_tickets}")
        logs.append(f"アイテム・素材・お宝・施設{len(levels)}種・城・ガマトト・神社を範囲内でランダム化")
        logs.append(f"レジェンド{legend_maps}章 / 真レジェンド{true_maps}章 / プレイ時間{hours}:{minutes:02d}")
        logs.append(f"キャラ追加{cat_stats['unlocked']}体・Lv/+値ランダム(レア+{cat_stats.get('rarities', {}).get(2, 0)}体)")
        if account_type_key == "advanced":
            manic_count = _preset_clear_manics(save)
            logs.append(f"大狂乱クリア({manic_count}ステージ)" if manic_count else "大狂乱は元テンプレートの進行を維持")
    except Exception as e:
        print(f"[PRESET] {account_type_key}: {e}")
        logs.append(f"{ACCOUNT_TYPE_PRESET_LABELS.get(account_type_key, account_type_key)}プリセット(一部適用)")
    return logs



def safe_custom_amounts(data):
    out = {}
    for k in CUSTOM_KEYS:
        try:
            val = int(data.get(k, 0))
        except (TypeError, ValueError):
            val = 0
        # 極端な負値・過大値を丸める
        out[k] = max(0, min(val, 2_000_000_000))
    return out


# bcsfe 3.6.0 / JP 15.5.1。lengthは入力上限で、実際の有効数は
# 最新Gatyaitembuy metadataとセーブ配列長の両方で適用時に確認する。
VIP_ITEM_GROUPS = {
    "battle_items": {"length": 64, "max": 9999},
    "catseyes": {"length": 256, "max": 9999},
    "catamins": {"length": 256, "max": 9999},
    "base_materials": {"length": 256, "max": 9999},
    "catfruit": {"length": 512, "max": 998},
    "event_tickets": {"length": 512, "max": 9999},
    "labyrinth_medals": {"length": 4, "max": 32767},
}

VIP_ITEM_LABELS = {
    "battle_items": ["スピードアップ", "トレジャーレーダー", "ネコボン", "ニャンピュータ", "おかめはちもく", "スニャイパー"],
    "catseyes": ["キャッツアイ【EX】", "キャッツアイ【レア】", "キャッツアイ【激レア】", "キャッツアイ【超激レア】", "キャッツアイ【伝説】", "キャッツアイ【闇】"],
    "catamins": ["ネコビタンA", "ネコビタンB", "ネコビタンC"],
    "base_materials": [
        "レンガ", "羽根", "備長炭", "鋼の歯車", "黄金", "宇宙石", "謎の骨", "アンモナイト",
        "レンガZ", "羽根Z", "備長炭Z", "鋼の歯車Z", "黄金Z", "宇宙石Z", "謎の骨Z", "アンモナイトZ",
    ],
    "catfruit": [
        "紫マタタビの種", "赤マタタビの種", "青マタタビの種", "緑マタタビの種", "黄マタタビの種",
        "紫マタタビ", "赤マタタビ", "青マタタビ", "緑マタタビ", "黄マタタビ", "虹マタタビ",
        "古代マタタビの種", "古代マタタビ", "虹マタタビの種", "金マタタビ", "悪マタタビの種",
        "悪マタタビ", "金マタタビの種", "紫獣石", "紅獣石", "蒼獣石", "翠獣石", "黄獣石",
        "紫獣結晶", "紅獣結晶", "蒼獣結晶", "翠獣結晶", "黄獣結晶", "虹獣石",
    ],
    "labyrinth_medals": ["ブロンズ勲章", "シルバー勲章", "ゴールド勲章", "プラチナ勲章"],
}

VIP_ITEM_SELECTED_VALUES = {
    "battle_items": {"battle_items", "custom_battle_items"},
    "catseyes": {"catseyes", "custom_catseyes"},
    "catamins": {"catamins", "custom_catamins"},
    "base_materials": {"base_materials", "custom_base_materials"},
    "catfruit": {"matatabi", "custom_matatabi"},
    "event_tickets": {"event_tickets", "custom_event_tickets"},
    "labyrinth_medals": {"labyrinth_medals"},
}


def safe_vip_items(data):
    """VIP個別アイテムを {group: {index: amount}} に正規化する。"""
    raw_groups = data.get("vip_items", {})
    if not isinstance(raw_groups, dict):
        return {}
    result = {}
    try:
        dynamic_groups = get_legend_metadata().get("vip_item_groups", {})
    except Exception as e:
        print(f"[WARN] vip item metadata fallback: {e}")
        dynamic_groups = {}
    for group_key, spec in VIP_ITEM_GROUPS.items():
        raw_values = raw_groups.get(group_key, {})
        if not isinstance(raw_values, dict):
            continue
        values = {}
        for raw_index, raw_amount in raw_values.items():
            try:
                index = int(raw_index)
                amount = int(raw_amount)
            except (TypeError, ValueError):
                continue
            labels = dynamic_groups.get(group_key) or VIP_ITEM_LABELS.get(group_key, [])
            valid_length = min(spec["length"], len(labels)) if labels else spec["length"]
            if 0 <= index < valid_length:
                values[index] = max(0, min(amount, spec["max"]))
        # 空dictも保持する。これは「親項目は選択済みだが個別項目は全解除」を表す。
        if group_key in raw_groups:
            result[group_key] = values
    if "event_tickets" in raw_groups:
        raw_values = raw_groups.get("event_tickets", {})
        values = {}
        try:
            valid_ids = {item["id"] for item in get_legend_metadata().get("event_tickets", [])}
        except Exception as e:
            print(f"[ERROR] event ticket metadata: {e}")
            valid_ids = set()
        if isinstance(raw_values, dict):
            for ticket_id, raw_amount in raw_values.items():
                try:
                    amount = int(raw_amount)
                except (TypeError, ValueError):
                    continue
                if ticket_id in valid_ids:
                    values[ticket_id] = max(0, min(amount, 9999))
        result["event_tickets"] = values
    return result


def apply_vip_items(save, vip_items):
    """bcsfeの保存配列に、選択された種類だけを書き込む。"""
    logs = []
    dynamic_labels = {}
    if vip_items:
        try:
            dynamic_labels = get_legend_metadata().get("vip_item_groups", {})
        except Exception:
            dynamic_labels = {}
    targets = {
        "battle_items": getattr(getattr(save, "battle_items", None), "items", []),
        "catseyes": getattr(save, "catseyes", []),
        "catamins": getattr(save, "catamins", []),
        "base_materials": getattr(getattr(getattr(save, "ototo", None), "base_materials", None), "materials", []),
        "catfruit": getattr(save, "catfruit", []),
        "labyrinth_medals": getattr(save, "labyrinth_medals", []),
    }
    for group_key, values in (vip_items or {}).items():
        if group_key == "event_tickets":
            arrays = {
                1: getattr(save, "event_capsules", []),
                8: getattr(save, "lucky_tickets", []),
                10: getattr(save, "event_capsules_2", []),
            }
            for ticket_id, amount in values.items():
                try:
                    category, index = map(int, ticket_id.split(":"))
                    target = arrays.get(category, [])
                    if 0 <= index < len(target):
                        target[index] = amount
                        logs.append(f"イベントチケット[{ticket_id}]({amount})")
                except (TypeError, ValueError, IndexError) as e:
                    print(f"[ERROR] event ticket {ticket_id}: {e}")
            continue
        target = targets.get(group_key)
        labels = dynamic_labels.get(group_key) or VIP_ITEM_LABELS.get(group_key, [])
        if target is None:
            continue
        if group_key == "labyrinth_medals" and len(target) < 4:
            target.extend([0] * (4 - len(target)))
        for index, amount in values.items():
            if not 0 <= index < len(target):
                continue
            try:
                entry = target[index]
                if hasattr(entry, "amount"):
                    entry.amount = amount
                else:
                    target[index] = amount
                label = labels[index] if index < len(labels) else f"{group_key}[{index}]"
                logs.append(f"{label}({amount})")
            except Exception as e:
                print(f"[ERROR] vip_items {group_key}[{index}]: {e}")
    return logs


ERROR_CAT_IDS = [156, 183, 286, 321, 340, 354, 433, 434, 466, 493, 498, 499, 500, 501,
                 741, 742, 743, 744, 745, 746, 789, 674]


def update_web_data():
    global ERROR_CAT_IDS
    while True:
        try:
            r = requests.get(
                "https://battlecats-db.com/unit/r_all.html",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
                proxies=PROXIES,
            )
            r.encoding = r.apparent_encoding
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                no_list = []
                table = soup.find("table")
                if table:
                    for tr in table.find_all("tr"):
                        cells = tr.find_all("td")
                        if len(cells) >= 10:
                            no_text = cells[0].get_text(strip=True)
                            if no_text.isdigit():
                                no_list.append(int(no_text))
                if no_list:
                    ERROR_CAT_IDS = sorted(
                        list(set(range(1, max(no_list) + 1)) - set(no_list))
                        + [674]
                    )
        except Exception as e:
            print(f"[ERROR] update_web_data: {e}")
        time.sleep(60 * 60 * 24)


if os.getenv("DISABLE_BACKGROUND_UPDATER", "0") != "1":
    _updater_thread = threading.Thread(target=update_web_data, daemon=True)
    _updater_thread.start()


CUSTOM_KEYS = [
    "custom_catfood", "custom_xp", "custom_np",
    "custom_normal_tickets", "custom_rare_tickets",
    "custom_platinum_tickets", "custom_legend_tickets",
    "custom_leadership", "custom_battle_items", "custom_catamins",
    "custom_catseyes", "custom_base_materials", "custom_matatabi",
    "custom_talent_orbs", "custom_base_upgrades", "custom_event_tickets",
]


# メインステージの章ラベル（表示順=ポジション0〜8に対応。VIP限定の章選択機能で使用）
MAIN_STORY_CHAPTER_LABELS = [
    "日本編1章", "日本編2章", "日本編3章",
    "未来編1章", "未来編2章", "未来編3章",
    "宇宙編1章", "宇宙編2章", "宇宙編3章",
]

# 表示上のポジション(0〜8) → save.story.chapters の実インデックスへの変換テーブル。
# ゾンビ編と同様に実データ上ではインデックス3が欠番のため、日本編0,1,2 → 未来編4,5,6 → 宇宙編7,8,9 とスキップする。
MAIN_STORY_REAL_INDEX = [0, 1, 2, 4, 5, 6, 7, 8, 9]


# レジェンド系の表示名はbcsfeのゲームデータから取得する。セーブ配列の種類と
# Map_Name / StageName のコードを同じ表で管理し、画面と適用処理のずれを防ぐ。
LEGEND_SERIES = {
    "legend": {"label": "レジェンドストーリー", "code": "N", "base_index": 0},
    "true_legend": {"label": "真レジェンドストーリー", "code": "NA", "base_index": 13000},
    "zero_legend": {"label": "零レジェンドストーリー", "code": "ND", "base_index": 34000},
}
LEGEND_SELECTED_VALUES = {
    "legend": "legend_clear",
    "true_legend": "true_legend_clear",
    "zero_legend": "zero_legend_clear",
}
EVENT_STAGE_FAMILIES = {
    "event": {"label": "イベントステージ", "code": "S", "base_index": 1000, "kind": "event", "group": 1},
    "collab": {"label": "コラボステージ", "code": "C", "base_index": 2000, "kind": "event", "group": 2},
    "dojo_ranking": {"label": "ランキングの間", "code": "R", "base_index": 6000, "kind": "event", "group": 3},
    "tower": {"label": "にゃんこ塔・異界にゃんこ塔", "code": "V", "base_index": 7000, "kind": "tower"},
    "catamin": {"label": "ネコビタンステージ", "code": "B", "base_index": 14000, "kind": "catamin_stages"},
    "legend_quest": {"label": "レジェンドクエスト", "code": "D", "base_index": 16000, "kind": "legend_quest"},
    "gauntlets": {"label": "強襲ステージ（各月・夏休み系を含む）", "code": "A", "base_index": 24000, "kind": "gauntlets"},
    "enigma": {"label": "発掘・地図ステージ", "code": "H", "base_index": 25000, "kind": "enigma_clears"},
    "collab_gauntlets": {"label": "コラボ強襲", "code": "CA", "base_index": 27000, "kind": "collab_gauntlets"},
    "behemoth": {"label": "超獣討伐ステージ", "code": "Q", "base_index": 31000, "kind": "behemoth_culling"},
    "labyrinth": {
        "label": "地底迷宮",
        "code": "L",
        "base_index": 33000,
        "kind": "labyrinth",
        "stage_file": "StageName_L_ja.csv",
        "uses_clear_count": False,
    },
    "colosseum": {"label": "異次元コロシアム", "code": "SR", "base_index": 36000, "kind": "event", "group": 4},
    "catclaw": {"label": "にゃんこ道検定", "code": "G", "base_index": 37000, "kind": "dojo_chapters", "stage_file": "StageName_G_ja.csv"},
}
MAX_STAGE_CLEAR_COUNT = 32767
MAX_DETAILED_STAGE_SELECTIONS = 50000
MAX_LABYRINTH_CHARACTERS = 10000
LABYRINTH_CHARACTER_ACTIONS = {
    "unlock_selected", "unlock_all", "seal_selected", "seal_all",
    "release_lineup_all",
}
_legend_metadata_cache = None
_legend_metadata_cached_at = 0.0
_legend_metadata_lock = threading.Lock()
LEGEND_METADATA_TTL = 15 * 60
_character_metadata_cache = None
_character_metadata_cached_at = 0.0
_character_metadata_lock = threading.Lock()
CHARACTER_METADATA_TTL = 15 * 60
CHARACTER_METADATA_RETRY_TTL = 60
_ototo_metadata_cache = None
_ototo_metadata_cached_at = 0.0
_ototo_metadata_lock = threading.Lock()
OTOTO_METADATA_TTL = 15 * 60

# GatyaData_Option_SetR.tsv の seriesID に対応する表示名。未登録の新規系列は
# ID付きの名称で表示し、データ更新で画面全体が使えなくならないようにする。
GACHA_SERIES_NAMES = {
    0: "伝説のネコルガ族", 1: "超激ダイナマイツ", 2: "戦国武神バサラーズ",
    3: "電脳学園ギャラクシーギャルズ", 4: "超破壊大帝ドラゴンエンペラーズ",
    5: "レッドバスターズ", 6: "超古代勇者ウルトラソウルズ",
    7: "逆襲の英雄ダークヒーローズ", 8: "ハロウィンガチャ",
    9: "クリスマスギャルズ", 11: "「ゆるドラシル」コラボガチャ",
    13: "「メルクストーリア」コラボガチャ", 14: "「生きろ！マンボウ！」コラボガチャ",
    15: "「消滅都市」コラボガチャ", 16: "新年ガチャ",
    17: "「ケリ姫スイーツ」コラボガチャ", 18: "究極降臨ギガントゼウス",
    19: "超ネコ祭", 21: "プラチナガチャ", 22: "エアバスターズ",
    23: "「魔法少女まどか☆マギカ」コラボガチャ",
    24: "革命軍隊アイアンウォーズ", 26: "イースターカーニバル",
    27: "極ネコ祭", 28: "絶命美少女ギャルズモンスターズ",
    32: "メタルバスターズ", 33: "大精霊エレメンタルピクシーズ",
    34: "劇場版「Fate/stay night [Heaven's Feel]」コラボガチャ",
    35: "超選抜祭", 37: "「エヴァンゲリオン」コラボガチャ",
    38: "「ビックリマン」コラボガチャ", 39: "極選抜祭", 42: "超極ネコ祭",
    43: "「初音ミク」コラボガチャ", 44: "「エヴァンゲリオン」コラボガチャ 2nd",
    45: "波動バスターズ", 46: "レジェンドガチャ", 47: "超国王祭",
    48: "バレンタインギャルズ", 49: "「らんま1/2」コラボガチャ",
    50: "女王祭", 52: "ホワイトデーガチャ", 53: "ジューンブライドガチャ",
    54: "「ストリートファイター6」コラボガチャ BLUE TEAM",
    55: "「ストリートファイター6」コラボガチャ RED TEAM",
    56: "超生命体バスターズ", 57: "熱血！大運動会 赤組",
    58: "熱血！大運動会 白組", 59: "バスターズ祭",
    60: "「メタルスラッグアタック」コラボガチャ",
    63: "「るろうに剣心 -明治剣客浪漫譚-」コラボガチャ",
    64: "サマーガールズ サンシャイン", 65: "サマーガールズ ブルーオーシャン",
    66: "ウルトラ4セレクション", 67: "ミラクル4セレクション",
    68: "エクセレント4セレクション", 69: "1億DL記念ガチャ",
    70: "DL記念選抜ガチャ", 71: "アウトレットガチャ",
    72: "「範馬刃牙」コラボガチャ", 73: "「ソニック・ザ・ヘッジホッグ」コラボガチャ",
    74: "アニメ「鬼滅の刃」コラボガチャ", 75: "熱血！大運動会 黒組",
}

# JP 15.6.0の系列に、確認済みJP 15.7.0の追加ガチャ所属を補完。取得失敗時も
# コラボ・ガチャ絞り込みを継続できるよう、gzip+base64で同梱する。
BUNDLED_CHARACTER_GROUPS_B64 = (
    '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'
)
BUNDLED_CHARACTER_RARITIES_B64 = (
    'H4sIAAAAAAACA41UW24EMQi7UD8W7Jxm1ftfo22GgGFmpQpNdpXwMhjer68p9m9BCPfpv3L9covtm5Jzw/iQGhyWHucRi/t6w008'
    'cvoU3SPTS8fCl0UeGFEOQgau8nDyPPoQ7YrO7ZNxc05rUbAjW95U/az5PDo2MvFWFTTUyBqVRuXoD7XtONDyZNSh7BkakBww/Hp2'
    'iwMVoh7MDNSrS1RKNxnd4u0fw9uzmOR07I43u1UVw9KSJ/ZBa3LNwgZS956hC29XyKW30tvfi+3P9y1bJS20z6u3Tl3sWImzuFnM'
    '94jGMVvYsV1mkGJHmcwL12o8rI4icUNm1m59QtsQl6xEg3bPhu8wp9jB5t+FG8W6mk4T/r+S0Tr9EJZWribTwuzy3FRI9Ja9R8up'
    'UHeUJrEo24EyQStqZFGvJdPUN4wLqjkX1t7QtLxNXreGbKVPNWewY4m3mRvbfnsS5ATW9lFta0ie/azvH8MVDAPlBgAA'
)
# JP 15.6.0にbattlecatsinfoのJP 15.7.0実装形態数を補完。Unit_Explanationには
# 未実装形態用として直前の名前を複製した行があるため、名前の行数では判定しない。
# ID456（モモコ）だけは提供された15.5.1実機の進化前後セーブ差分で第3形態を確認済み。
BUNDLED_CHARACTER_FORM_COUNTS_B64 = (
    'H4sIAAAAAAACA51T2w0DIQxbqD9xsk3V/deoyukgDwdOFarKQWIbJ3nr65+FsU4xNefe24yoODZ+1iJ1zPEcDn+tk9bFfYr/LQtf'
    'LOOKgtOHyYbwfmt1L2XYvqa/fVItLZxI7mRktMw+32Yta3VAcVjVJVQYKRaFrWrJyipW7cNVZSRO/2/k9Su3OoKm/5jWOCHV8ags'
    'qoxKULBYphJP0fIyf7PDvPfQLqU73UZrqQubpdzRdVJAekup+/4dQqdHiTbedUjOZsZcMxk7GVOBecY9vL/FnYpDydkRRzZ18jHS'
    'nMd7OaLKkVG22rxH7v7zBfK8YZnlBgAA'
)
# unitbuy.csvのforce_true_form_levelから生成した、自然な第三形態取得状態。
# 0=レベル到達で自動進化、2=素材/報酬等で取得。既存JP状態を保持。
BUNDLED_CHARACTER_TRUE_FORM_STATES_B64 = (
    'H4sIAAAAAAACA4s20EGHRqNwFI7CUTiC4GipN4xhLACeg4Ck5QYAAA=='
)
BUNDLED_TALENT_DATA_B64 = (
    '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'
)
BUNDLED_CHARACTER_DATA_B64 = (
    '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'
    'r1bV8PKb495cnmPYXQhS//3/+X9/bTL5UuUAAA=='
)
FACILITY_NAMES = [
    "にゃんこ砲攻撃力", "にゃんこ砲射程", "にゃんこ砲チャージ",
    "働きネコ仕事効率", "働きネコお財布", "お城体力",
    "研究力", "会計力", "勉強力", "統率力",
]


def _clean_stage_names(values):
    names = []
    for value in values or []:
        name = str(value).strip()
        if not name or name == "＠":
            break
        names.append(name)
    return names


def _latest_jp_game_data_getter(repo_metadata=None):
    """JP配布版を数値で選択し、通信失敗時にもCLI入力を要求しない。"""
    cc = core.CountryCode.from_code("jp")
    parsed_versions = list(core.GameDataGetter.get_downloaded_versions_region(cc))
    versions = {}
    try:
        if repo_metadata is None:
            repo_metadata = core.GameDataGetter.get_metadata(show_alt=False) or {}
        versions = core.GameDataGetter.get_versions(repo_metadata).get(cc.get_code(), {})
        for version in versions:
            try:
                parsed_versions.append(core.GameVersion.from_string(version))
            except (TypeError, ValueError):
                continue
    except Exception as e:
        repo_metadata = {}
        print(f"[WARN] game data version list: {e}")
    latest = max(parsed_versions, key=lambda version: version.game_version) if parsed_versions else None
    # BCSFEのコンストラクタはmetadataを再取得し、失敗時にCLIで質問する。
    # 取得済み一覧で初期化し、Webワーカーを入力待ちにしない。
    getter = object.__new__(core.GameDataGetter)
    getter.print = False
    getter.lang = core.core_data.config.get_str(core.ConfigKey.LOCALE)
    getter.real_cc = cc
    getter.cc = cc.get_cc_lang()
    getter.gv = latest or core.GameVersion(TARGET_GAME_VERSION_NUMBER)
    getter.version = latest.to_string() if latest is not None else None
    getter.metadata = repo_metadata
    getter.all_versions = core.GameDataGetter.get_versions(repo_metadata)
    getter.url = repo_metadata.get("base_url")
    getter.filepath = versions.get(getter.version)
    return getter


def _character_groups_from_series(series_members):
    """seriesIDごとのキャラ集合を画面用の安定した一覧へ変換する。"""
    groups = []
    for raw_series_id, raw_cat_ids in series_members.items():
        try:
            series_id = int(raw_series_id)
        except (TypeError, ValueError):
            continue
        cat_ids = sorted({
            max(1, min(9999, int(cat_id)))
            for cat_id in raw_cat_ids
            if str(cat_id).lstrip("-").isdigit() and 1 <= int(cat_id) <= 9999
        })
        if not cat_ids:
            continue
        name = GACHA_SERIES_NAMES.get(series_id, f"ガチャシリーズ #{series_id}")
        groups.append({
            "id": f"series_{series_id}",
            "series_id": series_id,
            "name": name,
            "kind": "collab" if "コラボ" in name else "gacha",
            "cat_ids": cat_ids,
        })
    return sorted(groups, key=lambda item: (item["kind"] != "collab", item["series_id"]))


def _bundled_character_groups():
    try:
        payload = json.loads(gzip.decompress(base64.b64decode(BUNDLED_CHARACTER_GROUPS_B64)))
    except (ValueError, OSError, gzip.BadGzipFile):
        return []
    return _character_groups_from_series(payload if isinstance(payload, dict) else {})


def _download_character_groups(getter):
    """最新版の有効ガチャから、コラボ・ガチャ系列ごとの所属キャラを作る。"""
    set_data = getter.download("DataLocal", "GatyaDataSetR1.csv")
    option_data = getter.download("DataLocal", "GatyaData_Option_SetR.tsv")
    if set_data is None or option_data is None:
        return _bundled_character_groups()

    sets = []
    for row in core.CSV(set_data, remove_empty=False).lines:
        members = []
        for field in row:
            cat_id = field.to_int()
            if cat_id < 0:
                break
            members.append(cat_id + 1)  # ゲームデータは0始まり、画面は1始まり。
        sets.append(members)

    series_members = {}
    lines = option_data.to_str().replace("\r\n", "\n").split("\n")
    for line in lines[1:]:
        columns = line.split("\t")
        if len(columns) < 6:
            continue
        try:
            set_id = int(columns[0])
            banner_enabled = int(columns[1])
            series_id = int(columns[5])
        except ValueError:
            continue
        if banner_enabled != 1 or not (0 <= set_id < len(sets)):
            continue
        series_members.setdefault(series_id, set()).update(sets[set_id])
    groups = _character_groups_from_series(series_members)
    return groups or _bundled_character_groups()


# JP 15.7.0: battlecatsinfo/battlecatsinfo.github.io, data/cat.tsv・catstat.tsv。
# Source commit: 4d6ef4f69f89865a4ce688182e23a78469c525db (2026-10-06)。
# MIT License: z readme/BATTLECATSINFO_LICENSE.txt。召喚される精霊は通常のキャラ選択から除外。
SUMMONED_SPIRIT_CAT_IDS = {876, 877}


def _bundled_character_metadata():
    """通信失敗時に使う、同梱済みJPキャラ情報。"""
    try:
        payload = json.loads(gzip.decompress(base64.b64decode(BUNDLED_CHARACTER_DATA_B64)))
        rarities = json.loads(gzip.decompress(base64.b64decode(BUNDLED_CHARACTER_RARITIES_B64)))
        form_counts = json.loads(gzip.decompress(base64.b64decode(BUNDLED_CHARACTER_FORM_COUNTS_B64)))
        true_form_states = json.loads(gzip.decompress(base64.b64decode(BUNDLED_CHARACTER_TRUE_FORM_STATES_B64)))
        bundled_talents = json.loads(gzip.decompress(base64.b64decode(BUNDLED_TALENT_DATA_B64)))
    except (ValueError, OSError, gzip.BadGzipFile) as e:
        raise RuntimeError("内蔵キャラ名データを読み込めません") from e

    rarity_names = ["基本キャラ", "EX", "レア", "激レア", "超激レア", "伝説レア"]
    characters = []
    for display_id, raw_names in payload.get("characters", []):
        display_id = int(display_id)
        names = [str(name).strip() for name in raw_names if str(name).strip()]
        if not names:
            continue
        rarity = rarities[display_id - 1] if display_id - 1 < len(rarities) else -1
        form_count = form_counts[display_id - 1] if display_id - 1 < len(form_counts) else 1
        # ユーザー提供のJP 15.5.1実機セーブで実装を確認した公開データ差分。
        # 自然な第三形態取得状態はunitbuy由来の0/2を別metadataで保持する。
        if display_id == 456:
            form_count = max(form_count, 3)
        form_count = max(1, min(4, int(form_count)))
        names = names[:form_count]
        while len(names) < form_count:
            names.append(names[-1] if names else f"No.{display_id} 第{len(names) + 1}形態")
        selectable = display_id not in ERROR_CAT_IDS and display_id not in SUMMONED_SPIRIT_CAT_IDS and not all(
            re.fullmatch(r"\d+[-_]\d+", name) for name in names
        )
        characters.append({
            "id": display_id,
            "names": names,
            "form_count": form_count,
            "selectable": selectable,
            "true_form_state": (
                int(true_form_states[display_id - 1])
                if display_id - 1 < len(true_form_states) else 2
            ),
            "rarity": rarity,
            "rarity_name": rarity_names[rarity] if 0 <= rarity < len(rarity_names) else "不明",
            "talents": bundled_talents.get(str(display_id), []),
        })
    return {
        "data_version": str(payload.get("version", "bundled-jp")),
        "source": "bundled",
        "rarity_names": rarity_names,
        "characters": characters,
        "groups": _bundled_character_groups(),
    }


def _download_character_metadata(getter=None):
    """最新BCSFE JPデータから名前・レアリティ・実装形態数を組み立てる。"""
    cc = core.CountryCode.from_code("jp")
    getter = getter or _latest_jp_game_data_getter()
    picture_data = getter.download("DataLocal", "nyankoPictureBookData.csv")
    unit_buy_data = getter.download("DataLocal", "unitbuy.csv")
    skill_data = getter.download("DataLocal", "SkillAcquisition.csv")
    skill_name_data = getter.download("resLocal", "SkillDescriptions.csv")
    if picture_data is None or unit_buy_data is None:
        raise RuntimeError("最新JPキャラ定義を取得できません")

    picture_rows = core.CSV(picture_data).lines
    unit_buy_rows = core.CSV(unit_buy_data).lines
    talent_names = {}
    if skill_name_data is not None:
        for row in core.CSV(
            skill_name_data,
            core.Delimeter.from_country_code_res(cc),
            remove_empty=False,
        ).lines[1:]:
            if len(row) >= 2:
                talent_names[row[0].to_int()] = row[1].to_str().split("<br>", 1)[0].strip()
    talents_by_cat = {}
    if skill_data is not None:
        for row in core.CSV(skill_data, remove_empty=False).lines[1:]:
            if len(row) < 16:
                continue
            cat_id = row[0].to_int()
            talents = []
            for slot, offset in enumerate(range(2, len(row) - 13, 14)):
                ability_id = row[offset].to_int()
                max_level = row[offset + 1].to_int() or 1
                text_id = row[offset + 10].to_int()
                is_ultra = bool(row[offset + 13].to_int())
                if ability_id <= 0 or text_id <= 0:
                    continue
                talents.append({
                    "id": ability_id,
                    "name": talent_names.get(text_id) or f"本能 {ability_id}",
                    "max_level": max(1, max_level),
                    "category": "超本能" if is_ultra else "本能",
                    "ultra": is_ultra,
                    "slot": slot,
                })
            if talents:
                talents_by_cat[cat_id] = talents
    rarity_names = ["基本キャラ", "EX", "レア", "激レア", "超激レア", "伝説レア"]
    characters = []
    for cat_id, picture_row in enumerate(picture_rows):
        if len(picture_row) < 3:
            continue
        form_count = max(1, min(4, picture_row[2].to_int()))
        rarity = unit_buy_rows[cat_id][13].to_int() if cat_id < len(unit_buy_rows) and len(unit_buy_rows[cat_id]) > 13 else -1
        name_data = getter.download("resLocal", f"Unit_Explanation{cat_id + 1}_ja.csv")
        names = []
        if name_data is not None:
            name_rows = core.CSV(
                name_data,
                core.Delimeter.from_country_code_res(cc),
                remove_empty=False,
            ).lines
            names = [row[0].to_str().strip() for row in name_rows if len(row)]
        # 15.5.1実機の進化前後セーブ差分で、モモコ(ID456)の第3形態を確認済み。
        # Unit_Explanationの未実装用重複行を一般化すると大量の誤表示になるため、
        # 公開picture bookとの差分補正は実測できたキャラだけに限定する。
        if cat_id + 1 == 456:
            form_count = max(form_count, 3)
        names = [name for name in names[:form_count] if name]
        while len(names) < form_count:
            names.append(names[-1] if names else f"No.{cat_id + 1} 第{len(names) + 1}形態")
        selectable = cat_id + 1 not in ERROR_CAT_IDS and cat_id + 1 not in SUMMONED_SPIRIT_CAT_IDS and not all(
            re.fullmatch(r"\d+[-_]\d+", name) for name in names
        )
        characters.append({
            "id": cat_id + 1,
            "names": names,
            "form_count": form_count,
            "selectable": selectable,
            "true_form_state": (
                0 if cat_id < len(unit_buy_rows)
                and len(unit_buy_rows[cat_id]) > 20
                and unit_buy_rows[cat_id][20].to_int() >= 0
                else 2
            ),
            "rarity": rarity,
            "rarity_name": rarity_names[rarity] if 0 <= rarity < len(rarity_names) else "不明",
            "talents": talents_by_cat.get(cat_id, []),
        })

    if not characters:
        raise RuntimeError("最新JPキャラ名データが空です")
    return {
        "data_version": getter.version or "latest-jp",
        "source": "bcsfe",
        "rarity_names": rarity_names,
        "characters": characters,
        "groups": _download_character_groups(getter),
    }


def _game_version_number(value):
    """公開版数を厳密に数値化する。15.10を15.9より新しく扱う。"""
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{1,2}", value):
        raise ValueError("ゲーム版数の形式が不正です")
    major, minor, patch = map(int, value.split("."))
    if major < 1:
        raise ValueError("ゲーム版数が不正です")
    return major * 10000 + minor * 100 + patch


def _game_update_get(url):
    """公開データだけを、タイムアウトとサイズ上限付きで取得する。"""
    with requests.get(url, timeout=(15, 20), stream=True,
                      headers={"User-Agent": "CatProxyService-GameData/1.0"}) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > GAME_UPDATE_MAX_BYTES:
                raise ValueError("更新データがサイズ上限を超えています")
            chunks.append(chunk)
        return b"".join(chunks)


def _fetch_latest_jp_app_version():
    data = json.loads(_game_update_get("https://itunes.apple.com/lookup?id=547145938&country=jp"))
    for result in data.get("results", []):
        if result.get("trackId") == 547145938 and result.get("bundleId") == "jp.co.ponos.battlecats":
            return _game_version_number(result.get("version"))
    raise ValueError("日本版にゃんこ大戦争の公開版数を取得できません")


def _validate_character_update(candidate, current):
    """欠損データ・旧版・不正な番号を採用せず、既知の形態を保持する。"""
    version = _game_version_number(candidate.get("data_version"))
    if version < _game_version_number(current["data_version"]):
        raise ValueError("旧版のキャラ定義です")
    characters = candidate.get("characters")
    if not isinstance(characters, list) or not len(current["characters"]) <= len(characters) <= 9999:
        raise ValueError("キャラ定義が欠損しています")
    for index, character in enumerate(characters):
        if character.get("id") != index + 1 or character.get("rarity") not in range(6):
            raise ValueError("キャラ番号・レアリティが不正です")
        count = character.get("form_count")
        names = character.get("names")
        if count not in (1, 2, 3, 4) or not isinstance(names, list) or len(names) != count:
            raise ValueError("キャラ形態の定義が不正です")
        if any(not isinstance(name, str) or not name.strip() or len(name) > 240
               or any(ord(ch) < 32 for ch in name) for name in names):
            raise ValueError("キャラ名の定義が不正です")
        if not isinstance(character.get("selectable"), bool) or character.get("true_form_state") not in (0, 2):
            raise ValueError("キャラ解放状態の定義が不正です")
        if index < len(current["characters"]) and count < current["characters"][index]["form_count"]:
            raise ValueError("既知のキャラ形態が減っています")
        talents = character.get("talents", [])
        if not isinstance(talents, list) or len(talents) > 64:
            raise ValueError("本能定義が不正です")
        seen = set()
        for talent in talents:
            if (not isinstance(talent.get("id"), int) or not 1 <= talent["id"] <= 10000
                    or talent["id"] in seen or not isinstance(talent.get("max_level"), int)
                    or not 1 <= talent["max_level"] <= 100 or not isinstance(talent.get("ultra"), bool)):
                raise ValueError("本能の番号・上限が不正です")
            seen.add(talent["id"])
    valid_ids = set(range(1, len(characters) + 1))
    groups = candidate.get("groups", [])
    if not isinstance(groups, list) or len(groups) > 1000:
        raise ValueError("ガチャ所属の定義が不正です")
    for group in groups:
        if (not isinstance(group.get("id"), str) or not isinstance(group.get("name"), str)
                or group.get("kind") not in ("gacha", "collab")
                or not isinstance(group.get("cat_ids"), list)
                or any(cat_id not in valid_ids for cat_id in group["cat_ids"])):
            raise ValueError("ガチャ所属の番号が不正です")
    return candidate


def _parse_published_characters(cat_text, stat_text, pools, current, revision):
    """同じ公開コミットのTSVから、保存番号とJP名・実装形態を組み立てる。"""
    rows = list(csv.DictReader(io.StringIO(cat_text), delimiter="\t"))
    stat_rows = list(csv.DictReader(io.StringIO(stat_text), delimiter="\t"))
    if not rows or not {"rarity", "form_count", "version", "talents", "obtain"} <= set(rows[0]):
        raise ValueError("公開キャラデータの形式が変わっています")
    if not stat_rows or not {"id", "name_jp", "name_tw"} <= set(stat_rows[0]):
        raise ValueError("公開キャラ名の形式が変わっています")
    versions = [int(row["version"]) for row in rows if row["version"]]
    data_version = max(versions, default=0)
    if data_version < _game_version_number(current["data_version"]):
        raise ValueError("公開キャラ定義が旧版です")
    stats = {}
    for row in stat_rows:
        cat_id = int(row["id"])
        if not 0 <= cat_id < len(rows):
            raise ValueError("公開キャラ名の番号が不正です")
        stats.setdefault(cat_id, []).append(row)
    old = {character["id"]: character for character in current["characters"]}
    talent_names = {talent["id"]: talent["name"] for character in current["characters"]
                    for talent in character.get("talents", [])}
    characters = []
    for cat_id, row in enumerate(rows):
        display_id = cat_id + 1
        count = int(row["form_count"])
        forms = stats.get(cat_id, [])
        if count not in (1, 2, 3, 4) or len(forms) != count:
            raise ValueError("公開キャラの実装形態と名前が一致しません")
        previous = old.get(display_id, {})
        previous_names = previous.get("names", [])
        # name_jp未登録の新規JPキャラは、配布元がname_twに仮置きしている名前を使用。
        names = [(form["name_jp"].strip() or
                  (previous_names[index] if index < len(previous_names) else form["name_tw"].strip()))
                 for index, form in enumerate(forms)]
        fields = row["talents"].split("|") if row["talents"] else []
        if fields and (len(fields) - 1) % 14:
            raise ValueError("公開本能データの形式が変わっています")
        talents = []
        for slot, offset in enumerate(range(1, len(fields) - 13, 14)):
            chunk = list(map(int, fields[offset:offset + 14]))
            ability_id, max_level, text_id, ultra = chunk[0], chunk[1] or 1, chunk[10], bool(chunk[13])
            if ability_id <= 0 or text_id <= 0:
                continue
            talents.append({"id": ability_id, "name": talent_names.get(ability_id, f"本能 {ability_id}"),
                            "max_level": max_level, "category": "超本能" if ultra else "本能",
                            "ultra": ultra, "slot": slot})
        rarity = int(row["rarity"])
        if rarity not in range(6):
            raise ValueError("公開レアリティが不正です")
        summoned = row["obtain"].strip() == "可由貓咪召喚出" or display_id in SUMMONED_SPIRIT_CAT_IDS
        selectable = not summoned and display_id not in ERROR_CAT_IDS and not all(
            re.fullmatch(r"\d+[-_]\d+", name) for name in names)
        characters.append({"id": display_id, "names": names, "form_count": count,
                           "rarity": rarity, "rarity_name": current["rarity_names"][rarity],
                           "selectable": selectable, "true_form_state": previous.get("true_form_state", 2),
                           "talents": talents})
    groups = copy.deepcopy(current.get("groups", []))
    def pool_key(name):
        name = unicodedata.normalize("NFKC", name).casefold()
        return re.sub(r"[\s「」『』\"'\-]|コラボ|ガチャ", "", name)
    for pool in pools:
        name = pool.get("jp_name", "").strip()
        if not name or pool.get("type") != "rare":
            continue
        members = sorted({int(item[1]) + 1 for items in pool.get("group_items", [])
                          for item in items if isinstance(item, list) and len(item) == 2 and item[0] == "unit"})
        if not members:
            continue
        matches = [group for group in groups if pool_key(group["name"]) == pool_key(name)]
        if len(matches) == 1:
            matches[0]["cat_ids"] = sorted(set(matches[0]["cat_ids"]) | set(members))
        elif not matches:
            groups.append({"id": "pool_" + hashlib.sha256(name.encode()).hexdigest()[:12],
                           "name": name, "kind": "collab" if pool.get("collab") else "gacha", "cat_ids": members})
    candidate = {"data_version": core.GameVersion(data_version).to_string(), "source": "battlecatsinfo",
                 "revision": revision, "rarity_names": current["rarity_names"],
                 "characters": characters, "groups": groups}
    return _validate_character_update(candidate, current)


def _fetch_published_characters(current):
    commit = json.loads(_game_update_get("https://api.github.com/repos/battlecatsinfo/battlecatsinfo.github.io/commits/master"))
    revision = commit.get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("公開データのコミット番号が不正です")
    if revision == current.get("revision"):
        return current
    base = "https://raw.githubusercontent.com/battlecatsinfo/battlecatsinfo.github.io/" + revision + "/data/"
    cats = _game_update_get(base + "cat.tsv").decode("utf-8-sig")
    stats = _game_update_get(base + "catstat.tsv").decode("utf-8-sig")
    pools = json.loads(_game_update_get(base + "pools.json"))
    if not isinstance(pools, list):
        raise ValueError("公開ガチャデータの形式が不正です")
    return _parse_published_characters(cats, stats, pools, current, revision)


def _validate_rank_update(candidate, current):
    thresholds = candidate.get("thresholds")
    if (_game_version_number(candidate.get("data_version")) < _game_version_number(current["data_version"])
            or not isinstance(thresholds, list) or not len(current["thresholds"]) <= len(thresholds) <= 10000
            or any(not isinstance(rank, int) or rank <= 0 for rank in thresholds)):
        raise ValueError("ユーザーランク報酬の更新データが不正です")
    # rankGift.csvの行番号は受取フラグの保存位置。並べ替え・index移動をしない。
    if thresholds[:len(current["thresholds"])] != current["thresholds"]:
        raise ValueError("ユーザーランク報酬の保存indexが変わっています")
    return candidate


def _ensure_game_update_state():
    global _game_update_state
    with _game_update_state_lock:
        if _game_update_state is not None:
            return _game_update_state
        bundled = _bundled_character_metadata()
        rank = {"data_version": core.GameVersion(BUNDLED_RANK_GIFT_VERSION_NUMBER).to_string(),
                "thresholds": json.loads(gzip.decompress(base64.b64decode(BUNDLED_RANK_GIFT_THRESHOLDS_B64)))}
        state = {"schema": 1, "game_version_number": TARGET_GAME_VERSION_NUMBER,
                 "characters": bundled, "rank_gifts": rank, "last_checked_at": None,
                 "next_check_at": 0, "status": "pending", "errors": []}
        try:
            with open(GAME_UPDATE_CACHE_PATH, "rb") as file:
                raw = file.read(GAME_UPDATE_MAX_BYTES + 1)
            if len(raw) > GAME_UPDATE_MAX_BYTES:
                raise ValueError("更新キャッシュがサイズ上限を超えています")
            cached = json.loads(raw)
            if cached.get("schema") != 1:
                raise ValueError("更新キャッシュの形式が不正です")
            version = cached.get("game_version_number")
            if not isinstance(version, int) or not 10000 <= version <= 999999:
                raise ValueError("更新キャッシュの版数が不正です")
            state["game_version_number"] = max(TARGET_GAME_VERSION_NUMBER, version)
            state["characters"] = _validate_character_update(cached["characters"], bundled)
            state["rank_gifts"] = _validate_rank_update(cached["rank_gifts"], rank)
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"[GAME UPDATE] cache fallback: {type(exc).__name__}")
            state.update(game_version_number=TARGET_GAME_VERSION_NUMBER, characters=bundled, rank_gifts=rank)
        _game_update_state = state
        return state


def _persist_game_update_state():
    temporary = None
    try:
        with _game_update_state_lock:
            data = json.dumps(_ensure_game_update_state(), ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) > GAME_UPDATE_MAX_BYTES:
            raise ValueError("更新キャッシュがサイズ上限を超えています")
        directory = os.path.dirname(GAME_UPDATE_CACHE_PATH) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="game_updates_", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.replace(temporary, GAME_UPDATE_CACHE_PATH)
    except Exception as exc:
        print(f"[GAME UPDATE] cache write skipped: {type(exc).__name__}")
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _refresh_game_updates():
    """更新はバックグラウンドで行い、画面表示や実行中ジョブを待たせない。"""
    if not _game_update_refresh_lock.acquire(blocking=False):
        return False
    errors, successes = [], 0
    try:
        _ensure_game_update_state()
        try:
            version = _fetch_latest_jp_app_version()
            with _game_update_state_lock:
                _game_update_state["game_version_number"] = max(_game_update_state["game_version_number"], version)
            successes += 1
        except Exception as exc:
            errors.append("app_version:" + type(exc).__name__)
        try:
            with _game_update_state_lock:
                current = _game_update_state["characters"]
            candidate = _fetch_published_characters(current)
            with _game_update_state_lock:
                _game_update_state["characters"] = _validate_character_update(candidate, _game_update_state["characters"])
            successes += 1
        except Exception as exc:
            errors.append("characters:" + type(exc).__name__)
        try:
            repo = json.loads(_game_update_get(core.GameDataGetter.repo_url()))
            if not isinstance(repo, dict) or not isinstance(repo.get("versions"), dict):
                raise ValueError("BCData配布データの形式が不正です")
            getter = _latest_jp_game_data_getter(repo_metadata=repo)
            if getter.version is not None:
                version = _game_version_number(getter.version)
                with _game_update_state_lock:
                    current = _game_update_state["characters"]
                    rank = _game_update_state["rank_gifts"]
                if version >= _game_version_number(current["data_version"]):
                    candidate = _download_character_metadata(getter=getter)
                    with _game_update_state_lock:
                        _game_update_state["characters"] = _validate_character_update(candidate, _game_update_state["characters"])
                if version > _game_version_number(rank["data_version"]):
                    data = getter.download("DataLocal", "rankGift.csv")
                    if data is None:
                        raise ValueError("rankGift.csvを取得できません")
                    thresholds = [row[0].to_int() for row in core.CSV(data).lines if len(row)]
                    candidate = {"data_version": getter.version, "thresholds": thresholds}
                    with _game_update_state_lock:
                        _game_update_state["rank_gifts"] = _validate_rank_update(candidate, _game_update_state["rank_gifts"])
            successes += 1
        except Exception as exc:
            errors.append("bcdata:" + type(exc).__name__)
        with _game_update_state_lock:
            now = time.time()
            _game_update_state.update(last_checked_at=now,
                                      next_check_at=now + (GAME_UPDATE_RETRY_INTERVAL if errors else GAME_UPDATE_INTERVAL),
                                      status="partial" if errors and successes else "offline" if errors else "ok", errors=errors)
        _persist_game_update_state()
        print(f"[GAME UPDATE] JP {core.GameVersion(get_target_game_version_number()).to_string()} / "
              f"characters {get_character_metadata()['data_version']} / "
              f"{'retry' if errors else 'ok'}")
        return True
    finally:
        _game_update_refresh_lock.release()


def _game_update_loop():
    while not _game_update_stop.is_set():
        try:
            _refresh_game_updates()
        except Exception as exc:
            print(f"[GAME UPDATE] refresh retry: {type(exc).__name__}")
        with _game_update_state_lock:
            due = _ensure_game_update_state()["next_check_at"]
        _game_update_stop.wait(max(GAME_UPDATE_RETRY_INTERVAL, due - time.time()))


def _start_game_auto_updates():
    global _game_update_thread
    _ensure_game_update_state()
    if not AUTO_GAME_UPDATE_ENABLED:
        return
    with _game_update_start_lock:
        if _game_update_thread is None or not _game_update_thread.is_alive():
            _game_update_thread = threading.Thread(target=_game_update_loop, name="game-data-updater", daemon=True)
            _game_update_thread.start()


def get_target_game_version_number():
    _start_game_auto_updates()
    with _game_update_state_lock:
        return _game_update_state["game_version_number"]


def get_game_update_status():
    _start_game_auto_updates()
    with _game_update_state_lock:
        state = _game_update_state
        return {"auto_update_enabled": AUTO_GAME_UPDATE_ENABLED,
                "game_version": core.GameVersion(state["game_version_number"]).to_string(),
                "character_data_version": state["characters"]["data_version"],
                "rank_gift_data_version": state["rank_gifts"]["data_version"],
                "character_count": sum(c.get("selectable", True) for c in state["characters"]["characters"]),
                "last_checked_at": state["last_checked_at"], "next_check_at": state["next_check_at"],
                "status": state["status"], "interval_seconds": GAME_UPDATE_INTERVAL}


def get_character_metadata():
    """画面には同梱/最後に検証できた一覧を即返し、外部取得は別スレッドで行う。"""
    _start_game_auto_updates()
    with _game_update_state_lock:
        return _game_update_state["characters"]



def _bundled_ototo_metadata():
    names = [
        "にゃんこ城強化", "スロウ砲", "鉄壁砲", "かみなり砲",
        "水鉄砲", "エンジェル砲", "キャノンブレイク砲", "呪い砲",
    ]
    output = []
    for cannon_id, name in enumerate(names):
        if cannon_id == 0:
            parts = [{"id": 0, "name": "城体力", "min_level": 1, "max_level": 30}]
        else:
            parts = [
                {"id": 0, "name": "主砲", "min_level": 1, "max_level": 30},
                {"id": 1, "name": "土台", "min_level": 0, "max_level": 20},
                {"id": 2, "name": "装飾", "min_level": 0, "max_level": 20},
            ]
        output.append({"id": cannon_id, "name": name, "parts": parts})
    return {"data_version": "bundled-jp-15.6.0", "source": "bundled", "cannons": output}


def _download_ototo_metadata():
    cc = core.CountryCode.from_code("jp")
    getter = _latest_jp_game_data_getter()
    description_data = getter.download("resLocal", "CastleRecipeDescriptions.csv")
    unlock_data = getter.download("DataLocal", "CastleRecipeUnlock.csv")
    if description_data is None or unlock_data is None:
        raise RuntimeError("オトート定義を取得できません")

    max_levels = {}
    for row in core.CSV(unlock_data, remove_empty=False).lines:
        if len(row) < 5:
            continue
        cannon_id, part_id, level = row[0].to_int(), row[1].to_int(), row[4].to_int()
        if cannon_id < 0 or part_id not in {0, 1, 2} or level < 0:
            continue
        max_levels[(cannon_id, part_id)] = max(max_levels.get((cannon_id, part_id), 0), level)

    cannons = []
    rows = core.CSV(
        description_data,
        delimiter=core.Delimeter.from_country_code_res(cc),
        remove_empty=False,
    ).lines
    for row in rows:
        if len(row) < 9:
            continue
        cannon_id = row[0].to_int()
        if cannon_id < 0:
            continue
        name = row[1].to_str().strip() or row[6].to_str().strip() or f"城強化 #{cannon_id}"
        part_ids = [part_id for part_id in (0, 1, 2) if (cannon_id, part_id) in max_levels]
        if not part_ids:
            continue
        part_names = {
            0: "城体力" if cannon_id == 0 else "主砲",
            1: row[7].to_str().strip() or "土台",
            2: row[8].to_str().strip() or "装飾",
        }
        parts = [{
            "id": part_id,
            "name": part_names[part_id],
            "min_level": 1 if part_id == 0 else 0,
            "max_level": max_levels[(cannon_id, part_id)],
        } for part_id in part_ids]
        cannons.append({"id": cannon_id, "name": name, "parts": parts})
    if not cannons:
        raise RuntimeError("オトート定義が空です")
    return {
        "data_version": getter.version or "latest-jp",
        "source": "bcsfe",
        "cannons": cannons,
    }


def get_ototo_metadata():
    global _ototo_metadata_cache, _ototo_metadata_cached_at
    if _ototo_metadata_cache is not None and time.time() - _ototo_metadata_cached_at < OTOTO_METADATA_TTL:
        return _ototo_metadata_cache
    with _ototo_metadata_lock:
        if _ototo_metadata_cache is not None and time.time() - _ototo_metadata_cached_at < OTOTO_METADATA_TTL:
            return _ototo_metadata_cache
        try:
            metadata = _download_ototo_metadata()
        except Exception as e:
            print(f"[WARN] latest ototo metadata fallback: {e}")
            metadata = _bundled_ototo_metadata()
        _ototo_metadata_cache = metadata
        _ototo_metadata_cached_at = time.time()
        return _ototo_metadata_cache


def get_legend_metadata():
    """bcsfeの最新JPゲームデータから章名・ステージ名・実装済み冠数を返す。"""
    global _legend_metadata_cache, _legend_metadata_cached_at
    if _legend_metadata_cache is not None and time.time() - _legend_metadata_cached_at < LEGEND_METADATA_TTL:
        return _legend_metadata_cache
    with _legend_metadata_lock:
        if _legend_metadata_cache is not None and time.time() - _legend_metadata_cached_at < LEGEND_METADATA_TTL:
            return _legend_metadata_cache

        cc = core.CountryCode.from_code("jp")
        # 文字列順では15.10と15.9を誤判定し得るため、配布版を数値比較する。
        getter = _latest_jp_game_data_getter()
        map_name_data = getter.download("resLocal", "Map_Name.csv")
        map_option_data = getter.download("DataLocal", "Map_option.csv")
        if map_name_data is None or map_option_data is None:
            raise RuntimeError("最新JPゲームデータを取得できません")
        map_name_csv = core.CSV(map_name_data, core.Delimeter.from_country_code_res(cc))
        all_map_names = {
            row[0].to_int(): row[1].to_str().strip()
            for row in map_name_csv if len(row) >= 2
        }
        map_option = core.MapOption.from_csv(core.CSV(map_option_data))
        # 異次元コロシアムは全章のMap_Nameが同名なので、特殊ルール名を章名へ足す。
        special_rule_names = {}
        special_rules_data = getter.download("DataLocal", "SpecialRulesMap.json")
        localizable_data = getter.download("resLocal", "localizable.tsv")
        if special_rules_data is not None and localizable_data is not None:
            localizable_names = {
                row[0].to_str().strip(): row[1].to_str().strip()
                for row in core.CSV(localizable_data, "\t") if len(row) >= 2
            }
            raw_rule_maps = core.JsonFile.from_data(special_rules_data).as_object().get("MapID", {})
            for raw_map_id, rule in raw_rule_maps.items():
                try:
                    full_map_id = int(raw_map_id)
                except (TypeError, ValueError):
                    continue
                label = str(rule.get("RuleNameLabel", "")) if isinstance(rule, dict) else ""
                if label and localizable_names.get(label):
                    special_rule_names[full_map_id] = localizable_names[label]
        metadata = {}
        for series_key, spec in LEGEND_SERIES.items():
            stage_data = getter.download(
                "resLocal", f"StageName_R{spec['code']}_ja.csv"
            )
            if stage_data is None:
                raise RuntimeError(f"{series_key}のステージ名を取得できません")
            stage_csv = core.CSV(stage_data, core.Delimeter.from_country_code_res(cc))
            maps = []
            for map_id, row in enumerate(stage_csv):
                stage_names = _clean_stage_names(row.to_str_list())
                map_name = all_map_names.get(spec["base_index"] + map_id)
                if not stage_names or not map_name:
                    continue
                option = map_option.get_map(spec["base_index"] + map_id) if map_option else None
                crown_count = max(1, min(4, int(option.crown_count if option else 4)))
                maps.append({
                    "id": map_id,
                    "name": map_name,
                    "stages": stage_names,
                    "crowns": crown_count,
                })
            metadata[series_key] = {"label": spec["label"], "maps": maps}

        # ゾンビ襲来はメイン3編×各3章。セーブ内のステージ順に並べ替えて返す。
        story_stage_names = {}
        for chapter_type in range(3):
            stage_data = getter.download("resLocal", f"StageName{chapter_type}_ja.csv")
            if stage_data is None:
                raise RuntimeError(f"ゾンビ編{chapter_type}のステージ名を取得できません")
            stage_csv = core.CSV(stage_data, core.Delimeter.from_country_code_res(cc))
            source_names = [row[0].to_str().strip() for row in stage_csv if len(row)]
            story_stage_names[chapter_type] = [
                source_names[stage_id if stage_id >= 46 else 45 - stage_id]
                for stage_id in range(min(48, len(source_names)))
            ]
        metadata["zombie"] = {
            "label": "ゾンビ襲来",
            "chapters": [
                {
                    "id": position,
                    "real_id": MAIN_STORY_REAL_INDEX[position],
                    "name": MAIN_STORY_CHAPTER_LABELS[position],
                    "stages": story_stage_names[position // 3],
                }
                for position in range(len(MAIN_STORY_CHAPTER_LABELS))
            ],
        }
        metadata["main_story"] = {
            "label": "メインステージ",
            "chapters": [
                {
                    "id": position,
                    "real_id": MAIN_STORY_REAL_INDEX[position],
                    "name": MAIN_STORY_CHAPTER_LABELS[position],
                    "stages": story_stage_names[position // 3],
                }
                for position in range(len(MAIN_STORY_CHAPTER_LABELS))
            ],
        }

        # bcsfeの各ステージ編集機能と同じ文字コード・ベースIDを使う。
        # 通常イベントだけでなく、塔・強襲・超獣・地図・コラボもまとめて返す。
        event_families = {}
        for family_key, family_spec in EVENT_STAGE_FAMILIES.items():
            stage_file = family_spec.get(
                "stage_file", f"StageName_R{family_spec['code']}_ja.csv"
            )
            stage_data = getter.download(
                "resLocal", stage_file
            )
            if stage_data is None:
                continue
            stage_csv = core.CSV(stage_data, core.Delimeter.from_country_code_res(cc))
            maps = []
            for map_id, row in enumerate(stage_csv):
                stage_names = _clean_stage_names(row.to_str_list())
                full_map_id = family_spec["base_index"] + map_id
                map_name = all_map_names.get(full_map_id)
                if not stage_names or not map_name:
                    continue
                if family_key == "colosseum" and special_rule_names.get(full_map_id):
                    map_name = f"{map_name}【{special_rule_names[full_map_id]}】"
                option = map_option.get_map(full_map_id) if map_option else None
                crown_count = max(1, min(4, int(option.crown_count if option else 1)))
                maps.append({
                    "id": map_id,
                    "name": map_name,
                    "stages": stage_names,
                    "crowns": crown_count,
                })
            event_families[family_key] = {
                "label": family_spec["label"],
                "maps": maps,
                "uses_clear_count": family_spec.get("uses_clear_count", True),
            }
        metadata["event_stage_families"] = event_families

        aku_stage_data = getter.download("resLocal", "StageName_DM_ja.csv")
        if aku_stage_data is None:
            raise RuntimeError("魔界編のステージ名を取得できません")
        aku_stage_csv = core.CSV(aku_stage_data, core.Delimeter.from_country_code_res(cc))
        aku_stage_names = _clean_stage_names([
            value.to_str() for row in aku_stage_csv for value in row
        ])
        metadata["aku"] = {
            "label": "魔界編",
            "chapters": [{"id": 0, "name": "魔界編", "stages": aku_stage_names}],
        }
        aku_ex_stage_data = getter.download("resLocal", "StageName_RE_ja.csv")
        if aku_ex_stage_data is None:
            raise RuntimeError("魔界編EXのステージ名を取得できません")
        aku_ex_stage_csv = core.CSV(aku_ex_stage_data, core.Delimeter.from_country_code_res(cc))
        aku_ex_names = _clean_stage_names(aku_ex_stage_csv[42].to_str_list())
        metadata["aku_ex"] = {
            "label": "魔界編EX",
            "chapters": [{
                "id": 0,
                "name": all_map_names.get(4042, "富士山EX"),
                "stages": aku_ex_names or ["富士山"],
            }],
        }

        metadata["data_version"] = getter.version

        ability_data = getter.download("DataLocal", "AbilityData.csv")
        if ability_data is None:
            raise RuntimeError("施設データを取得できません")
        ability_csv = core.CSV(ability_data)
        metadata["facilities"] = [
            {
                "id": facility_id,
                "name": FACILITY_NAMES[facility_id],
                "max_base": ability_csv[facility_id][2].to_int(),
                "max_plus": ability_csv[facility_id][3].to_int(),
                "max_level": ability_csv[facility_id][2].to_int() + ability_csv[facility_id][3].to_int(),
            }
            for facility_id in range(min(len(FACILITY_NAMES), len(ability_csv)))
        ]

        equipment_data = getter.download("DataLocal", "equipmentlist.json")
        grade_data = getter.download("DataLocal", "equipmentgrade.csv")
        attribute_data = getter.download("resLocal", "attribute_explonation.tsv")
        effect_data = getter.download("resLocal", "equipment_explonation.tsv")
        if any(value is None for value in (equipment_data, grade_data, attribute_data, effect_data)):
            raise RuntimeError("本能玉データを取得できません")
        raw_orbs = core.JsonFile.from_data(equipment_data).as_object().get("ID", [])
        grade_csv = core.CSV(grade_data)
        attribute_csv = core.CSV(attribute_data, "\t")
        effect_csv = core.CSV(effect_data, "\t")
        orb_groups = {}
        for orb_id, raw_orb in enumerate(raw_orbs):
            grade_id = int(raw_orb.get("gradeID", 0))
            effect_id = int(raw_orb.get("content", 0))
            target_id = raw_orb.get("attribute")
            target_id = int(target_id) if target_id is not None else None
            key = (target_id, effect_id)
            if key not in orb_groups:
                target = ""
                if target_id is not None and 0 <= target_id < len(attribute_csv):
                    row = attribute_csv[target_id]
                    target = (row[1].to_str() if len(row) > 1 else row[0].to_str()).strip()
                effect = effect_csv[effect_id][0].to_str().split("%@", 1)[0].strip()
                orb_groups[key] = {
                    "name": f"{target} {effect}".strip(),
                    "target_id": target_id,
                    "effect_id": effect_id,
                    "ranks": [],
                }
            rank = grade_csv[grade_id][3].to_str().strip()
            orb_groups[key]["ranks"].append({"id": orb_id, "rank": rank, "grade_id": grade_id})
        metadata["talent_orbs"] = list(orb_groups.values())

        ticket_buy_data = getter.download("DataLocal", "Gatyaitembuy.csv")
        ticket_name_data = getter.download("resLocal", "GatyaitemName.csv")
        if ticket_buy_data is None or ticket_name_data is None:
            raise RuntimeError("イベントチケットデータを取得できません")
        ticket_buy_csv = core.CSV(ticket_buy_data)
        ticket_name_csv = core.CSV(ticket_name_data, core.Delimeter.from_country_code_res(cc))
        ticket_category_labels = {1: "イベント", 8: "福引", 10: "福引2"}
        event_tickets = []
        for item_id, row in enumerate(ticket_buy_csv.lines[1:]):
            if len(row) < 8:
                continue
            category = row[6].to_int()
            index = row[7].to_int()
            if category not in ticket_category_labels or index < 0:
                continue
            name = ticket_name_csv[item_id][0].to_str().strip() if item_id < len(ticket_name_csv) else ""
            comment = row[12].to_str().strip() if len(row) > 12 else ""
            purpose = comment or name or "名称不明チケット"
            if purpose == name:
                label = f"{name}（{ticket_category_labels[category]}保存枠{index + 1}）"
            else:
                label = f"{purpose}（{name or '名称不明チケット'}・{ticket_category_labels[category]}保存枠{index + 1}）"
            event_tickets.append({
                "id": f"{category}:{index}",
                "item_id": item_id,
                "category": category,
                "index": index,
                "name": name or "名称不明チケット",
                "purpose": purpose,
                "label": label,
            })
        # Gatyaitembuy.csvには過去イベントの保存枠も残り続ける。
        # 同名チケットはitem_idが最も新しい枠だけを採用し、最新版追加時に自動更新する。
        latest_event_tickets = {}
        for ticket in event_tickets:
            previous = latest_event_tickets.get(ticket["name"])
            priority = ("ID統一用" in ticket["purpose"], ticket["item_id"])
            previous_priority = (
                "ID統一用" in previous["purpose"], previous["item_id"]
            ) if previous else (False, -1)
            if priority > previous_priority:
                latest_event_tickets[ticket["name"]] = ticket
        event_tickets = sorted(latest_event_tickets.values(), key=lambda item: item["item_id"])
        for ticket in event_tickets:
            if "ID統一用" in ticket["purpose"]:
                ticket["label"] = f"{ticket['name']}（最新共通枠）"
            elif ticket["purpose"] != ticket["name"]:
                ticket["label"] = f"{ticket['purpose']}（{ticket['name']}・最新枠）"
            else:
                ticket["label"] = f"{ticket['name']}（最新枠）"
        metadata["event_tickets"] = event_tickets

        # 既存のVIP個別アイテムもGatyaitembuyの保存インデックスから生成する。
        # 新しいマタタビ等が追加された場合、HTMLの固定配列を更新せず追従できる。
        category_by_group = {
            "battle_items": 3,
            "catfruit": 4,
            "catseyes": 5,
            "catamins": 6,
            "base_materials": 7,
        }
        vip_item_groups = {}
        for group_key, category in category_by_group.items():
            labels_by_index = {}
            for item_id, row in enumerate(ticket_buy_csv.lines[1:]):
                if len(row) < 8 or row[6].to_int() != category:
                    continue
                item_index = row[7].to_int()
                if item_index < 0:
                    continue
                item_name = ticket_name_csv[item_id][0].to_str().strip() if item_id < len(ticket_name_csv) else ""
                labels_by_index[item_index] = item_name or f"{group_key}[{item_index}]"
            if labels_by_index:
                max_index = max(labels_by_index)
                fallback_labels = VIP_ITEM_LABELS.get(group_key, [])
                vip_item_groups[group_key] = [
                    labels_by_index.get(
                        item_index,
                        fallback_labels[item_index] if item_index < len(fallback_labels) else f"{group_key}[{item_index}]",
                    )
                    for item_index in range(max_index + 1)
                ]
        metadata["vip_item_groups"] = vip_item_groups
        _legend_metadata_cache = metadata
        _legend_metadata_cached_at = time.time()
        return metadata


LATEST_STAGE_CONTAINER_MINIMUMS = {
    "gauntlets": 82,
    "collab_gauntlets": 28,
    "zero_legend": 34,
}
LATEST_TALENT_ORB_COUNT = 310


def _character_form_counts():
    return {
        int(character["id"]): int(character["form_count"])
        for character in get_character_metadata().get("characters", [])
    }


def _character_true_form_states():
    return {
        int(character["id"]): max(0, min(2, int(character.get("true_form_state", 2))))
        for character in get_character_metadata().get("characters", [])
    }


def _latest_talent_orb_ids():
    try:
        ids = [
            int(rank["id"])
            for group in get_legend_metadata().get("talent_orbs", [])
            for rank in group.get("ranks", [])
        ]
        if ids:
            return sorted(set(ids))
    except Exception as e:
        print(f"[WARN] talent orb metadata fallback: {e}")
    return list(range(LATEST_TALENT_ORB_COUNT))


def _ensure_gauntlet_map(container, map_id):
    """BCSFEのGauntletChaptersへ未実装時の空マップを追加する。"""
    if map_id < 0 or not getattr(container, "chapters", None):
        return False
    first = container.chapters[0]
    if not first.chapters:
        return False
    total_stars = len(first.chapters)
    total_stages = len(first.chapters[0].stages)
    while len(container.chapters) <= map_id:
        chapter = type(first).init(total_stages, total_stars)
        for star_chapter in chapter.chapters:
            star_chapter.total_stages = total_stages
        container.chapters.append(chapter)
        if hasattr(container, "unknown"):
            container.unknown.append(0)
    return True


def _ensure_standard_chapter_map(container, map_id):
    """Chapters系（ネコビタン）へ最新版で増えたマップ枠を非破壊で追加する。"""
    chapters = getattr(container, "chapters", None)
    if map_id < 0 or not chapters:
        return False
    first = chapters[0]
    if not first.chapters:
        return False
    total_stars = len(first.chapters)
    total_stages = len(first.chapters[0].stages)
    while len(chapters) <= map_id:
        chapter = type(first).init(total_stages, total_stars)
        for star_chapter in chapter.chapters:
            star_chapter.total_stages = total_stages
        chapters.append(chapter)
    return True


def _ensure_latest_stage_slots(save, metadata=None):
    """15.5.1の既存ステージ機能に必要な保存枠を進行状態を保って補完する。"""
    targets = dict(LATEST_STAGE_CONTAINER_MINIMUMS)
    if metadata:
        for family_key, attribute in (
            ("gauntlets", "gauntlets"),
            ("collab_gauntlets", "collab_gauntlets"),
            ("behemoth", "behemoth_culling"),
            ("enigma", "enigma_clears"),
        ):
            maps = metadata.get("event_stage_families", {}).get(family_key, {}).get("maps", [])
            if maps:
                targets[attribute] = max(targets.get(attribute, 0), max(item["id"] for item in maps) + 1)
        zero_maps = metadata.get("zero_legend", {}).get("maps", [])
        if zero_maps:
            targets["zero_legend"] = max(targets["zero_legend"], max(item["id"] for item in zero_maps) + 1)

    for attribute in ("gauntlets", "collab_gauntlets", "behemoth_culling", "enigma_clears"):
        target_length = targets.get(attribute, 0)
        container = getattr(save, attribute, None)
        if container is not None and target_length:
            _ensure_gauntlet_map(container, target_length - 1)
    if metadata:
        catamin_maps = metadata.get("event_stage_families", {}).get("catamin", {}).get("maps", [])
        if catamin_maps:
            previous_length = len(save.catamin_stages.chapters.chapters)
            _ensure_standard_chapter_map(
                save.catamin_stages.chapters,
                max(item["id"] for item in catamin_maps),
            )
            added = len(save.catamin_stages.chapters.chapters) - previous_length
            if added > 0:
                save.catamin_stages.unknown.extend([0] * added)
        catclaw_maps = metadata.get("event_stage_families", {}).get("catclaw", {}).get("maps", [])
        if catclaw_maps and getattr(save, "dojo_chapters", None) is not None:
            save.dojo_chapters.create(max(item["id"] for item in catclaw_maps))
    if targets.get("zero_legend"):
        _ensure_zero_map(save, targets["zero_legend"] - 1)


def ensure_latest_save_schema(save, target_version_number=None):
    """旧テンプレートを対象版へ補完し、新しい実機の版数は保持する。"""
    if not hasattr(save, "_schema_source_game_version_number"):
        save._schema_source_game_version_number = save.game_version.game_version
    if not hasattr(save, "_schema_target_game_version_number"):
        # 開始後に更新を検出しても、このジョブの通信・保存版数は途中で切り替えない。
        save._schema_target_game_version_number = max(
            save.game_version.game_version,
            target_version_number if target_version_number is not None else get_target_game_version_number(),
        )
    if save.game_version.game_version < save._schema_target_game_version_number:
        save.set_gv(core.GameVersion(save._schema_target_game_version_number))
    # BCSFE 3.6.0で15.5.0から追加された保存フィールド。
    if not hasattr(save, "ub39"):
        save.ub39 = False

    metadata = get_character_metadata()
    characters = metadata.get("characters", [])
    target_cat_count = max((int(character["id"]) for character in characters), default=len(save.cats.cats))
    while len(save.cats.cats) < target_cat_count:
        save.cats.cats.append(core.Cat.init(len(save.cats.cats)))

    legend_metadata = None
    try:
        legend_metadata = get_legend_metadata()
    except Exception as e:
        print(f"[WARN] latest stage schema fallback: {e}")
    _ensure_latest_stage_slots(save, legend_metadata)

    # 長さ付き保存配列は、最新の既存アイテム数までゼロで拡張する。
    item_groups = (legend_metadata or {}).get("vip_item_groups", {})
    for attribute, group_key in (("catfruit", "catfruit"), ("catseyes", "catseyes"), ("catamins", "catamins")):
        target = len(item_groups.get(group_key, []))
        values = getattr(save, attribute, None)
        if isinstance(values, list) and target > len(values):
            values.extend([0] * (target - len(values)))
    materials = getattr(getattr(getattr(save, "ototo", None), "base_materials", None), "materials", None)
    target_materials = len(item_groups.get("base_materials", []))
    if isinstance(materials, list) and materials and target_materials > len(materials):
        material_type = type(materials[0])
        materials.extend(material_type.init() for _ in range(target_materials - len(materials)))

    ticket_arrays = {
        1: ("event_capsules", "event_capsules_counter"),
        8: ("lucky_tickets",),
        10: ("event_capsules_2",),
    }
    for category, attributes in ticket_arrays.items():
        category_tickets = [
            ticket for ticket in (legend_metadata or {}).get("event_tickets", [])
            if int(ticket.get("category", -1)) == category
        ]
        required_length = max((int(ticket["index"]) + 1 for ticket in category_tickets), default=0)
        for attribute in attributes:
            values = getattr(save, attribute, None)
            if isinstance(values, list) and required_length > len(values):
                values.extend([0] * (required_length - len(values)))
    return save


def _character_definitions_older_than_save(save):
    """新しい実機の形態解放状態を、旧版の定義で消さないための判定。"""
    try:
        metadata_version = core.GameVersion.from_string(get_character_metadata()["data_version"]).game_version
        source_version = getattr(save, "_schema_source_game_version_number", save.game_version.game_version)
        return metadata_version < source_version
    except (KeyError, TypeError, ValueError):
        return True


def _set_cat_form(save, cat, form_count, requested_form=None, true_form_state=None):
    """実装済み形態数を越えず、実機の自然な形態フラグで設定する。"""
    total_forms = max(1, min(4, int(form_count)))
    form = max(1, min(int(requested_form or total_forms), total_forms))
    if not cat.unlocked:
        cat.unlock(save)

    # BCSFEのset_form()/true_form()は、2形態しかないキャラにも
    # unlocked_forms=1以上を付ける経路がある。これは実機セーブの
    # 「2形態キャラ=current_form 1 / unlocked_forms 0」と一致しないため、
    # 形態数と選択形態から3フィールドを必ず一組で検証する。
    old_current = max(0, int(cat.current_form))
    old_unlocked = max(0, int(cat.unlocked_forms))
    old_fourth = int(cat.fourth_form)
    cat.current_form = form - 1
    if _character_definitions_older_than_save(save):
        # 旧metadataで下位形態を選んでも、新版で獲得済みの進化権は保持する。
        if form >= 3:
            cat.unlocked_forms = max(old_unlocked, int(true_form_state if true_form_state is not None else 2))
        if form == 4:
            cat.fourth_form = 2
        return form
    if total_forms == 1:
        cat.unlocked_forms = 0
        cat.fourth_form = 0
    elif total_forms == 2:
        cat.unlocked_forms = 0
        cat.fourth_form = 0
    elif form == 4:
        cat.unlocked_forms = max(0, min(2, int(true_form_state if true_form_state is not None else 2)))
        cat.fourth_form = 2
    elif form == 3:
        cat.unlocked_forms = max(0, min(2, int(true_form_state if true_form_state is not None else 2)))
        cat.fourth_form = 0 if total_forms == 3 else max(0, min(old_fourth, 2))
    elif total_forms == 3:
        cat.unlocked_forms = min(old_unlocked, 3)
        cat.fourth_form = 0
    elif old_fourth == 2 or old_current == 3:
        cat.unlocked_forms = min(old_unlocked, 3)
        cat.fourth_form = 2
    else:
        cat.unlocked_forms = min(old_unlocked, 3)
        cat.fourth_form = max(0, min(old_fourth, 1))
    return form


def _set_cat_latest_form(save, cat, form_counts=None, true_form_states=None):
    counts = form_counts or _character_form_counts()
    total_forms = counts.get(cat.id + 1)
    if total_forms is None:
        # 配布metadataより新しいキャラを1形態と決めつけて壊さない。
        return None
    if _character_definitions_older_than_save(save):
        # 「最終形態」は定義より新しい既存形態を旧版へ戻す操作にしない。
        existing_form = max(int(cat.current_form) + 1, 4 if int(cat.fourth_form) == 2 else 1, 3 if int(cat.unlocked_forms) > 0 else 1)
        if existing_form > total_forms:
            return existing_form
    states = true_form_states or _character_true_form_states()
    return _set_cat_form(save, cat, total_forms, true_form_state=states.get(cat.id + 1, 2))


def _normalise_cat_form_flags(save, form_counts=None):
    """既存セーブに残った未実装形態・余分な進化権を安全に補正する。

    現在形態を可能な限り保ちつつ、実装形態数を越える値だけを縮める。
    第3/第4形態を既に解放済みで現在は下位形態を使用している状態は保持する。
    """
    if _character_definitions_older_than_save(save):
        return 0
    counts = form_counts or _character_form_counts()
    repaired = 0
    for cat in save.cats.cats:
        if cat.id + 1 not in counts:
            # 新版で追加された未知キャラは、metadataが追いつくまで現状を保つ。
            continue
        total_forms = max(1, min(4, int(counts[cat.id + 1])))
        before = (
            int(cat.current_form),
            int(cat.unlocked_forms),
            int(cat.fourth_form),
        )

        current_form = max(0, min(int(cat.current_form), total_forms - 1))
        unlocked_forms = max(0, int(cat.unlocked_forms))
        fourth_form = int(cat.fourth_form)

        if total_forms == 1:
            current_form = unlocked_forms = fourth_form = 0
        elif total_forms == 2:
            # 2形態キャラの第2形態は進化権を使用しない。
            unlocked_forms = 0
            fourth_form = 0
        elif total_forms == 3:
            # 第三形態のunlocked_formsは取得経路により0/1/2/3があり得る。
            # 推測で引き上げず、未実装の第4形態フラグだけを除去する。
            unlocked_forms = min(unlocked_forms, 3)
            fourth_form = 0
        else:
            # fourth_form=2 または実際に第4形態を使用中なら解放済み。
            has_fourth = fourth_form == 2 or current_form == 3
            if has_fourth:
                unlocked_forms = min(unlocked_forms, 3)
                fourth_form = 2
            else:
                current_form = min(current_form, 2)
                unlocked_forms = min(unlocked_forms, 3)
                fourth_form = max(0, min(fourth_form, 1))

        after = (current_form, unlocked_forms, fourth_form)
        if after != before:
            cat.current_form, cat.unlocked_forms, cat.fourth_form = after
            repaired += 1
    return repaired


def _talent_definitions_by_cat():
    """画面と適用処理が同じ最新JP本能定義を使うための索引。"""
    return {
        int(character["id"]): {
            int(talent["id"]): talent for talent in character.get("talents", [])
        }
        for character in get_character_metadata().get("characters", [])
        if character.get("talents")
    }


def _set_cat_talent_level(cat, ability_id, level):
    if cat.talents is None:
        cat.talents = []
    talent = next((item for item in cat.talents if item.id == ability_id), None)
    if talent is None:
        cat.talents.append(Talent(ability_id, level))
    else:
        talent.level = level


def _apply_character_talents(cat, display_id, setting, talent_definitions):
    """指定キャラの実装済み本能だけを設定。自然上限を越える値も保持する。"""
    definitions = talent_definitions.get(display_id, {})
    if not definitions:
        return 0
    action = setting.get("talent_action", "set")
    if action == "disable_all":
        if cat.talents is None:
            return 0
        for talent in cat.talents:
            talent.level = 0
        return len(cat.talents)
    levels = setting.get("talents", {})
    applied = 0
    for talent_id, requested_level in levels.items():
        ability_id = int(talent_id)
        if ability_id not in definitions:
            continue
        level = 0 if action == "disable_selected" else max(0, min(32767, int(requested_level)))
        _set_cat_talent_level(cat, ability_id, level)
        applied += 1
    return applied


BUNDLED_RANK_GIFT_THRESHOLDS_B64 = 'H4sIAAAAAAACAzWVW27kMAwEL5QPs/WwdZZg73+NdRWdACE0GnWTlouc3+e6fs77XxehCCEMwiQswibcBASFIiiCIiiCIiiCIiiCIiiCYqAYKAaKgWKYYy6sFtkIm3ATHsJ5w8B08O3AeXBk4jcvVphODBLCIGiKIhwenBscGRwZHBkcqX4WqsPxh0SkoPbRgaM8xaCc4aNQ00Q2cZuKsZxYTHwnRpP8E7tpxV4hlZRPi7Yslr2QKOSI9nyc5J3sTfJOvliXZbKi+IXVopaF36KWhenycXBZuCyqWlS1qGpR1aKqRVWLHIscixybHHv1PbwrEm1ybOw3zhuDjXZjv9HeKB4UN4obxY3iRnG/fz+3l4vsRnYje1Zj+K6QPcgeZA+yB8WD4kHxoDicO5w7nDucO6L6QvwCVY30u4n4ID6yfr430LSzFvpL6i+xv2Tykvmr35jUX2q7IXJ9uYhqu2W6Z7pfumG6Y2yZSmu7zVTZNmXflD1TNk2liz3nXZ6vIYlKR7eo0m4g26NWn9FGdEtsc/X++Xq674eoz+xe/2tEog4NqwCW8JW4laiVmNXqEdHZVQlTCVyJU4lSSVCJUMlQCVHtni9qhamkqcSphK9kqYSppKnEqaSNt1jvukeUDvfzvV72zze1iH/z7d2XsRKyeuZ3w+XEYUe35/5urLxbvtVZAkte09nFseSxBDL9LuSyGkxJLFGs02O052hPIIeoAEYAI6QRw4hhrn6b5xu8RLUN4KaSiGHEMKIaYYwwRhjT87sH+DfBzd4zvId4T/Ee4z3He5BLY6Qx0pjR41+tNEYaI4eRwzSHEhgJjARGAjP7t0OtBEYCI4GRwEhgJDDrm9eu+7b7h0etHEYOI4eRw8hh5DByGDmMHGb3r5ZaOYwcRg4jh5HDyGHkMHIYOUwTyEK5EEb8In4RvwDev/8f9enoeAcAAA=='


def _latest_rank_gift_thresholds():
    """最後に検証できたrankGift.csvの保存indexを保持して返す。"""
    _start_game_auto_updates()
    with _game_update_state_lock:
        return list(enumerate(_game_update_state["rank_gifts"]["thresholds"]))


def _set_all_user_rank_rewards(save, claimed):
    """現在到達済みUR報酬を一括変更し、未来報酬は未受取のままにする。"""
    thresholds = _latest_rank_gift_thresholds()
    required = max((index + 1 for index, _ in thresholds), default=0)
    while len(save.user_rank_rewards.rewards) < required:
        save.user_rank_rewards.rewards.append(Reward.init())
    user_rank = save.calculate_user_rank()
    original = [reward.claimed for reward in save.user_rank_rewards.rewards]
    if not claimed:
        for reward in save.user_rank_rewards.rewards:
            reward.claimed = False
    else:
        for index, threshold in thresholds:
            save.user_rank_rewards.rewards[index].claimed = threshold <= user_rank
    changed = sum(
        before != reward.claimed
        for before, reward in zip(original, save.user_rank_rewards.rewards)
    )
    return changed, sum(threshold <= user_rank for _, threshold in thresholds)


def _apply_user_rank_reward_actions(save, selected_items):
    """失敗を成功として隠さず、報酬状態と変更件数を返す。"""
    logs = []
    if "user_rank_rewards_claimed" in selected_items:
        changed, eligible = _set_all_user_rank_rewards(save, True)
        logs.append(f"UR報酬全受取({eligible}件・変更{changed}件)")
    if "user_rank_rewards_unclaimed" in selected_items:
        changed, _ = _set_all_user_rank_rewards(save, False)
        logs.append(f"UR報酬全未受取(変更{changed}件)")
    return logs


def _set_all_catguide_rewards(save, claimed):
    """受取化は所持キャラ、未受取化は全保存枠を対象にする。"""
    changed = 0
    targets = [cat for cat in save.cats.cats if cat.unlocked] if claimed else save.cats.cats
    for cat in targets:
        if cat.catguide_collected != bool(claimed):
            cat.catguide_collected = bool(claimed)
            changed += 1
    return changed, len(targets)


def _complete_all_defined_missions(save):
    """最新JP定義のメイン・スペシャル・週・月ミッションを全て達成状態へする。"""
    getter = _latest_jp_game_data_getter()
    data = getter.download("DataLocal", "Mission_Condition.csv")
    if data is None:
        raise RuntimeError("ミッション定義を取得できません")
    # 配信終了済みだがセーブに残っているミッションも取りこぼさない。
    seen = set(save.missions.clear_states)
    for mission_id in seen:
        save.missions.clear_states[mission_id] = 2
    for row in core.CSV(data).lines:
        if len(row) < 4:
            continue
        if not row[0].to_str().strip().lstrip("-").isdigit():
            continue
        mission_id = row[0].to_int()
        mission_type = row[1].to_int()
        seen.add(mission_id)
        save.missions.clear_states[mission_id] = 2
        save.missions.requirements[mission_id] = max(0, row[3].to_int())
        # この辞書は週次だけでなく、現在表示済みの期間ミッションIDも保持する。
        if mission_type in {1, 2, 3}:
            save.missions.weekly_missions[mission_id] = True
    return len(seen)


def validate_legend_stages_shape(data):
    """legend_stagesの構造と総選択数を軽量検証する。"""
    raw = data.get("legend_stages", {})
    if not isinstance(raw, dict) or any(key not in LEGEND_SERIES for key in raw):
        return False
    count = 0
    for maps in raw.values():
        if not isinstance(maps, dict) or len(maps) > 100:
            return False
        for stars in maps.values():
            if not isinstance(stars, dict) or len(stars) > 4:
                return False
            for stages in stars.values():
                if not isinstance(stages, list) or len(stages) > 12:
                    return False
                count += len(stages)
                if count > MAX_LEGEND_SELECTIONS:
                    return False
    return True


def validate_detailed_stage_settings_shape(data):
    """メイン・イベントのステージ別クリア回数のネストと総件数を検証する。"""
    total = 0
    main_raw = data.get("main_story_stages")
    if main_raw is not None:
        if not isinstance(main_raw, dict) or len(main_raw) > len(MAIN_STORY_REAL_INDEX):
            return False
        for stages in main_raw.values():
            if not isinstance(stages, dict) or len(stages) > 48:
                return False
            total += len(stages)

    event_raw = data.get("event_stage_settings")
    if event_raw is not None:
        if not isinstance(event_raw, dict) or any(key not in EVENT_STAGE_FAMILIES for key in event_raw):
            return False
        for family_key, maps in event_raw.items():
            if not isinstance(maps, dict) or len(maps) > 1000:
                return False
            for stars in maps.values():
                if not isinstance(stars, dict) or len(stars) > 4:
                    return False
                for stages in stars.values():
                    max_stages = 256 if family_key == "labyrinth" else 64
                    if not isinstance(stages, dict) or len(stages) > max_stages:
                        return False
                    total += len(stages)
    return total <= MAX_DETAILED_STAGE_SELECTIONS


def validate_labyrinth_character_settings_shape(data):
    """地底迷宮の封印・解除指定の型と件数だけを先に検証する。"""
    raw = data.get("labyrinth_characters")
    if raw is None:
        return True
    if not isinstance(raw, dict) or raw.get("action") not in LABYRINTH_CHARACTER_ACTIONS:
        return False
    ids = raw.get("ids", [])
    if not isinstance(ids, list) or len(ids) > MAX_LABYRINTH_CHARACTERS:
        return False
    try:
        return all(1 <= int(cat_id) <= 9999 for cat_id in ids)
    except (TypeError, ValueError):
        return False


def validate_lineup_settings_shape(data):
    """VIP編成設定の型を検証する。キャラ番号は画面と同じ1始まり、空枠はNone。"""
    raw = data.get("lineup_settings")
    if raw is None:
        return True
    if not isinstance(raw, dict):
        return False
    cats = raw.get("cats")
    forms = raw.get("forms")
    if not isinstance(cats, list) or len(cats) != 10 or not isinstance(forms, list) or len(forms) != 10:
        return False
    try:
        lineup_number = int(raw.get("lineup", 1))
        if not 1 <= lineup_number <= 20:
            return False
        return (
            all(cat_id is None or 1 <= int(cat_id) <= 9999 for cat_id in cats)
            and all(form is None or 1 <= int(form) <= 4 for form in forms)
        )
    except (TypeError, ValueError):
        return False


def validate_score_settings_shape(data):
    """VIP限定の道場・未来編スコア指定の型と件数を検証する。"""
    dojo = data.get("dojo_score_settings")
    if dojo is not None:
        if not isinstance(dojo, dict) or set(dojo) - {"hall_of_initiates"}:
            return False
        try:
            if not all(0 <= int(value) <= 2_147_483_647 for value in dojo.values()):
                return False
        except (TypeError, ValueError):
            return False

    future = data.get("future_score_settings")
    if future is not None:
        if not isinstance(future, dict) or len(future) > 3:
            return False
        try:
            if any(int(position) not in {3, 4, 5} for position in future):
                return False
            if not all(0 <= int(value) <= 9999 for value in future.values()):
                return False
        except (TypeError, ValueError):
            return False
    return True


def safe_dojo_score_settings(data):
    """BCSFEで保存可能な常設道場（入門の間）のスコアだけを返す。"""
    raw = data.get("dojo_score_settings")
    if not isinstance(raw, dict) or "hall_of_initiates" not in raw:
        return None
    try:
        score = max(0, min(2_147_483_647, int(raw["hall_of_initiates"])))
    except (TypeError, ValueError):
        return None
    return {"hall_of_initiates": score}


def safe_future_score_settings(data):
    """画面上の未来編1～3章（位置3～5）を0～9999へ正規化する。"""
    raw = data.get("future_score_settings")
    if not isinstance(raw, dict):
        return None
    output = {}
    for raw_position, raw_score in list(raw.items())[:3]:
        try:
            position = int(raw_position)
            score = max(0, min(9999, int(raw_score)))
        except (TypeError, ValueError):
            continue
        if position in {3, 4, 5}:
            output[position] = score
    return output or None


def safe_lineup_settings(data):
    """編成番号と10枠を、最新版キャラmetadataに照合して正規化する。"""
    raw = data.get("lineup_settings")
    if not isinstance(raw, dict):
        return None
    raw_cats = raw.get("cats", [])
    if isinstance(raw_cats, list) and not any(cat is not None for cat in raw_cats[:10]):
        try:
            lineup = max(1, min(20, int(raw.get("lineup", 1))))
        except (TypeError, ValueError):
            lineup = 1
        return {"lineup": lineup, "cats": [None] * 10, "forms": [None] * 10}
    try:
        characters = get_character_metadata().get("characters", [])
        form_counts = {
            int(character["id"]): int(character.get("form_count", 1))
            for character in characters if character.get("selectable", True)
        }
        valid_ids = set(form_counts)
    except Exception:
        valid_ids = set()
        form_counts = {}
    try:
        lineup_number = max(1, min(20, int(raw.get("lineup", 1))))
    except (TypeError, ValueError):
        lineup_number = 1
    cats = []
    for raw_id in raw.get("cats", [])[:10]:
        if raw_id is None:
            cats.append(None)
            continue
        try:
            cat_id = int(raw_id)
        except (TypeError, ValueError):
            cats.append(None)
            continue
        cats.append(cat_id if (not valid_ids or cat_id in valid_ids) else None)
    cats.extend([None] * (10 - len(cats)))
    forms = []
    for index, raw_form in enumerate(raw.get("forms", [])[:10]):
        cat_id = cats[index] if index < len(cats) else None
        if raw_form is None:
            forms.append(None)
            continue
        try:
            requested = int(raw_form)
        except (TypeError, ValueError):
            requested = 1
        forms.append(max(1, min(requested, form_counts.get(cat_id, 4))))
    forms.extend([None] * (10 - len(forms)))
    # 形態はキャラ単位の保存値。同じキャラで混在した場合は高い形態を優先する。
    final_forms = {}
    for cat_id, form in zip(cats, forms):
        if cat_id is not None and form is not None:
            final_forms[cat_id] = max(final_forms.get(cat_id, 1), form)
    forms = [final_forms.get(cat_id) for cat_id in cats]
    return {"lineup": lineup_number, "cats": cats, "forms": forms}


def safe_legend_stages(data):
    """章→星→ステージの選択をbcsfeメタデータに照合して正規化する。"""
    raw = data.get("legend_stages", {})
    if not isinstance(raw, dict) or not raw:
        return {}
    # A selected parent with every child cleared must stay selected/empty.
    if all(isinstance(value, dict) and not value for value in raw.values()):
        return {key: {} for key in raw if key in LEGEND_SERIES}
    try:
        metadata = get_legend_metadata()
    except Exception as e:
        print(f"[ERROR] legend metadata: {e}")
        return {}

    result = {}
    for series_key, raw_maps in raw.items():
        if series_key not in LEGEND_SERIES or not isinstance(raw_maps, dict):
            continue
        map_specs = {m["id"]: m for m in metadata.get(series_key, {}).get("maps", [])}
        clean_maps = {}
        for raw_map_id, raw_stars in raw_maps.items():
            try:
                map_id = int(raw_map_id)
            except (TypeError, ValueError):
                continue
            map_spec = map_specs.get(map_id)
            if map_spec is None or not isinstance(raw_stars, dict):
                continue
            clean_stars = {}
            for raw_star, raw_stages in raw_stars.items():
                try:
                    star = int(raw_star)
                except (TypeError, ValueError):
                    continue
                if not 0 <= star < map_spec["crowns"] or not isinstance(raw_stages, list):
                    continue
                stages = sorted({
                    int(stage) for stage in raw_stages
                    if str(stage).lstrip("-").isdigit() and 0 <= int(stage) < len(map_spec["stages"])
                })
                if stages:
                    clean_stars[star] = stages
            if clean_stars:
                clean_maps[map_id] = clean_stars
        # 空dictも「親は選択済みだが詳細は全解除」として保持する。
        result[series_key] = clean_maps
    return result


def full_legend_selection(series_key):
    """指定シリーズの実装済み章・冠・ステージをすべて選択した形にする。"""
    metadata = get_legend_metadata().get(series_key, {})
    maps = {}
    for map_spec in metadata.get("maps", []):
        maps[map_spec["id"]] = {
            star: list(range(len(map_spec["stages"])))
            for star in range(map_spec["crowns"])
        }
    return {series_key: maps}


def safe_vip_facilities(data):
    raw = data.get("vip_facilities", {})
    if not isinstance(raw, dict) or not raw:
        return {}
    try:
        specs = {item["id"]: item for item in get_legend_metadata().get("facilities", [])}
    except Exception as e:
        print(f"[ERROR] facility metadata: {e}")
        return {}
    result = {}
    for raw_id, raw_level in raw.items():
        try:
            facility_id, level = int(raw_id), int(raw_level)
        except (TypeError, ValueError):
            continue
        spec = specs.get(facility_id)
        if spec is not None:
            # VIPの「施設 指定」は通常の+上限を超える値も許可する。
            # 基礎Lvは適用時に正規上限へ収め、残りをすべて+値へ回す。
            result[facility_id] = max(1, level)
    return result


def safe_vip_talent_orbs(data):
    raw = data.get("vip_talent_orbs", {})
    if not isinstance(raw, dict) or not raw:
        return {}
    try:
        valid_ids = {
            rank["id"]
            for group in get_legend_metadata().get("talent_orbs", [])
            for rank in group.get("ranks", [])
        }
    except Exception as e:
        print(f"[ERROR] talent orb metadata: {e}")
        return {}
    result = {}
    for raw_id, raw_amount in raw.items():
        try:
            orb_id, amount = int(raw_id), int(raw_amount)
        except (TypeError, ValueError):
            continue
        if orb_id in valid_ids:
            result[orb_id] = max(0, min(amount, 998))
    return result


def apply_vip_facilities(save, facilities):
    logs = []
    if facilities is None:
        return logs
    try:
        specs = {item["id"]: item for item in get_legend_metadata().get("facilities", [])}
        valid_skills = save.special_skills.get_valid_skills()
        for facility_id, level in facilities.items():
            if facility_id >= len(valid_skills) or facility_id not in specs:
                continue
            spec = specs[facility_id]
            base_level = min(level, spec["max_base"])
            plus_level = max(level - spec["max_base"], 0)
            valid_skills[facility_id].upgrade.base = max(base_level - 1, 0)
            valid_skills[facility_id].upgrade.plus = plus_level
            # にゃんこ砲攻撃力にはセーブ内部のミラー枠がある。
            if facility_id == 0 and len(save.special_skills.skills) > 1:
                save.special_skills.skills[1].upgrade.base = max(base_level - 1, 0)
                save.special_skills.skills[1].upgrade.plus = plus_level
            logs.append(f"{spec['name']}(Lv.{level})")
    except Exception as e:
        print(f"[ERROR] vip facilities: {e}")
    return logs


def apply_vip_talent_orbs(save, talent_orbs):
    logs = []
    for orb_id, amount in (talent_orbs or {}).items():
        try:
            save.talent_orbs.set_orb(orb_id, amount)
            logs.append(f"本能玉ID{orb_id}({amount})")
        except Exception as e:
            print(f"[ERROR] vip talent orb {orb_id}: {e}")
    if logs:
        return [f"本能玉({len(logs)}種類)"]
    return []


def _ensure_zero_map(save, map_id):
    """古いテンプレートにも最新の零レジェンド章スロットを安全に追加する。"""
    chapters = save.zero_legends.chapters
    if not chapters:
        return False
    while len(chapters) <= map_id:
        chapters.append(copy.deepcopy(chapters[0]))
        for chapter in chapters[-1].chapters:
            chapter.selected_stage = 0
            chapter.clear_progress = 0
            chapter.unlock_state = 0
            if hasattr(chapter, "chapter_unlock_state"):
                chapter.chapter_unlock_state = 0
            for stage in chapter.stages:
                stage.clear_times = 0
    return True


def _sync_idi_clear(save):
    """古代研究所★4クリア時にイディ関連の取得状態を矛盾なく揃える。"""
    try:
        idi = save.cats.get_cat_by_id(568)
        if idi:
            idi.unlock(save)
        elif len(save.unit_drops) > 202:
            save.unit_drops[202] = 1
    except Exception as e:
        print(f"[ERROR] idi unlock: {e}")
    try:
        save.medals.add_medal(86)  # 【古代生命体撃破】
    except Exception as e:
        print(f"[ERROR] idi medal: {e}")
    try:
        # RE_026「太古の力」の報酬取得状態。配列があるバージョンだけ同期する。
        reward = save.item_reward_stages.sub_chapters[26].sub_chapters[0].stages[0]
        reward.claimed = True
    except (AttributeError, IndexError):
        pass


def apply_legend_stages(save, selections):
    """選択された章・冠・ステージだけをクリアする。"""
    logs = []
    for series_key, maps in (selections or {}).items():
        applied = 0
        for map_id, stars in maps.items():
            for star, stage_ids in stars.items():
                for stage_id in stage_ids:
                    try:
                        if series_key == "legend":
                            chapter = save.event_stages.chapters[0].chapters[map_id].chapters[star]
                            if stage_id >= len(chapter.stages):
                                continue
                            chapter.chapter_unlock_state = 3
                            chapter.clear_progress = max(chapter.clear_progress, stage_id + 1)
                            chapter.stages[stage_id].clear_stage(1, ensure_cleared_only=True)
                        elif series_key == "true_legend":
                            chapter = save.uncanny.chapters.chapters[map_id].chapters[star]
                            if stage_id >= len(chapter.stages):
                                continue
                            chapter.chapter_unlock_state = 3
                            chapter.clear_progress = max(chapter.clear_progress, stage_id + 1)
                            chapter.stages[stage_id].clear_stage(1, ensure_cleared_only=True)
                        elif series_key == "zero_legend":
                            if not _ensure_zero_map(save, map_id):
                                continue
                            chapter = save.zero_legends.chapters[map_id].chapters[star]
                            if stage_id >= len(chapter.stages):
                                continue
                            chapter.unlock_state = 3
                            chapter.chapter_unlock_state = 3
                            chapter.clear_progress = max(chapter.clear_progress, stage_id + 1)
                            chapter.stages[stage_id].clear_stage(1, ensure_cleared_only=True)
                        else:
                            continue
                        applied += 1
                    except (AttributeError, IndexError) as e:
                        print(f"[ERROR] {series_key}[{map_id}][{star}][{stage_id}]: {e}")

        if applied:
            label = LEGEND_SERIES[series_key]["label"]
            logs.append(f"{label}({applied}ステージ冠)")

    # レジェンド最終章「古代研究所」★4「太古の力」を選んだ時だけ同期する。
    idi_stages = selections.get("legend", {}).get(48, {}).get(3, []) if selections else []
    if 0 in idi_stages:
        _sync_idi_clear(save)
        logs.append("イディ取得・撃破済み")
    return logs


def safe_main_story_chapters(data):
    """main_story_chapters を検証済みの整数リスト(表示ポジション0〜8)として返す。不正・未指定なら None(=全章)。"""
    raw = data.get("main_story_chapters")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    try:
        indices = sorted({int(v) for v in raw if 0 <= int(v) <= 8})
    except (TypeError, ValueError):
        return None
    return indices if indices else None


def _safe_clear_count(value):
    try:
        return max(1, min(MAX_STAGE_CLEAR_COUNT, int(value)))
    except (TypeError, ValueError):
        return 1


def safe_main_story_stages(data):
    """章→ステージ→クリア回数。キーが存在する空辞書も親の全クリアを抑止する。"""
    if "main_story_stages" not in data:
        return None
    raw = data.get("main_story_stages")
    if not isinstance(raw, dict):
        return {}
    result = {}
    for raw_position, raw_stages in raw.items():
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        if not 0 <= position < len(MAIN_STORY_REAL_INDEX) or not isinstance(raw_stages, dict):
            continue
        stages = {}
        for raw_stage, raw_count in raw_stages.items():
            try:
                stage = int(raw_stage)
            except (TypeError, ValueError):
                continue
            if 0 <= stage < 48:
                stages[stage] = _safe_clear_count(raw_count)
        result[position] = stages
    return result


def safe_event_stage_settings(data):
    """イベント種別→マップ→冠→ステージ→クリア回数を安全な整数へ整形する。"""
    labyrinth_characters = safe_labyrinth_character_settings(data)
    if "event_stage_settings" not in data and labyrinth_characters is None:
        return None
    raw = data.get("event_stage_settings")
    if not isinstance(raw, dict):
        raw = {}
    result = {}
    for family_key, raw_maps in raw.items():
        if family_key not in EVENT_STAGE_FAMILIES or not isinstance(raw_maps, dict):
            continue
        maps = {}
        for raw_map, raw_stars in raw_maps.items():
            try:
                map_id = int(raw_map)
            except (TypeError, ValueError):
                continue
            if not 0 <= map_id < 1000 or not isinstance(raw_stars, dict):
                continue
            stars = {}
            for raw_star, raw_stages in raw_stars.items():
                try:
                    star = int(raw_star)
                except (TypeError, ValueError):
                    continue
                if not 0 <= star < 4 or not isinstance(raw_stages, dict):
                    continue
                stages = {}
                for raw_stage, raw_count in raw_stages.items():
                    try:
                        stage = int(raw_stage)
                    except (TypeError, ValueError):
                        continue
                    max_stages = 256 if family_key == "labyrinth" else 64
                    if 0 <= stage < max_stages:
                        stages[stage] = _safe_clear_count(raw_count)
                if stages:
                    stars[star] = stages
            if stars:
                maps[map_id] = stars
        result[family_key] = maps
    if labyrinth_characters is not None:
        result["_labyrinth_characters"] = labyrinth_characters
    return result


def safe_labyrinth_character_settings(data):
    """画面表示と同じ1始まりのキャラ番号を重複なしで正規化する。"""
    raw = data.get("labyrinth_characters")
    if not isinstance(raw, dict):
        return None
    action = raw.get("action")
    if action not in LABYRINTH_CHARACTER_ACTIONS:
        return None
    ids = []
    seen = set()
    for raw_id in raw.get("ids", [])[:MAX_LABYRINTH_CHARACTERS]:
        try:
            display_id = max(1, min(9999, int(raw_id)))
        except (TypeError, ValueError):
            continue
        if display_id in seen:
            continue
        seen.add(display_id)
        ids.append(display_id)
    if action.endswith("_selected") and not ids:
        return None
    return {"action": action, "ids": ids}


def safe_special_stages(data):
    """ゾンビ襲来・魔界編の詳細選択を検証する。空選択も親処理を抑止するため保持する。"""
    raw = data.get("special_stages")
    if not isinstance(raw, dict):
        return None
    result = {}
    if "zombie" in raw:
        zombie_raw = raw.get("zombie")
        zombie = {}
        if isinstance(zombie_raw, dict):
            for raw_chapter, raw_stages in zombie_raw.items():
                try:
                    chapter = int(raw_chapter)
                except (TypeError, ValueError):
                    continue
                if not 0 <= chapter < len(MAIN_STORY_REAL_INDEX) or not isinstance(raw_stages, list):
                    continue
                try:
                    zombie[chapter] = sorted({int(stage) for stage in raw_stages if 0 <= int(stage) < 48})
                except (TypeError, ValueError):
                    zombie[chapter] = []
        result["zombie"] = zombie
    if "aku" in raw:
        aku_raw = raw.get("aku")
        if isinstance(aku_raw, list):
            try:
                result["aku"] = sorted({int(stage) for stage in aku_raw if 0 <= int(stage) < 64})
            except (TypeError, ValueError):
                result["aku"] = []
        else:
            result["aku"] = []
    if "aku_ex" in raw:
        aku_ex_raw = raw.get("aku_ex")
        if isinstance(aku_ex_raw, list):
            try:
                result["aku_ex"] = sorted({int(stage) for stage in aku_ex_raw if int(stage) == 0})
            except (TypeError, ValueError):
                result["aku_ex"] = []
        else:
            result["aku_ex"] = []
    return result


def _sync_jagando_clear(save):
    """富士山EX（破壊神ジャガンドー）のクリア・報酬・キャラ取得を同期する。"""
    try:
        save.event_stages.clear_stage(
            1, 42, 0, 0, clear_amount=1, ensure_cleared_only=True
        )
    except (AttributeError, IndexError) as e:
        print(f"[ERROR] jagando stage: {e}")
    try:
        jagando_jr = save.cats.get_cat_by_id(622)
        if jagando_jr:
            jagando_jr.unlock(save)
    except Exception as e:
        print(f"[ERROR] jagando jr unlock: {e}")
    try:
        reward = save.item_reward_stages.sub_chapters[42].sub_chapters[0].stages[0]
        reward.claimed = True
    except (AttributeError, IndexError):
        pass


def apply_main_story_stages(save, selections):
    """選択されたメインステージだけを指定回数クリアし、お宝を最高にする。"""
    logs = []
    if selections is None:
        return logs
    applied = 0
    for position, stages in selections.items():
        if not 0 <= position < len(MAIN_STORY_REAL_INDEX):
            continue
        real_id = MAIN_STORY_REAL_INDEX[position]
        if real_id >= len(save.story.chapters):
            continue
        chapter = save.story.chapters[real_id]
        for stage_id, clear_count in stages.items():
            if stage_id >= min(48, len(chapter.stages)):
                continue
            try:
                chapter.clear_stage(stage_id, clear_count)
                chapter.stages[stage_id].treasure = 3
                applied += 1
            except (AttributeError, IndexError) as e:
                print(f"[ERROR] main_story_stages[{position}][{stage_id}]: {e}")
    if applied:
        logs.append(f"メイン詳細({applied}ステージ)")
    return logs


def apply_dojo_score_settings(save, settings):
    """常設道場の保存領域へスコアを設定する（BCSFEの道場編集と同じ0:0）。"""
    if not settings:
        return []
    try:
        score = int(settings["hall_of_initiates"])
        save.dojo.chapters.get_stage(0, 0).score = score
        return [f"道場・入門の間スコア({score})"]
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        print(f"[ERROR] dojo score: {e}")
        return []


def apply_future_score_settings(save, settings):
    """未来編の各章48ステージへ採点スコアを設定する。"""
    if not settings:
        return []
    logs = []
    for position, score in settings.items():
        if position not in {3, 4, 5}:
            continue
        real_id = MAIN_STORY_REAL_INDEX[position]
        try:
            chapter = save.story.chapters[real_id]
            stages = chapter.get_valid_treasure_stages()
            for stage in stages:
                stage.itf_timed_score = int(score)
            logs.append(f"{MAIN_STORY_CHAPTER_LABELS[position]}採点スコア({score}・{len(stages)}ステージ)")
        except (AttributeError, IndexError, TypeError, ValueError) as e:
            print(f"[ERROR] future score chapter {position}: {e}")
    return logs


def _labyrinth_value(save, names, default=None):
    """BCSFEが地底迷宮へ正式名を付けた後も、現行の未命名フィールドへ後方互換する。"""
    owners = [getattr(save, "labyrinth", None), save]
    for owner in owners:
        if owner is None:
            continue
        for name in names:
            if hasattr(owner, name):
                return getattr(owner, name)
    return default


def _set_labyrinth_value(save, names, value):
    owners = [getattr(save, "labyrinth", None), save]
    changed = False
    for owner in owners:
        if owner is None:
            continue
        for name in names:
            if hasattr(owner, name):
                setattr(owner, name, value.copy() if isinstance(value, list) else value)
                changed = True
    return changed


def _labyrinth_unlocked_cat_ids(save):
    return {
        int(cat.id) for cat in getattr(getattr(save, "cats", None), "cats", [])
        if bool(getattr(cat, "unlocked", False))
    }


def _sync_labyrinth_remaining(save, trapped=None, active=None):
    trapped = set(trapped if trapped is not None else _labyrinth_value(
        save, ("trapped_cats", "sealed_cats", "unavailable_cats", "ushl3"), []
    ))
    active = set(active if active is not None else _labyrinth_value(
        save, ("active_cats", "current_cats", "surviving_cats", "ushl4"), []
    ))
    unlocked = _labyrinth_unlocked_cat_ids(save)
    remaining = max(0, len(unlocked - trapped - active))
    _set_labyrinth_value(save, ("available_cat_count", "remaining_cat_count", "ush6"), remaining)
    _set_labyrinth_value(save, ("available_cat_count_2", "remaining_cat_count_2", "ush8"), remaining)
    return remaining


def _remove_labyrinth_lineup_cats(save, cat_ids):
    """地底迷宮専用編成（現行セーブではスロット20）から封印対象だけを外す。"""
    if not cat_ids:
        return
    try:
        slots = save.lineups.slots[20].slots
    except (AttributeError, IndexError):
        return
    encoded_ids = {cat_id + 2 for cat_id in cat_ids}
    for slot in slots:
        if getattr(slot, "cat_id", -1) in encoded_ids:
            slot.cat_id = -1


def apply_lineup_settings(save, settings):
    """通常編成1〜20の指定された10枠を書き換える。同一キャラの重複も保持する。"""
    if not settings:
        return []
    try:
        lineup_index = int(settings.get("lineup", 1)) - 1
        lineups = getattr(getattr(save, "lineups", None), "slots", [])
        # 現行セーブの21番目は地底迷宮専用。通常編成としては1〜20だけを扱う。
        if not 0 <= lineup_index < min(20, len(lineups)):
            return ["編成設定エラー(指定編成が存在しません)"]
        slots = lineups[lineup_index].slots
        cat_ids = list(settings.get("cats", []))[:10]
        cat_ids.extend([None] * (10 - len(cat_ids)))
        forms = list(settings.get("forms", []))[:10]
        forms.extend([None] * (10 - len(forms)))
        form_counts = _character_form_counts()
        true_form_states = _character_true_form_states()
        applied = 0
        for slot_index, display_id in enumerate(cat_ids):
            if slot_index >= len(slots):
                break
            if display_id is None:
                slots[slot_index].cat_id = -1
                continue
            display_id = int(display_id)
            cat = save.cats.get_cat_by_id(display_id - 1)
            if cat is None:
                slots[slot_index].cat_id = -1
                continue
            # 編成だけを設定する操作で外部ゲームデータ取得を発生させない。
            # 未解放キャラが選ばれた場合も編成が無効化されないよう必要フラグを立てる。
            cat.unlocked = 1
            cat.gatya_seen = 1
            if forms[slot_index] is not None:
                _set_cat_form(
                    save, cat, form_counts.get(display_id, 1), forms[slot_index],
                    true_form_states.get(display_id, 2),
                )
            # 編成内の保存値は、画面表示の1始まりキャラ番号に+1した値。
            slots[slot_index].cat_id = display_id + 1
            applied += 1
        save.lineups.selected_slot = lineup_index
        save.unlock_equip_menu()
        return [f"編成{lineup_index + 1}設定({applied}体・空枠{10 - applied})"]
    except Exception as e:
        print(f"[ERROR] lineup settings: {e}")
        return ["編成設定エラー"]


def apply_labyrinth_characters(save, settings):
    """地底迷宮の封印キャラを、最新キャラ数に追従しながら封印・解除する。"""
    if not settings:
        return []
    action = settings.get("action")
    if action not in LABYRINTH_CHARACTER_ACTIONS:
        return []

    trapped_names = ("trapped_cats", "sealed_cats", "unavailable_cats", "ushl3")
    active_names = ("active_cats", "current_cats", "surviving_cats", "ushl4")
    trapped = set(_labyrinth_value(save, trapped_names, []))
    active_order = list(dict.fromkeys(_labyrinth_value(save, active_names, [])))
    active = set(active_order)
    unlocked = _labyrinth_unlocked_cat_ids(save)
    selected = {
        display_id - 1 for display_id in settings.get("ids", [])
        if display_id >= 1 and display_id - 1 in unlocked
    }

    changed_ids = set()
    if action == "unlock_selected":
        changed_ids = trapped & selected
        trapped -= selected
    elif action == "unlock_all":
        changed_ids = set(trapped)
        trapped.clear()
    elif action == "seal_selected":
        changed_ids = selected - trapped
        trapped |= selected
        removed_active = active & selected
        if removed_active:
            active_order = [cat_id for cat_id in active_order if cat_id not in removed_active]
            active -= removed_active
            _remove_labyrinth_lineup_cats(save, removed_active)
    elif action == "seal_all":
        changed_ids = unlocked - trapped
        trapped = set(unlocked)
        _remove_labyrinth_lineup_cats(save, active)
        active_order = []
        active.clear()
    elif action == "release_lineup_all":
        changed_ids = set(active)
        _remove_labyrinth_lineup_cats(save, active)
        active_order = []
        active.clear()

    # 同じIDが封印中と現編成へ同時に入る状態は作らない。
    active_order = [cat_id for cat_id in active_order if cat_id not in trapped]
    active = set(active_order)
    trapped &= unlocked
    _set_labyrinth_value(save, trapped_names, sorted(trapped))
    _set_labyrinth_value(save, active_names, active_order)
    remaining = _sync_labyrinth_remaining(save, trapped, active)

    if hasattr(save, "ub25"):
        save.ub25 = True
    labels = {
        "unlock_selected": "指定封印解除",
        "unlock_all": "全封印解除",
        "seal_selected": "指定キャラ封印",
        "seal_all": "全キャラ封印",
        "release_lineup_all": "編成中キャラ全解除",
    }
    return [f"地底迷宮 {labels[action]}({len(changed_ids)}体・残り{remaining}体)"]


def apply_labyrinth_stages(save, maps):
    """選択された最深階までを連続クリアとして同期する。"""
    selected_floors = set()
    for map_id, stars in (maps or {}).items():
        if map_id != 0:
            continue
        for star, stages in stars.items():
            if star == 0:
                selected_floors.update(int(stage_id) for stage_id in stages)
    if not selected_floors:
        return []

    floor_order = _labyrinth_value(save, ("floor_order", "stage_order", "ushl2"), [])
    max_floors = len(floor_order) or 100
    requested = min(max_floors, max(selected_floors) + 1)
    current = int(_labyrinth_value(save, ("current_floor", "cleared_floor", "ush4"), 0) or 0)
    target = max(current, requested)

    stage_ids = list(getattr(save, "stage_ids_10s", []))
    known = set(stage_ids)
    for floor_index in range(target):
        stage_key = 33000000 + floor_index * 10
        if stage_key not in known:
            stage_ids.append(stage_key)
            known.add(stage_key)
    save.stage_ids_10s = stage_ids

    _set_labyrinth_value(save, ("current_floor", "cleared_floor", "ush4"), target)
    _set_labyrinth_value(save, ("highest_floor", "max_cleared_floor", "ush5"), target)
    _set_labyrinth_value(save, ("display_floor", "progress_floor", "ush7"), target)
    _set_labyrinth_value(save, ("remaining_floor_index", "uby10"), max(-128, min(127, max_floors - 1 - target)))
    if hasattr(save, "ui17"):
        save.ui17 = 33000
    if hasattr(save, "ub24"):
        save.ub24 = True
    if hasattr(save, "ub25"):
        save.ub25 = True
    remaining = _sync_labyrinth_remaining(save)
    return [f"地底迷宮 地底{target}層までクリア(残り{remaining}体)"]


def apply_event_stage_settings(save, selections):
    """イベント・塔・強襲・超獣などをステージ別の指定回数でクリアする。"""
    logs = []
    if selections is None:
        return logs
    labyrinth_characters = selections.get("_labyrinth_characters")
    for family_key, maps in selections.items():
        if family_key == "_labyrinth_characters":
            continue
        spec = EVENT_STAGE_FAMILIES.get(family_key)
        if not spec:
            continue
        if spec["kind"] == "labyrinth":
            logs.extend(apply_labyrinth_stages(save, maps))
            continue
        applied = 0
        for map_id, stars in maps.items():
            for star, stages in stars.items():
                for stage_id, clear_count in stages.items():
                    try:
                        if spec["kind"] == "event":
                            save.event_stages.clear_stage(
                                spec["group"], map_id, star, stage_id,
                                clear_amount=clear_count,
                            )
                        elif spec["kind"] == "tower":
                            save.tower.chapters.clear_stage(
                                map_id, star, stage_id, clear_count
                            )
                        elif spec["kind"] == "catamin_stages":
                            chapters = save.catamin_stages.chapters
                            _ensure_standard_chapter_map(chapters, map_id)
                            chapters.clear_stage(
                                map_id, star, stage_id, clear_count
                            )
                            # ネコビタンステージは通常のステージ別回数に加えて、
                            # マップ単位の表示回数も別dictへ保存される。
                            completion_key = spec["base_index"] + map_id
                            save.event_stages.chapter_completion_count[completion_key] = max(
                                clear_count,
                                save.event_stages.chapter_completion_count.get(completion_key, 0),
                            )
                        elif spec["kind"] == "dojo_chapters":
                            save.dojo_chapters.create(map_id)
                            chapter = save.dojo_chapters.chapters[map_id].chapters[star]
                            chapter.clear_progress = max(chapter.clear_progress, stage_id + 1)
                            chapter.stages[stage_id].clear_times = clear_count
                            chapter.unlock_state = 3
                        else:
                            container = getattr(save, spec["kind"])
                            _ensure_gauntlet_map(container, map_id)
                            container.clear_stage(map_id, star, stage_id, clear_count)
                        applied += 1
                    except (AttributeError, IndexError, TypeError) as e:
                        print(f"[ERROR] event_stage_settings[{family_key}][{map_id}][{star}][{stage_id}]: {e}")
        if applied:
            logs.append(f"{spec['label']}({applied}ステージ)")
    logs.extend(apply_labyrinth_characters(save, labyrinth_characters))
    return logs


def apply_special_stages(save, selections):
    """選択されたゾンビ襲来・魔界編ステージだけをクリアする。"""
    logs = []
    if selections is None:
        return logs

    if "zombie" in selections:
        applied = 0
        zombie = selections.get("zombie", {})
        for position, stage_ids in zombie.items():
            if not 0 <= position < len(MAIN_STORY_REAL_INDEX):
                continue
            chapter_key = MAIN_STORY_REAL_INDEX[position]
            if chapter_key not in save.outbreaks.chapters:
                save.outbreaks.chapters[chapter_key] = ZombieChapter(chapter_key, {})
            chapter = save.outbreaks.chapters[chapter_key]
            current_chapter = save.outbreaks.current_outbreaks.get(chapter_key)
            for stage_id in stage_ids:
                chapter.outbreaks[stage_id] = ZombieOutbreak(True)
                if current_chapter is not None:
                    current_chapter.outbreaks.pop(stage_id, None)
                applied += 1
            if current_chapter is not None and not current_chapter.outbreaks:
                save.outbreaks.current_outbreaks.pop(chapter_key, None)
        if applied:
            logs.append(f"ゾンビ({applied}ステージ)")

    if "aku" in selections:
        applied_ids = set()
        stage_ids = selections.get("aku", [])
        if hasattr(save, "aku") and save.aku and save.aku.chapters:
            for chapters_stars in save.aku.chapters:
                for chapter in chapters_stars.chapters:
                    valid_ids = [stage_id for stage_id in stage_ids if stage_id < len(chapter.stages)]
                    for stage_id in valid_ids:
                        chapter.stages[stage_id].clear_times = 1
                        applied_ids.add(stage_id)
                    if valid_ids:
                        chapter.current_stage = max(chapter.current_stage, max(valid_ids))
        if applied_ids:
            logs.append(f"魔界編({len(applied_ids)}ステージ)")
    if "aku_ex" in selections and 0 in selections.get("aku_ex", []):
        _sync_jagando_clear(save)
        logs.append("富士山EX(破壊神ジャガンドー)")
    return logs


def apply_ototo_settings(save, ototo_settings):
    """個別指定された城だけを更新する。part 0はセーブ上だけ0始まり。"""
    if not ototo_settings:
        return []
    applied = []
    name_by_id = {
        int(item["id"]): item.get("name", f"城強化 #{item['id']}")
        for item in get_ototo_metadata().get("cannons", [])
    }
    for raw_cannon_id, setting in ototo_settings.items():
        try:
            cannon_id = int(raw_cannon_id)
            cannon = save.ototo.cannons.cannons.get(cannon_id)
            if cannon is None:
                cannon = core.game.gamoto.ototo.Cannon(0, [])
                save.ototo.cannons.cannons[cannon_id] = cannon
            required_size = 1 if cannon_id == 0 else 3
            levels = list(cannon.levels)
            while len(levels) < required_size:
                levels.append(0)
            changed_parts = []
            for raw_part_id, raw_level in setting.get("levels", {}).items():
                part_id = int(raw_part_id)
                display_level = int(raw_level)
                if not (0 <= part_id < required_size):
                    continue
                levels[part_id] = display_level - 1 if part_id == 0 else display_level
                changed_parts.append(display_level)
            if not changed_parts:
                continue
            cannon.levels = levels[:required_size]
            if cannon_id != 0:
                cannon.development = 3
            applied.append(f"{name_by_id.get(cannon_id, f'城強化 #{cannon_id}')}個別設定")
        except Exception as e:
            print(f"[ERROR] ototo detail {raw_cannon_id}: {e}")
    return applied


def apply_daiko_segment(save, selected_items: list, char_list: list, custom_amounts: dict, custom_playtime: str = "", main_story_chapters=None, character_settings=None) -> list:
    applied_logs = []
    save.show_ban_message = False

    def amt(key):
        return int(custom_amounts.get(key, 0))

    if "catfood" in selected_items:
        save.catfood = 58000; applied_logs.append("猫缶(58000)")
    elif "custom_catfood" in selected_items:
        save.catfood = amt("custom_catfood"); applied_logs.append(f"猫缶({amt('custom_catfood')})")

    if "xp" in selected_items:
        save.xp = 99999999; applied_logs.append("XP(99999999)")
    elif "custom_xp" in selected_items:
        save.xp = amt("custom_xp"); applied_logs.append(f"XP({amt('custom_xp')})")

    if "np" in selected_items:
        save.np = 9999; applied_logs.append("NP(9999)")
    elif "custom_np" in selected_items:
        save.np = amt("custom_np"); applied_logs.append(f"NP({amt('custom_np')})")

    if "normal_tickets" in selected_items:
        save.normal_tickets = 999; applied_logs.append("銀チケ(999)")
    elif "custom_normal_tickets" in selected_items:
        save.normal_tickets = amt("custom_normal_tickets"); applied_logs.append(f"銀チケ({amt('custom_normal_tickets')})")

    if "rare_tickets" in selected_items:
        save.rare_tickets = 999; applied_logs.append("金チケ(999)")
    elif "custom_rare_tickets" in selected_items:
        save.rare_tickets = amt("custom_rare_tickets"); applied_logs.append(f"金チケ({amt('custom_rare_tickets')})")

    if "platinum_tickets" in selected_items:
        save.platinum_tickets = 20; applied_logs.append("プラチナ(20)")
    elif "custom_platinum_tickets" in selected_items:
        save.platinum_tickets = amt("custom_platinum_tickets"); applied_logs.append(f"プラチナ({amt('custom_platinum_tickets')})")

    if "legend_tickets" in selected_items:
        save.legend_tickets = 10; applied_logs.append("レジェンドチケ(10)")
    elif "custom_legend_tickets" in selected_items:
        save.legend_tickets = amt("custom_legend_tickets"); applied_logs.append(f"レジェンドチケ({amt('custom_legend_tickets')})")

    if "leadership" in selected_items:
        save.leadership = 999; applied_logs.append("リーダーシップ(999)")
    elif "custom_leadership" in selected_items:
        save.leadership = amt("custom_leadership"); applied_logs.append(f"リーダーシップ({amt('custom_leadership')})")

    if "catseyes" in selected_items:
        try: save.catseyes = [999] * len(save.catseyes); applied_logs.append("キャッツアイ(999)")
        except Exception as e: print(f"[ERROR] catseyes: {e}")
    elif "custom_catseyes" in selected_items:
        try: save.catseyes = [amt("custom_catseyes")] * len(save.catseyes); applied_logs.append(f"キャッツアイ({amt('custom_catseyes')})")
        except Exception as e: print(f"[ERROR] custom_catseyes: {e}")

    if "catamins" in selected_items:
        try: save.catamins = [999] * len(save.catamins); applied_logs.append("ネコビタン(999)")
        except Exception as e: print(f"[ERROR] catamins: {e}")
    elif "custom_catamins" in selected_items:
        try: save.catamins = [amt("custom_catamins")] * len(save.catamins); applied_logs.append(f"ネコビタン({amt('custom_catamins')})")
        except Exception as e: print(f"[ERROR] custom_catamins: {e}")

    if "base_materials" in selected_items:
        try:
            if hasattr(save, 'ototo') and hasattr(save.ototo, 'base_materials'):
                for material in save.ototo.base_materials.materials: material.amount = 999
                applied_logs.append("城素材(999)")
        except Exception as e: print(f"[ERROR] base_materials: {e}")
    elif "custom_base_materials" in selected_items:
        try:
            if hasattr(save, 'ototo') and hasattr(save.ototo, 'base_materials'):
                for material in save.ototo.base_materials.materials: material.amount = amt("custom_base_materials")
                applied_logs.append(f"城素材({amt('custom_base_materials')})")
        except Exception as e: print(f"[ERROR] custom_base_materials: {e}")

    if "matatabi" in selected_items:
        try: save.catfruit = [998] * len(save.catfruit); applied_logs.append("マタタビ(998)")
        except Exception as e: print(f"[ERROR] matatabi: {e}")
    elif "custom_matatabi" in selected_items:
        try: save.catfruit = [amt("custom_matatabi")] * len(save.catfruit); applied_logs.append(f"マタタビ({amt('custom_matatabi')})")
        except Exception as e: print(f"[ERROR] custom_matatabi: {e}")

    if "talent_orbs" in selected_items:
        try:
            orb_ids = _latest_talent_orb_ids()
            for orb_id in orb_ids:
                save.talent_orbs.set_orb(orb_id, 998)
            applied_logs.append(f"本能玉({len(orb_ids)}種類・998)")
        except Exception as e: print(f"[ERROR] talent_orbs: {e}")
    elif "custom_talent_orbs" in selected_items:
        try:
            orb_ids = _latest_talent_orb_ids()
            for orb_id in orb_ids:
                save.talent_orbs.set_orb(orb_id, amt("custom_talent_orbs"))
            applied_logs.append(f"本能玉({len(orb_ids)}種類・{amt('custom_talent_orbs')})")
        except Exception as e: print(f"[ERROR] custom_talent_orbs: {e}")

    if "battle_items" in selected_items:
        try:
            for b_item in save.battle_items.items: b_item.amount = 9999
            applied_logs.append("バトルアイテム(9999)")
        except Exception as e: print(f"[ERROR] battle_items: {e}")
    elif "custom_battle_items" in selected_items:
        try:
            for b_item in save.battle_items.items: b_item.amount = amt("custom_battle_items")
            applied_logs.append(f"バトルアイテム({amt('custom_battle_items')})")
        except Exception as e: print(f"[ERROR] custom_battle_items: {e}")

    if "main_story_clear" in selected_items:
        try:
            chapters = save.story.chapters
            if main_story_chapters is None:
                target_indices = list(range(len(chapters)))
                selected_positions = list(range(len(MAIN_STORY_CHAPTER_LABELS)))
            else:
                selected_positions = [p for p in main_story_chapters if p < len(MAIN_STORY_REAL_INDEX)]
                target_indices = [MAIN_STORY_REAL_INDEX[p] for p in selected_positions if MAIN_STORY_REAL_INDEX[p] < len(chapters)]
            for idx in target_indices:
                chapter = chapters[idx]
                chapter.clear_chapter()
                for i in range(len(chapter.stages)):
                    if i < 48: chapter.stages[i].treasure = 3
            if main_story_chapters is None or len(selected_positions) == len(MAIN_STORY_CHAPTER_LABELS):
                applied_logs.append("メインクリア")
            else:
                names = [MAIN_STORY_CHAPTER_LABELS[p] for p in selected_positions if p < len(MAIN_STORY_CHAPTER_LABELS)]
                applied_logs.append(f"メインクリア({'/'.join(names)})")
        except Exception as e: print(f"[ERROR] main_story_clear: {e}")

    if "zombie_clear" in selected_items:
        try:
            for chapter_key in [0, 1, 2, 4, 5, 6, 7, 8, 9]:
                if chapter_key not in save.outbreaks.chapters:
                    save.outbreaks.chapters[chapter_key] = ZombieChapter(chapter_key, {})
                chapter = save.outbreaks.chapters[chapter_key]
                for stage_id in range(48):
                    chapter.outbreaks[stage_id] = ZombieOutbreak(True)
            save.outbreaks.current_outbreaks = {}
            applied_logs.append("ゾンビ")
        except Exception as e:
            print(f"[ERROR] zombie_clear: {e}")

    if "aku_clear" in selected_items:
        try:
            if hasattr(save, 'aku') and save.aku and save.aku.chapters:
                for chapters_stars in save.aku.chapters:
                    for chapter in chapters_stars.chapters:
                        if chapter.stages and len(chapter.stages) > 0:
                            chapter.current_stage = len(chapter.stages) - 1
                            for stage in chapter.stages:
                                stage.clear_times = 1
                _sync_jagando_clear(save)
            applied_logs.append("魔界編")
        except Exception as e: print(f"[ERROR] aku_clear: {e}")

    if "legend_clear" in selected_items:
        try: applied_logs.extend(apply_legend_stages(save, full_legend_selection("legend")))
        except Exception as e: print(f"[ERROR] legend_clear: {e}")

    if "true_legend_clear" in selected_items:
        try:
            applied_logs.extend(apply_legend_stages(save, full_legend_selection("true_legend")))
        except Exception as e: print(f"[ERROR] true_legend_clear: {e}")

    if "zero_legend_clear" in selected_items:
        try:
            applied_logs.extend(apply_legend_stages(save, full_legend_selection("zero_legend")))
        except Exception as e: print(f"[ERROR] zero_legend_clear: {e}")

    if "event_clear" in selected_items:
        try:
            for group_id in range(len(save.event_stages.chapters)): save.event_stages.clear_group(group_id)
            applied_logs.append("イベント")
        except Exception as e: print(f"[ERROR] event_clear: {e}")

    if "gamatoto_max" in selected_items:
        try:
            g_levels = core.core_data.get_gamatoto_levels(save)
            save.gamatoto.xp = g_levels.get_xp_from_level(g_levels.get_max_level())
            applied_logs.append("ガマトト")
        except Exception as e: print(f"[ERROR] gamatoto_max: {e}")

    if "gamatoto_helpers" in selected_items:
        try:
            new_helpers = []
            for i in range(129, 139): new_helpers.append(core.game.gamoto.gamatoto.Helper(i))
            save.gamatoto.helpers.helpers = new_helpers
            applied_logs.append("助手")
        except Exception as e: print(f"[ERROR] gamatoto_helpers: {e}")

    if "ototo_max" in selected_items:
        try:
            for cannon_id, cannon in save.ototo.cannons.cannons.items():
                cannon.development = 3
                if cannon_id == 0: cannon.levels = [29]
                else: cannon.levels = [29, 20, 20]
            applied_logs.append("オトート")
        except Exception as e: print(f"[ERROR] ototo_max: {e}")

    if "cat_shrine_max" in selected_items:
        try:
            save.cat_shrine.xp_offering = 100000000
            applied_logs.append("神社")
        except Exception as e: print(f"[ERROR] cat_shrine_max: {e}")

    if "gold_membership" in selected_items:
        try:
            save.officer_pass.gold_pass.get_gold_pass(114514, 365, save)
            applied_logs.append("ゴールド")
        except Exception as e: print(f"[ERROR] gold_membership: {e}")

    if "slots_max" in selected_items:
        try: save.lineups.unlocked_slots = 19; applied_logs.append("スロット")
        except Exception as e: print(f"[ERROR] slots_max: {e}")

    if "medals_all" in selected_items:
        try:
            medal_names = core.core_data.get_medal_names(save)
            if medal_names.medal_names:
                for i in range(len(medal_names.medal_names)): save.medals.add_medal(i)
            applied_logs.append("メダル")
        except Exception as e: print(f"[ERROR] medals_all: {e}")

    if "skip_tutorial" in selected_items:
        try: save.story.clear_tutorial(save); applied_logs.append("チュートリアル")
        except Exception as e: print(f"[ERROR] skip_tutorial: {e}")

    if "hide_rank_up" in selected_items:
        try:
            save.unlock_popups_11 = [1] * 3
            disable_rank_up_sale(save)
            applied_logs.append("ポップアップ非表示")
        except Exception as e: print(f"[ERROR] hide_rank_up: {e}")

    applied_logs.extend(_apply_user_rank_reward_actions(save, selected_items))

    if "catguide_rewards_claimed" in selected_items:
        try:
            changed, targets = _set_all_catguide_rewards(save, True)
            applied_logs.append(f"猫図鑑報酬全受取({targets}体・変更{changed}体)")
        except Exception as e:
            print(f"[ERROR] catguide_rewards_claimed: {e}")

    if "catguide_rewards_unclaimed" in selected_items:
        try:
            changed, targets = _set_all_catguide_rewards(save, False)
            applied_logs.append(f"猫図鑑報酬全未受取(変更{changed}体)")
        except Exception as e:
            print(f"[ERROR] catguide_rewards_unclaimed: {e}")

    if "cat_scratcher_reset" in selected_items:
        try:
            # 実機の未実施データでも開催日(start_times)は残る。
            # BCSFE reset()はそれまで消すため、結果と完了フラグだけ戻す。
            save.cat_scratcher.completed = {}
            save.cat_scratcher.values = {}
            applied_logs.append("スクラッチ未実施化")
        except Exception as e:
            print(f"[ERROR] cat_scratcher_reset: {e}")

    if "unlock_enemy_guide" in selected_items:
        try:
            enemy_dict = core.game.battle.enemy.EnemyDictionary(save)
            valid_enemies = enemy_dict.get_valid_enemies()
            if valid_enemies:
                for enemy_id in valid_enemies:
                    save.enemy_guide[enemy_id] = 1
                applied_logs.append("敵図鑑全解放")
        except Exception as e: print(f"[ERROR] unlock_enemy_guide: {e}")

    if "add_playtime_720h" in selected_items:
        try:
            current_play_time = PlayTime(save.officer_pass.play_time)
            added_time = PlayTime.from_hours(720)
            new_play_time = current_play_time + added_time
            save.officer_pass.play_time = new_play_time.frames
            applied_logs.append("プレイ時間+720h")
        except Exception as e: print(f"[ERROR] add_playtime_720h: {e}")
    elif "custom_playtime" in selected_items:
        try:
            parsed = validate_playtime_str(str(custom_playtime))
            if parsed:
                hours, minutes = parsed
                save.officer_pass.play_time = PlayTime.from_hours_mins_secs(hours, minutes, 0).frames
                applied_logs.append(f"プレイ時間({hours}:{minutes:02d})")
            else:
                print("[ERROR] custom_playtime: 不正な形式のためスキップ")
        except Exception as e: print(f"[ERROR] custom_playtime: {e}")

    if "all_missions_clear" in selected_items:
        try:
            completed = _complete_all_defined_missions(save)
            applied_logs.append(f"全ミッションクリア({completed}件・全種類)")
        except Exception as e:
            print(f"[ERROR] all_missions_clear: {e}")
            traceback.print_exc()

    if "base_upgrades_max" in selected_items:
        try:
            for i in range(len(save.special_skills.skills)):
                if i == 2: save.special_skills.skills[i].upgrade.base = 9; save.special_skills.skills[i].upgrade.plus = 0
                else: save.special_skills.skills[i].upgrade.base = 19; save.special_skills.skills[i].upgrade.plus = 10
            applied_logs.append("全施設Max")
        except Exception as e: print(f"[ERROR] base_upgrades_max: {e}")
    elif "custom_base_upgrades" in selected_items:
        try:
            v = amt("custom_base_upgrades")
            for i in range(len(save.special_skills.skills)):
                if i == 2:
                    save.special_skills.skills[i].upgrade.base = max(v - 1, 0)
                    save.special_skills.skills[i].upgrade.plus = 0
                else:
                    base_level = min(max(v - 1, 0), 19)
                    plus_level = max(v - (base_level + 1), 0)
                    save.special_skills.skills[i].upgrade.base = base_level
                    save.special_skills.skills[i].upgrade.plus = plus_level
            applied_logs.append(f"全施設({v})")
        except Exception as e: print(f"[ERROR] custom_base_upgrades: {e}")

    if "unlock_all_cats" in selected_items:
        try:
            selectable_ids = {character["id"] for character in get_character_metadata()["characters"] if character.get("selectable", True)}
            for cat in save.cats.cats:
                if cat.id + 1 in selectable_ids:
                    cat.unlock(save)
            applied_logs.append("全解放")
        except Exception as e: print(f"[ERROR] unlock_all_cats: {e}")

    if char_list:
        char_applied = []
        for char_val in char_list:
            try:
                cat_id = int(char_val) - 1
                cat = save.cats.get_cat_by_id(cat_id)
                if not cat: continue
                if "unlock_specific" in selected_items: cat.unlock(save); char_applied.append(f"解放({char_val})")
                if "remove_specific" in selected_items: cat.remove(reset=True, save_file=save); char_applied.append(f"削除({char_val})")
            except: pass
        if char_applied: applied_logs.extend(char_applied)

    # 名前選択UIの指定キャラ。従来の1始まりID入力とは独立して併用できる。
    if character_settings:
        char_applied = []
        form_counts = _character_form_counts()
        true_form_states = _character_true_form_states()
        talent_definitions = _talent_definitions_by_cat() if "talents_specific" in selected_items else {}
        for setting in character_settings:
            try:
                display_id = int(setting["id"])
                cat = save.cats.get_cat_by_id(display_id - 1)
                if not cat:
                    continue
                if "unlock_specific" in selected_items:
                    cat.unlock(save)
                    char_applied.append(f"解放({display_id})")
                if "remove_specific" in selected_items:
                    cat.remove(reset=True, save_file=save)
                    char_applied.append(f"削除({display_id})")
                    continue
                if "level_specific" in selected_items:
                    cat.unlock(save)
                    if setting.get("level_mode") == "random":
                        base_min = int(setting.get("base_level_min", 1))
                        base_max = int(setting.get("base_level_max", 60))
                        plus_min = int(setting.get("plus_level_min", 0))
                        plus_max = int(setting.get("plus_level_max", 90))
                        base_level = base_min + secrets.randbelow(base_max - base_min + 1)
                        plus_level = plus_min + secrets.randbelow(plus_max - plus_min + 1)
                    else:
                        base_level = int(setting["base_level"])
                        plus_level = int(setting["plus_level"])
                    cat.upgrade.base = max(0, base_level - 1)
                    cat.upgrade.plus = max(0, plus_level)
                    char_applied.append(
                        f"Lv.{base_level}+{plus_level}({display_id})"
                    )
                if "form_specific" in selected_items:
                    form = _set_cat_form(
                        save,
                        cat,
                        form_counts.get(display_id, 1),
                        int(setting["form"]),
                        true_form_states.get(display_id, 2),
                    )
                    char_applied.append(f"第{form}形態({display_id})")
                if "max_specific" in selected_items:
                    cat.unlock(save)
                    cat.upgrade.base = 59
                    cat.upgrade.plus = 90
                    char_applied.append(f"Lv.Max+({display_id})")
                if "max_specific_2" in selected_items:
                    cat.unlock(save)
                    cat.upgrade.base = 59
                    char_applied.append(f"Lv.Max({display_id})")
                if "true_form_specific" in selected_items:
                    form = _set_cat_latest_form(save, cat, form_counts, true_form_states)
                    if form is None:
                        char_applied.append(f"最終形態スキップ・形態定義未取得({display_id})")
                    else:
                        char_applied.append(f"最終形態・第{form}形態({display_id})")
                if "talents_specific" in selected_items:
                    if not cat.unlocked:
                        char_applied.append(f"本能スキップ・未解放({display_id})")
                    else:
                        applied = _apply_character_talents(
                            cat, display_id, setting, talent_definitions
                        )
                        if applied:
                            action_label = {
                                "set": "本能設定",
                                "disable_selected": "選択本能無効",
                                "disable_all": "全本能無効",
                            }.get(setting.get("talent_action"), "本能設定")
                            char_applied.append(f"{action_label}({display_id}: {applied}個)")
            except Exception as e:
                print(f"[ERROR] character_settings: {e}")
        if char_applied:
            applied_logs.extend(char_applied)

    if "max_all_cats_plus" in selected_items:
        try:
            for cat in save.cats.cats:
                if cat.unlocked:
                    cat.upgrade.base = 59; cat.upgrade.plus = 90
            applied_logs.append("全Lv.Max+")
        except Exception as e: print(f"[ERROR] max_all_cats_plus: {e}")

    if "max_all_cats" in selected_items:
        try:
            for cat in save.cats.cats:
                if cat.unlocked:
                    cat.upgrade.base = 59
            applied_logs.append("全Lv.Max")
        except Exception as e: print(f"[ERROR] max_all_cats: {e}")

    if "true_form_all" in selected_items:
        try:
            unlocked_cats = [cat for cat in save.cats.cats if cat.unlocked]
            form_counts = _character_form_counts()
            true_form_states = _character_true_form_states()
            for cat in unlocked_cats:
                _set_cat_latest_form(save, cat, form_counts, true_form_states)
            skipped = sum(1 for cat in unlocked_cats if cat.id + 1 not in form_counts)
            applied_logs.append(
                f"全最大形態(形態定義未取得{skipped}体を除外)" if skipped else "全最大形態"
            )
        except Exception as e: print(f"[ERROR] true_form_all: {e}")

    if "talents_all" in selected_items:
        try:
            definitions = _talent_definitions_by_cat()
            for cat in save.cats.cats:
                if cat.unlocked:
                    for ability_id, talent in definitions.get(cat.id + 1, {}).items():
                        _set_cat_talent_level(cat, ability_id, talent["max_level"])
            applied_logs.append("全本能")
        except Exception as e:
            print(f"[ERROR] talents_all: {e}")

    if "talents_disable_all" in selected_items:
        try:
            disabled = 0
            for cat in save.cats.cats:
                if cat.talents is None:
                    continue
                for talent in cat.talents:
                    if talent.level:
                        talent.level = 0
                        disabled += 1
            applied_logs.append(f"全キャラ本能無効({disabled}個)")
        except Exception as e:
            print(f"[ERROR] talents_disable_all: {e}")

    if char_list:
        char_applied = []
        for char_val in char_list:
            try:
                cat_id = int(char_val) - 1
                cat = save.cats.get_cat_by_id(cat_id)
                if not cat or not cat.unlocked: continue
                if "max_specific" in selected_items: cat.upgrade.base = 59; cat.upgrade.plus = 90; char_applied.append(f"Lv.Max+({char_val})")
                if "max_specific_2" in selected_items: cat.upgrade.base = 59; char_applied.append(f"Lv.Max({char_val})")
                if "true_form_specific" in selected_items:
                    form = _set_cat_latest_form(save, cat)
                    if form is None:
                        char_applied.append(f"最終形態スキップ・形態定義未取得({char_val})")
                    else:
                        char_applied.append(f"最終形態・第{form}形態({char_val})")
            except: pass
        if char_applied: applied_logs.extend(char_applied)

    if "remove_error_cats" in selected_items:
        try:
            removed_count = 0
            for err_id in ERROR_CAT_IDS:
                cat = save.cats.get_cat_by_id(err_id - 1)
                if cat: cat.remove(reset=True, save_file=save); removed_count += 1
            if removed_count > 0:
                applied_logs.append(f"エラーキャラ削除({removed_count}体)")
        except Exception as e: print(f"[ERROR] remove_error_cats: {e}")

    return applied_logs


def _apply_all_segments_unlocked(save_file, selected, char_list, custom_amounts, custom_playtime="", main_story_chapters=None, vip_items=None, legend_stages=None, vip_facilities=None, vip_talent_orbs=None, special_stages=None, character_settings=None, main_story_stages=None, event_stage_settings=None, lineup_settings=None, ototo_settings=None, dojo_score_settings=None, future_score_settings=None):
    final_logs = apply_vip_items(save_file, vip_items)
    final_logs.extend(apply_legend_stages(save_file, legend_stages))
    final_logs.extend(apply_vip_facilities(save_file, vip_facilities))
    final_logs.extend(apply_vip_talent_orbs(save_file, vip_talent_orbs))
    final_logs.extend(apply_special_stages(save_file, special_stages))
    final_logs.extend(apply_main_story_stages(save_file, main_story_stages))
    final_logs.extend(apply_event_stage_settings(save_file, event_stage_settings))
    final_logs.extend(apply_lineup_settings(save_file, lineup_settings))
    final_logs.extend(apply_ototo_settings(save_file, ototo_settings))
    final_logs.extend(apply_dojo_score_settings(save_file, dojo_score_settings))
    final_logs.extend(apply_future_score_settings(save_file, future_score_settings))
    s1 = selected.get("s1", [])
    s2 = selected.get("s2", [])
    s3 = selected.get("s3", [])
    s4 = selected.get("s4", [])
    for i, segment in enumerate([s1, s2, s3, s4], 1):
        if not segment and (i != 3 or not char_list): continue
        items = []
        for item in segment:
            if item == "all_stage_clear":
                items.extend(["main_story_clear", "zombie_clear", "aku_clear",
                               "legend_clear", "true_legend_clear", "zero_legend_clear", "event_clear"])
            else:
                items.append(item)
        # VIP個別指定がある親項目は、従来の「全配列を一括更新」処理から除外する。
        if vip_items:
            granular_values = set().union(*(
                VIP_ITEM_SELECTED_VALUES.get(group_key, set()) for group_key in vip_items
            ))
            items = [item for item in items if item not in granular_values]
        # 詳細選択が送られたシリーズは従来の全クリア処理を抑止する。
        if legend_stages is not None:
            granular_legend_values = {
                LEGEND_SELECTED_VALUES[key] for key in legend_stages if key in LEGEND_SELECTED_VALUES
            }
            items = [item for item in items if item not in granular_legend_values]
        if special_stages is not None:
            granular_special_values = {
                "zombie": "zombie_clear", "aku": "aku_clear", "aku_ex": "aku_clear"
            }
            suppressed = {granular_special_values[key] for key in special_stages if key in granular_special_values}
            items = [item for item in items if item not in suppressed]
        if main_story_stages is not None:
            items = [item for item in items if item != "main_story_clear"]
        if event_stage_settings is not None:
            items = [item for item in items if item != "event_clear"]
        if vip_facilities is not None:
            items = [item for item in items if item not in {"base_upgrades_max", "custom_base_upgrades"}]
        if vip_talent_orbs is not None:
            items = [item for item in items if item not in {"talent_orbs", "custom_talent_orbs"}]
        if ototo_settings is not None:
            items = [item for item in items if item not in {"ototo_max", "ototo_detailed"}]
        # UR報酬は全キャラ解放・レベル指定が完了した後のランクで判定する。
        items = [item for item in items if item not in {"user_rank_rewards_claimed", "user_rank_rewards_unclaimed"}]
        final_logs.extend(apply_daiko_segment(
            save_file,
            list(dict.fromkeys(items)),
            char_list if i == 3 else [],
            custom_amounts,
            custom_playtime,
            main_story_chapters,
            character_settings if i == 3 else None,
        ))
    # 形態を扱う操作時だけ既存の不可能な状態も修復する。通常のアイテム・
    # ステージ操作で、配布metadataより新しい実機形態を触らないための制限。
    form_actions = {"form_specific", "true_form_specific", "true_form_all"}
    should_repair_forms = any(form_actions.intersection(segment) for segment in (s1, s2, s3, s4))
    if should_repair_forms or lineup_settings is not None:
        repaired_forms = _normalise_cat_form_flags(save_file)
        if repaired_forms:
            final_logs.append(f"形態・進化権自動修正({repaired_forms}体)")
    if any("hide_character_new" in segment for segment in (s1, s2, s3, s4)):
        # 後続のキャラ解放・形態・図鑑指定を終えてから、Newを最終的に解除する。
        changed = clear_created_account_new_marks(save_file)
        final_logs.append(f"所持キャラのNew非表示・図鑑報酬受取済み({changed}体)")
    if any("hide_medal_new" in segment for segment in (s1, s2, s3, s4)):
        changed = clear_owned_medal_new_marks(save_file)
        final_logs.append(f"獲得済みメダルのNew通知を解除({changed}件)")
    reward_actions = set().union(*(set(segment) for segment in (s1, s2, s3, s4)))
    final_logs.extend(_apply_user_rank_reward_actions(save_file, reward_actions))
    return final_logs


def _apply_all_segments(save_file, selected, char_list, custom_amounts, custom_playtime="", main_story_chapters=None, vip_items=None, legend_stages=None, vip_facilities=None, vip_talent_orbs=None, special_stages=None, character_settings=None, main_story_stages=None, event_stage_settings=None, lineup_settings=None, ototo_settings=None, dojo_score_settings=None, future_score_settings=None):
    """BCSFEのsave依存グローバルキャッシュを別ジョブと混在させず適用する。"""
    with BCSFE_EDIT_LOCK:
        ensure_latest_save_schema(save_file)
        # BCSFE 3.6.0はgetterやchara_drop等をプロセス全体でキャッシュする。
        # すべて同じ最新版へ固定し、saveを保持する派生キャッシュはジョブごとに破棄する。
        core.core_data.game_data_getter = _latest_jp_game_data_getter()
        for cache_name in (
            "gatya_item_names", "gatya_item_buy", "chara_drop", "gamatoto_levels",
            "gamatoto_members_name", "localizable", "abilty_data", "enemy_names",
            "rank_gift_descriptions", "rank_gifts", "treasure_text", "cat_shrine_levels",
            "medal_names", "mission_names", "mission_conditions",
        ):
            if hasattr(core.core_data, cache_name):
                setattr(core.core_data, cache_name, None)
        return _apply_all_segments_unlocked(
            save_file, selected, char_list, custom_amounts, custom_playtime,
            main_story_chapters, vip_items, legend_stages, vip_facilities,
            vip_talent_orbs, special_stages, character_settings,
            main_story_stages, event_stage_settings, lineup_settings, ototo_settings,
            dojo_score_settings, future_score_settings,
        )


def run_job_daiko(job_id, operation_id, transfer_code, auth_code, selected, char_list, custom_amounts, custom_playtime="", main_story_chapters=None, vip_items=None, legend_stages=None, vip_facilities=None, vip_talent_orbs=None, special_stages=None, character_settings=None, main_story_stages=None, event_stage_settings=None, lineup_settings=None, ototo_settings=None, dojo_score_settings=None, future_score_settings=None, access_tier="free"):
    def update(d):
        d["updated_at"] = time.time()
        _update_job(job_id, d)
    handler = None
    checkpoint_codes = None
    final_issue_started = False
    try:
        update({"status": "running", "started_at": time.time(), "timing_stage": "connect", "log": "サーバーに接続中..."})
        _update_operation(operation_id, status="running", error=None)

        cc = core.CountryCode.from_code("jp")
        gv = core.GameVersion(get_target_game_version_number())

        char_list = [c.strip() for c in char_list if str(c).strip().isdigit()]

        handler, result = ServerHandler.from_codes(
            transfer_code, auth_code, cc, gv, save_backup=False
        )
        if handler is None:
            update({"status": "error", "error": "エラー: コード無効。"})
            _close_operation(
                operation_id,
                status="error",
                recovery_status="not_available",
                error="コード無効のため、セーブデータは取得されていません。",
            )
            return

        update({
            "transfer_received": True,
            "timing_stage": "protect", "log": "引き継ぎ取得済み・復旧用コードを保存中...",
        })
        handler.save_file._schema_target_game_version_number = max(gv.game_version, handler.save_file.game_version.game_version)
        _save_operation_snapshot(operation_id, handler.save_file)

        # 入力コードはfrom_codes成功時点で消費される。編集・保存中に
        # プロセスが落ちてもアカウントを回収できるよう、変更前データで
        # 復旧用コードを先に発行し、SQLiteのジョブ記録へ即時保存する。
        checkpoint_codes = handler.get_codes(tries=2)
        if not checkpoint_codes:
            update({
                "status": "error",
                "transfer_received": True,
                "admin_recovery_required": True,
                "error": (
                    "引き継ぎ取得後、作業前の復旧用コードを発行できませんでした。"
                    "同じ操作を再実行せず管理者へ連絡してください。"
                ),
            })
            _update_operation(
                operation_id, status="error",
                error="引き継ぎ取得後、作業前の復旧用コードを発行できませんでした。",
                recovery_status="reissue_failed",
            )
            return
        update({
            "recovery_transfer_code": checkpoint_codes[0],
            "recovery_auth_code": checkpoint_codes[1],
            "admin_recovery_required": False,
            "timing_stage": "apply", "log": "復旧用コード保存済み・データを適用中...",
        })

        final_logs = _apply_all_segments(handler.save_file, selected, char_list, custom_amounts, custom_playtime, main_story_chapters, vip_items, legend_stages, vip_facilities, vip_talent_orbs, special_stages, character_settings, main_story_stages, event_stage_settings, lineup_settings, ototo_settings, dojo_score_settings, future_score_settings)
        # 最終発行だけ失敗した時は、編集済みデータから管理者が再発行できるよう更新。
        _save_operation_snapshot(operation_id, handler.save_file)

        update({"timing_stage": "save", "log": "サーバーに保存中..."})
        final_issue_started = True
        codes = handler.get_codes()
        if codes:
            t, a = codes
            total_count = increment_usage_count()
            update({
                "status": "done", "transfer_code": t, "auth_code": a,
                "applied": final_logs, "recovery_transfer_code": None,
                "recovery_auth_code": None, "admin_recovery_required": False,
            })
            _close_operation(
                operation_id, status="done", recovery_status="not_needed"
            )
            send_usage_log(access_tier, final_logs, total_count, USAGE_DB_PATH)
        else:
            # 最終POSTはサーバー側だけ成功して応答が失われた可能性がある。
            # 直後に自動再発行すると既知のコードまで無効化し得るため、最新の
            # 編集済みスナップショットを残して管理パネルから再発行する。
            error_text = (
                "引き継ぎ取得後の保存通信を確認できませんでした。"
                "同じ操作を再実行せず、管理パネルから引き継ぎコードを再発行してください。"
            )
            update({
                "status": "error",
                "error": error_text,
                "transfer_received": True,
                "admin_recovery_required": True,
            })
            _update_operation(
                operation_id,
                status="error",
                error=error_text,
                recovery_status="reissue_failed",
            )

    except Exception as e:
        payload = {"status": "error", "error": _safe_error_message(e)}
        # 最終発行を始める前ならチェックポイントコードは確実に既知。
        # 発行開始後は応答喪失の可能性があるため、再送せずsnapshotを残す。
        if checkpoint_codes and not final_issue_started:
            payload.update({
                "recovery_transfer_code": checkpoint_codes[0],
                "recovery_auth_code": checkpoint_codes[1],
                "admin_recovery_required": False,
                "error": "処理中にエラーが発生しました。引き継ぎコードを確認してください。",
            })
        elif handler is not None:
            payload.update({
                "transfer_received": True,
                "admin_recovery_required": True,
                "error": (
                    "引き継ぎ取得後にエラーが発生しました。"
                    "同じ操作を再実行せず、管理パネルから引き継ぎコードを再発行してください。"
                ),
            })
        if checkpoint_codes and not final_issue_started:
            payload["admin_recovery_required"] = False
            _close_operation(
                operation_id,
                status="error",
                recovery_status="client_code_issued",
                error=payload["error"],
                issued_codes=checkpoint_codes,
            )
        elif handler is None:
            payload["admin_recovery_required"] = False
            _close_operation(
                operation_id,
                status="error",
                recovery_status="not_available",
                error=payload["error"],
            )
        else:
            payload["admin_recovery_required"] = True
            _update_operation(
                operation_id, status="error", error=payload["error"],
                recovery_status="reissue_failed",
            )
        update(payload)
        print(traceback.format_exc())


def run_job_create(job_id, selected, char_list, custom_amounts, count=1, custom_playtime="", account_type_key="new", main_story_chapters=None, vip_items=None, legend_stages=None, vip_facilities=None, vip_talent_orbs=None, special_stages=None, character_settings=None, main_story_stages=None, event_stage_settings=None, lineup_settings=None, ototo_settings=None, dojo_score_settings=None, future_score_settings=None, access_tier="free"):
    def update(d):
        d["updated_at"] = time.time()
        _update_job(job_id, d)
    try:
        update({"status": "running", "started_at": time.time(), "timing_stage": "create", "timing_unit": 1, "log": "アカウントを作成中..."})

        cc = core.CountryCode.from_code("jp")
        gv = core.GameVersion(get_target_game_version_number())

        char_list = [c.strip() for c in char_list if str(c).strip().isdigit()]

        filename = ACCOUNT_TYPE_FILES.get(account_type_key, "Nyanko_new")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        if not os.path.exists(path):
            update({"status": "error", "error": f"エラー: {filename} が見つかりません。"})
            return

        count = max(1, min(MAX_COUNT, int(count)))
        accounts = []
        final_logs = []

        unit_durations = []
        for i in range(count):
            unit_started_at = time.time()
            update({"timing_stage": "create", "timing_unit": i+1,
                    "log": f"アカウントを作成中... ({i+1}/{count})"})
            save = core.SaveFile(core.Data.from_file(core.Path(path)), cc=cc)
            ensure_latest_save_schema(save, gv.game_version)
            handler = ServerHandler(save)
            if not handler.create_new_account():
                update({"status": "error", "error": f"エラー: アカウント作成失敗 ({i+1}個目)。"})
                return

            # 初期垢以外は、選択した難易度に応じた自然なベース状態を先に適用。
            # その後に画面で選んだ詳細設定を適用するので、従来の編集自由度は維持する。
            preset_logs = apply_account_type_preset(handler.save_file, account_type_key)

            update({"timing_stage": "apply", "log": f"データを適用中... ({i+1}/{count})"})
            if i == 0:
                custom_logs = _apply_all_segments(handler.save_file, selected, char_list, custom_amounts, custom_playtime, main_story_chapters, vip_items, legend_stages, vip_facilities, vip_talent_orbs, special_stages, character_settings, main_story_stages, event_stage_settings, lineup_settings, ototo_settings, dojo_score_settings, future_score_settings)
                final_logs = [*preset_logs, *custom_logs]
            else:
                _apply_all_segments(handler.save_file, selected, char_list, custom_amounts, custom_playtime, main_story_chapters, vip_items, legend_stages, vip_facilities, vip_talent_orbs, special_stages, character_settings, main_story_stages, event_stage_settings, lineup_settings, ototo_settings, dojo_score_settings, future_score_settings)

            # 個別の解放・レベル・編成指定で立つNewフラグも、作成完了時にまとめて消す。
            cleared_new = clear_created_account_new_marks(handler.save_file)
            if i == 0 and cleared_new:
                final_logs.append(f"解放済みキャラのNew表示を解除({cleared_new}体)")
            cleared_medals = clear_owned_medal_new_marks(handler.save_file)
            if i == 0:
                final_logs.append(f"獲得済みメダルのNew通知を解除({cleared_medals}件)")
            disable_rank_up_sale(handler.save_file)
            if i == 0:
                final_logs.append("ランクアップセール案内を非表示")

            update({"timing_stage": "save", "log": f"サーバーに保存中... ({i+1}/{count})"})
            codes = handler.get_codes()
            if codes:
                accounts.append({"tc": codes[0], "ac": codes[1]})
            else:
                update({"status": "error", "error": f"エラー: 保存失敗 ({i+1}個目)。"})
                return

            unit_durations.append(time.time() - unit_started_at)
            update({"timing_completed_units": i+1, "timing_unit_durations": list(unit_durations)})

        total_count = increment_usage_count()
        update({
            "status": "done",
            "accounts": accounts,
            "transfer_code": accounts[0]["tc"],
            "auth_code": accounts[0]["ac"],
            "applied": final_logs,
        })
        send_usage_log(
            access_tier,
            [f"新規アカウント作成 ×{len(accounts)}", *final_logs],
            total_count,
            USAGE_DB_PATH,
        )

    except Exception as e:
        update({"status": "error", "error": str(e)})
        print(traceback.format_exc())


class SafeCloneServerHandler(ServerHandler):
    """Create at most one copy account and snapshot it as soon as its ID exists."""

    def __init__(self, save_file, snapshot_callback=None, print=True):
        super().__init__(save_file, print=print)
        self.snapshot_callback = snapshot_callback
        self.account_assigned = False

    def get_password(self, tries=0):
        # BCSFE's default fallback calls create_new_account() again when account
        # setup communication fails. A clone must never silently abandon the
        # first issued account ID and create another one.
        password = self.get_stored_password()
        if password is not None:
            return password
        password = self.refresh_password()
        if password is not None:
            return password
        return self.get_password_new()

    def create_new_account(self, tries=1):
        new_inquiry_code = self.get_new_inquiry_code()
        if new_inquiry_code is None:
            return False

        self.save_file.inquiry_code = new_inquiry_code
        self.account_assigned = True
        self.remove_stored_auth_token()
        self.remove_stored_save_key_data()
        self.remove_stored_password()
        fail_text = "EXPECT_THIS_TO_FAIL"
        start_count = (40 - len(fail_text)) // 2
        end_count = 40 - len(fail_text) - start_count
        self.save_file.password_refresh_token = (
            "_" * start_count + fail_text + "_" * end_count
        )

        # ID発行直後に永続化。以降の認証通信が失敗しても管理パネルから
        # 同じコピーアカウントの再発行を試せる。
        if self.snapshot_callback is not None:
            self.snapshot_callback(self.save_file)

        password = self.get_password()
        auth_token = self.get_auth_token()
        save_key_data = self.get_save_key()
        self.update_managed_items()
        self.save_file.show_ban_message = False
        return password is not None and auth_token is not None and save_key_data is not None


def run_job_clone(job_id, operation_id, transfer_code, auth_code, count=1, access_tier="free"):
    def update(d):
        d["updated_at"] = time.time()
        _update_job(job_id, d)
    handler = None
    orig_codes = None
    copies = []
    active_copy_handler = None
    copy_recovery_pending = False
    try:
        update({"status": "running", "started_at": time.time(), "timing_stage": "connect", "log": "サーバーに接続中..."})
        _update_operation(operation_id, status="running", error=None)

        cc = core.CountryCode.from_code("jp")
        gv = core.GameVersion(get_target_game_version_number())

        handler, result = ServerHandler.from_codes(
            transfer_code, auth_code, cc, gv, save_backup=False
        )
        if handler is None:
            update({"status": "error", "error": "エラー: コード無効。"})
            _close_operation(
                operation_id,
                status="error",
                recovery_status="not_available",
                error="コード無効のため、セーブデータは取得されていません。",
            )
            return

        update({"transfer_received": True, "timing_stage": "protect", "log": "引き継ぎ取得済み・元アカウントを保存中..."})
        handler.save_file._schema_target_game_version_number = max(gv.game_version, handler.save_file.game_version.game_version)
        _save_operation_snapshot(operation_id, handler.save_file)

        update({"log": "セーブデータを取得中..."})
        save_data = handler.save_file.data

        # 最終転送POSTの応答が失われた場合、直後の再送は最初に発行された
        # コードを無効化し得る。BCSFE内部の段階別試行だけに限定する。
        orig_codes = handler.get_codes()
        if not orig_codes:
            error_text = (
                "引き継ぎ取得後、元アカウントの復旧コードを発行できませんでした。"
                "同じ操作を連続実行せず管理者へ連絡してください。"
            )
            update({
                "status": "error",
                "error": error_text,
                "transfer_received": True,
                "admin_recovery_required": True,
            })
            _update_operation(operation_id, status="error", error=error_text, recovery_status="reissue_failed")
            return

        # コピー作成中に後続処理が失敗しても、ここで確定した
        # 元アカウントの最新コードをエラー画面から回収できるよう保持する。
        update({
            "recovery_transfer_code": orig_codes[0],
            "recovery_auth_code": orig_codes[1],
            "admin_recovery_required": False,
            "log": "元アカウント保護済み・コピーを作成中...",
        })
        count = max(1, min(MAX_COUNT, int(count)))

        unit_durations = []
        for i in range(count):
            unit_started_at = time.time()
            copy_recovery_pending = True
            update({
                "timing_stage": "create", "timing_unit": i+1,
                "log": f"コピーアカウントを作成中... ({i+1}/{count})",
                "return_pending": True,
            })
            copy_save = core.SaveFile(core.Data(save_data), cc=cc)
            ensure_latest_save_schema(copy_save, gv.game_version)
            active_copy_handler = SafeCloneServerHandler(
                copy_save,
                snapshot_callback=lambda save: _save_operation_snapshot(operation_id, save),
            )
            if not active_copy_handler.create_new_account():
                error_text = f"エラー: コピーアカウント作成失敗 ({i+1}個目)。"
                if active_copy_handler.account_assigned:
                    # 新しいIDは既に発行済み。snapshotを破棄せず、同じIDの
                    # データから管理パネルで再発行できる状態にする。
                    update({
                        "status": "error", "error": error_text,
                        "admin_recovery_required": True,
                        "return_pending": False,
                    })
                    _update_operation(
                        operation_id,
                        status="error",
                        recovery_status="reissue_failed",
                        error=error_text,
                    )
                else:
                    copy_recovery_pending = False
                    update({
                        "status": "error", "error": error_text,
                        "admin_recovery_required": False,
                        "return_pending": False,
                    })
                    _close_operation(
                        operation_id,
                        status="error",
                        recovery_status="client_code_issued",
                        error=error_text,
                        issued_codes=orig_codes,
                    )
                return

            update({"timing_stage": "save", "log": f"コピーアカウントを保存中... ({i+1}/{count})"})
            _save_operation_snapshot(operation_id, active_copy_handler.save_file)
            copy_codes = active_copy_handler.get_codes()
            if not copy_codes:
                update({
                    "status": "error",
                    "error": f"エラー: コピーアカウントの保存失敗 ({i+1}個目)。",
                    "admin_recovery_required": True,
                    "return_pending": False,
                })
                _update_operation(
                    operation_id, status="error", recovery_status="reissue_failed",
                    error=f"コピーアカウントの保存失敗 ({i+1}個目)。",
                )
                return
            copies.append({"tc": copy_codes[0], "ac": copy_codes[1]})
            copy_recovery_pending = False
            unit_durations.append(time.time() - unit_started_at)
            update({"return_pending": False, "copies": copy.deepcopy(copies),
                    "timing_completed_units": i+1, "timing_unit_durations": list(unit_durations)})
            # このコピーは返却済みなので、次のコピー作成中は元アカウントを復旧対象に戻す。
            _save_operation_snapshot(operation_id, handler.save_file)
            active_copy_handler = None

        total_count = increment_usage_count()
        update({
            "status": "done",
            "orig_transfer_code": orig_codes[0],
            "orig_auth_code": orig_codes[1],
            "copies": copies,
            "copy_transfer_code": copies[0]["tc"],
            "copy_auth_code": copies[0]["ac"],
            "admin_recovery_required": False,
        })
        _close_operation(
            operation_id, status="done", recovery_status="not_needed"
        )
        send_usage_log(
            access_tier,
            [f"アカウント複製 ×{len(copies)}"],
            total_count,
            USAGE_DB_PATH,
        )

    except Exception as e:
        payload = {"status": "error", "error": _safe_error_message(e)}
        if orig_codes:
            payload.update({
                "recovery_transfer_code": orig_codes[0],
                "recovery_auth_code": orig_codes[1],
                "copies": copy.deepcopy(copies),
            })
        if handler is not None:
            payload.update({
                "transfer_received": True,
                "error": (
                    "引き継ぎ取得後に複製エラーが発生しました。"
                    "同じ操作を再実行せず、表示されたコードまたは管理パネルを使用してください。"
                ),
            })

        # コピーID発行後・コード返却前なら、そのコピーsnapshotを必ず保持。
        # 元アカウントの既知コードを例外処理で再発行して無効化しない。
        if copy_recovery_pending and active_copy_handler is not None:
            try:
                if active_copy_handler.account_assigned:
                    _save_operation_snapshot(operation_id, active_copy_handler.save_file)
            except Exception as snapshot_error:
                print(f"[ERROR] clone snapshot: {snapshot_error}")
            payload["admin_recovery_required"] = True
            payload["return_pending"] = False
            _update_operation(
                operation_id,
                status="error",
                error=payload["error"],
                recovery_status="reissue_failed",
            )
        elif orig_codes:
            payload["admin_recovery_required"] = False
            _close_operation(
                operation_id,
                status="error",
                recovery_status="client_code_issued",
                error=payload["error"],
                issued_codes=orig_codes,
            )
        elif handler is not None:
            payload["admin_recovery_required"] = True
            _update_operation(
                operation_id, status="error", error=payload["error"],
                recovery_status="reissue_failed",
            )
        else:
            payload["admin_recovery_required"] = False
            _close_operation(
                operation_id,
                status="error",
                recovery_status="not_available",
                error=payload["error"],
            )
        update(payload)
        print(traceback.format_exc())


class RecoveryServerHandler(ServerHandler):
    """Admin recovery must never silently turn the save into a new account."""
    def create_new_account(self, tries=3):
        return False


def run_admin_reissue(operation_id: str):
    record = _load_operation(operation_id)
    if not record or not record.get("snapshot"):
        _update_operation(
            operation_id, status="error", recovery_status="reissue_failed",
            error="復旧用データが見つかりません。",
        )
        return
    try:
        raw = gzip.decompress(_decrypt_snapshot(record["snapshot"], operation_id))
        cc = core.CountryCode.from_code("jp")
        with BCSFE_EDIT_LOCK:
            save_file = core.SaveFile(core.Data(raw), cc=cc)
            inquiry_before = str(save_file.inquiry_code)
            handler = RecoveryServerHandler(save_file, print=False)
            codes = handler.get_codes(tries=2)
            inquiry_after = str(handler.save_file.inquiry_code)
        if inquiry_after != inquiry_before:
            raise RuntimeError("復旧中にアカウントIDが変化したため中断しました。")
        if not codes:
            raise RuntimeError("引き継ぎコードを発行できませんでした。")
        job = _load_job(record["job_id"])
        if job:
            _update_job(record["job_id"], {
                "recovery_transfer_code": codes[0],
                "recovery_auth_code": codes[1],
                "updated_at": time.time(),
            })
        _update_operation(
            operation_id, status="done", error=None,
            recovery_status="admin_reissued",
            # 成功後は二重発行を防ぐためsnapshotを破棄。
            snapshot=None,
            reissue_result=_encrypt_snapshot(
                json.dumps({"tc": codes[0], "ac": codes[1]}, separators=(",", ":")).encode(),
                operation_id,
            ),
        )
    except Exception as exc:
        # スナップショットは残し、通信復旧後の再試行を可能にする。
        original_error = str(record.get("error") or "").strip()
        reissue_error = _safe_error_message(exc)
        combined_error = f"{original_error}\n管理者再発行: {reissue_error}".strip()[:2000]
        _update_operation(operation_id, recovery_status="reissue_failed", error=combined_error)
        print(f"[ERROR] admin reissue {operation_id}: {exc}")


# =====================
# APIエンドポイント
# =====================
def _reserve_free_quota(data: dict, job_id: str):
    """無料版の実行1回分を原子的に予約する。"""
    try:
        free_usage_manager.reserve(
            real_ip(),
            data.get("fingerprint", ""),
            job_id,
            allow_month_end_unlimited=bool(current_site_user()),
        )
        return job_id, None
    except QuotaExhausted as exc:
        return None, (jsonify({"error": str(exc), "code": "quota_exhausted"}), 402)
    except IdentityConflict as exc:
        return None, (jsonify({"error": str(exc), "code": "identity_conflict"}), 409)
    except FreeUsageError as exc:
        return None, (jsonify({"error": str(exc), "code": "identity_invalid"}), 400)
    except sqlite3.Error:
        return None, (jsonify({"error": "利用回数を確認できませんでした。少し待ってから再実行してください。"}), 503)


def _reserve_site_vip_job(job_id: str):
    """有料VIPを再確認し、無料体験なら実行1回を原子的に予約する。"""
    user = current_site_user()
    if not user or not user.get("is_vip"):
        return None, (jsonify({"error": "VIP利用権を確認できませんでした。"}), 403)
    try:
        result = account_store.reserve_vip_job(user["id"], job_id)
        return result.get("reservation_id"), None
    except AccountPermissionError as exc:
        return None, (jsonify({"error": str(exc), "code": "vip_trial_unavailable"}), 409)
    except sqlite3.Error:
        return None, (jsonify({"error": "VIP利用権を確認できませんでした。少し待ってから再実行してください。"}), 503)


def _refund_unattached_quota(reservation_id: str | None) -> None:
    """ジョブ保存前の失敗で、紐付けられなかった予約を返却する。"""
    if not reservation_id:
        return
    try:
        free_usage_manager.settle(reservation_id, success=False)
    except Exception as exc:
        print(f"[ERROR] free quota refund {reservation_id}: {type(exc).__name__}")


def _refund_unattached_vip_trial(reservation_id: str | None) -> None:
    """ジョブ保存前の失敗で、予約したVIP体験1回を返却する。"""
    if not reservation_id:
        return
    try:
        account_store.settle_vip_job(reservation_id, success=False)
    except Exception as exc:
        print(f"[ERROR] VIP trial refund {reservation_id}: {type(exc).__name__}")


def _discard_unstarted_job(job_id: str) -> None:
    """ワーカー投入前に失敗したジョブのメモリ・永続レコードを片付ける。"""
    with jobs_lock:
        jobs.pop(job_id, None)
    try:
        with _job_db_connect() as conn:
            conn.execute("DELETE FROM background_jobs WHERE job_id=?", (job_id,))
    except sqlite3.Error:
        pass


@app.route("/api/run_daiko", methods=["POST"])
@limiter.limit("30 per minute")
def api_run_daiko():
    data = request.get_json(silent=True)
    err = validate_common_input(data)
    if err:
        return err
    err = validate_transfer_auth_codes(data)
    if err:
        return err
    if not validate_and_consume_api_key(data.get("api_key", "")):
        return jsonify({"error": "無効なAPIキー"}), 403
    if count_inflight_jobs() >= MAX_INFLIGHT_JOBS:
        return jsonify({"error": "混雑しています。少し待ってからお試しください。"}), 503

    custom_amounts = safe_custom_amounts(data)
    custom_playtime = safe_custom_playtime(data)
    character_settings = safe_character_settings(data)
    selected_payload = copy.deepcopy(data.get("selected", {}))

    # VIP権限はサイトアカウントの契約状態から判定する。
    is_vip_confirmed = site_vip_confirmed()
    if not is_vip_confirmed:
        return vip_required_response()
    if is_vip_confirmed:
        main_story_chapters = safe_main_story_chapters(data)
        main_story_stages = safe_main_story_stages(data)
        event_stage_settings = safe_event_stage_settings(data)
        vip_items = safe_vip_items(data)
        legend_stages = safe_legend_stages(data) if "legend_stages" in data else None
        vip_facilities = safe_vip_facilities(data) if "vip_facilities" in data else None
        vip_talent_orbs = safe_vip_talent_orbs(data) if "vip_talent_orbs" in data else None
        special_stages = safe_special_stages(data) if "special_stages" in data else None
        lineup_settings = safe_lineup_settings(data) if "lineup_settings" in data else None
        ototo_settings = safe_ototo_settings(data) if "ototo_settings" in data else None
        dojo_score_settings = safe_dojo_score_settings(data) if "dojo_score_settings" in data else None
        future_score_settings = safe_future_score_settings(data) if "future_score_settings" in data else None
    else:
        main_story_chapters = None
        main_story_stages = None
        event_stage_settings = None
        vip_items = {}
        legend_stages = None
        vip_facilities = None
        vip_talent_orbs = None
        special_stages = None
        lineup_settings = None
        ototo_settings = None
        dojo_score_settings = None
        future_score_settings = None
        selected_payload["s3"] = [
            value for value in selected_payload.get("s3", [])
            if value not in {"talents_specific", "talents_disable_all", "lineup_custom"}
        ]
        selected_payload["s2"] = [
            value for value in selected_payload.get("s2", [])
            if value not in VIP_ONLY_SYSTEM_ACTIONS
        ]

    # 外部metadataを使う入力整理が完了してからジョブIDを発行する。
    # 開始レスポンス前に通信が切れても、見えない孤立ジョブを実行しない。
    job_id = str(uuid.uuid4())
    job_token, job_access_hash = _new_job_access_token()
    operation_id = _safe_operation_id(data)
    if not operation_id:
        return jsonify({"error": "受付IDは30桁の英数字です"}), 400
    quota_reservation_id = None
    trial_vip_reservation_id = None
    if is_vip_confirmed:
        trial_vip_reservation_id, vip_error = _reserve_site_vip_job(job_id)
        if vip_error:
            return vip_error
    else:
        quota_reservation_id, quota_error = _reserve_free_quota(data, job_id)
        if quota_error:
            return quota_error
    now = time.time()
    try:
        _create_operation(operation_id, job_id, "daiko", now)
    except sqlite3.IntegrityError:
        _refund_unattached_quota(quota_reservation_id)
        _refund_unattached_vip_trial(trial_vip_reservation_id)
        return jsonify({"error": "その受付IDは既に登録されています"}), 409
    except sqlite3.Error:
        _refund_unattached_quota(quota_reservation_id)
        _refund_unattached_vip_trial(trial_vip_reservation_id)
        return jsonify({"error": "受付情報を保存できませんでした。"}), 503
    try:
        _create_job(job_id, {"status": "pending", "log": "待機中...",
                             "site_account_id": session.get("site_account_id"),
                             "operation_id": operation_id, "operation_type": "daiko",
                             **timing_profile(data, "daiko", 1),
                             "job_access_hash": job_access_hash,
                             "quota_reservation_id": quota_reservation_id,
                             "trial_vip_reservation_id": trial_vip_reservation_id,
                             "error": None, "transfer_code": None, "auth_code": None,
                             "applied": [], "created_at": now, "updated_at": now,
                             "transfer_received": False})
    except Exception:
        _discard_unstarted_job(job_id)
        _refund_unattached_quota(quota_reservation_id)
        _refund_unattached_vip_trial(trial_vip_reservation_id)
        _close_operation(
            operation_id, status="error", recovery_status="not_available",
            error="ジョブ情報の保存に失敗しました。",
        )
        return jsonify({"error": "処理を開始できませんでした"}), 503
    register_job_to_session(job_id)
    try:
        executor.submit(
            run_job_daiko,
            job_id, operation_id, str(data.get("transfer_code", "")).strip(), str(data.get("auth_code", "")).strip(),
            selected_payload, data.get("char_list", []), custom_amounts, custom_playtime, main_story_chapters, vip_items, legend_stages, vip_facilities, vip_talent_orbs, special_stages, character_settings, main_story_stages, event_stage_settings, lineup_settings, ototo_settings, dojo_score_settings, future_score_settings,
            "VIP" if is_vip_confirmed else "free",
        )
    except Exception as exc:
        error_text = _safe_error_message(exc)
        _update_job(job_id, {"status": "error", "error": error_text, "updated_at": time.time()})
        _close_operation(
            operation_id,
            status="error",
            recovery_status="not_available",
            error=error_text,
        )
        return jsonify({"error": "処理を開始できませんでした"}), 503
    return jsonify({"job_id": job_id, "job_token": job_token, "operation_id": operation_id})


@app.route("/api/run_create", methods=["POST"])
@limiter.limit("30 per minute")
def api_run_create():
    data = request.get_json(silent=True)
    err = validate_common_input(data)
    if err:
        return err
    if not validate_and_consume_api_key(data.get("api_key", "")):
        return jsonify({"error": "無効なAPIキー"}), 403
    if count_inflight_jobs() >= MAX_INFLIGHT_JOBS:
        return jsonify({"error": "混雑しています。少し待ってからお試しください。"}), 503

    custom_amounts = safe_custom_amounts(data)
    custom_playtime = safe_custom_playtime(data)
    character_settings = safe_character_settings(data)
    selected_payload = copy.deepcopy(data.get("selected", {}))
    # アカウント種別選択・詳細指定はサイトVIP契約者のみ許可。
    is_vip_confirmed = site_vip_confirmed()
    if not is_vip_confirmed:
        return vip_required_response()
    if is_vip_confirmed:
        count = safe_count(data)
        account_type_key = safe_account_type(data)
        main_story_chapters = safe_main_story_chapters(data)
        main_story_stages = safe_main_story_stages(data)
        event_stage_settings = safe_event_stage_settings(data)
        vip_items = safe_vip_items(data)
        legend_stages = safe_legend_stages(data) if "legend_stages" in data else None
        vip_facilities = safe_vip_facilities(data) if "vip_facilities" in data else None
        vip_talent_orbs = safe_vip_talent_orbs(data) if "vip_talent_orbs" in data else None
        special_stages = safe_special_stages(data) if "special_stages" in data else None
        lineup_settings = safe_lineup_settings(data) if "lineup_settings" in data else None
        ototo_settings = safe_ototo_settings(data) if "ototo_settings" in data else None
        dojo_score_settings = safe_dojo_score_settings(data) if "dojo_score_settings" in data else None
        future_score_settings = safe_future_score_settings(data) if "future_score_settings" in data else None
    else:
        count = safe_count(data, 2)
        account_type_key = "new"
        main_story_chapters = None
        main_story_stages = None
        event_stage_settings = None
        vip_items = {}
        legend_stages = None
        vip_facilities = None
        vip_talent_orbs = None
        special_stages = None
        lineup_settings = None
        ototo_settings = None
        dojo_score_settings = None
        future_score_settings = None
        selected_payload["s3"] = [
            value for value in selected_payload.get("s3", [])
            if value not in {"talents_specific", "talents_disable_all", "lineup_custom"}
        ]
        selected_payload["s2"] = [
            value for value in selected_payload.get("s2", [])
            if value not in VIP_ONLY_SYSTEM_ACTIONS
        ]

    job_id = str(uuid.uuid4())
    job_token, job_access_hash = _new_job_access_token()
    quota_reservation_id = None
    trial_vip_reservation_id = None
    if is_vip_confirmed:
        trial_vip_reservation_id, vip_error = _reserve_site_vip_job(job_id)
        if vip_error:
            return vip_error
    else:
        quota_reservation_id, quota_error = _reserve_free_quota(data, job_id)
        if quota_error:
            return quota_error
    now = time.time()
    try:
        _create_job(job_id, {"status": "pending", "log": "待機中...",
                             "site_account_id": session.get("site_account_id"),
                             "job_access_hash": job_access_hash,
                             "quota_reservation_id": quota_reservation_id,
                             "trial_vip_reservation_id": trial_vip_reservation_id,
                             "error": None, "transfer_code": None, "auth_code": None,
                             "operation_type": "create", **timing_profile(data, "create", count), "applied": [], "accounts": [], "created_at": now, "updated_at": now})
    except Exception:
        _discard_unstarted_job(job_id)
        _refund_unattached_quota(quota_reservation_id)
        _refund_unattached_vip_trial(trial_vip_reservation_id)
        return jsonify({"error": "処理を開始できませんでした"}), 503
    register_job_to_session(job_id)
    try:
        executor.submit(
            run_job_create,
            job_id, selected_payload,
            data.get("char_list", []), custom_amounts, count, custom_playtime, account_type_key, main_story_chapters, vip_items, legend_stages, vip_facilities, vip_talent_orbs, special_stages, character_settings, main_story_stages, event_stage_settings, lineup_settings, ototo_settings, dojo_score_settings, future_score_settings,
            "VIP" if is_vip_confirmed else "free",
        )
    except Exception as exc:
        error_text = _safe_error_message(exc)
        _update_job(job_id, {"status": "error", "error": error_text, "updated_at": time.time()})
        return jsonify({"error": "処理を開始できませんでした"}), 503
    return jsonify({"job_id": job_id, "job_token": job_token})


@app.route("/api/run_clone", methods=["POST"])
@limiter.limit("30 per minute")
def api_run_clone():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "入力が不正です"}), 400
    err = validate_transfer_auth_codes(data)
    if err:
        return err
    if not validate_and_consume_api_key(data.get("api_key", "")):
        return jsonify({"error": "無効なAPIキー"}), 403
    if count_inflight_jobs() >= MAX_INFLIGHT_JOBS:
        return jsonify({"error": "混雑しています。少し待ってからお試しください。"}), 503

    is_vip_confirmed = site_vip_confirmed()
    if not is_vip_confirmed:
        return vip_required_response()

    count = safe_count(data, MAX_COUNT if is_vip_confirmed else 2)
    job_id = str(uuid.uuid4())
    job_token, job_access_hash = _new_job_access_token()
    operation_id = _safe_operation_id(data)
    if not operation_id:
        return jsonify({"error": "受付IDは30桁の英数字です"}), 400
    quota_reservation_id = None
    trial_vip_reservation_id = None
    if is_vip_confirmed:
        trial_vip_reservation_id, vip_error = _reserve_site_vip_job(job_id)
        if vip_error:
            return vip_error
    else:
        quota_reservation_id, quota_error = _reserve_free_quota(data, job_id)
        if quota_error:
            return quota_error
    now = time.time()
    try:
        _create_operation(operation_id, job_id, "clone", now)
    except sqlite3.IntegrityError:
        _refund_unattached_quota(quota_reservation_id)
        _refund_unattached_vip_trial(trial_vip_reservation_id)
        return jsonify({"error": "その受付IDは既に登録されています"}), 409
    except sqlite3.Error:
        _refund_unattached_quota(quota_reservation_id)
        _refund_unattached_vip_trial(trial_vip_reservation_id)
        return jsonify({"error": "受付情報を保存できませんでした。"}), 503
    try:
        _create_job(job_id, {"status": "pending", "log": "待機中...",
                             "site_account_id": session.get("site_account_id"),
                             "operation_id": operation_id, "operation_type": "clone",
                             **timing_profile(data, "clone", count),
                             "job_access_hash": job_access_hash,
                             "quota_reservation_id": quota_reservation_id,
                             "trial_vip_reservation_id": trial_vip_reservation_id,
                             "error": None,
                             "orig_transfer_code": None, "orig_auth_code": None,
                             "copy_transfer_code": None, "copy_auth_code": None,
                             "copies": [], "created_at": now, "updated_at": now,
                             "transfer_received": False})
    except Exception:
        _discard_unstarted_job(job_id)
        _refund_unattached_quota(quota_reservation_id)
        _refund_unattached_vip_trial(trial_vip_reservation_id)
        _close_operation(
            operation_id, status="error", recovery_status="not_available",
            error="ジョブ情報の保存に失敗しました。",
        )
        return jsonify({"error": "処理を開始できませんでした"}), 503
    register_job_to_session(job_id)
    try:
        executor.submit(
            run_job_clone,
            job_id, operation_id, str(data.get("transfer_code", "")).strip(), str(data.get("auth_code", "")).strip(), count,
            "VIP" if is_vip_confirmed else "free",
        )
    except Exception as exc:
        error_text = _safe_error_message(exc)
        _update_job(job_id, {"status": "error", "error": error_text, "updated_at": time.time()})
        _close_operation(
            operation_id,
            status="error",
            recovery_status="not_available",
            error=error_text,
        )
        return jsonify({"error": "処理を開始できませんでした"}), 503
    return jsonify({"job_id": job_id, "job_token": job_token, "operation_id": operation_id})


@app.route("/api/job/<job_id>")
@limiter.limit("120 per minute")
def api_job_status(job_id):
    with jobs_lock:
        job = copy.deepcopy(jobs.get(job_id))
    if job is None:
        job = _load_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    if not session_owns_job(job_id, job):
        return jsonify({"error": "unauthorized"}), 403
    _settle_job_quota(job)
    job.pop("job_access_hash", None)
    job.pop("quota_reservation_id", None)
    job.pop("trial_vip_reservation_id", None)
    job["timing"] = job_timing_store.estimate(job)
    return jsonify(job)


@app.route("/api/get_key", methods=["POST"])
@limiter.limit("10 per minute")
def api_get_key():
    key = generate_api_key()
    return jsonify({"api_key": key})


@app.route("/api/usage_count")
@limiter.limit("60 per minute")
def api_usage_count():
    return jsonify({"count": get_usage_count()})


@app.route("/api/free/status", methods=["POST"])
@limiter.limit("60 per minute")
def api_free_status():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "入力が不正です"}), 400
    site_account = current_site_user()
    try:
        _process_invitation_vip_trials()
        result = free_usage_manager.status(
            real_ip(), data.get("fingerprint", ""),
            allow_month_end_unlimited=bool(site_account),
        )
    except IdentityConflict as exc:
        return jsonify({"error": str(exc), "code": "identity_conflict"}), 409
    except FreeUsageError as exc:
        return jsonify({"error": str(exc), "code": "identity_invalid"}), 400
    except sqlite3.Error:
        return jsonify({"error": "利用回数を確認できませんでした。"}), 503
    site_account = current_site_user()
    result["trial"] = {
        "logged_in": bool(site_account),
        "is_vip": bool(site_account and site_account.get("is_vip")),
        "claimed": bool(site_account and site_account.get("trial_vip_claimed_at")),
        "offer_active": bool(site_account and site_account.get("trial_offer_active")),
        "offer_expires_at": site_account.get("trial_offer_expires_at") if site_account else None,
        "uses_remaining": int(site_account.get("trial_vip_uses_remaining") or 0) if site_account else 0,
        "vip_expires_at": site_account.get("vip_expires_at") if site_account else None,
    }
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/free/invitation", methods=["POST"])
@limiter.limit("10 per minute")
def api_free_invitation():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "入力が不正です"}), 400
    try:
        result = free_usage_manager.invitation_link(
            real_ip(), data.get("fingerprint", ""), PUBLIC_BASE_URL,
            allow_month_end_unlimited=bool(current_site_user()),
        )
    except IdentityConflict as exc:
        return jsonify({"error": str(exc), "code": "identity_conflict"}), 409
    except FreeUsageError as exc:
        return jsonify({"error": str(exc)}), 400
    except sqlite3.Error:
        return jsonify({"error": "招待リンクを発行できませんでした。"}), 503
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/free/invitation/redeem", methods=["POST"])
@limiter.limit("10 per minute")
def api_free_invitation_redeem():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "入力が不正です"}), 400
    try:
        result = free_usage_manager.redeem_invitation(
            data.get("code", ""), real_ip(), data.get("fingerprint", ""),
            allow_month_end_unlimited=bool(current_site_user()),
        )
    except IdentityConflict as exc:
        return jsonify({"error": str(exc), "code": "identity_conflict"}), 409
    except InvitationError as exc:
        return jsonify({"error": str(exc), "code": "invitation_rejected"}), 409
    except FreeUsageError as exc:
        return jsonify({"error": str(exc)}), 400
    except sqlite3.Error:
        return jsonify({"error": "招待報酬を処理できませんでした。"}), 503
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/admin/operations/<operation_id>")
@limiter.limit("60 per minute")
@admin_api_required
def api_admin_operation(operation_id):
    if not OPERATION_ID_RE.fullmatch(operation_id):
        return jsonify({"error": "受付IDの形式が不正です"}), 400
    record = _load_operation(operation_id)
    if not record:
        return jsonify({"error": "not found"}), 404
    result = _operation_public(record)
    if record.get("recovery_status") in {"admin_reissued", "client_code_issued"}:
        try:
            codes = json.loads(
                _decrypt_snapshot(record.get("reissue_result"), operation_id).decode()
            )
            result["recovery_transfer_code"] = codes.get("tc")
            result["recovery_auth_code"] = codes.get("ac")
        except Exception:
            result["recovery_transfer_code"] = None
            result["recovery_auth_code"] = None
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/admin/operations/<operation_id>/reissue", methods=["POST"])
@limiter.limit("5 per minute")
@admin_api_required
def api_admin_operation_reissue(operation_id):
    if not request.is_json:
        return jsonify({"error": "JSONリクエストが必要です"}), 415
    if not _valid_admin_csrf():
        return jsonify({"error": "CSRF検証に失敗しました"}), 403
    if not OPERATION_ID_RE.fullmatch(operation_id):
        return jsonify({"error": "受付IDの形式が不正です"}), 400
    with _operation_db_connect() as conn:
        cur = conn.execute(
            """UPDATE operation_records SET recovery_status='issuing',updated_at=?
               WHERE operation_id=? AND snapshot IS NOT NULL
                 AND recovery_status IN ('snapshot_saved','reissue_failed')
                 AND status='error' AND created_at>=?""",
            (time.time(), operation_id, time.time() - OPERATION_RETENTION),
        )
    if cur.rowcount != 1:
        return jsonify({"error": "再発行できない状態です"}), 409
    executor.submit(run_admin_reissue, operation_id)
    return jsonify({"status": "issuing"}), 202


@app.route("/api/char_dict")
@limiter.limit("30 per minute")
def api_char_dict():
    try:
        char_dict = {}
        for character in get_character_metadata().get("characters", []):
            if not character.get("selectable", True):
                continue
            display_id = int(character["id"])
            number = str(display_id).zfill(3)
            for name in character.get("names", []):
                if name:
                    char_dict[str(name)] = number
        return jsonify(char_dict)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vip_legend_metadata")
@limiter.limit("20 per minute")
def api_vip_legend_metadata():
    if not site_vip_confirmed():
        return jsonify({"error": "VIP限定機能です"}), 403
    try:
        return jsonify(get_legend_metadata())
    except Exception as e:
        print(f"[ERROR] vip legend metadata: {e}")
        return jsonify({"error": "ステージ名データを読み込めませんでした"}), 500


@app.route("/api/character_metadata")
@limiter.limit("10 per minute")
def api_character_metadata():
    try:
        metadata = dict(get_character_metadata())
        metadata["game_version"] = core.GameVersion(get_target_game_version_number()).to_string()
        return jsonify(metadata)
    except Exception as e:
        print(f"[ERROR] character metadata: {e}")
        return jsonify({"error": "キャラ名データを読み込めませんでした"}), 500


@app.route("/api/vip_ototo_metadata")
@limiter.limit("20 per minute")
def api_vip_ototo_metadata():
    if not site_vip_confirmed():
        return jsonify({"error": "VIP限定機能です"}), 403
    try:
        return jsonify(get_ototo_metadata())
    except Exception as e:
        print(f"[ERROR] vip ototo metadata: {e}")
        return jsonify({"error": "オトート定義を読み込めませんでした"}), 500




@app.after_request
def inject_infra_credit(response):
    """HTMLレスポンスの </body> 直前にクレジットフッターを自動挿入する（テンプレート編集不要）。"""
    try:
        credit_html = globals().get("INFRA_CREDIT_HTML", "")
        if (
            credit_html
            and response.status_code == 200
            and response.content_type
            and response.content_type.startswith("text/html")
            and not response.direct_passthrough
        ):
            body = response.get_data(as_text=True)
            if "</body>" in body and 'id="infra-credit"' not in body:
                response.set_data(body.replace("</body>", credit_html + "\n</body>", 1))
    except Exception as e:
        print(f"[ERROR] inject_infra_credit: {e}")
    return response


# =====================
# Minecraftテーマ管理（選択内容は各ブラウザの localStorage に保存）
# =====================
THEME_VIDEO_DIR = os.path.join(base_dir, "static", "videos")
THEME_ALLOWED_VIDEO_EXT = {".mp4", ".webm", ".ogg", ".mov"}

def selected_ui_layout():
    value = request.cookies.get("catps_layout", "classic")
    return value if value in {"classic", "simple", "dark", "eva", "eva-amber"} else "classic"


def ui_page_template(classic_name):
    if selected_ui_layout() in {"simple", "dark", "eva", "eva-amber"}:
        stem, extension = os.path.splitext(classic_name)
        return stem + "_simple" + extension
    return classic_name

def _safe_theme_filename(name):
    name = os.path.basename(str(name or "").strip())
    if not name or name != str(name).strip() or os.path.splitext(name)[1].lower() not in THEME_ALLOWED_VIDEO_EXT:
        return None
    return name

def _theme_videos():
    os.makedirs(THEME_VIDEO_DIR, exist_ok=True)
    return sorted(f for f in os.listdir(THEME_VIDEO_DIR) if _safe_theme_filename(f) and os.path.isfile(os.path.join(THEME_VIDEO_DIR, f)))

@app.context_processor
def _theme_context():
    default_name = "minecraft_bg.mp4"
    if not os.path.isfile(os.path.join(THEME_VIDEO_DIR, default_name)):
        videos = _theme_videos()
        default_name = videos[0] if videos else ""
    return {
        "theme_video_url": url_for("static", filename="videos/" + default_name) if default_name else "",
        "theme_layout": selected_ui_layout(),
        "ui_language": "en" if request.cookies.get("catps_lang") == "en" else "ja",
    }

@app.get("/api/theme/videos")
@limiter.limit("120 per minute")
def theme_video_list():
    videos = _theme_videos()
    return jsonify({"videos": [{"name": name, "url": url_for("static", filename="videos/" + name)} for name in videos]})

# BGM is opt-in in each browser. Only files deployed in static/bgm are listed.
THEME_BGM_DIR = os.path.join(base_dir, "static", "bgm")
THEME_ALLOWED_BGM_EXT = {".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac"}


def _bgm_tracks():
    if not os.path.isdir(THEME_BGM_DIR):
        return []
    tracks = []
    for name in sorted(os.listdir(THEME_BGM_DIR), key=str.casefold):
        if (name.startswith(".") or "/" in name or "\\" in name
                or os.path.splitext(name)[1].lower() not in THEME_ALLOWED_BGM_EXT):
            continue
        path = os.path.join(THEME_BGM_DIR, name)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        tracks.append({
            "name": name,
            "title": os.path.splitext(name)[0],
            "url": url_for("static", filename="bgm/" + name),
        })
    return tracks


@app.get("/api/theme/bgm")
@limiter.limit("120 per minute")
def theme_bgm_list():
    response = jsonify({"tracks": _bgm_tracks()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/theme-settings", methods=["GET", "POST"])
@app.route("/admin/theme-manager", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def theme_manager():
    # 背景の選択自体はブラウザ側に保存するので、サイトアカウントなしでも利用可能。
    message = ""
    error = ""
    if request.method == "POST":
        # 動画ファイルの追加だけはサーバーへ保存され、全利用者の選択肢に追加される。
        f = request.files.get("video")
        if not f or not f.filename:
            error = "動画ファイルを選択してください。"
        else:
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in THEME_ALLOWED_VIDEO_EXT:
                error = "mp4 / webm / ogg / mov のみ対応しています。"
            else:
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(f.filename)).strip("._") or ("background" + ext)
                f.save(os.path.join(THEME_VIDEO_DIR, safe))
                message = f"「{safe}」を背景候補に追加しました。"
    return render_template("theme_manager.html", videos=_theme_videos(), message=message, error=error)

# =====================
# ページルート（/ と /vip）
# =====================
@app.route("/")
@limiter.limit("5 per second")
def index():
    user = current_site_user()
    response = redirect("/vip" if user and user.get("is_vip") else "/vip-guide")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    return response


@app.route("/admin/panel")
@limiter.limit("20 per minute")
def admin_panel():
    if not current_user():
        return redirect("/auth/login?next=/admin/panel")
    if not is_admin_user():
        return app.make_response(("このDiscordアカウントは管理者に登録されていません。", 403))
    operation_id = request.args.get("id", "").strip()
    record = None
    search_error = None
    if operation_id:
        if not OPERATION_ID_RE.fullmatch(operation_id):
            search_error = "IDは30桁の英数字で入力してください。"
        else:
            loaded = _load_operation(operation_id)
            if loaded:
                record = _operation_public(loaded)
            else:
                search_error = "そのIDは存在しません。"
    response = app.make_response(render_template(
        "admin_panel.html", user=current_user(), operation=record,
        operation_id=operation_id, search_error=search_error,
        csrf_token=_admin_csrf_token(),
        vip_overview=account_store.admin_overview(),
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/vip")
@limiter.limit("5 per second")
def vip_page():
    user = current_site_user()
    if not user:
        return redirect("/account/login?next=/vip")
    if not user.get("is_vip"):
        return redirect("/vip-guide")
    response = app.make_response(render_template(
        ui_page_template("vip.html"),
        user=user, discord_user=current_user(), discord_is_admin=is_admin_user()
    ))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    return response


@app.route("/vip-guide")
@limiter.limit("10 per minute")
def vip_guide_page():
    """サイトアカウント方式の公開VIP案内・購入導線ページ。"""
    user = current_site_user()
    response = app.make_response(render_template(
        "vip_guide.html",
        user=user,
        discord_user=current_user(),
        discord_is_admin=is_admin_user(),
        price_label=VIP_PRICE_LABEL,
        plan_label=VIP_PLAN_LABEL,
        plans=account_store.vip_plans(),
        vip_stats=account_store.public_vip_stats(),
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


register_access_guard(app, account_store, real_ip=real_ip, limiter=limiter)

# 実行・APIキー発行・サービス設定はDBのVIP権限で制限する。
# 既に開始した処理の結果取得は、従来のジョブ所有者/トークン検証を維持する。
# 体験VIPは実行開始時に残数を予約するため、結果取得まで再VIP判定すると取得不能になる。
VIP_SERVICE_ENDPOINTS = {
    "api_run_daiko", "api_run_create", "api_run_clone", "api_get_key",
    "api_char_dict", "api_vip_legend_metadata", "api_character_metadata", "api_vip_ototo_metadata",
    "api_free_status", "api_free_invitation", "api_free_invitation_redeem",
    "theme_manager",
}


def vip_required_response():
    response = jsonify({
        "error": "このサービスはVIPユーザー専用です。VIP契約中のサイトアカウントでログインしてください。",
        "code": "vip_required",
        "login_url": "/account/login?next=/vip",
        "purchase_url": "/vip-guide",
    })
    response.status_code = 403
    response.headers["Cache-Control"] = "private, no-store"
    return response


def enforce_vip_service_access():
    if request.endpoint in VIP_SERVICE_ENDPOINTS and not site_vip_confirmed():
        if request.endpoint == "theme_manager" and request.method in {"GET", "HEAD"}:
            return redirect("/vip-guide" if current_site_user() else "/account/login?next=/vip")
        return vip_required_response()
    return None


app.before_request(enforce_vip_service_access)

register_account_routes(
    app,
    account_store,
    current_discord_user=current_user,
    is_admin_user=is_admin_user,
    real_ip=real_ip,
    limiter=limiter,
    static_dir=os.path.join(BASE_DIR, "static"),
    free_identity_resolver=free_usage_manager.identity_account_id,
)


@app.route("/api/game_update_status")
@limiter.limit("20 per minute")
def api_game_update_status():
    return jsonify(get_game_update_status())


# wsgi:app（Render）と直接起動の両方で、起動時から自動更新を開始。
_start_game_auto_updates()


if __name__ == "__main__":
    print("=" * 50)
    print("CatProxyService - Web版")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5001, debug=True)
