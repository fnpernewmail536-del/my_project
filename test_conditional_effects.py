import copy,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from local_chat.interpreter import interpret_request
from local_chat.conditional_engine import compile_conditional,apply_conditional
from local_chat.parser import ParseError
from test_detailed_requests import Cell
from test_conditional_requests import CASES

def fixture():
 entries=[(1,0,'ネコ',False,20,90),(2,0,'タンクネコ',False,20,90),(3,1,'ちびネコ',False,50,50),(4,2,'ネコキョンシー',False,50,80),(5,2,'ノノ',True,50,80),(6,3,'ネコカンカン',False,50,70),(7,4,'通常超激',False,60,70),(8,4,'比古清十郎',True,60,70),(9,4,'志々雄真実',True,60,70),(10,4,'他るろ剣キャラ',True,60,70),(11,5,'伝説キャラ',False,50,0)]
 chars=[];cats={};rows=[]
 for cid,rarity,name,collab,basecap,pluscap in entries:
  chars.append({'id':cid,'rarity':rarity,'names':[name],'collab':collab,'form_count':3,'talents':[{'id':1,'name':'移動速度アップ','max_level':10},{'id':2,'name':'攻撃力アップ','max_level':10}]})
  c=NS(id=cid-1,unlocked=True,upgrade=NS(base=24,plus=15),talents=[NS(id=1,level=3)],current_form=0,unlocked_forms=1,favourite=True)
  c.unlock=lambda save,c=c:setattr(c,'unlocked',True);cats[cid]=c
  row=[Cell(0) for _ in range(52)];row[13]=Cell(rarity);row[50]=Cell(basecap);row[51]=Cell(pluscap);rows.append(row)
 metadata={'characters':chars,'groups':[{'kind':'collab','name':'るろうに剣心コラボガチャ','cat_ids':[8,9,10]}]}
 def story():
  stages=[NS(clear_times=0,treasure=0) for _ in range(48)];ch=NS(stages=stages,progress=0)
  ch.get_valid_treasure_stages=lambda:stages
  def clear(i,v):stages[i].clear_times=v;ch.progress=max(ch.progress,i+1)
  ch.clear_stage=clear;return ch
 def crown():
  stages=[];ch=NS(clear_progress=0,chapter_unlock_state=0)
  for _ in range(2):
   s=NS(clear_times=0);s.clear_stage=lambda v,ensure_cleared_only=False,s=s:setattr(s,'clear_times',max(s.clear_times,v));stages.append(s)
  ch.stages=stages;return ch
 maps=NS(chapters=[NS(chapters=[crown() for _ in range(4)])])
 save=NS(catfood=50000,xp=80000000,np=2000,normal_tickets=10,rare_tickets=100,platinum_tickets=7,legend_tickets=3,leadership=40,cats=NS(get_cat_by_id=lambda cid:cats[cid+1]),battle_items=NS(items=[NS(amount=v) for v in [7,13,50,16,150,400]]),catseyes=[9]*6,catamins=[8]*3,catfruit=[8]*3,ototo=NS(base_materials=NS(materials=[NS(amount=7)]*2),cannons=NS(cannons={})),talent_orbs=NS(orbs={1:6,2:15,3:8}),story=NS(chapters=[story() for _ in range(10)]),event_stages=NS(chapters=[maps,NS(chapters=[])]),uncanny=NS(chapters=NS(chapters=[NS(chapters=[crown() for _ in range(4)])])),zero_legends=NS(chapters=[NS(chapters=[crown() for _ in range(4)])]),lineups=NS(serialize=lambda:{'cats':list(range(10))}),gamatoto=NS(helpers=NS(helpers=[NS(id=99),NS(id=200)]+[NS(id=-1) for _ in range(8)])))
 save.talent_orbs.set_orb=lambda i,v:save.talent_orbs.orbs.__setitem__(i,v)
 legends={'legend':{'maps':[{'id':0,'name':'章','stages':['1','2'],'crowns':4}]},'true_legend':{'maps':[{'id':0,'name':'真','stages':['1','2'],'crowns':4}]},'zero_legend':{'maps':[{'id':0,'name':'零','stages':['1','2'],'crowns':4}]},'vip_item_groups':{'battle_items':['スピードアップ','トレジャーレーダー','ネコボン','ニャンピュータ','おかめはちもく','スニャイパー'],'catseyes':['キャッツアイ【EX】','キャッツアイ【レア】','キャッツアイ【激レア】','キャッツアイ【超激レア】','キャッツアイ【伝説】','キャッツアイ【闇】'],'catfruit':['赤マタタビ','蒼獣石','緑マタタビ']},'talent_orbs':[{'ranks':[{'id':1,'rank':'S'},{'id':2,'rank':'S'},{'id':3,'rank':'A'}]}]}
 helpers=[[Cell('header')]]+[[Cell(i),Cell(0),Cell(0),Cell('名'),Cell('レジェンド')] for i in range(200,210)]
 module=NS(get_character_metadata=lambda:metadata,get_legend_metadata=lambda:legends,_latest_jp_game_data_getter=lambda:NS(download=lambda loc,name:helpers if 'Members_name' in name else rows),core=NS(CSV=lambda data,*a,**kw:data,Delimeter=NS(from_country_code_res=lambda x:','),CountryCode=NS(from_code=lambda x:x),game=NS(gamoto=NS(gamatoto=NS(Helper=lambda i:NS(id=i))))),MAIN_STORY_REAL_INDEX=[0,1,2,4,5,6,7,8,9])
 def set_talent(c,tid,v):
  old=next((t for t in c.talents if t.id==tid),None)
  if old:old.level=v
  else:c.talents.append(NS(id=tid,level=v))
 module._set_cat_talent_level=set_talent
 return module,save,cats

