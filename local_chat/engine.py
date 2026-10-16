"""Bridge to the user's existing local main13.py; no public website needed."""
import importlib.util
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from .details import compile_details, apply_details, has_details
from .conditional_engine import compile_conditional, apply_conditional
from .parser import ParseError
from .backups import BeforeBackup
from .menus import MAX_ITEM_AMOUNTS

def resolve_maximums(plan):
    """Pass configured MAX values as quantities; original main13 defaults stay untouched."""
    selected = {group:list(keys) for group,keys in plan['selected'].items()}
    amounts = dict(plan['custom_amounts'])
    explicit = {key for keys in selected.values() for key in keys if key.startswith('custom_')}
    for group, keys in selected.items():
        resolved=[]
        for key in keys:
            if key.removeprefix('custom_') == 'matatabi' and plan.get('fruit_filters',{}).get('matatabi') is not None:
                continue
            if key in MAX_ITEM_AMOUNTS:
                custom='custom_'+key
                if custom in explicit: continue
                amounts[custom]=MAX_ITEM_AMOUNTS[key]
                key=custom
            if key not in resolved:resolved.append(key)
        selected[group]=resolved
    return selected,amounts

class Engine:
    def __init__(self, project_dir, data_dir):
        self.project_dir = Path(project_dir).resolve()
        self.data_dir = Path(data_dir)
        self.module = None
        self.error = ""
        self.loading = False
        self.lock = threading.Lock()
        self.detail_context = threading.local()
        self.backup_adapter = False

    def load(self):
        self.loading = True
        try:
            candidates = [self.project_dir / "main13.py", self.project_dir / "Paython" / "main13.py"]
            path = next((p for p in candidates if p.is_file()), None)
            if path is None:
                raise RuntimeError("main13.py が見つかりません。local_chatフォルダーと起動CMDをmy_project直下に置いてください。")
            # Stable local key preserves the original engine's encrypted snapshots.
            self.data_dir.mkdir(parents=True, exist_ok=True)
            keyfile = self.data_dir / "snapshot.key"
            if not keyfile.exists():
                with keyfile.open("x", encoding="ascii") as f:
                    f.write(secrets.token_hex(32))
                try: keyfile.chmod(0o600)
                except OSError: pass
            # Honor the existing project's .env key instead of overriding it.
            from dotenv import dotenv_values
            original_key = dotenv_values(self.project_dir / '.env').get('FLASK_SECRET_KEY')
            os.environ.setdefault("FLASK_SECRET_KEY", original_key or keyfile.read_text().strip())
            os.environ.setdefault("AUTO_GAME_UPDATE_ENABLED", "0")
            for directory in [self.project_dir, path.parent]:
                sys.path.insert(0, str(directory))
            spec = importlib.util.spec_from_file_location("catproxy_local_engine", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            required = ["run_job_daiko", "_create_job", "_create_operation", "_load_job", "_new_job_access_token"]
            if any(not hasattr(module, name) for name in required):
                raise RuntimeError("このmain13.pyには必要な処理がありません。2026-10-10のmy_project(4).zipの版を使用してください。")
            # This is a local-only tool; do not send Discord usage notifications.
            module.send_usage_log = lambda *args, **kwargs: None
            self.install_detail_adapter(module)
            self.module = module
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.loading = False

    def install_detail_adapter(self, module):
        if not hasattr(module, '_apply_all_segments_unlocked'): return
        original_apply = module._apply_all_segments_unlocked
        module._local_original_apply = original_apply
        def detailed_apply(save, *args, **kwargs):
            compiled = getattr(self.detail_context, 'compiled', None)
            if compiled and compiled.get('_conditional'):
                return apply_conditional(module,save,compiled)
            deferred = set()
            if compiled and args and isinstance(args[0],dict):
                selected = args[0]
                deferred = {'user_rank_rewards_claimed','user_rank_rewards_unclaimed'}.intersection(set().union(*(set(v) for v in selected.values())))
                if deferred:
                    selected = {group:[key for key in keys if key not in deferred] for group,keys in selected.items()}
                    args = (selected,) + args[1:]
            logs = original_apply(save, *args, **kwargs)
            if compiled:
                logs.extend(apply_details(module, save, compiled))
            if deferred: logs.extend(module._apply_user_rank_reward_actions(save,deferred))
            return logs
        module._apply_all_segments_unlocked = detailed_apply
        if all(callable(getattr(module,key,None)) for key in ['_save_operation_snapshot','_encrypt_snapshot','_update_job']):
            original_snapshot=module._save_operation_snapshot
            original_update=module._update_job
            def save_snapshot(operation_id,save):
                original_snapshot(operation_id,save)
                backup=getattr(self.detail_context,'backup',None)
                if backup and backup.operation_id==operation_id:backup.save_before(save)
            def update_job(job_id,changes):
                original_update(job_id,changes)
                backup=getattr(self.detail_context,'backup',None)
                if backup and job_id==getattr(self.detail_context,'job_id',None):backup.record_update(changes)
            module._save_operation_snapshot=save_snapshot
            module._update_job=update_job
            self.backup_adapter=True

    def status(self):
        return {"ready": self.module is not None, "loading": self.loading, "error": self.error}

    def character_metadata(self):
        if self.module is None:return None
        getter=getattr(self.module,'get_character_metadata',None)
        return getter() if callable(getter) else None

    def prepare(self, job_id, operation_id):
        m = self.module
        if m is None: raise RuntimeError(self.error or "代行エンジンを読み込み中です。")
        now = time.time()
        _, token_hash = m._new_job_access_token()
        m._create_operation(operation_id, job_id, "daiko", now)
        m._create_job(job_id, {
            "status": "pending", "log": "ローカルBotの実行待ち", "operation_id": operation_id,
            "operation_type": "daiko", "job_access_hash": token_hash,
            "error": None, "transfer_code": None, "auth_code": None, "applied": [],
            "created_at": now, "updated_at": now, "transfer_received": False,
            "quota_reservation_id": None, "trial_vip_reservation_id": None,
        })

    def execute(self, job_id, operation_id, plan):
        # Serialize game editing. The existing engine owns backups and recovery.
        with self.lock:
            if plan.get('_review_required') and not plan.get('_review_confirmed'):
                raise ParseError('お客さんの確認が必要です。引き継ぎを取得していません。')
            if not plan.get('ready') or plan.get('blockers'):
                raise ParseError('未確認の指定があります。引き継ぎを取得していません。')
            try:
                self.detail_context.job_id=job_id
                self.detail_context.backup=None
                if plan.get('backup_requested'):
                    if not self.backup_adapter:raise ParseError('代行前バックアップを接続できません。引き継ぎを取得していません。')
                    self.detail_context.backup=BeforeBackup(self.module,self.data_dir,operation_id,plan)
                # Resolve definitions before run_job_daiko consumes transfer codes.
                try:
                    self.detail_context.compiled = ({**compile_conditional(self.module,plan),'_conditional':True} if plan.get('conditional_mode') else compile_details(self.module, plan) if has_details(plan) else None)
                except ParseError:
                    raise
                except Exception as exc:
                    raise ParseError(f'詳細指定の定義確認に失敗しました（{type(exc).__name__}）。引き継ぎ取得前に停止しました。') from exc
                options = {'access_tier': 'local'}
                if plan.get('custom_playtime'): options['custom_playtime'] = plan['custom_playtime']
                for key in ['future_score_settings','dojo_score_settings']:
                    if plan.get(key) is not None:options[key]=plan[key]
                selected, amounts = resolve_maximums(plan)
                self.module.run_job_daiko(
                    job_id, operation_id, plan['transfer_code'], plan['auth_code'],
                    selected, [], amounts, **options,
                )
                backup=self.detail_context.backup
                if backup:
                    self.module._update_job(job_id,{'backup_before_saved':backup.saved,'backup_operation_id':operation_id})
            finally:
                self.detail_context.compiled = None
                self.detail_context.backup = None
                self.detail_context.job_id = None

    def get_job(self, job_id):
        if self.module is None: return None
        # The original admin panel may update recovery in a different process.
        saved = self.module._load_job(job_id)
        if saved: return saved
        with self.module.jobs_lock:
            job = self.module.jobs.get(job_id)
            if job: job = dict(job)
        return job

