"""Authenticated Flask chat; jobs use the site's existing admission and recovery."""
import copy
from pathlib import Path
import secrets
import threading
import time

from flask import Blueprint, jsonify, redirect, request, send_from_directory, session
from .conditional_engine import compile_conditional
from .details import compile_details, has_details
from .engine import Engine, resolve_maximums
from .interpreter import interpret_request
from .parser import ParseError


def install_chat(module):
    app = module.app
    bp = Blueprint('chat', __name__, url_prefix='/chat')
    root = Path(__file__).resolve().parent
    lock = threading.RLock()
    drafts = {}
    ttl = 1200
    # Attach only the editing adapter. Never load a second application, replace
    # its signing key, disable notifications, or create a local HTTP server.
    engine = Engine(root.parent, root / 'data')
    engine.module = module
    engine.install_detail_adapter(module)

    def run_render_chat_job(*args, chat_execution, **kwargs):
        try:
            engine.detail_context.compiled = chat_execution['compiled']
            engine.detail_context.backup = None
            engine.detail_context.job_id = args[0]
            return module.run_job_daiko(*args, **kwargs)
        finally:
            engine.detail_context.compiled = None
            engine.detail_context.backup = None
            engine.detail_context.job_id = None

    module.run_render_chat_job = run_render_chat_job

    @bp.before_request
    def access():
        if not module.site_vip_confirmed():
            if request.path.rstrip('/') == '/chat':
                return redirect('/account/login?next=/chat/' if not module.current_site_user() else '/vip-guide')
            return module.vip_required_response()
        if request.method == 'POST':
            if request.content_length is None or request.content_length > 65536:
                return jsonify(error='送信内容が大きすぎます。'), 413
            expected = session.get('chat_csrf', '')
            supplied = request.headers.get('X-CSRF-Token', '')
            if not expected or not secrets.compare_digest(expected, supplied):
                return jsonify(error='ページを再読み込みしてから送信してください。'), 403
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return jsonify(error='同じサイトから送信してください。'), 403

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Vary'] = 'Cookie'
        return response

    def state():
        now = time.monotonic()
        for key in list(drafts):
            if now - drafts[key]['updated'] > ttl:
                del drafts[key]
        sid = session.get('chat_sid')
        if not sid:
            sid = secrets.token_hex(32)
            session['chat_sid'] = sid
        key = (str(session.get('site_account_id')), sid)
        if key not in drafts:
            if len(drafts) >= 256:
                oldest = min(drafts, key=lambda k: drafts[k]['updated'])
                del drafts[oldest]
            drafts[key] = {'draft': None, 'receipts': {}, 'updated': now}
        drafts[key]['updated'] = now
        return drafts[key]

    def review(plan):
        message = '数量と対象を確認し、よければ「確認して実行」を押してください。'
        if plan['missing']:
            message = 'あと ' + '・'.join(plan['missing']) + ' を項目名付きで送ってください。'
        if plan.get('blockers'):
            message = '判断できない項目を確認してください。\n' + '\n'.join(plan['blockers'])
        return {k: plan.get(k, []) for k in ('labels', 'missing', 'blockers', 'unresolved', 'inferences', 'quoted_prices')} | {
            'kind': 'draft', 'ready': plan['ready'], 'confirmation_id': plan['confirmation_id'],
            'requires_confirmation': True, 'message': message}

    def job(jid):
        with module.jobs_lock:
            value = copy.deepcopy(module.jobs.get(jid))
        value = value or module._load_job(jid)
        if not value or not module.session_owns_job(jid, value):
            return None
        # Do not return hashes, quota internals, or another user's records.
        keys = ('status', 'log', 'error', 'applied', 'transfer_code', 'auth_code',
                'recovery_transfer_code', 'recovery_auth_code', 'admin_recovery_required',
                'transfer_received', 'operation_id')
        return {'job_id': jid, **{k: value[k] for k in keys if k in value}}

    def history():
        return [value for jid in reversed(session.get('job_ids', [])[-20:]) if (value := job(jid))]

    @bp.get('/')
    def index():
        return send_from_directory(root / 'static', 'index.html')

    @bp.get('/<asset>')
    def asset(asset):
        if asset not in ('app.js', 'style.css'):
            return jsonify(error='not found'), 404
        return send_from_directory(root / 'static', asset)

    @bp.get('/api/bootstrap')
    def bootstrap():
        with lock:
            session.setdefault('chat_csrf', secrets.token_hex(32))
            draft = state()['draft']
            return jsonify(csrf=session['chat_csrf'], engine=engine.status(), jobs=history(), draft=review(draft) if draft else None)

    @bp.get('/api/status')
    def status():
        return jsonify(engine=engine.status())

    @bp.get('/api/menu')
    def menu():
        from .menus import CATALOGUE
        return jsonify(CATALOGUE)

    @bp.get('/api/jobs')
    def jobs():
        return jsonify(jobs=history())

    @bp.get('/api/job/<jid>')
    def get_job(jid):
        value = job(jid)
        return (jsonify(value), 200) if value else (jsonify(error='処理が見つかりません。'), 404)

    @bp.post('/api/reset')
    def reset():
        with lock:
            state()['draft'] = None
        return jsonify(message='新しい依頼を開始しました。実行中の処理は継続します。')

    @bp.post('/api/chat')
    @bp.post('/api/confirm')
    @module.limiter.limit('30 per minute')
    def chat():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='入力が不正です。'), 400
        rid = payload.get('request_id')
        if not isinstance(rid, str) or not 8 <= len(rid) <= 80:
            return jsonify(error='送信IDが不正です。'), 400
        with lock:
            st = state()
            if rid in st['receipts']:
                return jsonify(st['receipts'][rid])
            if request.path.endswith('/confirm'):
                plan = st['draft']
                confirmation = payload.get('confirmation_id')
                if not plan or not isinstance(confirmation, str) or not secrets.compare_digest(confirmation, plan['confirmation_id']):
                    return jsonify(error='確認内容が変わったか、有効期限が切れました。依頼から入力し直してください。'), 409
                if not plan['ready'] or plan.get('blockers'):
                    return jsonify(error='不足・未解釈の項目を解消してください。'), 400
                try:
                    compiled = ({**compile_conditional(module, plan), '_conditional': True} if plan.get('conditional_mode')
                                else compile_details(module, plan) if has_details(plan) else None)
                    selected, amounts = resolve_maximums(plan)
                    data = {'selected': selected, 'char_list': [], **amounts,
                            'transfer_code': plan['transfer_code'], 'auth_code': plan['auth_code'],
                            'operation_id': plan['_operation_id'], 'api_key': module.generate_api_key(),
                            'custom_playtime': plan.get('custom_playtime', '')}
                    for key in ('future_score_settings', 'dojo_score_settings'):
                        if plan.get(key) is not None:
                            data[key] = plan[key]
                    # Internal request context data cannot be supplied through JSON.
                    request.environ['catproxy.chat_execution'] = {'compiled': compiled}
                    request._cached_json = (data, data)
                    result = app.make_response(module.api_run_daiko())
                except ParseError as exc:
                    return jsonify(error=str(exc)), 400
                except Exception:
                    return jsonify(error='定義確認または受付に失敗しました。履歴を確認してください。自動再実行はしていません。'), 503
                finally:
                    request.environ.pop('catproxy.chat_execution', None)
                if result.status_code >= 400:
                    return result
                started = result.get_json()
                response = {'kind': 'started', 'job_id': started['job_id'], 'operation_id': started['operation_id'],
                            'labels': plan['labels'], 'message': '確認した内容で代行を開始しました。'}
                st['draft'] = None
            else:
                text = payload.get('message')
                if not isinstance(text, str) or not 0 < len(text) <= 16000:
                    return jsonify(error='依頼は1〜16000文字で入力してください。'), 400
                from .confirmation import is_confirmation_reply
                if is_confirmation_reply(text):
                    if not st['draft']:
                        return jsonify(error='先に依頼内容を送ってください。'), 400
                    response = review(st['draft'])
                else:
                    try:
                        plan = interpret_request(text, st['draft'], engine.character_metadata)
                    except (ParseError, RuntimeError) as exc:
                        return jsonify(error=str(exc)), 400
                    plan['confirmation_id'] = secrets.token_hex(32)
                    plan['_operation_id'] = secrets.token_hex(15)
                    st['draft'] = plan
                    response = review(plan)
            if len(st['receipts']) >= 100:
                st['receipts'].pop(next(iter(st['receipts'])))
            st['receipts'][rid] = response
            return jsonify(response)

    app.register_blueprint(bp)
