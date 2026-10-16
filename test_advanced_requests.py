"""New inputs, exact target selection and native BCSFE save roundtrips."""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
from bcsfe import core
from local_chat.interpreter import interpret_request
from local_chat.conditional_engine import compile_conditional,apply_conditional
from local_chat.parser import ParseError


def fixture():
    core.core_data.config=core.Config(None)
    entries=[(0,'ネコ',False),(3,'ネコカンカン',False),(3,'コラボ激レア',True),
             (4,'鬼にゃんま',False),(4,'漆黒の魔女ダークキャスリィ',False),
             (5,'暁のイザナギ',False),(2,'ノノ',True),(2,'ちびガウ',True)]
    metadata={'characters':[{'id':i+1,'rarity':rarity,'names':[name],'collab':collab,'selectable':True,'form_count':3} for i,(rarity,name,collab) in enumerate(entries)],
              'groups':[{'kind':'gacha','cat_ids':[1,2,4,5,6]},{'kind':'collab','cat_ids':[2,3,7,8]}]}
    rows=[]
    for i,(rarity,_,_) in enumerate(entries):
        row=[0]*52;row[13]=rarity;row[50]=60;row[51]=0 if rarity==5 else 90;rows.append(','.join(map(str,row)))
    labels={'battle_items':['スピードアップ','トレジャーレーダー','ネコボン','ニャンピュータ','おかめはちもく','スニャイパー'],
            'catseyes':['キャッツアイ【EX】','キャッツアイ【レア】','キャッツアイ【激レア】','キャッツアイ【超激レア】','キャッツアイ【伝説】','キャッツアイ【闇】'],
            'catamins':['ネコビタンA','ネコビタンB','ネコビタンC']}
    legend={'vip_item_groups':labels,'event_stage_families':{
        'tower':{'maps':[{'id':i,'name':name,'stages':[str(n) for n in range(1,51)],'crowns':1} for i,name in enumerate(['風雲にゃんこ塔','異界にゃんこ塔'])]},
        'labyrinth':{'maps':[{'id':0,'name':'地底迷宮','stages':[str(n) for n in range(1,101)],'crowns':1}]}}}
    helper_rows=['id,rarity,bonus,name,class']
    for rarity,name,start,count in [(3,'レジェンド',100,10),(1,'熟練',200,5),(2,'カリスマ',300,5)]:
        helper_rows.extend(f'{i},{rarity},0,fixture-{i},{name}' for i in range(start,start+count))
    def download(location,name):
        if name=='unitbuy.csv':return core.Data('\n'.join(rows).encode())
        if name=='GamatotoExpedition_Members_name_ja.csv':return core.Data('\n'.join(helper_rows).encode())
        if name=='GamatotoExpedition.csv':return core.Data('\n'.join(str(i*100) for i in range(150)).encode())
        if name=='GamatotoExpedition_Limit.csv':return core.Data(b'150')
        raise AssertionError(name)
    module=NS(core=core,get_character_metadata=lambda:metadata,get_legend_metadata=lambda:legend,
              _latest_jp_game_data_getter=lambda:NS(download=download),VIP_ITEM_LABELS=labels)
    source=Path(__file__).resolve().parents[1]/'theme_update_source/main13.py'
    if not source.exists():source=Path(__file__).resolve().parents[1]/'my_project/main13.py'
    if source.exists():
        tree=ast.parse(source.read_text());functions={'_labyrinth_value','_set_labyrinth_value','_labyrinth_unlocked_cat_ids','_sync_labyrinth_remaining','apply_labyrinth_stages'}
        env={};exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in functions],type_ignores=[]),'labyrinth','exec'),env)
        for name in functions:setattr(module,name,env[name])
    save=core.SaveFile(cc=core.CountryCode.from_code('jp'),gv=core.GameVersion(150501))
    save.cats.cats=[core.Cat.init(i) for i in range(len(entries))];save.menu_unlocks=[0]*3
    for cat in save.cats.cats:cat.unlocked=True;cat.upgrade.base=19;cat.upgrade.plus=2
    save.catseyes=[9]*6;save.catamins=[8]*3
    save.cats.favourites={1:True};save.order_ids=['fixture-order']
    return module,save


class AdvancedTests(unittest.TestCase):
    def execute(self,text,module=None,save=None):
        if module is None:module,save=fixture()
        plan=interpret_request(text,character_metadata=module.get_character_metadata())
        self.assertEqual(plan['unresolved'],[],plan['unresolved'])
        compiled=compile_conditional(module,plan)
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):apply_conditional(module,save,compiled)
        loaded=core.SaveFile(core.Data(save.to_data().data),cc=save.cc)
        self.assertEqual(loaded.game_version.game_version,150501)
        self.assertEqual(loaded.order_ids,save.order_ids);self.assertEqual(loaded.cats.favourites,save.cats.favourites)
        return loaded,plan

    def test_resources_and_all_item_types_roundtrip(self):
        s,p=self.execute('プラチナのかけらは7個。\nトレジャーレーダーは128個。\nニャンピュータは354個。\nキャッツアイ闇は31個。\nネコビタンAは176個。\nネコビタンBは93個。\nネコビタンCは48個。')
        self.assertEqual(s.platinum_shards,7);self.assertEqual(s.battle_items.items[1].amount,128);self.assertEqual(s.battle_items.items[3].amount,354)
        self.assertEqual(s.catseyes,[9,9,9,9,9,31]);self.assertEqual(s.catamins,[176,93,48])

    def test_named_levels_aliases_and_unlocks(self):
        m,s=fixture();s.cats.cats[3].unlocked=False
        loaded,_=self.execute('鬼にゃんまを未所持の場合だけ解放して、レベル60＋8。\n黒キャスリィ：60＋3。\nイザナギ：50＋1。',m,s)
        self.assertTrue(loaded.cats.cats[3].unlocked)
        self.assertEqual((loaded.cats.cats[3].upgrade.base,loaded.cats.cats[3].upgrade.plus),(59,8))
        self.assertEqual((loaded.cats.cats[4].upgrade.base,loaded.cats.cats[4].upgrade.plus),(59,3))
        self.assertEqual(loaded.cats.cats[5].upgrade.plus,0)  # verified unsupported plus

    def test_regular_gacha_includes_banner_fillers_but_not_collab_units(self):
        loaded,_=self.execute('通常ガチャの激レアキャラだけ、＋値を30〜70の範囲でキャラごとにおまかせにしてください。')
        self.assertTrue(30<=loaded.cats.cats[1].upgrade.plus<=70)
        self.assertEqual(loaded.cats.cats[2].upgrade.plus,2)
        self.assertTrue(all(cat.upgrade.base==19 for cat in loaded.cats.cats))

    def test_named_plus_only_preserves_base(self):
        loaded,_=self.execute('鬼にゃんまは＋値だけ8。')
        self.assertEqual((loaded.cats.cats[3].upgrade.base,loaded.cats.cats[3].upgrade.plus),(19,8))

    def test_previous_hours_are_carried_into_added_requests(self):
        m,s=fixture();old=interpret_request('メニュー84=3247')
        plan=interpret_request('プラチナのかけらは7個。',old,character_metadata=m.get_character_metadata())
        self.assertFalse(plan['unresolved'])
        apply_conditional(m,s,compile_conditional(m,plan))
        self.assertEqual(s.officer_pass.play_time,core.PlayTime.from_hours(3247).frames)

    def test_count_target_never_relocks_existing_units(self):
        m,s=fixture();before=copy.deepcopy(s.cats.serialize())
        p=interpret_request('所持キャラ数は1体、調整対象:鬼にゃんま。',character_metadata=m.get_character_metadata())
        with self.assertRaisesRegex(ParseError,'多い'):apply_conditional(m,s,compile_conditional(m,p))
        self.assertEqual(s.cats.serialize(),before)

    def test_unsupported_playdays_keep_is_safe_but_set_stays_blocked(self):
        m,s=fixture();p=interpret_request('プレイ日数は現在のまま保持。\nプラチナのかけらは7個。',character_metadata=m.get_character_metadata())
        self.assertFalse(p['unresolved']);self.assertTrue(all(r['domain']!='days' for r in p['conditional_rules']))

    def test_playtime_minutes_seconds_and_gamatoto(self):
        loaded,_=self.execute('プレイ時間は3247時間18分45秒。\nガマトトはレベル130。')
        self.assertEqual(loaded.officer_pass.play_time,core.PlayTime.from_hours_mins_secs(3247,18,45).frames)
        self.assertEqual(loaded.gamatoto.xp,12900)

    def test_helper_mix_preserves_existing_matching_members(self):
        m,s=fixture();Helper=core.game.gamoto.gamatoto.Helper
        s.gamatoto.helpers.helpers=[Helper(102),Helper(203)]+[Helper(-1) for _ in range(8)]
        loaded,_=self.execute('隊員はレジェンド8体、熟練2体。',m,s)
        ids=[h.id for h in loaded.gamatoto.helpers.helpers]
        self.assertIn(102,ids);self.assertIn(203,ids)
        self.assertEqual(sum(100<=i<110 for i in ids),8);self.assertEqual(sum(200<=i<205 for i in ids),2)

    def test_unknown_other_helper_class_blocks(self):
        m,s=fixture();p=interpret_request('隊員はレジェンド8体、その他2体。',character_metadata=m.get_character_metadata())
        self.assertFalse(p['ready']);self.assertIn('クラス',p['unresolved'][0]['reason'])

    def test_towers_preserve_unrequested_higher_floors_and_other_map(self):
        m,s=fixture()
        loaded,_=self.execute('にゃんこ塔は50階までクリア。\n異界にゃんこ塔は30階までクリア。',m,s)
        self.assertTrue(all(st.clear_times>0 for st in loaded.tower.chapters.chapters[0].chapters[0].stages[:50]))
        self.assertEqual(loaded.tower.chapters.chapters[1].chapters[0].stages[30].clear_times,0)

    def test_labyrinth_highest_does_not_change_current_floor(self):
        m,s=fixture()
        if not hasattr(m,'apply_labyrinth_stages'):self.skipTest('既存main13.pyが必要です')
        s.ush4=15;s.ush5=20
        loaded,_=self.execute('地底迷宮の最高到達地点は地下87階にしてください。',m,s)
        self.assertEqual(loaded.ush5,87);self.assertEqual(loaded.ush4,15)

    def test_rank_adjustment_uses_only_named_plus_values(self):
        m,s=fixture();old=[(c.upgrade.base,c.upgrade.plus) for c in s.cats.cats];target=s.calculate_user_rank()+15
        loaded,_=self.execute(f'ユーザーランクは{target}、調整対象:ネコカンカン。',m,s)
        self.assertEqual(loaded.calculate_user_rank(),target)
        for i,c in enumerate(loaded.cats.cats):
            if i!=1:self.assertEqual((c.upgrade.base,c.upgrade.plus),old[i])

    def test_count_adjustment_unlocks_only_declared_candidates(self):
        m,s=fixture();s.cats.cats[3].unlocked=False;s.cats.cats[4].unlocked=False
        loaded,_=self.execute('超激レアの数は1体、調整対象:鬼にゃんま。',m,s)
        self.assertTrue(loaded.cats.cats[3].unlocked);self.assertFalse(loaded.cats.cats[4].unlocked)
        self.assertEqual(loaded.cats.cats[3].upgrade.base,19)

    def test_missing_targets_or_unverified_days_are_blockers(self):
        m,s=fixture()
        for text in ['ユーザーランクは45218。','所持キャラ数は782体。','プレイ日数は3586日。']:
            p=interpret_request(text,character_metadata=m.get_character_metadata());self.assertFalse(p['ready']);self.assertTrue(p['unresolved'])

    def test_caps_and_missing_definitions_never_become_guesses(self):
        m,s=fixture();p=interpret_request('にゃんこ塔は51階までクリア。',character_metadata=m.get_character_metadata())
        with self.assertRaises(ParseError):compile_conditional(m,p)
        p=interpret_request('ユーザーランクは999999、調整対象:ネコカンカン。',character_metadata=m.get_character_metadata())
        with self.assertRaises(ParseError):apply_conditional(m,s,compile_conditional(m,p))


if __name__=='__main__':unittest.main()
