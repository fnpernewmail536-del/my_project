"""Offline behavior tests for the pasted request and detailed save application."""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch
from local_chat.parser import parse_request, ParseError
from local_chat.details import compile_details, apply_details
from local_chat.engine import Engine, resolve_maximums
import test_local_chat as original_tests

REQUEST = '''【アイテム】
猫缶、XPをMAX
NP5000
バトルアイテム全て300
キャッツアイ全て200
本能Sのみ10(その他不要)
城素材やマタタビなどその他アイテム不要
【チケット類】
レアMAX、その他不要
【キャラ類】
コラボ含め全て解放
《レベル変更》
基本全てレベル20+90
EX全てレベル30(+付けられるものは全て+Maxまで)
レア全てレベル50+Max
(コラボ系は+20〜30)
激レア全てレベル50+Max
(コラボ系は+10〜20)
超激レア全てレベル30+10〜15
(+10〜15のばらつきか不可であれば全て+10)
伝説全てレベル50+なし
キャラの本能は全て解放MAX
【ステージ】
全てクリアお宝もMAX
【その他】
施設MAX
エラー削除
【ガマトト】
全てレジェンド、城と神社MAX
【仕様】
74.78.82.83
プレイ時間は8000〜8200の間で適当
ban保証'''

class DetailedParserTests(unittest.TestCase):
    def test_complete_pasted_request(self):
        p = parse_request(REQUEST)
        self.assertEqual(p['selected']['s1'], ['catfood','xp','custom_np','custom_battle_items','custom_catseyes','rare_tickets'])
        self.assertEqual(p['custom_amounts'], {'custom_np':5000,'custom_battle_items':300,'custom_catseyes':200})
        self.assertEqual(p['orb_s_amount'], 10)
        self.assertEqual(p['spec_numbers'], [74,78,82,83])
        self.assertEqual(len(p['level_rules']), 6)
        self.assertEqual(p['level_rules']['2']['collab_plus'], [20,30])
        self.assertEqual(p['level_rules']['3']['collab_plus'], [10,20])
        self.assertEqual(p['level_rules']['5']['plus'], [0,0])
        self.assertTrue(p['helpers_legend']); self.assertTrue(p['talents_max'])
        self.assertIn('all_stage_clear', p['selected']['s2'])
        self.assertIn('ototo_max', p['selected']['s4'])
        self.assertTrue(8000 <= int(p['custom_playtime'].split(':')[0]) <= 8200)
        p = parse_request('引き継ぎコード:abc123456\n認証番号:0123', p)
        self.assertTrue(p['ready']); self.assertEqual(p['blockers'], [])
        self.assertTrue(p['backup_requested']);self.assertEqual(p['labyrinth_amount'],999)
        self.assertIn('medals_all',p['selected']['s4']);self.assertIn('user_rank_rewards_unclaimed',p['selected']['s4'])
        self.assertIn('unlock_enemy_guide',p['selected']['s4'])
        p = parse_request('仕様不要\nBAN保証不要', p)
        self.assertTrue(p['ready']); self.assertEqual(p['blockers'], [])
    def test_missing_quantity_is_max_but_explicit_numbers_win(self):
        p = parse_request('猫缶、XP、NP5000、施設')
        self.assertIn('catfood',p['selected']['s1']); self.assertIn('xp',p['selected']['s1'])
        self.assertEqual(p['custom_amounts']['custom_np'],5000)
        self.assertIn('base_upgrades_max',p['selected']['s2'])
        p = parse_request('【アイテム】\nバトルアイテム\nキャッツアイ\n本能Sのみ',p)
        self.assertIn('battle_items',p['selected']['s1']); self.assertIn('catseyes',p['selected']['s1'])
        self.assertEqual(p['orb_s_amount'],998)
        self.assertEqual(parse_request('プレイ時間')['custom_playtime'],'9999:00')
    def test_shared_trailing_quantity(self):
        p = parse_request('猫缶、XPをMAX')
        self.assertEqual(p['selected']['s1'],['catfood','xp'])
        p = parse_request('猫缶、XP5000')
        self.assertEqual(p['custom_amounts']['custom_catfood'],5000)
        self.assertEqual(p['custom_amounts']['custom_xp'],5000)
    def test_exclusions_remove_prior_selections_without_zero_writes(self):
        p = parse_request('アイテム全部MAX、チケット全部MAX')
        p = parse_request('【アイテム】\n猫缶\nその他不要\n【チケット類】\nレア\nその他不要',p)
        self.assertEqual(p['selected']['s1'],['catfood','rare_tickets'])
        self.assertEqual(p['custom_amounts'],{})
        p = parse_request('猫缶不要',p)
        self.assertNotIn('catfood',p['selected']['s1'])
    def test_s_orbs_and_all_orbs_override_each_other(self):
        p = parse_request('本能玉MAX'); p = parse_request('本能Sのみ10',p)
        self.assertNotIn('talent_orbs',p['selected']['s1']);self.assertEqual(p['orb_s_amount'],10)
        p = parse_request('本能玉20',p)
        self.assertIsNone(p['orb_s_amount']);self.assertEqual(p['custom_amounts']['custom_talent_orbs'],20)
    def test_no_silent_unknown_annotations(self):
        for text in ['【謎】猫缶','【レベル変更】\nレア50+MAX(コラボ以外不要)',
                     '【レベル変更】\nレア50+MAX\n(コラボ系は+30〜20)',
                     '【レベル変更】\n超激レア30+10〜15\n(+10〜16のばらつきか不可であれば全て+10)',
                     '猫缶不要100','プレイ時間は8200〜8000の間で適当']:
            with self.subTest(text=text), self.assertRaises(ParseError): parse_request(text)
    def test_ranges_survive_credential_messages(self):
        p = parse_request(REQUEST); snapshot=copy.deepcopy(p)
        q = parse_request('引き継ぎコード:abc123456\n認証番号:0123',p)
        self.assertEqual(q['custom_playtime'],p['custom_playtime']);self.assertEqual(q['level_rules'],p['level_rules'])
        self.assertEqual(p,snapshot)
    def test_category_and_global_modes_override_each_other(self):
        p = parse_request('キャラMAX+')
        p = parse_request('【レベル変更】\nレア50+MAX',p)
        self.assertNotIn('max_all_cats_plus',p['selected']['s3'])
        p = parse_request('キャラMAX',p)
        self.assertEqual(p['level_rules'],{})
    def test_omitted_category_levels_use_metadata_max(self):
        p = parse_request('【レベル変更】\n基本\nEX\nレア\n激レア\n超激レア\n伝説')
        self.assertEqual(len(p['level_rules']),6)
        self.assertTrue(all(r['base']=='max' and r['plus']=='max' for r in p['level_rules'].values()))

