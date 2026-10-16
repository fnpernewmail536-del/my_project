"""Offline tests. No game servers, actual accounts, or external messages."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from local_chat.parser import parse_request, ParseError
from local_chat.server import State, make_handler
from local_chat.engine import Engine
from types import SimpleNamespace

class FakeEngine:
    error=''
    def __init__(self): self.calls=[];self.jobs={};self.prepared=[]
    def status(self): return {'ready':True,'loading':False,'error':''}
    def prepare(self,jid,oid): self.prepared.append((jid,oid));self.jobs[jid]={'status':'pending'}
    def execute(self,jid,oid,plan):
        self.calls.append(plan)
        self.jobs[jid]={'status':'done','transfer_code':'abcdef123','auth_code':'0123','applied':['test'],'job_access_hash':'hidden'}
    def get_job(self,jid): return self.jobs.get(jid)

class ParserTests(unittest.TestCase):
    def test_whole_request(self):
        p=parse_request('アイテム全部MAX、キャラ全解放、施設MAX\n引き継ぎコード：ABC123456\n認証番号：0123')
        self.assertTrue(p['ready']);self.assertEqual(p['transfer_code'],'abc123456');self.assertEqual(p['auth_code'],'0123')
        self.assertEqual(len(p['selected']['s1']),10);self.assertIn('unlock_all_cats',p['selected']['s3'])
    def test_incremental_codes(self):
        p=parse_request('キャラ全解放');p=parse_request('abc123456',p);p=parse_request('0123',p)
        self.assertTrue(p['ready']);self.assertEqual(len(p['labels']),1)
    def test_numbers_and_override(self):
        p=parse_request('猫缶30000、XP5000万、金チケ100');self.assertEqual(p['custom_amounts']['custom_xp'],50000000)
        p=parse_request('猫缶MAX',p);self.assertIn('catfood',p['selected']['s1']);self.assertNotIn('custom_catfood',p['selected']['s1'])
        p=parse_request('猫缶12000',p);self.assertNotIn('catfood',p['selected']['s1']);self.assertEqual(p['custom_amounts']['custom_catfood'],12000)
    def test_all_help_examples(self):
        for s in ['チケット全部MAX','チケット全部MAX（イベント以外）','キャラMAX','キャラMAX+','全ステージMAX','メインクリア','レジェンドクリア','真レジェンドクリア','ゼロレジェンドクリア','施設MAX','オトートMAX','神社MAX','ガマトトMAX','エラーキャラ削除']:
            with self.subTest(s=s): self.assertTrue(parse_request(s)['labels'])
    def test_unknown_rejects_everything(self):
        for s in ['猫缶10000、キャラレベル50','猫缶10000にしない','全ステージMAX、日本編だけ','キャラ全解放、隊員10体','猫缶-3','キャラMAX+10','猫缶2147483648','猫缶10000、イベントチケットMAX']:
            with self.subTest(s=s),self.assertRaises(ParseError):parse_request(s)
    def test_invalid_codes(self):
        for s in ['キャラ全解放\n引き継ぎコード:xxxxxxxxx','認証番号:12345','引き継ぎコード:abcdef1234']:
            with self.assertRaises(ParseError):parse_request(s)
    def test_failing_message_does_not_mutate_draft(self):
        p=parse_request('猫缶10000')
        with self.assertRaises(ParseError):parse_request('猫缶50000、知らない処理',p)
        self.assertEqual(p['custom_amounts']['custom_catfood'],10000)
    def test_level_modes_are_mutually_exclusive(self):
        p=parse_request('キャラMAX+');p=parse_request('キャラMAX',p)
        self.assertEqual(p['selected']['s3'],['max_all_cats_plus'])

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.engine=FakeEngine();self.state=State(self.engine,self.temp.name)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.state));self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.port=self.server.server_port;self.cookie='';self.csrf=''
        status,payload,headers=self.request('GET','/api/bootstrap');self.assertEqual(status,200)
        self.cookie=dict(headers)['Set-Cookie'].split(';')[0];self.csrf=payload['csrf']
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.state.worker.shutdown(wait=True);self.temp.cleanup()
    def request(self,method,path,payload=None,extra=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        headers={'Cookie':self.cookie,'X-CSRF-Token':self.csrf};headers.update(extra or {})
        if payload is not None:headers['Content-Type']='application/json'
        c.request(method,path,body=json.dumps(payload) if payload is not None else None,headers=headers)
        response=c.getresponse();body=response.read();status=response.status;result=json.loads(body) if response.getheader('Content-Type','').startswith('application/json') else body
        output=(status,result,response.getheaders());c.close();return output
    def chat(self,text,request_id=None,auto=True):
        return self.request('POST','/api/chat',{'message':text,'auto_run':auto,'request_id':request_id or str(uuid.uuid4())})
    def confirm(self,draft,request_id=None):
        return self.request('POST','/api/confirm',{'confirmation_id':draft['confirmation_id'],'request_id':request_id or str(uuid.uuid4())})
    def test_incomplete_and_unknown_do_not_run(self):
        self.assertEqual(self.chat('猫缶10000')[1]['kind'],'draft')
        self.assertFalse(self.chat('引き継ぎコード:abc123456\n認証番号:0123、隊員追加')[1]['ready'])
        self.assertFalse(self.engine.calls)
    def test_duplicate_request_only_executes_once(self):
        rid=str(uuid.uuid4());text='猫缶10000\n引き継ぎコード:abc123456\n認証番号:0123'
        draft=self.chat(text,rid)[1];again=self.chat(text,rid)[1]
        self.assertEqual(draft['confirmation_id'],again['confirmation_id']);self.assertFalse(self.engine.calls)
        confirm_id=str(uuid.uuid4());a=self.confirm(draft,confirm_id);b=self.confirm(draft,confirm_id)
        self.assertEqual(a[1]['job_id'],b[1]['job_id'])
        self.state.worker.shutdown(wait=True);self.assertEqual(len(self.engine.calls),1)
        job=self.request('GET','/api/job/'+a[1]['job_id'])[1]
        self.assertEqual(job['transfer_code'],'abcdef123');self.assertNotIn('job_access_hash',job)
    def test_manual_mode_waits_for_execute(self):
        response=self.chat('施設MAX\n引き継ぎコード:abc123456\n認証番号:0123',auto=False)
        self.assertTrue(response[1]['ready']);self.assertFalse(self.engine.calls)
        draft=self.chat('お願いします')[1];self.assertEqual(draft['kind'],'draft');self.assertFalse(self.engine.calls)
        self.assertEqual(self.confirm(draft)[1]['kind'],'started')
    def test_access_controls(self):
        for extra in [{'Host':'evil.test'},{'Origin':'https://evil.test'},{'X-CSRF-Token':''}]:
            with self.subTest(extra=extra):self.assertEqual(self.request('POST','/api/reset',{},extra)[0],403)
    def test_static_and_traversal(self):
        for path in ['/','/style.css','/app.js']:self.assertEqual(self.request('GET',path)[0],200)
        self.assertEqual(self.request('GET','/../engine.py')[0],404)
    def test_other_browser_cannot_read_job(self):
        draft=self.chat('施設MAX\n引き継ぎコード:abc123456\n認証番号:0123')[1];a=self.confirm(draft)[1]
        self.cookie='';self.assertEqual(self.request('GET','/api/job/'+a['job_id'])[0],404)
    def test_restart_does_not_reexecute(self):
        owner=self.cookie.split('=')[1];jid='interrupted-test'
        with self.state.db() as db:db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(jid,'operation',owner,'[]','running','{}',1))
        restarted=State(self.engine,self.temp.name)
        try:
            self.assertEqual(restarted.job(owner,jid)['status'],'interrupted')
            self.assertFalse(self.engine.calls)
        finally:restarted.worker.shutdown(wait=True)

class BridgeTests(unittest.TestCase):
    def test_exact_original_worker_arguments(self):
        calls=[]
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=SimpleNamespace(run_job_daiko=lambda *args,**kwargs:calls.append((args,kwargs)))
            plan=parse_request('猫缶30000、キャラ全解放\n引き継ぎコード:abc123456\n認証番号:0123')
            engine.execute('jid','oid',plan)
            args,kwargs=calls[0]
            self.assertEqual(args[:4],('jid','oid','abc123456','0123'));self.assertEqual(args[4]['s3'],['unlock_all_cats'])
            self.assertEqual(args[6]['custom_catfood'],30000);self.assertEqual(kwargs['access_tier'],'local')

if __name__=='__main__': unittest.main(verbosity=2)
