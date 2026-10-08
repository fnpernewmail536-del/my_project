from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_from_directory
import os
import re
import copy
import base64
import gzip
import json
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
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import os
import sys

# main13.py が置かれているディレクトリ (Paython) を検索パスの最優先に追加
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# --- 39行目の既存インポート ---
from ACCOUNT.access_guard import register_access_guard

import os
import sys

# main13.py があるディレクトリを Python のインポートパスに追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import os
import sys

# main13.py が置いてあるディレクトリを Python のモジュール検索パスに追加
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# --- ここより下に既存のインポートを記述 ---
from ACCOUNT.access_guard import register_access_guard

# この下から既存のインポートを記述
from ACCOUNT.access_guard import register_access_guard
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
MAX_WORKERS = 10             # ジョブ実行ワーカー数
MAX_INFLIGHT_JOBS = 30       # 同時に受け付ける未完了ジョブ数
JOB_TIMEOUT = 300            # 進捗が更新されないジョブのタイムアウト(秒)
JOB_ABSOLUTE_TIMEOUT = 600   # 進捗が続いていても待つ絶対上限(秒)
CLONE_JOB_TIMEOUT = 600      # 複製中の連続した認証・保存通信は最大10分待つ
CLONE_JOB_ABSOLUTE_TIMEOUT = 1800  # 複数コピーの連続通信を考慮して30分
MAX_CHAR_LIST = 300          # char_list の最大要素数
MAX_CHARACTER_SETTINGS = 1000  # レアリティ単位・全871体の名前選択に対応
MAX_COUNT = 5                # 作成/複製の最大個数
MAX_LEGEND_SELECTIONS = 5000 # レジェンド系の章/星/ステージ選択数上限

# にゃんこ大戦争 JP 15.5.1。通信・新規作成・複製・ゲームデータ参照で
# 別々の値を使うと、新キャラ枠や第四形態の判定が旧版へ戻るため一元管理する。
TARGET_GAME_VERSION_NUMBER = 150501

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
# 第2引数にローカル用URLを入れておく
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://127.0.0.1:5001/auth/callback")
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

TARGET_GAME_VERSION = core.GameVersion(TARGET_GAME_VERSION_NUMBER)
BCSFE_EDIT_LOCK = threading.RLock()

import os
from flask import Flask

# プロジェクトの根元（my_project）のパスを基準にする
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

app = Flask(
    __name__,
    static_folder=os.path.join(base_dir, 'static'),
    template_folder=os.path.join(base_dir, 'templates')
)

# SECRET_KEYの固定（起動ごとにランダム生成されないようにする）
app.secret_key = os.getenv("SECRET_KEY", "your-fixed-secret-key-12345")

# Render(HTTPS)用のセッションCookie設定
app.config.update(
    SESSION_COOKIE_SECURE=True,      # HTTPS通信時のみCookieを有効化
    SESSION_COOKIE_HTTPONLY=True,    # JSからの悪意あるアクセスを防止
    SESSION_COOKIE_SAMESITE='Lax',   # ページ移動・リダイレクト時もCookieを保持
)

# VIPのイベント詳細は、全選択時に数千ステージ分の指定を送る。
# 64KBでは正常な操作でも413になるため、既存機能が収まる上限へ更新する。
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

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
app.config["SESSION_COOKIE_NAME"] = "catps_session"
app.config["SESSION_COOKIE_PATH"] = "/"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://autocat.jp").strip().rstrip("/")


# =====================
# 実IP取得（Cloudflare 前提）
# =====================
def real_ip():
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
# 使用回数カウンター
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
        job.update(changes)
        snapshot = copy.deepcopy(job)
    _persist_job(job_id, snapshot)
    _settle_job_quota(snapshot)


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
                                "admin_recovery_required": True
                            })
                            _update_operation(
                                operation_id,
                                status="error",
                                error=job.get("error"),
                                recovery_status="snapshot_saved",
                            )
                        else:
                            _update_operation(
                                operation_id,
                                status="error",
                                error=job.get("error"),
                                recovery_status="not_available",
                            )

threading.Thread(target=cleanup_jobs, daemon=True).start()