def execute(text,module,save):
 p=interpret_request('今回は固定値ではなく、現在のデータを基準にして変更をお願いします。\n'+text,character_metadata=module.get_character_metadata())
 if p['unresolved']:raise AssertionError(p['unresolved'])
 return apply_conditional(module,save,compile_conditional(module,p))

class EffectTests(unittest.TestCase):
 def test_np_thresholds_use_original(self):
  for before,after in [(0,4000),(2999,6999),(3000,5000),(5999,7999),(6000,6000),(9999,9999)]:
   m,s,c=fixture();s.np=before
   execute('NP：現在値が3000未満なら＋4000、3000以上6000未満なら＋2000、6000以上なら変更不要。',m,s)
   self.assertEqual(s.np,after)
 def test_formal_ticket_conditions(self):
  for before,after in [(0,300),(99,300),(100,200),(299,399),(300,300),(900,900)]:
   m,s,c=fixture();s.rare_tickets=before
   execute('レアチケットは現在値をxとして、\nx＜100の場合は300枚、\n100≦x＜300の場合はx＋100枚、\nx≧300の場合は現在値を維持。',m,s)
   self.assertEqual(s.rare_tickets,after)
 def test_plus_thresholds(self):
  for v,expected in [(19,30),(20,20),(50,50)]:
   m,s,c=fixture();c[4].upgrade.plus=v
   execute(CASES[1],m,s);self.assertEqual(c[4].upgrade.plus,expected)
 def test_normal_only_base_threshold(self):
  m,s,c=fixture();c[7].upgrade.base=25;c[8].upgrade.base=25
  execute(CASES[0],m,s);self.assertEqual(c[7].upgrade.base,49);self.assertEqual(c[8].upgrade.base,25)
 def test_collab_names_and_group(self):
  m,s,c=fixture();execute(CASES[3],m,s)
  self.assertEqual([c[i].upgrade.base+1 for i in (8,9,10)],[50,50,40])
 def test_unlock_only_owned_levels_kept(self):
  m,s,c=fixture();c[11].unlocked=False;before=c[11].upgrade.base
  execute(CASES[2],m,s);self.assertTrue(c[11].unlocked);self.assertEqual(c[11].upgrade.base,before)
 def test_speed_and_talents(self):
  m,s,c=fixture();execute(CASES[4],m,s)
  for cat in c.values():
   self.assertEqual(next(t.level for t in cat.talents if t.id==1),3)
   self.assertEqual(next(t.level for t in cat.talents if t.id==2),10)
 def test_normal_treasure_stays(self):
  m,s,c=fixture();s.story.chapters[0].stages[0].treasure=2
  execute(CASES[5],m,s);self.assertEqual(s.story.chapters[0].stages[0].treasure,2);self.assertEqual(s.story.chapters[0].stages[1].treasure,3)
 def test_space_uncleared_untouched(self):
  m,s,c=fixture();st=s.story.chapters[9].stages;st[0].clear_times=7;st[1].treasure=1
  execute(CASES[6],m,s);self.assertEqual((st[0].clear_times,st[0].treasure),(7,3));self.assertEqual((st[1].clear_times,st[1].treasure),(0,1))
 def test_crown4_stays(self):
  m,s,c=fixture();chap=s.event_stages.chapters[0].chapters[0].chapters;chap[3].stages[0].clear_times=5
  execute(CASES[7],m,s)
  self.assertTrue(all(st.clear_times>0 for ch in chap[:3] for st in ch.stages));self.assertEqual([st.clear_times for st in chap[3].stages],[5,0])
 def test_resource_add_cap(self):
  m,s,c=fixture();s.catfood=56000;execute(CASES[9],m,s);self.assertEqual(s.catfood,58999)
 def test_xp_keep_above_threshold(self):
  m,s,c=fixture();s.xp=99999999;execute(CASES[8],m,s);self.assertEqual(s.xp,99999999)
 def test_helpers_fill_keeps_existing(self):
  m,s,c=fixture();before=s.gamatoto.helpers.helpers[:2];execute(CASES[11],m,s)
  self.assertIs(s.gamatoto.helpers.helpers[0],before[0]);self.assertIs(s.gamatoto.helpers.helpers[1],before[1]);self.assertEqual(len(s.gamatoto.helpers.helpers),10)
 def test_specific_items_override_max(self):
  m,s,c=fixture();before=s.battle_items.items[0].amount
  execute('アイテム全部MAX。\nネコボンは400個。\nスピードアップは現在の所持数を維持。\nスニャイパーは現在の2倍。',m,s)
  self.assertEqual(s.battle_items.items[2].amount,400);self.assertEqual(s.battle_items.items[5].amount,800);self.assertEqual(s.battle_items.items[0].amount,before)
 def test_later_star_replaces_earlier(self):
  m,s,c=fixture();execute('真レジェンドは星4まで全クリア。\n真レジェンドは星1だけ全クリア。',m,s)
  ch=s.uncanny.chapters.chapters[0].chapters
  self.assertTrue(all(st.clear_times>0 for st in ch[0].stages));self.assertTrue(all(st.clear_times==0 for crown in ch[1:] for st in crown.stages))
 def test_no_double_add(self):
  m,s,c=fixture();s.catfood=10000;execute('ネコカンは現在値に15000追加。\nネコカンは現在値に20000追加。',m,s);self.assertEqual(s.catfood,30000)
 def test_preflight_name_failure(self):
  m,s,c=fixture();p=interpret_request('ネコカンは現在値に15000追加。\nネコ偽名はレベル50。',character_metadata=m.get_character_metadata())
  self.assertFalse(p['ready']);self.assertTrue(p['blockers'])
 def test_cap_support(self):
  m,s,c=fixture();execute('基本キャラは基本レベル20、＋値だけ現在値に100追加。\n伝説レアは全員50＋MAX。',m,s)
  self.assertEqual(c[1].upgrade.plus,90);self.assertEqual(c[11].upgrade.plus,0)
 def test_metadata_change_blocks(self):
  m,s,c=fixture();p=interpret_request('未解放の伝説レアは全て解放。',character_metadata=m.get_character_metadata())
  m.get_character_metadata()['characters'][-1]['rarity']=4
  with self.assertRaises(ParseError):compile_conditional(m,p)
 def test_multiple_examples_parse_with_real_names(self):
  from test_collaboration_levels import original_metadata
  for f in Path(__file__).with_name('examples').glob('複雑依頼_*.txt'):
   p=interpret_request(f.read_text(),character_metadata=original_metadata())
   self.assertEqual(p['unresolved'],[],f.name)

 def test_ambiguous_examples_block(self):
  from test_collaboration_levels import original_metadata
  for f in Path(__file__).with_name('examples').glob('停止例_*.txt'):
   p=interpret_request(f.read_text(),character_metadata=original_metadata())
   self.assertFalse(p['ready']);self.assertTrue(p['blockers'])
 def test_unresolved_never_fetches_account(self):
  from local_chat.engine import Engine
  m,s,c=fixture();calls=[];m.run_job_daiko=lambda *a,**kw:calls.append(a)
  import tempfile
  with tempfile.TemporaryDirectory() as tmp:
   engine=Engine(tmp,tmp);engine.module=m
   p=interpret_request('XPは多めで。現在値から考えて。\n引き継ぎコード:abc123456\n認証番号:0123',character_metadata=m.get_character_metadata())
   with self.assertRaises(ParseError):engine.execute('job','op',p)
  self.assertEqual(calls,[])
 def test_favorites_and_order_and_forms_stay(self):
  m,s,c=fixture();s.order_ids=['1:2','3:4'];s.cats.favourites={1:True,2:False}
  execute('未解放の伝説レアは全て解放してください。\n基本キャラは全員20＋90。',m,s)
  self.assertEqual(s.order_ids,['1:2','3:4']);self.assertEqual(s.cats.favourites,{1:True,2:False});self.assertTrue(all(cat.current_form==0 for cat in c.values()))
 def test_prior_draft_survives_additional_condition(self):
  m,s,c=fixture();old=interpret_request('レアチケット300\n猫缶50000')
  p=interpret_request('XPは現在値に25000000追加。',old,character_metadata=m.get_character_metadata())
  self.assertFalse(p['unresolved'])
  apply_conditional(m,s,compile_conditional(m,p))
  self.assertEqual(s.rare_tickets,300);self.assertEqual(s.catfood,50000);self.assertEqual(s.xp,99999999)
 def test_four_exclusions_not_two(self):
  from test_collaboration_levels import original_metadata
  f=Path(__file__).with_name('examples')/'複雑依頼_連続訂正.txt'
  p=interpret_request(f.read_text(),character_metadata=original_metadata())
  names={n for r in p['conditional_rules'] if r['field']=='unlocked' and r['expr']['op']=='keep' for n in r.get('selector',{}).get('names',[])}
  self.assertEqual(names,{'ちびガウ','ノノ','ネコカンカン','ネコ半魚人'})
