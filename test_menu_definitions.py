"""Number-to-action tests and durable menu-64 backup verification; no game traffic."""
import ast
import gzip
import hashlib
import json
from pathlib import Path
import secrets
import tempfile
from types import SimpleNamespace as NS
import unittest
from local_chat.parser import parse_request,ParseError
from local_chat.menus import CATALOGUE,MENU_BY_NUMBER
from local_chat.engine import Engine
from local_chat.details import compile_details,apply_details
from test_detailed_requests import fixture_module,fixture_save,Cell
import test_local_chat as original_tests

class MenuTests(unittest.TestCase):
    def test_literal_names_use_user_catalogue(self):
        p=parse_request('にゃんこメダル全解放\nユーザーランク報酬全未受取化\n敵キャラ図鑑全解放')
        self.assertEqual(set(p['selected']['s4']),{'medals_all','user_rank_rewards_unclaimed','unlock_enemy_guide'})
        self.assertFalse(p['blockers'])
    def test_rarity_unlock_only_targets_selected_rarity(self):
        m=fixture_module();save=fixture_save()
        for cat in save.cats.cats:
            cat.unlocked=False
            cat.unlock=lambda current,c=cat:setattr(c,'unlocked',True)
        apply_details(m,save,compile_details(m,parse_request('メニュー22')))
        self.assertEqual([c.id for c in save.cats.cats if c.unlocked],[3,4])
    def test_gamatoto_numeric_level_uses_definition_and_rejects_above_limit(self):
        m=fixture_module();save=fixture_save()
        tables={'GamatotoExpedition.csv':[[Cell(0)],[Cell(100)],[Cell(300)]],
                'GamatotoExpedition_Limit.csv':[[Cell(3)]]}
        m._latest_jp_game_data_getter=lambda:NS(download=lambda local,filename:tables.get(filename))
        apply_details(m,save,compile_details(m,parse_request('メニュー66=3')))
        self.assertEqual(save.gamatoto.xp,300)
        with self.assertRaises(ParseError):compile_details(m,parse_request('メニュー66=4'))
    def test_catalogue_numbers_prices_and_policy(self):
        self.assertEqual(sorted(MENU_BY_NUMBER),list(range(1,85)))
        self.assertEqual(len(CATALOGUE['items']),84)
        self.assertEqual(MENU_BY_NUMBER[74]['price_jpy'],750)
        self.assertEqual(MENU_BY_NUMBER[78]['price_jpy'],300)
        self.assertEqual(MENU_BY_NUMBER[82]['price_jpy'],300)
        self.assertEqual(MENU_BY_NUMBER[82]['max_quantity'],32767)
        self.assertTrue(CATALOGUE['backup_policy']['no_ban_prevention_guarantee'])
    def test_specification_actions_are_resolved(self):
        p=parse_request('【仕様】\n74.78.82.83\nBAN保障\n引き継ぎコード:abc123456\n認証番号:0123')
        self.assertTrue(p['ready']);self.assertFalse(p['blockers'])
        self.assertEqual(set(p['selected']['s4']),{'medals_all','user_rank_rewards_unclaimed','unlock_enemy_guide'})
        self.assertEqual(p['labyrinth_amount'],999);self.assertTrue(p['backup_requested'])
    def test_all_connected_entries_can_be_selected(self):
        for n,entry in MENU_BY_NUMBER.items():
            if entry['status']!='ready':continue
            with self.subTest(n=n):
                p=parse_request(f'メニュー{n}\n引き継ぎコード:abc123456\n認証番号:0123')
                if entry.get('target_required'):
                    self.assertFalse(p['ready']);self.assertTrue(p['blockers']);continue
                self.assertTrue(p['ready']);self.assertFalse(p['blockers'])
    def test_pending_entries_never_execute_and_keep_names(self):
        for n,entry in MENU_BY_NUMBER.items():
            if entry['status']!='pending':continue
            with self.subTest(n=n):
                p=parse_request(f'メニュー{n}\n引き継ぎコード:abc123456\n認証番号:0123')
                self.assertFalse(p['ready']);self.assertIn(entry['name'],p['blockers'][0])
    def test_explicit_quantity_overrides_default_without_using_price(self):
        p=parse_request('メニュー4=30000\nメニュー6=5000\nメニュー82=100\nメニュー84=8100')
        self.assertEqual(p['custom_amounts']['custom_catfood'],30000)
        self.assertEqual(p['custom_amounts']['custom_np'],5000)
        self.assertEqual(p['labyrinth_amount'],100);self.assertEqual(p['custom_playtime'],'8100:00')
        self.assertEqual(parse_request('メニュー4')['selected']['s1'],['catfood'])
        for text in ['メニュー82=300円','メニュー74=750','メニュー84=10000','メニュー82=32768','メニュー4=-1']:
            with self.subTest(text=text),self.assertRaises(ParseError):parse_request(text)
    def test_reward_modes_are_exclusive_and_last_setting_wins(self):
        p=parse_request('仕様77.78.79.80')
        self.assertEqual(set(p['selected']['s4']),{'user_rank_rewards_unclaimed','catguide_rewards_unclaimed'})
        self.assertEqual(p['spec_numbers'],[78,80])
        p=parse_request('仕様77.79',p)
        self.assertEqual(set(p['selected']['s4']),{'user_rank_rewards_claimed','catguide_rewards_claimed'})
        self.assertNotIn(78,p['menu_numbers']);self.assertNotIn(80,p['menu_numbers'])
    def test_remove_menu_preserves_unrelated_plan(self):
        p=parse_request('猫缶30000\n仕様74.78.82.83\nBAN保証')
        p=parse_request('メニュー82不要',p)
        self.assertIsNone(p['labyrinth_amount']);self.assertIn('medals_all',p['selected']['s4'])
        self.assertEqual(p['custom_amounts']['custom_catfood'],30000);self.assertTrue(p['labels'])
        p=parse_request('仕様不要\nBAN保障不要',p)
        self.assertFalse(p['backup_requested']);self.assertFalse(p['selected']['s4'])
        self.assertEqual(p['selected']['s1'],['custom_catfood'])
    def test_category_level_number_mapping(self):
        p=parse_request('メニュー29=40+70\nメニュー34=50+なし')
        self.assertEqual(len(p['level_rules']),6)
        self.assertEqual(p['level_rules']['5'],{'base':50,'plus':[0,0]})
        self.assertEqual(p['level_rules']['2'],{'base':40,'plus':[70,70]})
    def test_labyrinth_writes_only_four_types(self):
        save=fixture_save();save.labyrinth_medals=[1,2,3,4,555]
        apply_details(fixture_module(),save,compile_details(fixture_module(),parse_request('メニュー82=100')))
        self.assertEqual(save.labyrinth_medals,[100,100,100,100,555])
    def test_matatabi_and_stones_are_separate(self):
        m=fixture_module();m.get_legend_metadata=lambda:{'vip_item_groups':{'catfruit':['紫マタタビ','虹マタタビ','紫獣石','虹獣石','紫獣結晶']}}
        save=fixture_save();save.catfruit=[1,2,3,4,5]
        apply_details(m,save,compile_details(m,parse_request('メニュー12=100')))
        self.assertEqual(save.catfruit,[100,100,3,4,5])
        apply_details(m,save,compile_details(m,parse_request('メニュー14=300')))
        self.assertEqual(save.catfruit,[100,100,300,300,5])
    def test_original_worker_applies_medals_ur_and_enemy_guide(self):
        source=Path(__file__).resolve().parents[1]/'source/my_project/main13.py'
        if not source.exists():self.skipTest('元エンジンは配布ZIPに含めません')
        tree=ast.parse(source.read_text())
        names={'apply_daiko_segment','_apply_user_rank_reward_actions','_set_all_user_rank_rewards'}
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        core=NS(core_data=NS(get_medal_names=lambda save:NS(medal_names=['a','b','c'])),
                game=NS(battle=NS(enemy=NS(EnemyDictionary=lambda save:NS(get_valid_enemies=lambda:[1,4])))))
        env={'core':core,'_latest_rank_gift_thresholds':lambda:[(0,10),(1,100)],'Reward':NS(init=lambda:NS(claimed=False))}
        exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),env)
        added=[];save=fixture_save();save.medals=NS(add_medal=lambda cid:added.append(cid))
        save.enemy_guide=[0]*6;save.user_rank_rewards=NS(rewards=[NS(claimed=True),NS(claimed=True),NS(claimed=True)])
        save.calculate_user_rank=lambda:50
        logs=env['apply_daiko_segment'](save,parse_request('仕様74.78.83')['selected']['s4'],[],{})
        self.assertEqual(added,[0,1,2]);self.assertEqual(save.enemy_guide,[0,1,0,0,1,0])
        self.assertTrue(all(not x.claimed for x in save.user_rank_rewards.rewards))
        self.assertTrue(logs)
    def test_rank_rewards_run_after_detailed_levels(self):
        m=fixture_module();save=fixture_save();order=[]
        def original(current,selected):
            self.assertNotIn('user_rank_rewards_claimed',selected['s4']);order.append('original');return []
        def rewards(current,selected):
            self.assertEqual(current.cats.cats[3].upgrade.base+1,50);order.append('rewards');return ['UR']
        m._apply_all_segments_unlocked=original;m._apply_user_rank_reward_actions=rewards
        m.run_job_daiko=lambda *args,**kwargs:m._apply_all_segments_unlocked(save,args[4])
        p=parse_request('メニュー31=50+80\n仕様77\n引き継ぎコード:abc123456\n認証番号:0123')
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=m;engine.install_detail_adapter(m);engine.execute('job','operation',p)
        self.assertEqual(order,['original','rewards'])

