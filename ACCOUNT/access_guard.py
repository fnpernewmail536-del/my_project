"""Site-wide IP/fingerprint GBAN enforcement."""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlsplit

from flask import jsonify, make_response, redirect, render_template, request, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ACCOUNT.account_store import AccountError, AccountStore


GBAN_REDIRECT_URL = "https://discord.gg/zgngnSfbUE"
_SESSION_FINGERPRINT = "access_fingerprint"
_ACCESS_TOKEN_SALT = "catps-access-check-v2"
_ACCESS_TOKEN_MAX_AGE = 10 * 60


def _safe_next(raw: object) -> str:
    value = str(raw or "").strip()
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith("/"):
        return "/"
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _redirect_to_blacklist():
    response = redirect(GBAN_REDIRECT_URL, code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def register_access_guard(app, store: AccountStore, real_ip: Callable[[], str], limiter) -> None:
    """Require a signed fingerprint session and redirect every GBAN match.

    The access-check token is signed and time-limited instead of being stored as a
    single session nonce. This prevents harmless parallel requests (for example a
    favicon request) from replacing the nonce between rendering access_check.html
    and POSTing /api/access/identify.
    """

    signer = URLSafeTimedSerializer(app.secret_key, salt=_ACCESS_TOKEN_SALT)

    @app.post("/api/access/identify")
    @limiter.limit("20 per minute")
    def access_identify():
        try:
            if store.check_global_ban(real_ip())["blocked"]:
                session.pop(_SESSION_FINGERPRINT, None)
                return _redirect_to_blacklist()
        except AccountError:
            pass

        supplied = str(request.form.get("nonce") or "").strip()
        if not supplied:
            return jsonify({"error": "アクセス確認の有効期限が切れました。再読み込みしてください。"}), 403
        try:
            payload = signer.loads(supplied, max_age=_ACCESS_TOKEN_MAX_AGE)
            if not isinstance(payload, dict) or payload.get("purpose") != "access-check":
                raise BadSignature("invalid access token")
        except (SignatureExpired, BadSignature):
            return jsonify({"error": "アクセス確認の有効期限が切れました。再読み込みしてください。"}), 403

        fingerprint = str(request.form.get("fingerprint") or "").strip().lower()
        if not fingerprint:
            return jsonify({"error": "ブラウザ情報を確認できませんでした。ページを再読み込みしてください。"}), 400

        try:
            result = store.check_global_ban(real_ip(), fingerprint)
        except AccountError as exc:
            return jsonify({"error": str(exc)}), 400
        if result["blocked"]:
            session.pop(_SESSION_FINGERPRINT, None)
            return _redirect_to_blacklist()

        session[_SESSION_FINGERPRINT] = fingerprint
        session.permanent = True
        return redirect(_safe_next(request.form.get("next")), code=303)

    @app.before_request
    def enforce_global_ban():
        path = request.path
        # The fingerprint collector itself must run before verification.
        if path == "/api/access/identify":
            return None

        fingerprint = str(session.get(_SESSION_FINGERPRINT) or "").strip().lower()
        try:
            result = store.check_global_ban(real_ip(), fingerprint or None)
        except AccountError:
            result = {"blocked": False}
            fingerprint = ""
        if result["blocked"]:
            session.pop(_SESSION_FINGERPRINT, None)
            return _redirect_to_blacklist()
        if fingerprint:
            return None

        if request.method in {"GET", "HEAD"} and request.accept_mimetypes.accept_html:
            access_token = signer.dumps({"purpose": "access-check"})
            response = make_response(render_template(
                "access_check.html",
                nonce=access_token,
                next_url=_safe_next(request.full_path.rstrip("?")),
            ))
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            return response

        response = jsonify({
            "error": "アクセス確認が必要です。最初にブラウザでサイトを開いてください。",
            "verification_url": "/",
        })
        response.status_code = 428
        response.headers["Cache-Control"] = "no-store"
        return response