class Cell:
    def __init__(self,value): self.value=value
    def to_int(self): return int(self.value)
    def to_str(self): return str(self.value)


def fixture_module():
    chars=[];rows=[]
    # id, rarity, base cap, plus cap, collab flag.
    for cid,rarity,base,plus,collab in [(1,0,20,90,False),(2,1,50,0,False),(3,1,50,20,False),
                                     (4,2,50,80,False),(5,2,50,80,True),(6,3,50,70,False),
                                     (7,3,50,70,True),(8,4,60,70,False),(9,5,50,0,False)]:
        chars.append({'id':cid,'rarity':rarity,'selectable':True,'talents':[{'id':10,'max_level':10}] if cid==8 else []})
        row=[Cell(0) for _ in range(52)];row[13]=Cell(rarity);row[50]=Cell(base);row[51]=Cell(plus);rows.append(row)
    helper_rows=[[Cell('header')]] + [[Cell(cid),Cell(5),Cell(0),Cell('隊員'),Cell('レジェンド')] for cid in range(201,211)]
    getter=NS(download=lambda local,filename:helper_rows if 'Members_name' in filename else rows)
    module=NS(get_character_metadata=lambda:{'characters':chars,'groups':[
                  {'kind':'collab','cat_ids':[4,5,6,7]},
                  {'kind':'gacha','cat_ids':[1,2,3,4,6,8,9]}]},
              get_legend_metadata=lambda:{'talent_orbs':[{'ranks':[{'id':0,'rank':'D'},{'id':1,'rank':'S'},{'id':2,'rank':'S'}]}]},
              _latest_jp_game_data_getter=lambda:getter,_apply_all_segments_unlocked=lambda *args:[],
              core=NS(CSV=lambda data,*args,**kwargs:data,Delimeter=NS(from_country_code_res=lambda cc:','),
                      CountryCode=NS(from_code=lambda cc:cc),game=NS(gamoto=NS(gamatoto=NS(Helper=lambda cid:NS(id=cid))))))
    def talent(cat,tid,level): cat.talents[tid]=level
    module._set_cat_talent_level=talent
    return module


