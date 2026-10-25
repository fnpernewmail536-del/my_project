"""Reproduce the fresh-account basic roster through the native create worker."""
import ast
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import traceback
import unittest
from unittest.mock import Mock, patch

from local_chat.engine import Engine
from local_chat.interpreter import interpret_request
from local_chat.details import compile_details
from test_advanced_requests import fixture, core


ORDER = '''## キャラ解放
【その他】
基本キャラ:全員解放
## キャラレベル変更
基本キャラ:基本Lv20、プラス値+90
第3形態にできるキャラは第3形態にしてください。
'''


class BasicCreationTests(unittest.TestCase):
    def test_initial_template_reaches_upload_with_all_basics_and_third_forms(self):
        module, _ = fixture()
        metadata = json.loads(Path('testdata/basic_characters_jp_15_7_0.json').read_text())
        module.get_character_metadata = lambda: metadata
        module._apply_all_segments_unlocked = lambda *args, **kwargs: []
        tree = ast.parse(Path('main13.py').read_text(encoding='utf-8'))
        names = {'character_third_form_state','clear_created_account_new_marks',
                 'clear_owned_medal_new_marks','disable_rank_up_sale','run_job_create'}
        functions = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        original = core.SaveFile(core.Data(Path('Nyanko_new').read_bytes()), cc=core.CountryCode.from_code('jp'))
        self.assertEqual([c.id+1 for c in original.cats.cats if c.unlocked], [1])
        before = copy.deepcopy(original.cats.serialize())
        job = {}
        uploaded = []

        class Handler:
            def __init__(self, save):
                self.save_file = save

            def create_new_account(self):
                # Network allocation is replaced; edits and final serialization
                # still follow the production worker and post-processing.
                return True

            def get_codes(self):
                uploaded.append(core.SaveFile(core.Data(self.save_file.to_data().data), cc=self.save_file.cc))
                return ('fixture-code', '0000')

        env = {'core':core,'os':os,'time':time,'traceback':traceback,'__file__':str(Path('main13.py').resolve()),
               '_update_job':lambda jid, fields:job.update(fields),
               'get_target_game_version_number':lambda:150501,
               'ACCOUNT_TYPE_FILES':{'new':'Nyanko_new'},'MAX_COUNT':5,
               'ensure_latest_save_schema':lambda *args:None,'ServerHandler':Handler,
               'apply_account_type_preset':lambda *args:[],
               '_apply_all_segments':lambda *args:module._apply_all_segments_unlocked(*args),
               'increment_usage_count':lambda:1,'send_usage_log':Mock(),'USAGE_DB_PATH':'fixture'}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'native-basic-create','exec'),env)
        module.character_third_form_state = env['character_third_form_state']
        plan = interpret_request(ORDER, character_metadata=metadata)
        self.assertFalse(plan['unresolved'], plan['unresolved'])
        compiled = compile_details(module, plan)
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory,directory)
            engine.module = module
            engine.install_detail_adapter(module)
            engine.detail_context.compiled = compiled
            try:
                with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):
                    env['run_job_create']('fixture-job',{},[],{},account_type_key='new')
            finally:
                engine.detail_context.compiled = None
        self.assertEqual(job['status'], 'done', job.get('error'))
        self.assertEqual(len(uploaded), 1)
        loaded = uploaded[0]
        expected_ids = {int(c['id']) for c in metadata['characters']}
        self.assertEqual(expected_ids, {1,2,3,4,5,6,7,8,9,644})
        self.assertEqual({c.id+1 for c in loaded.cats.cats if c.unlocked}, expected_ids)
        for cid in expected_ids:
            cat = loaded.cats.get_cat_by_id(cid-1)
            self.assertEqual((cat.upgrade.base+1,cat.upgrade.plus,cat.current_form), (20,90,2), cid)
            self.assertEqual(cat.unlocked_forms, 0, cid)  # Natural basic evolution state.
            self.assertEqual(cat.gatya_seen, 0, cid)
            self.assertTrue(cat.catguide_collected, cid)
        after = loaded.cats.serialize()
        self.assertEqual([c for i,c in enumerate(after['cats'],1) if i not in expected_ids],
                         [c for i,c in enumerate(before['cats'],1) if i not in expected_ids])
        self.assertEqual(loaded.game_version.game_version, 150501)
        self.assertTrue(any('第3形態へ切替：10体' in line for line in job['applied']))

    def test_basic_unlock_phrase_requires_explicit_ownership_action(self):
        metadata = json.loads(Path('testdata/basic_characters_jp_15_7_0.json').read_text())
        level_only = interpret_request('## キャラレベル変更\n基本キャラ:基本Lv20、プラス値+90', character_metadata=metadata)
        self.assertFalse(any(r['field']=='unlocked' for r in level_only['document_rules']))
        full = interpret_request(ORDER, character_metadata=metadata)
        unlock = next(r for r in full['document_rules'] if r['field']=='unlocked')
        self.assertEqual(unlock['expected_ids'], [1,2,3,4,5,6,7,8,9,644])
        label=next(x['label'] for x in full['labels'] if '基本キャラ:全員解放' in x['label'])
        self.assertIn('全10体',label)
        self.assertIn('ネコ超人（No.644）',label)


if __name__ == '__main__':
    unittest.main()
