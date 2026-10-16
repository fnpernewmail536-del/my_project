"""Menu 64 retains the pre-edit save and code history as separate encrypted files."""
import gzip
import json
import os
from pathlib import Path
import re
import secrets
import time
from .parser import ParseError


class BeforeBackup:
    def __init__(self, module, data_dir, operation_id, plan):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', operation_id): raise ParseError('バックアップの受付IDが不正です。')
        required = ['_save_operation_snapshot', '_encrypt_snapshot', '_update_job']
        if any(not callable(getattr(module,key,None)) for key in required):
            raise ParseError('このエンジンにはバックアップに必要な機能がありません。引き継ぎを取得していません。')
        self.module=module;self.operation_id=operation_id
        self.directory=Path(data_dir)/'backups'/operation_id
        self.saved=False
        self.codes={'input_transfer_code':plan['transfer_code'],'input_auth_code':plan['auth_code'],
                    'created_at':time.time(),'operation_id':operation_id,
                    'note':'入力コードは引き継ぎ取得時に消費されます。保存はコードの有効性やBAN防止を保証しません。'}
        # Verify filesystem and encryption before the worker consumes account codes.
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            self.write_codes()
        except Exception as exc:
            raise ParseError(f'代行前バックアップの準備に失敗しました（{type(exc).__name__}）。引き継ぎを取得していません。') from exc

    def write(self, filename, plaintext):
        blob=self.module._encrypt_snapshot(gzip.compress(plaintext),self.operation_id)
        target=self.directory/filename
        temporary=self.directory/('.'+filename+'.'+secrets.token_hex(5)+'.tmp')
        try:
            with temporary.open('xb') as handle:
                handle.write(blob);handle.flush();os.fsync(handle.fileno())
            try:temporary.chmod(0o600)
            except OSError:pass
            os.replace(temporary,target)
        finally:
            temporary.unlink(missing_ok=True)

    def save_before(self, save):
        if self.saved:return
        self.write('before_save.enc',save.to_data().to_bytes())
        self.saved=True

    def write_codes(self):
        self.write('codes.enc',json.dumps(self.codes,ensure_ascii=False).encode('utf-8'))

    def record_update(self, changes):
        names=['status','transfer_received','transfer_code','auth_code','recovery_transfer_code','recovery_auth_code']
        update={key:changes[key] for key in names if key in changes}
        if not update:return
        self.codes.update(update);self.codes['updated_at']=time.time();self.write_codes()