def fixture_save():
    cats=[NS(id=cid,unlocked=True,upgrade=NS(base=0,plus=7),talents={}) for cid in range(9)]
    orbs={0:21,1:22,2:23}
    return NS(cats=NS(cats=cats,get_cat_by_id=lambda cid:cats[cid] if 0<=cid<len(cats) else None),
              talent_orbs=NS(orbs=orbs,set_orb=lambda cid,amount:orbs.__setitem__(cid,amount)),
              gamatoto=NS(helpers=NS(helpers=[])),labyrinth_medals=[1,2,3,4])

class DetailedApplicationTests(unittest.TestCase):
    def test_bridge_runs_details_after_original_and_clears_context(self):
        module=fixture_module();save=fixture_save();calls=[];order=[]
        def original_apply(current,*args,**kwargs):
            order.append('original')
            current.cats.cats[7].upgrade.plus=99
            return ['元処理']
        module._apply_all_segments_unlocked=original_apply
        def worker(*args,**kwargs):
            calls.append(kwargs)
            logs=module._apply_all_segments_unlocked(save)
            self.assertEqual(logs[0],'元処理');self.assertTrue(10<=save.cats.cats[7].upgrade.plus<=15)
        module.run_job_daiko=worker
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=module;engine.install_detail_adapter(module)
            plan=parse_request('【レベル変更】\n超激レア30+10〜15\nプレイ時間は8000〜8200の間で適当\n引き継ぎコード:abc123456\n認証番号:0123')
            engine.execute('jid','oid',plan)
            self.assertIsNone(engine.detail_context.compiled)
            self.assertEqual(calls[0]['custom_playtime'],plan['custom_playtime'])
            self.assertEqual(order,['original'])
            # An unrelated engine call must not replay the local chat's details.
            self.assertEqual(module._apply_all_segments_unlocked(save),['元処理'])
            self.assertEqual(save.cats.cats[7].upgrade.plus,99)
    def test_apply_levels_orbs_talents_and_helpers(self):
        module=fixture_module();save=fixture_save();plan=parse_request(REQUEST)
        compiled=compile_details(module,plan); logs=apply_details(module,save,compiled)
        levels=[(c.upgrade.base+1,c.upgrade.plus) for c in save.cats.cats]
        self.assertEqual(levels[0],(20,90)); self.assertEqual(levels[1],(30,0));self.assertEqual(levels[2],(30,90))
        self.assertEqual(levels[3],(50,90));self.assertTrue(20<=levels[4][1]<=30)
        self.assertEqual(levels[5],(50,90));self.assertTrue(10<=levels[6][1]<=20)
        self.assertTrue(10<=levels[7][1]<=15);self.assertEqual(levels[8],(50,0))
        self.assertEqual(save.talent_orbs.orbs,{0:21,1:10,2:10});self.assertEqual(save.cats.cats[7].talents,{10:10})
        self.assertEqual([x.id for x in save.gamatoto.helpers.helpers],list(range(201,211)))
        self.assertTrue(logs)
    def test_unspecified_rarities_and_locked_characters_are_unchanged(self):
        module=fixture_module();save=fixture_save();save.cats.cats[4].unlocked=False
        apply_details(module,save,compile_details(module,parse_request('【レベル変更】\nレア50+MAX')))
        self.assertEqual(save.cats.cats[3].upgrade.plus,90)
        for cid in [0,1,2,4,5,6,7,8]:self.assertEqual(save.cats.cats[cid].upgrade.plus,7)
    def test_missing_metadata_fails_before_transfer(self):
        module=fixture_module();module._latest_jp_game_data_getter=lambda:NS(download=lambda *args:None)
        calls=[];module.run_job_daiko=lambda *args,**kwargs:calls.append(args)
        plan=parse_request('【レベル変更】\nEX全てレベル30(+付けられるものは全て+Maxまで)\n引き継ぎコード:abc123456\n認証番号:0123')
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=module
            with self.assertRaises(ParseError):engine.execute('jid','oid',plan)
            self.assertFalse(calls);self.assertIsNone(engine.detail_context.compiled)
    def test_missing_target_does_not_report_success(self):
        module=fixture_module();save=fixture_save();save.cats.get_cat_by_id=lambda cid:None
        with self.assertRaises(RuntimeError):apply_details(module,save,compile_details(module,parse_request('【レベル変更】\nレア50+MAX')))
    def test_omitted_category_levels_use_fixed_defaults(self):
        module=fixture_module();save=fixture_save()
        apply_details(module,save,compile_details(module,parse_request('【レベル変更】\n超激レア')))
        self.assertEqual((save.cats.cats[7].upgrade.base+1,save.cats.cats[7].upgrade.plus),(60,90))
    def test_real_original_item_worker_keeps_excluded_resources(self):
        source=Path(__file__).resolve().parents[1]/'source/my_project/main13.py'
        if not source.exists():self.skipTest('元エンジンは配布ZIPに同梱しません')
        tree=ast.parse(source.read_text()); fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='apply_daiko_segment')
        module=fixture_module()
        env={'_apply_user_rank_reward_actions':lambda *args:[]}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),env)
        save=fixture_save();save.catfood=1;save.xp=2;save.np=3;save.catseyes=[4,5,6]
        save.battle_items=NS(items=[NS(amount=11),NS(amount=12)])
        save.normal_tickets=41;save.rare_tickets=42;save.platinum_tickets=43;save.legend_tickets=44
        save.catfruit=[51];save.catamins=[61];save.leadership=71
        p=parse_request(REQUEST)
        selected,amounts=resolve_maximums(p)
        env['apply_daiko_segment'](save,selected['s1'],[],amounts)
        self.assertEqual((save.catfood,save.xp,save.np),(58999,99999999,5000))
        self.assertEqual([x.amount for x in save.battle_items.items],[300,300]);self.assertEqual(save.catseyes,[200,200,200])
        self.assertEqual((save.normal_tickets,save.rare_tickets,save.platinum_tickets,save.legend_tickets),(41,999,43,44))
        self.assertEqual((save.catfruit,save.catamins,save.leadership),([51],[61],71))

