"""Configured MAX values exercised through the bridge and original editing code."""
import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from local_chat.parser import parse_request
from local_chat.engine import Engine,resolve_maximums
from local_chat.details import compile_details,apply_details
from test_detailed_requests import fixture_module,fixture_save

class MaxDefaultsTests(unittest.TestCase):
    def test_item_aliases_and_values(self):
        p=parse_request('猫缶、XP、NP、プラチケ、レジェチケ、マタたび、本能玉、バトルアイテム')
        _,amounts=resolve_maximums(p)
        self.assertEqual(amounts['custom_catfood'],58999)
        self.assertEqual(amounts['custom_xp'],99999999);self.assertEqual(amounts['custom_np'],9999)
        self.assertEqual(amounts['custom_platinum_tickets'],99);self.assertEqual(amounts['custom_legend_tickets'],99)
        self.assertEqual(p['fruit_filters']['matatabi'],998);self.assertEqual(amounts['custom_talent_orbs'],998)
        self.assertEqual(amounts['custom_battle_items'],999)
    def test_all_rarity_max_uses_60_plus_90(self):
        m=fixture_module();save=fixture_save()
        apply_details(m,save,compile_details(m,parse_request('メニュー29')))
        self.assertTrue(all((cat.upgrade.base+1,cat.upgrade.plus)==(60,90) for cat in save.cats.cats))
        for text in ['キャラMAX','キャラMAX+','キャラ']:
            self.assertEqual(parse_request(text)['selected']['s3'],['max_all_cats_plus'])
    def test_explicit_values_and_exclusions_override_defaults(self):
        p=parse_request('【レベル変更】\n基本20+90\nレアMAX+10\n伝説50+なし\n【アイテム】\n猫缶10000\nXP不要\nNP5000\nプラチケ20')
        selected,amounts=resolve_maximums(p)
        self.assertEqual(amounts['custom_catfood'],10000);self.assertEqual(amounts['custom_np'],5000)
        self.assertEqual(amounts['custom_platinum_tickets'],20);self.assertNotIn('custom_xp',selected['s1'])
        m=fixture_module();save=fixture_save();apply_details(m,save,compile_details(m,p))
        self.assertEqual((save.cats.cats[0].upgrade.base+1,save.cats.cats[0].upgrade.plus),(20,90))
        self.assertEqual((save.cats.cats[3].upgrade.base+1,save.cats.cats[3].upgrade.plus),(60,10))
        self.assertEqual((save.cats.cats[8].upgrade.base+1,save.cats.cats[8].upgrade.plus),(50,0))
    def test_numbered_stones_and_medals_default_to_999(self):
        self.assertEqual(parse_request('メニュー14')['fruit_filters']['stones'],999)
        self.assertEqual(parse_request('メニュー82')['labyrinth_amount'],999)
        self.assertEqual(parse_request('メニュー12')['fruit_filters']['matatabi'],998)
        p=parse_request('猫缶10000');p=parse_request('アイテム全部MAX',p)
        _,amounts=resolve_maximums(p);self.assertEqual(amounts['custom_catfood'],58999)
    def test_original_worker_receives_new_defaults_and_separate_material_quantities(self):
        source=Path(__file__).resolve().parents[1]/'source/my_project/main13.py'
        if not source.exists():self.skipTest('元エンジンは配布ZIPに含めません')
        function=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='apply_daiko_segment')
        env={'_apply_user_rank_reward_actions':lambda *args:[],'_latest_talent_orb_ids':lambda:[0,1,2]}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),env)
        save=fixture_save();save.catfood=0;save.xp=0;save.np=0
        save.normal_tickets=0;save.rare_tickets=0;save.platinum_tickets=0;save.legend_tickets=0
        save.leadership=0;save.battle_items=NS(items=[NS(amount=0) for _ in range(6)])
        save.catseyes=[0]*6;save.catamins=[0]*3;save.catfruit=[0]*5
        save.ototo=NS(base_materials=NS(materials=[NS(amount=0) for _ in range(3)]))
        m=fixture_module();m.get_legend_metadata=lambda:{'vip_item_groups':{'catfruit':['紫マタタビ','虹マタタビ','紫獣石','虹獣石','紫獣結晶']}}
        def original(current,selected,chars,amounts):
            logs=[]
            for group in ['s1','s2','s3','s4']:logs.extend(env['apply_daiko_segment'](current,selected.get(group,[]),chars,amounts))
            return logs
        m._apply_all_segments_unlocked=original
        m.run_job_daiko=lambda *args,**kwargs:m._apply_all_segments_unlocked(save,args[4],args[5],args[6])
        p=parse_request('アイテム全部MAX、チケット全部MAX、キャラMAX\n引き継ぎコード:abc123456\n認証番号:0123')
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=m;engine.install_detail_adapter(m);engine.execute('jid','oid',p)
        self.assertEqual((save.catfood,save.xp,save.np),(58999,99999999,9999))
        self.assertEqual((save.normal_tickets,save.rare_tickets,save.platinum_tickets,save.legend_tickets),(999,999,99,99))
        self.assertEqual(save.leadership,999);self.assertEqual([i.amount for i in save.battle_items.items],[999]*6)
        self.assertEqual(save.catseyes,[999]*6);self.assertEqual(save.catamins,[999]*3)
        self.assertEqual([i.amount for i in save.ototo.base_materials.materials],[999]*3)
        self.assertEqual(save.catfruit,[998,998,999,999,999]);self.assertEqual(save.talent_orbs.orbs,{0:998,1:998,2:998})
        self.assertTrue(all((c.upgrade.base+1,c.upgrade.plus)==(60,90) for c in save.cats.cats))

if __name__=='__main__':unittest.main(verbosity=2)