class BackupTests(unittest.TestCase):
    def test_before_save_and_codes_are_encrypted_and_not_overwritten(self):
        try:from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:self.skipTest('cryptography not installed')
        cipher=AESGCM(hashlib.sha256(b'test-only-key').digest())
        def encrypt(data,oid):
            nonce=secrets.token_bytes(12);return b'ACS2'+nonce+cipher.encrypt(nonce,data,oid.encode())
        def decrypt(data,oid):return gzip.decompress(cipher.decrypt(data[4:16],data[16:],oid.encode()))
        m=fixture_module();job={};snapshots=[];state={'raw':b'BEFORE: private test save'}
        save=NS(to_data=lambda:NS(to_bytes=lambda:state['raw']))
        m._encrypt_snapshot=encrypt
        m._save_operation_snapshot=lambda oid,current:snapshots.append(current.to_data().to_bytes())
        m._update_job=lambda jid,changes:job.update(changes)
        def worker(*args,**kwargs):
            oid=args[1];m._save_operation_snapshot(oid,save)
            m._update_job(args[0],{'status':'running','recovery_transfer_code':'abcdef123','recovery_auth_code':'4567'})
            state['raw']=b'AFTER: changed test save';m._save_operation_snapshot(oid,save)
            m._update_job(args[0],{'status':'done','transfer_code':'fedcba123','auth_code':'5678'})
        m.run_job_daiko=worker
        p=parse_request('メニュー64\n引き継ぎコード:abc123456\n認証番号:0123')
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=m;engine.install_detail_adapter(m)
            engine.execute('jid','oid',p)
            directory=Path(temp)/'backups'/'oid'
            raw=(directory/'before_save.enc').read_bytes()
            self.assertEqual(decrypt(raw,'oid'),b'BEFORE: private test save')
            self.assertNotIn(b'private test save',raw)
            codes_blob=(directory/'codes.enc').read_bytes();self.assertNotIn(b'abc123456',codes_blob)
            codes=json.loads(decrypt(codes_blob,'oid'))
            self.assertEqual(codes['input_transfer_code'],'abc123456')
            self.assertEqual(codes['recovery_transfer_code'],'abcdef123');self.assertEqual(codes['transfer_code'],'fedcba123')
            self.assertTrue(job['backup_before_saved']);self.assertIsNone(engine.detail_context.backup)
            # No context: unrelated admin snapshots must not overwrite the retained backup.
            m._save_operation_snapshot('oid',save)
            self.assertEqual((directory/'before_save.enc').read_bytes(),raw)
        self.assertEqual(snapshots[:2],[b'BEFORE: private test save',b'AFTER: changed test save'])
    def test_unavailable_backup_stops_before_worker(self):
        m=fixture_module();calls=[];m.run_job_daiko=lambda *a,**k:calls.append(a)
        p=parse_request('BAN保障\n引き継ぎコード:abc123456\n認証番号:0123')
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=m;engine.install_detail_adapter(m)
            with self.assertRaises(ParseError):engine.execute('jid','oid',p)
            self.assertFalse(calls)

