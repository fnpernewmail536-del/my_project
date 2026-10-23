"""Authenticated Flask chat; jobs use the site's existing admission and recovery."""
import copy
from pathlib import Path
import secrets
import threading
import time
import re
import unicodedata

from flask import Blueprint, jsonify, redirect, request, send_from_directory, session
from .conditional_engine import compile_conditional
from .details import compile_details, has_details
from .engine import Engine, resolve_maximums
from .interpreter import interpret_request
from .parser import ParseError
from .confirmation import is_confirmation_reply


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

    def run_render_chat_create(*args, chat_execution, **kwargs):
        try:
            engine.detail_context.compiled = chat_execution['compiled']
            engine.detail_context.backup = None
            engine.detail_context.job_id = args[0]
            return module.run_job_create(*args, **kwargs)
        finally:
            engine.detail_context.compiled = None
            engine.detail_context.backup = None
            engine.detail_context.job_id = None

    module.run_render_chat_create = run_render_chat_create

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
        response.headers['X-CPS-Chat-Version'] = '2'
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
            drafts[key] = {'draft': None, 'receipts': {}, 'updated': now,
                           'settings': {'mode':'daiko', 'account_type':'new', 'count':1}}
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
            'requires_confirmation': True, 'message': message,
            'settings':plan.get('_settings', {}),
            'credentials':{'transfer_code':bool(plan.get('transfer_code')), 'auth_code':bool(plan.get('auth_code'))},
            'interpretation':{'review_items':sum(not x['key'].startswith('chat_') for x in plan.get('labels', [])),
                              'needs_review':len(plan.get('unresolved', [])),
                              'rewrites':len(plan.get('inferences', []))}}

    def configure(plan, settings):
        plan['_settings'] = copy.deepcopy(settings)
        plan['labels'] = [x for x in plan.get('labels', []) if not x['key'].startswith('chat_')]
        if settings['mode'] == 'create':
            plan.pop('transfer_code', None)
            plan.pop('auth_code', None)
            plan['missing'] = [x for x in plan.get('missing', []) if x not in ('引き継ぎコード','認証番号','代行内容')]
            name = {'new':'初期アカウント','beginner':'初心者','intermediate':'中級者','advanced':'上級者'}[settings['account_type']]
            plan['labels'].insert(0, {'key':'chat_create', 'label':f'新規作成：{name} ×{settings["count"]}個'})
        else:
            plan['missing'] = [x for x in plan.get('missing', []) if x not in ('引き継ぎコード','認証番号')]
            plan['missing'] += [label for key,label in [('transfer_code','引き継ぎコード'),('auth_code','認証番号')] if not plan.get(key)]
            if not plan['labels'] and '代行内容' not in plan['missing']:
                plan['missing'].append('代行内容')
        plan['ready'] = not plan['missing'] and not plan.get('blockers')
        plan['confirmation_id'] = secrets.token_hex(32)
        plan['_operation_id'] = secrets.token_hex(15)
        return plan

    def job(jid):
        with module.jobs_lock:
            value = copy.deepcopy(module.jobs.get(jid))
        value = value or module._load_job(jid)
        if not value or not module.session_owns_job(jid, value):
            return None
        # Do not return hashes, quota internals, or another user's records.
        keys = ('status', 'log', 'error', 'applied', 'transfer_code', 'auth_code',
                'recovery_transfer_code', 'recovery_auth_code', 'admin_recovery_required',
                'transfer_received', 'operation_id', 'created_at')
        accounts = [{'tc':x['tc'], 'ac':x['ac']} for x in value.get('accounts', [])
                    if isinstance(x,dict) and x.get('tc') and x.get('ac')]
        return {'job_id': jid, 'operation_id':value.get('operation_id',jid), 'labels':[{'label':x} for x in value.get('applied', [])],
                'accounts':accounts, **{k: value[k] for k in keys if k in value}}

    def history():
        return [value for jid in reversed(session.get('job_ids', [])[-20:]) if (value := job(jid))]

    @bp.get('/')
    def index():
        return send_from_directory(root / 'static', 'index.html')

    @bp.get('/<asset>')
    def asset(asset):
        if asset not in ('app.js', 'style.css', 'theme.js'):
            return jsonify(error='not found'), 404
        return send_from_directory(root / 'static', asset)

    @bp.get('/api/bootstrap')
    def bootstrap():
        with lock:
            session.setdefault('chat_csrf', secrets.token_hex(32))
            draft = state()['draft']
            return jsonify(csrf=session['chat_csrf'], engine=engine.status(), jobs=history(),
                           settings=state()['settings'], draft=review(draft) if draft else None)

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

    @bp.post('/api/settings')
    def settings():
        data = request.get_json(silent=True)
        if not isinstance(data,dict) or set(data) != {'mode','account_type','count'}:
            return jsonify(error='作成設定が不正です。'), 400
        if data['mode'] not in ('daiko','create') or data['account_type'] not in ('new','beginner','intermediate','advanced'):
            return jsonify(error='モード・アカウント種別を選び直してください。'), 400
        if type(data['count']) is not int or not 1 <= data['count'] <= min(5, getattr(module,'MAX_COUNT',5)):
            return jsonify(error='作成数は1〜5個です。'), 400
        with lock:
            st = state()
            changed_mode = st['settings']['mode'] != data['mode']
            st['settings'] = copy.deepcopy(data)
            if changed_mode:
                # Changing purpose clears credentials and old editing targets.
                st['draft'] = None
            if st['draft'] is None:
                from .parser import parse_request
                st['draft'] = parse_request('お願いします')
            configure(st['draft'], st['settings'])
            return jsonify(review(st['draft']))

    @bp.post('/api/credentials')
    def credentials():
        data = request.get_json(silent=True)
        if not isinstance(data,dict) or set(data) != {'transfer_code','auth_code'}:
            return jsonify(error='引き継ぎ情報の入力が不正です。'), 400
        codes = {}
        for key,pattern in [('transfer_code',r'[0-9a-f]{9}'),('auth_code',r'[0-9]{4}')]:
            if not isinstance(data[key],str):
                return jsonify(error='コードは文字列で入力してください。'), 400
            value = unicodedata.normalize('NFKC',data[key]).strip().lower()
            if value and not re.fullmatch(pattern,value):
                return jsonify(error='引き継ぎコードは9桁の英数字（0〜9・a〜f）、認証番号は4桁の数字です。'), 400
            codes[key] = value
        with lock:
            st = state()
            if st['settings']['mode'] != 'daiko':
                return jsonify(error='新規作成では引き継ぎ情報は不要です。'), 400
            if st['draft'] is None:
                from .parser import parse_request
                st['draft'] = parse_request('お願いします')
            st['draft'].update(codes)
            configure(st['draft'], st['settings'])
            return jsonify(review(st['draft']))

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
            approval = is_confirmation_reply(payload.get('message')) and bool(payload.get('confirmation_id'))
            if request.path.endswith('/confirm') or approval:
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
                            'operation_id': plan['_operation_id'], 'api_key': module.generate_api_key(),
                            'custom_playtime': plan.get('custom_playtime', '')}
                    settings = plan['_settings']
                    if settings['mode'] == 'create':
                        if plan.get('backup_requested'):
                            raise ParseError('新規作成に「代行前バックアップ」は指定できません。')
                        data.update(account_type=settings['account_type'], count=settings['count'])
                    else:
                        data.update(transfer_code=plan['transfer_code'], auth_code=plan['auth_code'])
                    for key in ('future_score_settings', 'dojo_score_settings'):
                        if plan.get(key) is not None:
                            data[key] = plan[key]
                    # Internal request context data cannot be supplied through JSON.
                    request.environ['catproxy.chat_execution'] = {'compiled': compiled}
                    request._cached_json = (data, data)
                    result = app.make_response(module.api_run_create() if settings['mode']=='create' else module.api_run_daiko())
                except ParseError as exc:
                    return jsonify(error=str(exc)), 400
                except Exception:
                    return jsonify(error='定義確認または受付に失敗しました。履歴を確認してください。自動再実行はしていません。'), 503
                finally:
                    request.environ.pop('catproxy.chat_execution', None)
                if result.status_code >= 400:
                    return result
                started = result.get_json()
                response = {'kind': 'started', 'job_id': started['job_id'], 'operation_id': started.get('operation_id', started['job_id']),
                            'labels': plan['labels'], 'message': '確認した内容で代行を開始しました。'}
                st['draft'] = None
            else:
                text = payload.get('message')
                if not isinstance(text, str) or not 0 < len(text) <= 16000:
                    return jsonify(error='依頼は1〜16000文字で入力してください。'), 400
                if is_confirmation_reply(text):
                    if not st['draft']:
                        return jsonify(error='先に依頼内容を送ってください。'), 400
                    response = review(st['draft'])
                else:
                    try:
                        if st['settings']['mode']=='create' and re.search(r'引き?継ぎ|引継ぎ|認証|transfer|auth',text,re.I):
                            raise ParseError('新規作成では引き継ぎ情報は不要です。追加したい内容だけ送ってください。')
                        plan = interpret_request(text, st['draft'], engine.character_metadata)
                    except (ParseError, RuntimeError) as exc:
                        return jsonify(error=str(exc)), 400
                    configure(plan, st['settings'])
                    st['draft'] = plan
                    response = review(plan)
            if len(st['receipts']) >= 100:
                st['receipts'].pop(next(iter(st['receipts'])))
            st['receipts'][rid] = response
            return jsonify(response)

    app.register_blueprint(bp)
