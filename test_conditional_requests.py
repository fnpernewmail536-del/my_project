import unittest,copy
from types import SimpleNamespace as NS
from unittest.mock import patch
from local_chat.interpreter import interpret_request
from local_chat.conditional_engine import value,predicate,compile_conditional,apply_conditional
from local_chat.parser import ParseError

CASES=[
'現在レベル30未満の超激レアだけ50にしてください。ただしコラボキャラは対象外です。',
'現在＋値が20以上のレアキャラは＋値を変更せず、20未満のキャラだけ＋30にしてください。',
'未解放の伝説レアは全て解放してください。ただし、すでに持っている伝説レアのレベルは変更しないでください。',
'るろうに剣心コラボのキャラは全解放でお願いします。ただし比古清十郎と志々雄真実だけレベル50、ほかはレベル40にしてください。',
'本能は基本MAXですが、移動速度アップだけ未解放のままにしてください。すでに解放している移動速度アップ本能は変更しないでください。',
'日本編のお宝は全て最高にしてください。ただし、現在普通のお宝になっているものは普通のままにしてください。',
'宇宙編第3章はクリア済みステージだけお宝を最高にしてください。未クリアステージには触らないでください。',
'レジェンドストーリーは星1〜3を全クリアにして、星4だけ現在の進行状況を維持してください。',
'XPがすでに90000000以上なら変更しないでください。90000000未満なら90000000〜95000000の範囲でランダムにしてください。',
'ネコカンは現在の所持数に15000個追加してください。ただし、合計が58999個を超える場合は58999個にしてください。',
'すでにMAXになっているアイテムは変更せず、MAX未満のものだけMAXにしてください。',
'ガマトト隊員は現在の隊員を残し、足りない人数だけ追加してください。既存隊員の名前・レアリティは変更しないでください。',
'キャラクターの解放状態は変更しても、現在使用中の編成10枠は一切変更しないでください。',
'全ての処理が終わったあと、指定した値が反映されているか確認してください。条件に合わないものがあれば、その項目だけ報告してください。',
]
class ConditionalTests(unittest.TestCase):
 def test_arithmetic(self):
  self.assertEqual(value({'op':'mul','value':'1.5'},111,58999),166)
  self.assertEqual(value({'op':'add','value':15000},55000,58999),58999)
  self.assertEqual(value({'op':'set','value':20},40,90,True),40)
 def test_predicates(self):
  self.assertTrue(predicate([{'op':'odd'}],{'plus':3},'plus'))
  self.assertFalse(predicate([{'op':'ge','value':20}],{'plus':19},'plus'))
 def test_effects(self):
  from test_detailed_requests import fixture_module,fixture_save
  mod=fixture_module();meta=mod.get_character_metadata()
  for c in meta['characters']:
   c['names']=['キャラ'+str(c['id'])];c['collab']=c['id'] in (5,7)
   for t in c['talents']:t['name']='攻撃力アップ'
  mod.get_character_metadata=lambda:meta
  p=interpret_request('ネコカンは現在値の1.5倍。ただし58999を上限とする。\n基本キャラは基本レベルを一切変更せず、＋値だけ現在値に20追加してください。\n現在レベル30未満の超激レアだけ50にしてください。ただしコラボキャラは対象外です。\n本能は全解放MAX。移動速度だけ現在値キープで。',character_metadata=meta)
  self.assertEqual(p['unresolved'],[])
  save=fixture_save();save.catfood=50000
  for c in save.cats.cats:c.talents=[]
  mod._set_cat_talent_level=lambda c,tid,v:c.talents.append(NS(id=tid,level=v))
  save.cats.cats[0].upgrade.plus=80;save.cats.cats[7].upgrade.base=20
  compiled=compile_conditional(mod,p);apply_conditional(mod,save,compiled)
  self.assertEqual(save.catfood,58999);self.assertEqual(save.cats.cats[0].upgrade.plus,90);self.assertEqual(save.cats.cats[7].upgrade.base,49)
 def test_unknown(self):
  p=interpret_request('おまかせでいいので強いデータにしてほしいです！\nNPはそこそこで。\nいらないキャラは解放しなくていいです。')
  self.assertFalse(p['ready']);self.assertTrue(any('数量' in x['reason'] for x in p['unresolved']))
 def test_examples_are_never_silently_replaced(self):
  for s in CASES:
   with self.subTest(s=s):
    p=interpret_request('今回は固定値ではなく、現在のデータを基準にして変更をお願いします。\n'+s)
    self.assertTrue(p.get('conditional_rules') or p['unresolved'] or re.search('編成|確認',s))
import re
if __name__=='__main__':
 for i,s in enumerate(CASES):
  p=interpret_request('今回は固定値ではなく、現在のデータを基準にして変更をお願いします。\n'+s)
  print(i+1,p['unresolved'],len(p['conditional_rules']))