class DetailedHttpTests(unittest.TestCase):
    setUp = original_tests.HttpTests.setUp
    tearDown = original_tests.HttpTests.tearDown
    request = original_tests.HttpTests.request
    chat = original_tests.HttpTests.chat
    confirm = original_tests.HttpTests.confirm
    def test_blockers_are_persistent_and_never_execute(self):
        data=self.chat(REQUEST+'\nメニュー999\nメニュー39\n引き継ぎコード:abc123456\n認証番号:0123')[1]
        self.assertEqual(data['kind'],'draft');self.assertFalse(data['ready']);self.assertFalse(self.engine.calls)
        again=self.chat('お願いします')[1];self.assertFalse(again['ready']);self.assertEqual(len(again['blockers']),2)
        draft=self.chat('メニュー999不要\nメニュー39不要')[1];self.assertFalse(self.engine.calls)
        started=self.confirm(draft)[1]
        self.assertEqual(started['kind'],'started')
        self.state.worker.shutdown(wait=True)
        self.assertEqual(len(self.engine.calls),1)
        self.assertEqual(self.engine.calls[0]['orb_s_amount'],10)
    def test_long_request_without_blockers_runs_once(self):
        text=REQUEST[:REQUEST.index('【仕様】')]+'\nプレイ時間は8000〜8200の間で適当\n引き継ぎコード:abc123456\n認証番号:0123'
        draft=self.chat(text,'long-request-id')[1];again=self.chat(text,'long-request-id')[1]
        self.assertEqual(draft['confirmation_id'],again['confirmation_id']);self.assertFalse(self.engine.calls)
        first=self.confirm(draft,'long-confirm-id')[1];second=self.confirm(draft,'long-confirm-id')[1]
        self.assertEqual(first['job_id'],second['job_id'])
        self.state.worker.shutdown(wait=True);self.assertEqual(len(self.engine.calls),1)

if __name__=='__main__':unittest.main(verbosity=2)
