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
import sys
import sqlite3
import functools as _ft
import urllib.request as _urllib_req
from datetime import timedelta
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask.sessions import SecureCookieSessionInterface

# --- モジュール検索パス設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# --- 内部モジュールインポート ---
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

TARGET_GAME_VERSION_NUMBER = 150501

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
# Discord OAuth2
# =====================
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
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
PLAYTIME_RE = re.compile(r'^(\d{1,4}):([0-5]?\d)$')
OPERATION_ID_RE = re.compile(r'^[A-Za-z0-9]{30}$')
ADMIN_USER = (os.getenv("ADMIN_USER") or os.getenv("Admin_USER", "")).strip()
ADMIN_SNAPSHOT_KEY = os.getenv("ADMIN_SNAPSHOT_KEY", "").strip()

def validate_transfer_auth_codes(data):
    tc = str(data.get("transfer_code", "")).strip()
    ac = str(data.get("auth_code", "")).strip()
    if not TRANSFER_CODE_RE.match(tc):
        return jsonify({"error": "引き継ぎコードは9桁の16進数（0-9,a-f）で入力してください"}), 400
    if not AUTH_CODE: