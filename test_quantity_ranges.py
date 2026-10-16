import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from local_chat.interpreter import interpret_request
from local_chat.details import apply_details,compile_details
from local_chat.parser import ParseError

TEXT='''【アイテム】
猫缶は42000〜58000からおまかせ
XPは65000000〜95000000からおまかせ
NPは3500〜8500からおまかせ
リーダーシップは200〜700からおまかせ
バトルアイテムは種類ごとに150〜650
キャッツアイは種類ごとに80〜280
ネコビタンは種類ごとに40〜180
城素材は種類ごとに200〜800
マタタビは種類ごとに250〜750
本能玉は種類・ランクごとに5〜35
獣石は種類ごとに100〜500
それぞれの種類で個別にランダムな数量を選んでください。
全種類を同じ数量にそろえないでください。
【チケット類】
通常チケットは450〜950
レアチケットは250〜750
プラチナチケットは15〜55
レジェンドチケットは8〜35
各チケットの数量は別々に選んでください。
【キャラ類】
コラボ含め全キャラ解放
【レベル変更】
基本キャラ：基本Lv20、＋値45〜85
EXキャラ：基本Lv25〜30、＋値20〜55
レアキャラ：基本Lv35〜50、＋値40〜80
激レアキャラ：基本Lv35〜50、＋値25〜65
超激レアキャラ：基本Lv30〜45、＋値5〜18
伝説レア：基本Lv40〜50、＋値1〜4
レアのコラボキャラ：＋値12〜24
激レアのコラボキャラ：＋値7〜16
超激レアのコラボキャラ：＋値1〜5
基本Lvと＋値はキャラ1体ごとに個別に選んでください。
範囲指定は全員共通の1つの数値にしないでください。
＋値を付けられないキャラには付けないでください。
レベル除外：
・ネコ
・タンクネコ
・バトルネコ
・キモネコ
・ウシネコ
・ネコノトリ
・ネコフィッシュ
・ネコトカゲ
・巨神ネコ
・ネコエステ
上記10体は基本Lv・＋値ともに現在のまま
キャラの本能は全て解放MAX
【ステージ】
メニュー38
メニュー48
メニュー53
メニュー55
メニュー56
メニュー57
未来編採点スコアは全章の各ステージで7500〜10000
ステージごとに個別に数値を選んでください。
道場スコアは180000〜380000からおまかせ
【その他】
施設MAX
エラーキャラ削除
メニュー64
【ガマトト】
ガマトトレベルは95〜120からおまかせ
全てレジェンド
オトートMAX
神社MAX
【仕様】
メニュー72
メニュー73
メニュー74
メニュー75
メニュー76
メニュー78
メニュー79
メニュー81
地底迷宮勲章は4種類それぞれ350〜950
種類ごとに個別に数量を選んでください。
メニュー83
プレイ時間6800〜8300'''

class RangeTests(unittest.TestCase):
 def test_full(self):
  p=interpret_request(TEXT)
  self.assertEqual(p['unresolved'],[])
  self.assertEqual(p['unlock_queries'],[])
  self.assertEqual(p['level_rules']['1']['base'],[25,30])
  self.assertTrue(all(r.get('plus_only_supported') for r in p['level_rules'].values()))
  self.assertEqual(len(p['level_exclusion_queries']),10)
  self.assertEqual(p['quantity_ranges']['46'],[7500,10000])
 def test_override(self):
  p=interpret_request('キャッツアイ80〜280')
  p=interpret_request('キャッツアイ123',p)
  self.assertNotIn('9',p['quantity_ranges'])
  self.assertFalse(any(x['key']=='range_9' for x in p['labels']))
 def test_remove(self):
  p=interpret_request('メニュー9=80〜280');p=interpret_request('メニュー9不要',p)
  self.assertNotIn('9',p['quantity_ranges'])
 def test_boundaries(self):
  for text in ['猫缶60000〜70000','プラチナチケット15〜100','NP500〜400']:
   with self.assertRaises(ParseError):interpret_request(text)
 def test_group_write(self):
  stages=[NS(itf_timed_score=0) for _ in range(3)]
  save=NS(catseyes=[0]*3,catamins=[0]*3,battle_items=NS(items=[NS(amount=0) for _ in range(3)]),ototo=NS(base_materials=NS(materials=[NS(amount=0) for _ in range(3)])),labyrinth_medals=[0]*4,story=NS(chapters=[NS(get_valid_treasure_stages=lambda:stages)]*3),talent_orbs=NS(set_orb=lambda i,v:orbs.update({i:v})))
  orbs={};module=NS(_latest_talent_orb_ids=lambda:[1,2,3],MAIN_STORY_REAL_INDEX={3:0,4:1,5:2})
  counter=iter([0,1,2]*20)
  with patch('local_chat.range_input.secrets.randbelow',side_effect=lambda n:next(counter)%n):
   apply_details(module,save,{'quantity_ranges':{str(n):[10,20] for n in [8,9,10,11,13,46,82]},'labyrinth_amount':10})
  self.assertEqual(save.catseyes,[10,11,12]);self.assertEqual(set(orbs.values()),{10,11,12})
  self.assertGreater(len(set(save.labyrinth_medals)),1)
  self.assertEqual([s.itf_timed_score for s in stages],[10,11,12])

 def test_character_roll_and_exclusion(self):
  from test_collab_exclusions import metadata,module,save_data,snapshot
  meta=metadata();mod=module(meta)
  p=interpret_request('レアキャラ：基本Lv35〜50、＋値40〜80\nレアのコラボキャラ：＋値12〜24\nレベル除外=チビガウとメルストのノノ',character_metadata=meta)
  save,cats=save_data();before=snapshot(cats)
  draws=iter([0,0,1,1]*10)
  with patch('local_chat.range_input.secrets.randbelow',side_effect=lambda n:next(draws)%n):
   compiled=compile_details(mod,p);apply_details(mod,save,compiled)
  for cid in (68,112):self.assertEqual((cats[cid].upgrade.base,cats[cid].upgrade.plus),before[cid][:2])
  self.assertEqual(cats[10].upgrade.base+1,35)
  self.assertEqual(cats[120].upgrade.base+1,36)
  self.assertTrue(12<=cats[120].upgrade.plus<=24)
 def test_unsupported_plus(self):
  from test_detailed_requests import fixture_module
  p=interpret_request('EXキャラ：基本Lv25〜30、＋値20〜55\n＋値を付けられないキャラには付けないでください')
  compiled=compile_details(fixture_module(),p)
  self.assertEqual(next(x['plus'] for x in compiled['levels'] if x['id']==2),0)
  self.assertTrue(all(25<=x['base']<=30 for x in compiled['levels']))
 def test_fruit_types(self):
  mod=NS(_apply_all_segments_unlocked=lambda *a:[],get_legend_metadata=lambda:{'vip_item_groups':{'catfruit':['緑マタタビ','紫マタタビ','紅獣石','蒼獣石','その他']}})
  p=interpret_request('マタタビ種類ごとに250〜750\n獣石種類ごとに100〜500')
  with patch('local_chat.range_input.secrets.randbelow',side_effect=[1,2,3,4]):compiled=compile_details(mod,p)
  save=NS(catfruit=[9]*5);apply_details(mod,save,compiled)
  self.assertEqual(save.catfruit,[251,252,103,104,9])