class MenuHttpTests(unittest.TestCase):
    setUp=original_tests.HttpTests.setUp;tearDown=original_tests.HttpTests.tearDown
    request=original_tests.HttpTests.request;chat=original_tests.HttpTests.chat
    confirm=original_tests.HttpTests.confirm
    def test_catalogue_endpoint_lists_all_84(self):
        status,data,_=self.request('GET','/api/menu')
        self.assertEqual(status,200);self.assertEqual(len(data['items']),84)
    def test_specifications_only_run_once_after_confirmation(self):
        text='仕様74.78.82.83\nBAN保証\n引き継ぎコード:abc123456\n認証番号:0123'
        draft=self.chat(text,'specification-request')[1];self.assertEqual(draft['kind'],'draft');self.assertFalse(self.engine.calls)
        first=self.confirm(draft,'specification-confirm')[1];second=self.confirm(draft,'specification-confirm')[1]
        self.assertEqual(first['kind'],'started');self.assertEqual(first['job_id'],second['job_id'])
        self.state.worker.shutdown(wait=True)
        self.assertEqual(len(self.engine.calls),1)
        self.assertTrue(self.engine.calls[0]['backup_requested']);self.assertEqual(self.engine.calls[0]['labyrinth_amount'],999)
    def test_unknown_menu_keeps_pending_without_running(self):
        data=self.chat('メニュー999\n猫缶\n引き継ぎコード:abc123456\n認証番号:0123')[1]
        self.assertEqual(data['kind'],'draft');self.assertFalse(data['ready']);self.assertFalse(self.engine.calls)
        self.assertIn('定義表にありません',data['blockers'][0])

if __name__=='__main__':unittest.main(verbosity=2)
