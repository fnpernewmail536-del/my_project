"""Loopback-only HTTP server; standard library UI, existing engine for game work."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import uuid
import webbrowser
from .engine import Engine
from .parser import parse_request, ParseError
from .menus import CATALOGUE
from .interpreter import interpret_request
from .confirmation import is_confirmation_reply
import copy

ROOT = Path(__file__).resolve().parent

class State:
    def __init__(self, engine, data_dir):
        self.engine = engine
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.dbpath = self.data_dir / "chat.db"
        self.lock = threading.RLock()
        self.sessions = {}
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-daiko")
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, operation_id TEXT, owner TEXT, labels TEXT, state TEXT, result TEXT, created REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS receipts(owner TEXT, request_id TEXT, response TEXT, PRIMARY KEY(owner,request_id))")
            db.execute("UPDATE jobs SET state='interrupted' WHERE state IN ('pending','running')")
        try: self.dbpath.chmod(0o600)
        except OSError: pass

    def db(self):
        connection = sqlite3.connect(self.dbpath, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def session(self, sid):
        with self.lock:
            if sid not in self.sessions:
                # Cookie is an opaque 256-bit secret; drafts never contain input codes on disk.
                sid = sid if isinstance(sid,str) and len(sid)==64 and all(c in '0123456789abcdef' for c in sid) else secrets.token_hex(32)
                self.sessions[sid] = {"csrf": secrets.token_hex(32), "draft": None, 'chat_receipts': {}}
            return sid, self.sessions[sid]

    def submit(self, owner, request_id, plan):
        # Receipt check and job registration are protected across concurrent requests.
        with self.lock:
            if not plan.get('ready') or plan.get('blockers') or not plan.get('_review_confirmed'):
                raise ParseError('確認が必要な項目があります。まだ実行していません。')
            with self.db() as db:
                row = db.execute("SELECT response FROM receipts WHERE owner=? AND request_id=?", (owner, request_id)).fetchone()
                if row: return json.loads(row[0])
                if not self.engine.status()["ready"]: raise ParseError(self.engine.error or "代行エンジンを読み込み中です。少し待ってください。")
                active = db.execute("SELECT 1 FROM jobs WHERE state IN ('pending','running','interrupted')").fetchone()
                if active: raise ParseError("未完了の処理があります。履歴で結果を確認してから次の依頼を送ってください。")
                jid, oid = str(uuid.uuid4()), secrets.token_hex(15)
                self.engine.prepare(jid, oid)
                response = {"kind":"started", "job_id":jid, "operation_id":oid, "labels":plan["labels"], "message":"確認を受け付けました。確認した内容で代行を開始します。"}
                db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (jid, oid, owner, json.dumps(plan["labels"],ensure_ascii=False), 'pending', '{}', time.time()))
                db.execute("INSERT INTO receipts VALUES(?,?,?)", (owner, request_id, json.dumps(response,ensure_ascii=False)))
            self.worker.submit(self.run, jid, oid, plan)
            return response

    def run(self, jid, oid, plan):
        with self.db() as db: db.execute("UPDATE jobs SET state='running' WHERE id=?", (jid,))
        try:
            self.engine.execute(jid, oid, plan)
            job = self.engine.get_job(jid) or {"status":"error", "error":"実行結果を取得できません。再実行せず受付IDを確認してください。"}
        except ParseError as exc:
            job = {'status': 'error', 'transfer_received': False, 'error': str(exc)}
            # Metadata errors occur before transfer, close the original pending job too.
            if getattr(self.engine, 'module', None):
                self.engine.module._update_job(jid, {**job, 'updated_at': time.time()})
                self.engine.module._close_operation(oid, status='error', recovery_status='not_available', error=str(exc))
        except Exception as exc:
            # There is no automatic retry: account codes may already be consumed.
            job = self.engine.get_job(jid) or {}
            job.update(status="error", error=f"処理が中断されました（{type(exc).__name__}）。再実行せず結果を確認してください。")
        public = self.public(job)
        with self.db() as db: db.execute("UPDATE jobs SET state=?,result=? WHERE id=?", (public.get('status','error'), json.dumps(public,ensure_ascii=False), jid))

    @staticmethod
    def public(job):
        keys = ('status','log','error','applied','transfer_code','auth_code','recovery_transfer_code','recovery_auth_code','admin_recovery_required','transfer_received','backup_before_saved','backup_operation_id')
        return {k:job[k] for k in keys if k in job}

    def job(self, owner, jid):
        with self.db() as db: row = db.execute("SELECT * FROM jobs WHERE owner=? AND id=?", (owner,jid)).fetchone()
        if not row: return None
        saved = json.loads(row['result'])
        if row['state'] in ('pending','running','interrupted','error'):
            current = self.engine.get_job(jid)
            if current:
                saved = self.public(current)
                if row['state']=='interrupted' and hasattr(self.engine,'module') and self.engine.module:
                    operation = self.engine.module._load_operation(row['operation_id'])
                    if operation and operation.get('recovery_status')=='admin_reissued':
                        saved.update(status='error',admin_recovery_required=False,error='管理パネルで復旧用コードが発行されました。下のコードを確認してください。')
                if saved.get('status') in ('done','error'):
                    with self.db() as db: db.execute("UPDATE jobs SET state=?,result=? WHERE id=?", (saved['status'],json.dumps(saved,ensure_ascii=False),jid))
            if row['state']=='interrupted' and saved.get('status') not in ('done','error'):
                saved.update(status='interrupted',error='前回の起動中に処理が中断されました。自動で再実行していません。元サイトの管理パネルで受付IDを確認してください。')
        saved.setdefault('status',row['state'])
        return {**saved, 'job_id':jid, 'operation_id':row['operation_id'], 'labels':json.loads(row['labels']), 'created_at':row['created']}

    def history(self, owner):
        with self.db() as db: rows=db.execute("SELECT id FROM jobs WHERE owner=? ORDER BY created DESC LIMIT 20",(owner,)).fetchall()
        return [self.job(owner,row['id']) for row in rows]

    @staticmethod
    def draft_response(plan):
        reasons=plan.get('blockers',[])
        message='次の内容で合っていますか？数量と対象を確認し、よければ「確認して実行」を押してください。'
        if plan['missing']:message='読み取りました。あと '+ '・'.join(plan['missing'])+' を送ってください。実行前に内容を確認します。'
        if reasons:message='読み取れた内容を表示しました。判断できない項目を確認してください。\n'+'\n'.join(reasons)
        return {'kind':'draft','labels':plan['labels'],'missing':plan['missing'],'blockers':reasons,'ready':plan['ready'],
                'confirmation_id':plan.get('confirmation_id'),'inferences':plan.get('inferences',[]),
                'quoted_prices':plan.get('quoted_prices',[]),
                'unresolved':plan.get('unresolved',[]),'requires_confirmation':True,'message':message}

    def recover_confirmation_reply(self, session):
        """Remove only replies misclassified by older versions; require a fresh review."""
        plan = session['draft']
        if not plan or not any(is_confirmation_reply(item.get('input')) for item in plan.get('unresolved', [])):
            return False
        restored = copy.deepcopy(plan)
        restored['unresolved'] = [item for item in plan['unresolved'] if not is_confirmation_reply(item.get('input'))]
        restored = interpret_request('お願いします', restored, getattr(self.engine,'character_metadata',None))
        restored['confirmation_id'] = secrets.token_hex(32)
        session['draft'] = restored
        session['chat_receipts'] = {}
        return True

def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Avoid writing credentials or request content to console logs.
            pass

        def send(self, status, payload, content_type='application/json; charset=utf-8'):
            body = json.dumps(payload,ensure_ascii=False).encode('utf-8') if isinstance(payload,(dict,list)) else payload
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
            if hasattr(self,'sid'): self.send_header('Set-Cookie',f'local_chat={self.sid}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers(); self.wfile.write(body)

        def guard(self, post=False):
            port=self.server.server_address[1]
            allowed={f'127.0.0.1:{port}',f'localhost:{port}'}
            if not ipaddress.ip_address(self.client_address[0]).is_loopback or self.headers.get('Host') not in allowed:
                self.send(403,{'error':'localhostから開いてください。'}); return False
            origin=self.headers.get('Origin')
            if origin and origin not in {f'http://{host}' for host in allowed}:
                self.send(403,{'error':'異なるサイトからの操作は受け付けません。'}); return False
            c=cookies.SimpleCookie()
            try: c.load(self.headers.get('Cookie',''))
            except cookies.CookieError: pass
            sid=c['local_chat'].value if 'local_chat' in c else None
            self.sid,self.session=state.session(sid)
            if post and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.session['csrf']):
                self.send(403,{'error':'ページを再読み込みしてください。'}); return False
            return True

        def do_GET(self):
            if not self.guard(): return
            path=urlsplit(self.path).path
            if path=='/api/bootstrap':
                with state.lock:
                    state.recover_confirmation_reply(self.session)
                    draft=self.session['draft']
                    return self.send(200,{'csrf':self.session['csrf'],'engine':state.engine.status(),'jobs':state.history(self.sid),'draft':state.draft_response(draft) if draft else None})
            if path=='/api/status': return self.send(200,{'engine':state.engine.status()})
            if path=='/api/menu': return self.send(200,CATALOGUE)
            if path=='/api/jobs': return self.send(200,{'jobs':state.history(self.sid)})
            if path.startswith('/api/job/'):
                job=state.job(self.sid,path.rsplit('/',1)[-1])
                return self.send(200 if job else 404,job or {'error':'処理が見つかりません。'})
            static={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','application/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
            if path in static:
                name,mime=static[path];return self.send(200,(ROOT/'static'/name).read_bytes(),mime)
            self.send(404,{'error':'not found'})

        def do_POST(self):
            if not self.guard(post=True): return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=131072: return self.send(413,{'error':'送信内容が大きすぎます。'})
                payload=json.loads(self.rfile.read(size))
                if not isinstance(payload,dict): raise ValueError()
            except (ValueError,UnicodeDecodeError): return self.send(400,{'error':'入力が不正です。'})
            path=urlsplit(self.path).path
            with state.lock:
                if path=='/api/reset':
                    self.session['draft']=None; self.session['chat_receipts']={};return self.send(200,{'message':'新しい依頼を開始しました。実行中の処理は継続します。'})
                if path not in ('/api/chat','/api/confirm'): return self.send(404,{'error':'not found'})
                rid=payload.get('request_id')
                if not isinstance(rid,str) or len(rid)>80 or len(rid)<8:
                    return self.send(400,{'error':'送信IDが不正です。'})
                with state.db() as db:
                    receipt=db.execute('SELECT response FROM receipts WHERE owner=? AND request_id=?',(self.sid,rid)).fetchone()
                if receipt: return self.send(200,json.loads(receipt[0]))
                if path=='/api/chat' and rid in self.session['chat_receipts']:
                    return self.send(200,self.session['chat_receipts'][rid])
                approval_reply = path=='/api/chat' and is_confirmation_reply(payload.get('message'))
                if path=='/api/confirm' or approval_reply:
                    if state.recover_confirmation_reply(self.session):
                        response=state.draft_response(self.session['draft'])
                        response['message']='承認の返事を認識しました。数量と対象をもう一度確認してから「確認して実行」を押してください。'
                        if approval_reply: self.remember_chat(rid,response)
                        return self.send(200,response)
                    plan=self.session['draft'];confirmation=payload.get('confirmation_id')
                    if approval_reply and confirmation is None:
                        if plan:
                            response=state.draft_response(plan)
                            if plan['ready']: response['message']='数量と対象をもう一度確認してから「確認して実行」を押すか、承認の返事を送ってください。'
                        else:
                            response={'kind':'draft','labels':[],'missing':['依頼内容'],'blockers':[],
                                      'ready':False,'unresolved':[],'requires_confirmation':True,
                                      'message':'代行内容を先に送ってください。確認内容を表示してから承認を受け付けます。'}
                        self.remember_chat(rid,response)
                        return self.send(200,response)
                    if not plan or not isinstance(confirmation,str) or len(confirmation)!=64 or any(c not in '0123456789abcdef' for c in confirmation) or not secrets.compare_digest(confirmation,plan.get('confirmation_id','')):
                        return self.send(409,{'error':'確認対象が変わりました。最新の内容をもう一度確認してください。'})
                    if not plan['ready'] or plan.get('blockers'):
                        return self.send(400,{'error':'不足・未解釈の項目があります。内容を修正してから確認してください。'})
                    try:
                        approved=copy.deepcopy(plan);approved['_review_confirmed']=True
                        response=state.submit(self.sid,rid,approved)
                        self.session['draft']=None
                        return self.send(200,response)
                    except (ParseError,RuntimeError) as exc:return self.send(400,{'error':str(exc)})
                    except Exception as exc:return self.send(503,{'error':f'確認受付を保存できませんでした（{type(exc).__name__}）。履歴を確認してください。'})
                try:
                    state.recover_confirmation_reply(self.session)
                    plan=interpret_request(payload.get('message'),self.session['draft'],getattr(state.engine,'character_metadata',None))
                    plan['confirmation_id']=secrets.token_hex(32)
                    self.session['draft']=plan
                    response=state.draft_response(plan)
                    self.remember_chat(rid,response)
                    return self.send(200,response)
                except (ParseError,RuntimeError) as exc: return self.send(400,{'error':str(exc)})
                except Exception as exc: return self.send(503,{'error':f'受付情報を保存できませんでした（{type(exc).__name__}）。履歴を確認してください。'})

        def remember_chat(self, request_id, response):
            self.session['chat_receipts'][request_id]=response
            if len(self.session['chat_receipts'])>50:
                self.session['chat_receipts'].pop(next(iter(self.session['chat_receipts'])))
    return Handler

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project-dir',default=str(ROOT.parent))
    parser.add_argument('--port',type=int,default=5002)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    data_dir=ROOT/'data'
    engine=Engine(args.project_dir,data_dir)
    state=State(engine,data_dir)
    try: server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(state))
    except OSError as exc:
        print(f'起動できません: {exc}\n同じ起動CMDが開いていないか確認してください。');return
    server.daemon_threads=True
    threading.Thread(target=engine.load,daemon=True).start()
    url=f'http://127.0.0.1:{args.port}/'
    print(f'CatProxyService Local Chat\n{url}\n終了するには Ctrl+C。処理中は閉じないでください。',flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        state.worker.shutdown(wait=True)

if __name__=='__main__': main()
