"""Multi-turn target selection, preserved save values and mandatory review."""
import copy
import unittest
from local_chat.interpreter import interpret_request
from local_chat.parser import ParseError
from local_chat.details import compile_details, apply_details
from test_detailed_requests import fixture_module, fixture_save
import test_local_chat as originals

CODES='\n引き継ぎコード:abc123456\n認証番号:0123'

def save_with_distinct_levels():
    save=fixture_save()
    for i,cat in enumerate(save.cats.cats):
        cat.upgrade.base=20+i;cat.upgrade.plus=30+i
    return save

def apply(plan,save=None):
    save=save or save_with_distinct_levels();module=fixture_module()
    logs=apply_details(module,save,compile_details(module,plan))
    return save,logs

class LanguageRefinementTests(unittest.TestCase):
    def test_items_only_cancel_old_material_edits_prices_and_keep_tickets(self):
        p=interpret_request('猫缶指定：50円\nNP指定：50円\nまたたび全種類：1000円\n獣石：500円\nチケットMAX\nアイテムMAX')
        p=interpret_request('猫缶だけ40000',p)
        self.assertEqual(set(p['selected']['s1']),{'custom_catfood','normal_tickets','rare_tickets','platinum_tickets','legend_tickets'})
        self.assertEqual(p['custom_amounts'],{'custom_catfood':40000})
        self.assertEqual(p['fruit_filters'],{});self.assertIsNone(p['orb_s_amount'])
        self.assertEqual(p['menu_numbers'],[4])
        self.assertEqual([q['numbers'] for q in p['quoted_prices']],[[4]])

    def test_ticket_pair_only_cancels_previous_tickets_and_pending_event_target(self):
        p=interpret_request('NP5000\nチケットMAX\nメニュー19=イベントA')
        p=interpret_request('チケットはプラチケとレジェチケだけ99枚',p)
        self.assertFalse(p['menu_pending']);self.assertFalse(p['blockers'])
        self.assertNotIn('19',p['menu_target_values'])
        self.assertEqual(set(p['selected']['s1']),{'custom_np','custom_platinum_tickets','custom_legend_tickets'})
        self.assertEqual(p['custom_amounts']['custom_np'],5000)

    def test_cancelling_items_preserves_ticket_character_and_membership_edits(self):
        p=interpret_request('アイテムMAX\nプラチケ20\nレアレベル30+50\nゴールド会員化')
        p=interpret_request('アイテムは全部変更しない',p)
        self.assertEqual(p['selected']['s1'],['custom_platinum_tickets'])
        self.assertEqual(p['fruit_filters'],{})
        self.assertEqual(p['level_rules']['2'],{'base':30,'plus':[50,50]})
        self.assertIn('gold_membership',p['selected']['s4'])

    def test_cancelling_tickets_preserves_items(self):
        p=interpret_request('NP5000\nチケットMAX\nメニュー19=イベントA')
        p=interpret_request('チケットはそのまま',p)
        self.assertFalse(p['menu_pending']);self.assertEqual(p['selected']['s1'],['custom_np'])
        self.assertFalse(p['explicit_tickets'])

    def test_cancelling_levels_removes_legacy_max_and_pending_specific_level_edits(self):
        p=interpret_request('キャラMAX\nキャラ全解放\nコラボ別レベル変更：300円\n指定キャラ1体レベル変更：150円\nNP5000\n施設MAX')
        p=interpret_request('キャラレベルは変更しない',p)
        self.assertFalse(p['level_rules']);self.assertFalse(p['plus_only_rules']);self.assertFalse(p['menu_pending'])
        self.assertEqual(p['selected']['s3'],['unlock_all_cats'])
        self.assertIn('base_upgrades_max',p['selected']['s2'])
        self.assertFalse(p['quoted_prices'])

    def test_direct_resource_exclusion_removes_old_menu_and_quote(self):
        p=interpret_request('猫缶指定：50円\nNP指定：50円')
        p=interpret_request('NPはそのまま',p)
        self.assertEqual(p['menu_numbers'],[4]);self.assertEqual([q['numbers'] for q in p['quoted_prices']],[[4]])
        self.assertEqual(p['selected']['s1'],['catfood']);self.assertNotIn('np',p['explicit_items'])

    def test_only_keep_plus_is_not_an_executable_edit(self):
        p=interpret_request('全キャラプラスはそのまま'+CODES)
        self.assertFalse(p['ready']);self.assertIn('代行内容',p['missing'])

    def test_all_plus_collab_exception_and_legend_override_keep_all_bases(self):
        p=interpret_request('全キャラのプラス値だけ90にして\nコラボのプラス10〜15\n伝説プラス3')
        before=save_with_distinct_levels();save,_=apply(p,save_with_distinct_levels())
        for i,cat in enumerate(save.cats.cats):
            self.assertEqual(cat.upgrade.base,before.cats.cats[i].upgrade.base)
            self.assertEqual(cat.unlocked,before.cats.cats[i].unlocked)
            if i in (4,6):self.assertTrue(10<=cat.upgrade.plus<=15)
            elif i==8:self.assertEqual(cat.upgrade.plus,3)
            else:self.assertEqual(cat.upgrade.plus,90)
        text=' '.join(x['label'] for x in p['labels'])
        self.assertIn('コラボは個別指定を優先',text);self.assertIn('伝説は個別指定を優先',text)

    def test_later_all_plus_replaces_previous_exceptions(self):
        p=interpret_request('コラボ＋10〜15\n伝説＋3')
        p=interpret_request('全キャラのプラスを2にして',p)
        self.assertEqual(p['plus_only_rules'],{'all':[2,2]})
        save,_=apply(p);self.assertTrue(all(cat.upgrade.plus==2 for cat in save.cats.cats))

    def test_explicit_rarity_level_and_plus_replace_both_global_defaults_for_that_rarity(self):
        p=interpret_request('全キャラ＋90\nコラボ＋10〜15')
        p=interpret_request('レアレベル50+40',p)
        self.assertIn(2,p['all_plus_excluded_rarities']);self.assertIn(2,p['collab_plus_excluded_rarities'])
        save,_=apply(p)
        for i in (3,4):self.assertEqual((save.cats.cats[i].upgrade.base+1,save.cats.cats[i].upgrade.plus),(50,40))
        self.assertEqual(save.cats.cats[5].upgrade.plus,90);self.assertTrue(10<=save.cats.cats[6].upgrade.plus<=15)

    def test_base_only_correction_keeps_global_plus_default_and_collab_exception(self):
        p=interpret_request('全キャラ＋90\nコラボ＋10〜15\n伝説＋3')
        p=interpret_request('レアのレベルを50にして',p)
        save,_=apply(p)
        self.assertEqual((save.cats.cats[3].upgrade.base+1,save.cats.cats[3].upgrade.plus),(50,90))
        self.assertEqual(save.cats.cats[4].upgrade.base+1,50);self.assertTrue(10<=save.cats.cats[4].upgrade.plus<=15)
        self.assertEqual(save.cats.cats[8].upgrade.plus,3)

    def test_global_plus_keep_after_legacy_max_changes_only_base(self):
        p=interpret_request('キャラMAX\nプラスはそのまま')
        before=save_with_distinct_levels();save,_=apply(p,save_with_distinct_levels())
        self.assertTrue(all(cat.upgrade.base+1==60 for cat in save.cats.cats))
        self.assertEqual([c.upgrade.plus for c in save.cats.cats],[c.upgrade.plus for c in before.cats.cats])

    def test_collab_plus_keep_changes_normal_fillers_but_keeps_actual_collabs(self):
        p=interpret_request('メニュー29=50+90\nコラボのプラスは変更しない')
        before=save_with_distinct_levels();save,_=apply(p,save_with_distinct_levels())
        for i,cat in enumerate(save.cats.cats):
            self.assertEqual(cat.upgrade.base+1,50)
            self.assertEqual(cat.upgrade.plus,before.cats.cats[i].upgrade.plus if i in (4,6) else 90)

    def test_paired_rarity_base_and_keep_plus_do_not_cancel_other_rarity_edits(self):
        p=interpret_request('レアレベル30+20\n激レアレベル40+15\n伝説＋3')
        p=interpret_request('レアのレベルは50、プラスはそのまま',p)
        before=save_with_distinct_levels();save,_=apply(p,save_with_distinct_levels())
        for i in (3,4):self.assertEqual((save.cats.cats[i].upgrade.base+1,save.cats.cats[i].upgrade.plus),(50,before.cats.cats[i].upgrade.plus))
        self.assertEqual((save.cats.cats[5].upgrade.base+1,save.cats.cats[5].upgrade.plus),(40,15))
        self.assertEqual(save.cats.cats[8].upgrade.plus,3)

    def test_reenabling_all_items_removes_stale_unchanged_material_label(self):
        p=interpret_request('猫缶だけ40000');p=interpret_request('アイテムMAX',p)
        self.assertIn('other_fruit',p['fruit_filters'])
        self.assertNotIn('exclude_other_fruit',{item['key'] for item in p['labels']})

    def test_invalid_batch_compound_and_range_leave_previous_draft_unchanged(self):
        p=interpret_request('NP5000\n伝説＋3');before=copy.deepcopy(p)
        for text in ['猫缶とXPを300000万にして','全キャラプラス20〜10','猫缶は5万99999']:
            with self.subTest(text=text),self.assertRaises(ParseError):interpret_request(text,p)
        self.assertEqual(p,before)

class LanguageRefinementHttpTests(unittest.TestCase):
    setUp=originals.HttpTests.setUp;tearDown=originals.HttpTests.tearDown
    request=originals.HttpTests.request;chat=originals.HttpTests.chat;confirm=originals.HttpTests.confirm
    def test_only_target_correction_requires_new_review_and_runs_only_corrected_targets(self):
        first=self.chat('アイテムMAX\nチケットMAX'+CODES)[1]
        second=self.chat('猫缶だけ40000')[1]
        self.assertFalse(self.engine.prepared);self.assertFalse(self.engine.calls)
        self.assertEqual(self.confirm(first)[0],409)
        self.assertEqual(self.confirm(second)[1]['kind'],'started')
        self.state.worker.shutdown(wait=True)
        actual=self.engine.calls[0]
        self.assertEqual(actual['fruit_filters'],{})
        self.assertEqual(set(actual['selected']['s1']),{'custom_catfood','normal_tickets','rare_tickets','platinum_tickets','legend_tickets'})

if __name__=='__main__':unittest.main(verbosity=2)
