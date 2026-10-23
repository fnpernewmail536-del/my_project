import ast
import copy
from pathlib import Path
from types import SimpleNamespace as NS
import secrets
import sqlite3
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch
from flask import Flask, jsonify, request, session
from local_chat.web import install_chat


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test-only-not-a-production-key'
        self.records = {}
        self.executor = Mock()
        self.vip = True
        self.m = NS(app=self.app, limiter=NS(limit=lambda *a, **k: lambda f: f),
                    site_vip_confirmed=lambda: self.vip,
                    current_site_user=lambda: {'id': 'u'},
                    vip_required_response=lambda: (jsonify(error='VIP required'), 403),
                    jobs_lock=threading.Lock(), jobs=self.records,
                    _load_job=lambda jid: self.records.get(jid),
                    session_owns_job=lambda jid, job: jid in session.get('job_ids', []),
                    get_character_metadata=lambda: None,
                    _apply_all_segments_unlocked=Mock(return_value=['original']),
                    generate_api_key=lambda: 'test-api-key', run_job_daiko=Mock(), run_job_create=Mock(), MAX_COUNT=5)
        # Execute the production admission function, including reservations,
        # native operation/job registration and the actual executor call.
        tree = ast.parse(Path('main13.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_run_daiko')
        fn.decorator_list = []
        self.env = dict(vars(self.m), request=request, session=session, jsonify=jsonify,
                        copy=copy, uuid=uuid, secrets=secrets, time=time, sqlite3=sqlite3,
                        validate_common_input=lambda d: None, validate_transfer_auth_codes=lambda d: None,
                        validate_and_consume_api_key=lambda k: k == 'test-api-key',
                        count_inflight_jobs=lambda: 0, MAX_INFLIGHT_JOBS=2,
                        safe_custom_amounts=lambda d: {k: v for k, v in d.items() if k.startswith('custom_')},
                        safe_custom_playtime=lambda d: d.get('custom_playtime', ''),
                        safe_character_settings=lambda d: [], compile_vip_form_extras=lambda d: {},
                        _new_job_access_token=lambda: ('token', 'hash'),
                        _safe_operation_id=lambda d: d['operation_id'],
                        _reserve_site_vip_job=Mock(return_value=('reserved', None)),
                        _create_operation=Mock(), _create_job=lambda jid, value: self.records.update({jid: value}),
                        _refund_unattached_quota=Mock(), _refund_unattached_vip_trial=Mock(),
                        _discard_unstarted_job=Mock(), _close_operation=Mock(),
                        _update_job=Mock(), _safe_error_message=lambda e: type(e).__name__,
                        register_job_to_session=lambda jid: session.update(job_ids=session.get('job_ids', [])+[jid]),
                        timing_profile=lambda *a: {}, executor=self.executor)
        self.env.update(safe_count=lambda d:int(d.get('count',1)),safe_account_type=lambda d:d.get('account_type','new'))
        for key in ('main_story_chapters', 'main_story_stages', 'event_stage_settings', 'vip_items',
                    'legend_stages', 'vip_facilities', 'vip_talent_orbs', 'special_stages',
                    'lineup_settings', 'ototo_settings', 'dojo_score_settings', 'future_score_settings'):
            self.env['safe_'+key] = lambda d: None
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'native-admission', 'exec'), self.env)
        self.m.api_run_daiko = self.env['api_run_daiko']
        create = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_run_create')
        create.decorator_list = []
        exec(compile(ast.Module(body=[create],type_ignores=[]),'native-create-admission','exec'),self.env)
        self.m.api_run_create = self.env['api_run_create']
        install_chat(self.m)
        self.env['run_render_chat_job'] = self.m.run_render_chat_job
        self.env['run_render_chat_create'] = self.m.run_render_chat_create
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s['site_account_id'] = 'u'
        self.csrf = self.client.get('/chat/api/bootstrap').json['csrf']

    def post(self, path, data, client=None):
        return (client or self.client).post('/chat/api/'+path, json=data, headers={'X-CSRF-Token': self.csrf})

    def draft(self, text='猫缶45000'):
        a = self.post('chat', {'message': text, 'request_id': secrets.token_hex(8)})
        self.assertEqual(a.status_code, 200, a.json)
        # Explicit credential labels are parsed without echoing their values.
        a = self.post('chat', {'message': '引き継ぎコード：123abc456\n認証番号：1234', 'request_id': secrets.token_hex(8)})
        self.assertEqual(a.status_code, 200, a.json)
        self.assertTrue(a.json['ready'], a.json)
        return a.json

    def test_vip_gate_every_surface(self):
        self.vip = False
        self.assertEqual(self.client.get('/chat/').status_code, 302)
        for path in ('api/bootstrap', 'api/menu', 'api/jobs', 'api/job/anything', 'app.js'):
            self.assertEqual(self.client.get('/chat/'+path).status_code, 403)
        self.assertEqual(self.post('chat', {'message': '猫缶1', 'request_id': 'test-req1'}).status_code, 403)

    def test_csrf_and_size_and_invalid_input(self):
        self.assertEqual(self.client.post('/chat/api/chat', json={}).status_code, 403)
        self.assertEqual(self.post('chat', {}).status_code, 400)
        self.assertEqual(self.post('chat', {'request_id': 'test-req1', 'message': 'x'*16001}).status_code, 400)

    def test_review_before_execution_and_stale_confirmation(self):
        p = self.draft()
        self.executor.submit.assert_not_called()
        r = self.post('confirm', {'confirmation_id': 'wrong', 'request_id': 'confirm-1'})
        self.assertEqual(r.status_code, 409)
        self.post('chat', {'message': '猫缶40000', 'request_id': 'changed-1'})
        self.assertEqual(self.post('confirm', {'confirmation_id': p['confirmation_id'], 'request_id': 'confirm-2'}).status_code, 409)

    def test_native_admission_reservation_and_idempotency(self):
        p = self.draft()
        payload = {'confirmation_id': p['confirmation_id'], 'request_id': 'confirm-1'}
        a = self.post('confirm', payload)
        self.assertEqual(a.status_code, 200, a.json)
        self.assertEqual(a.json['kind'], 'started')
        b = self.post('confirm', payload)
        self.assertEqual(a.json, b.json)
        self.executor.submit.assert_called_once()
        self.env['_reserve_site_vip_job'].assert_called_once()
        args, kwargs = self.executor.submit.call_args
        self.assertIs(args[0], self.m.run_render_chat_job)
        self.assertEqual(args[-1], 'VIP')
        self.assertIn('chat_execution', kwargs)
        self.assertEqual(self.client.get('/chat/api/job/'+a.json['job_id']).status_code, 200)

    def test_ownership_and_secret_projection(self):
        self.records['other'] = {'status': 'done', 'auth_code': 'hidden', 'job_access_hash': 'private'}
        self.assertEqual(self.client.get('/chat/api/job/other').status_code, 404)
        with self.client.session_transaction() as s:
            s['job_ids'] = ['other']
        value = self.client.get('/chat/api/job/other').json
        self.assertNotIn('job_access_hash', value)
        self.assertIn('no-store', self.client.get('/chat/api/bootstrap').headers['Cache-Control'])

    def test_new_resources_route_and_compile_before_transfer(self):
        p = self.draft('プラチナのかけらは8個。\nネコビタンAは164個。')
        with patch('local_chat.web.compile_conditional', side_effect=ValueError('missing metadata')):
            a = self.post('confirm', {'confirmation_id': p['confirmation_id'], 'request_id': 'confirm-1'})
        self.assertEqual(a.status_code, 503)
        self.executor.submit.assert_not_called()
        self.env['_reserve_site_vip_job'].assert_not_called()

    def test_thread_context_does_not_affect_normal_jobs(self):
        compiled = {'_conditional': True}
        def run(*args, **kwargs):
            return self.m._apply_all_segments_unlocked('save')
        self.m.run_job_daiko = run
        with patch('local_chat.engine.apply_conditional', return_value=['chat']) as apply:
            self.assertEqual(self.m.run_render_chat_job('jid', chat_execution={'compiled': compiled}), ['chat'])
            apply.assert_called_once()
        self.assertEqual(self.m._apply_all_segments_unlocked('save'), ['original'])

    def test_public_admission_to_native_save_roundtrip(self):
        from test_advanced_requests import fixture
        from bcsfe import core
        native, save = fixture()
        for key, value in vars(native).items():
            setattr(self.m, key, value)
        p = self.draft('プラチナのかけらは8個。\nトレジャーレーダーは143個。\nネコビタンAは164個。\nプレイ時間は2986時間42分。')
        r = self.post('confirm', {'confirmation_id': p['confirmation_id'], 'request_id': 'confirm-native'})
        self.assertEqual(r.status_code, 200, r.json)
        args, kwargs = self.executor.submit.call_args
        self.m.run_job_daiko = lambda *a, **k: self.m._apply_all_segments_unlocked(save)
        args[0](*args[1:], **kwargs)
        loaded = core.SaveFile(core.Data(save.to_data().data), cc=save.cc)
        self.assertEqual(loaded.platinum_shards, 8)
        self.assertEqual(loaded.battle_items.items[1].amount, 143)
        self.assertEqual(loaded.catamins, [164, 8, 8])
        self.assertEqual(loaded.officer_pass.play_time, core.PlayTime.from_hours_mins_secs(2986, 42, 0).frames)

    def test_reset_and_separate_browser(self):
        self.draft()
        other = self.app.test_client()
        self.assertIsNone(other.get('/chat/api/bootstrap').json['draft'])
        self.post('reset', {})
        self.assertIsNone(self.client.get('/chat/api/bootstrap').json['draft'])

    def test_assets_are_scoped_and_navigation_present(self):
        with self.client.get('/chat/') as response:
            self.assertIn(b'/chat/app.js', response.data)
        with self.client.get('/chat/app.js') as response:
            self.assertIn(b'fetch("/chat"+path', response.data)
        self.assertIn('/chat/', Path('HTML/vip.html').read_text())
        self.assertEqual(self.client.get('/chat/web.py').status_code, 404)

    def test_separate_credentials_are_not_echoed_and_invalidate_confirmation(self):
        p = self.draft()
        response = self.post('credentials', {'transfer_code':'abcdef123','auth_code':'0007'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertTrue(response.json['ready'])
        self.assertEqual(response.json['credentials'],{'transfer_code':True,'auth_code':True})
        self.assertNotIn('abcdef123',response.get_data(as_text=True))
        self.assertNotIn('0007',response.get_data(as_text=True))
        self.assertEqual(self.post('confirm',{'confirmation_id':p['confirmation_id'],'request_id':'stale-code'}).status_code,409)
        self.assertNotIn('abcdef123',str(self.records))

    def test_invalid_credentials_do_not_overwrite_valid_draft(self):
        p = self.draft()
        self.assertEqual(self.post('credentials',{'transfer_code':'notvalid!','auth_code':'1234'}).status_code,400)
        self.assertEqual(self.client.get('/chat/api/bootstrap').json['draft']['confirmation_id'],p['confirmation_id'])

    def test_new_account_creation_uses_native_quota_and_all_parameters(self):
        r=self.post('settings',{'mode':'create','account_type':'intermediate','count':3})
        self.assertTrue(r.json['ready'],r.json)
        self.assertEqual(r.json['missing'],[])
        self.assertIn('中級者 ×3個',r.json['labels'][0]['label'])
        confirm={'confirmation_id':r.json['confirmation_id'],'request_id':'create-confirm'}
        a=self.post('confirm',confirm)
        self.assertEqual(a.status_code,200,a.json)
        args,kwargs=self.executor.submit.call_args
        self.assertIs(args[0],self.m.run_render_chat_create)
        self.assertEqual(args[5],3)
        self.assertEqual(args[7],'intermediate')
        self.env['_reserve_site_vip_job'].assert_called_once()
        self.assertEqual(self.post('confirm',confirm).json,a.json)
        self.executor.submit.assert_called_once()

    def test_mode_switch_erases_credentials_and_invalidates_old_confirmation(self):
        p=self.draft()
        r=self.post('settings',{'mode':'create','account_type':'new','count':1})
        self.assertFalse(r.json['credentials']['transfer_code'])
        self.assertEqual(self.post('credentials',{'transfer_code':'abcdef123','auth_code':'1234'}).status_code,400)
        self.assertEqual(self.post('confirm',{'confirmation_id':p['confirmation_id'],'request_id':'stale-mode'}).status_code,409)
        r=self.post('settings',{'mode':'daiko','account_type':'new','count':1})
        self.assertFalse(r.json['ready'])
        self.assertIn('代行内容',r.json['missing'])

    def test_create_with_advanced_edits_and_no_credentials(self):
        from test_advanced_requests import fixture
        native,save=fixture()
        for key,value in vars(native).items():setattr(self.m,key,value)
        self.post('settings',{'mode':'create','account_type':'new','count':1})
        r=self.post('chat',{'message':'プラチナのかけらは8個。','request_id':'create-edit'})
        self.assertTrue(r.json['ready'],r.json)
        a=self.post('confirm',{'confirmation_id':r.json['confirmation_id'],'request_id':'create-execute'})
        self.assertEqual(a.status_code,200,a.json)
        args,kwargs=self.executor.submit.call_args
        self.m.run_job_create=lambda *a,**k:self.m._apply_all_segments_unlocked(save)
        args[0](*args[1:],**kwargs)
        from bcsfe import core
        loaded=core.SaveFile(core.Data(save.to_data().data),cc=save.cc)
        self.assertEqual(loaded.platinum_shards,8)

    def test_create_rejects_unknowns_count_and_credentials(self):
        for count in (0,6,True,'2'):
            self.assertEqual(self.post('settings',{'mode':'create','account_type':'new','count':count}).status_code,400)
        self.post('settings',{'mode':'create','account_type':'new','count':1})
        r=self.post('chat',{'message':'未定義のものをいい感じにして','request_id':'ambiguous-create'})
        self.assertFalse(r.json['ready'])
        self.assertEqual(self.post('confirm',{'confirmation_id':r.json['confirmation_id'],'request_id':'blocked-create'}).status_code,400)
        self.assertEqual(self.post('chat',{'message':'認証番号:1234','request_id':'unneeded-codes'}).status_code,400)
        self.executor.submit.assert_not_called()

    def test_all_created_accounts_projected_only_to_owner(self):
        self.records['created']={'status':'error','accounts':[{'tc':'test1','ac':'0001','private':'hidden'},{'tc':'test2','ac':'0002'}]}
        with self.client.session_transaction() as s:s['job_ids']=['created']
        r=self.client.get('/chat/api/job/created')
        self.assertEqual(len(r.json['accounts']),2)
        self.assertNotIn('private',str(r.json))
        self.assertEqual(self.app.test_client().get('/chat/api/job/created').status_code,404)

    def test_confirmation_reply_uses_same_confirmation_gate(self):
        p=self.draft()
        r=self.post('chat',{'message':'はい','confirmation_id':p['confirmation_id'],'request_id':'approve-text'})
        self.assertEqual(r.json['kind'],'started',r.json)
        self.executor.submit.assert_called_once()

    def test_native_create_worker_keeps_completed_account_when_next_fails(self):
        from test_advanced_requests import fixture
        from bcsfe import core
        import os
        import tempfile
        import traceback
        native,seed=fixture()
        for key,value in vars(native).items():setattr(self.m,key,value)
        self.post('settings',{'mode':'create','account_type':'new','count':2})
        plan=self.post('chat',{'message':'プラチナのかけらは8個。','request_id':'partial-plan'}).json
        started=self.post('confirm',{'confirmation_id':plan['confirmation_id'],'request_id':'partial-start'}).json
        jid=started['job_id']
        created=[];checked=[]
        class Handler:
            def __init__(self,save):self.save_file=save
            def create_new_account(self):
                created.append(self)
                return len(created)==1
            def get_codes(self):
                saved=core.SaveFile(core.Data(self.save_file.to_data().data),cc=self.save_file.cc)
                checked.append(saved.platinum_shards)
                return ('abc123456','1234')
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'seed').write_bytes(seed.to_data().data)
            env={'core':core,'time':time,'os':os,'traceback':traceback,'__file__':directory+'/main13.py',
                 '_update_job':lambda key,changes:self.records[key].update(changes),
                 'get_target_game_version_number':lambda:150501,'ACCOUNT_TYPE_FILES':{'new':'seed'},
                 'MAX_COUNT':5,'ensure_latest_save_schema':lambda *a:None,'ServerHandler':Handler,
                 'apply_account_type_preset':lambda *a:[],
                 '_apply_all_segments':lambda *a:self.m._apply_all_segments_unlocked(*a),
                 'clear_created_account_new_marks':lambda *a:0,'clear_owned_medal_new_marks':lambda *a:0,
                 'disable_rank_up_sale':lambda *a:None,'increment_usage_count':lambda:1,
                 'send_usage_log':Mock(),'USAGE_DB_PATH':'test-only'}
            tree=ast.parse(Path('main13.py').read_text())
            fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='run_job_create')
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'native-create-worker','exec'),env)
            self.m.run_job_create=env['run_job_create']
            args,kwargs=self.executor.submit.call_args
            args[0](*args[1:],**kwargs)
        self.assertEqual(checked,[8])
        self.assertEqual(self.records[jid]['status'],'error')
        self.assertEqual(len(self.records[jid]['accounts']),1)


if __name__ == '__main__':
    unittest.main()
