"""Plus-only requests keep base levels and ordinary banner fillers unchanged."""
import copy
import unittest
from local_chat.interpreter import interpret_request
from local_chat.parser import parse_request, ParseError
from local_chat.details import compile_details, apply_details
from test_detailed_requests import fixture_module, fixture_save
import test_local_chat as originals

CODES='\n引き継ぎコード:abc123456\n認証番号:0123'
TEXT='''コラボキャラ：＋値を10〜15の範囲でランダムに設定してください。
伝説レア：＋値を3に設定してください。
基本レベルは変更せず、＋値だけ変更してください。'''

class PlusOnlyTests(unittest.TestCase):
    def test_entire_previously_unrecognized_text_is_recognized(self):
        p=interpret_request(TEXT+CODES)
        self.assertTrue(p['ready']);self.assertFalse(p['unresolved'])
        self.assertEqual(p['plus_only_rules'],{'collab':[10,15],'rarity:5':[3,3]})
        self.assertTrue(p['preserve_base_levels'])
        self.assertTrue(all('基本Lvは維持' in item['label'] for item in p['labels']))

    def test_only_collab_and_legend_plus_are_changed(self):
        save=fixture_save()
        for i,cat in enumerate(save.cats.cats):
            cat.upgrade.base=i+20;cat.upgrade.plus=i+50
        before=copy.deepcopy(save)
        apply_details(fixture_module(),save,compile_details(fixture_module(),interpret_request(TEXT)))
        for i,cat in enumerate(save.cats.cats):
            self.assertEqual(cat.upgrade.base,before.cats.cats[i].upgrade.base)
            if i in (4,6):self.assertTrue(10<=cat.upgrade.plus<=15)
            elif i==8:self.assertEqual(cat.upgrade.plus,3)
            else:self.assertEqual(cat.upgrade.plus,before.cats.cats[i].upgrade.plus)

    def test_legend_specific_value_wins_even_if_legend_is_collab(self):
        for text in ['コラボ＋10〜15\n伝説レア＋3','伝説レア＋3\nコラボ＋10〜15']:
            with self.subTest(text=text):
                module=fixture_module();meta=module.get_character_metadata()
                meta['characters'][8]['collab']=True
                module.get_character_metadata=lambda:meta
                save=fixture_save();save.cats.cats[8].upgrade.base=49
                apply_details(module,save,compile_details(module,interpret_request(text)))
                self.assertEqual((save.cats.cats[8].upgrade.base,save.cats.cats[8].upgrade.plus),(49,3))

    def test_typo_is_visible_in_inferences_and_still_needs_review(self):
        p=interpret_request('コラボ系のプラス値10〜15\n伝説もプラス3z'+CODES)
        self.assertTrue(p['ready']);self.assertEqual(p['plus_only_rules']['rarity:5'],[3,3])
        self.assertTrue(any(item['input']=='伝説もプラス3z' for item in p['inferences']))
        self.assertNotIn('_review_confirmed',p)

    def test_halfwidth_and_fullwidth_brackets(self):
        for text in ['コラボキャラ（＋10〜15）\n伝説レア（＋3）','コラボキャラ(+10~15)\n伝説レア(+3)']:
            with self.subTest(text=text):
                p=interpret_request(text)
                self.assertFalse(p['unresolved']);self.assertEqual(p['plus_only_rules'],{'collab':[10,15],'rarity:5':[3,3]})

    def test_footer_preserves_base_even_when_legacy_max_was_selected(self):
        p=interpret_request('キャラMAX\n'+TEXT)
        self.assertNotIn('max_all_cats_plus',p['selected']['s3'])
        self.assertTrue(all(rule['base']=='keep' for rule in p['level_rules'].values()))
        save=fixture_save()
        for i,cat in enumerate(save.cats.cats):cat.upgrade.base=i+20
        apply_details(fixture_module(),save,compile_details(fixture_module(),p))
        self.assertEqual([cat.upgrade.base for cat in save.cats.cats],list(range(20,29)))
        self.assertEqual(save.cats.cats[3].upgrade.plus,90)

    def test_rules_survive_later_codes_without_changing_values(self):
        first=interpret_request('コラボ＋10〜15\n伝説レア＋3')
        later=interpret_request(CODES,first)
        self.assertEqual(first['plus_only_rules'],later['plus_only_rules'])
        self.assertFalse(first['ready']);self.assertTrue(later['ready'])

    def test_invalid_ranges_are_not_guessed(self):
        p=interpret_request('猫缶40000');snapshot=copy.deepcopy(p)
        for text in ['コラボ＋15〜10','伝説レア＋32768']:
            with self.subTest(text=text),self.assertRaises(ParseError):interpret_request(text,p)
        self.assertEqual(p,snapshot)

    def test_unknown_limit_and_negative_are_preserved(self):
        for text in ['コラボ＋10〜15にしない','伝説レア＋3以外']:
            with self.subTest(text=text):
                p=interpret_request(text+CODES)
                self.assertFalse(p['ready']);self.assertTrue(p['unresolved'])

    def test_locked_characters_are_not_unlocked(self):
        save=fixture_save();save.cats.cats[4].unlocked=False
        apply_details(fixture_module(),save,compile_details(fixture_module(),parse_request('コラボ＋10〜15')))
        self.assertFalse(save.cats.cats[4].unlocked);self.assertEqual(save.cats.cats[4].upgrade.plus,7)

class PlusOnlyHttpTests(unittest.TestCase):
    setUp=originals.HttpTests.setUp;tearDown=originals.HttpTests.tearDown
    request=originals.HttpTests.request;chat=originals.HttpTests.chat;confirm=originals.HttpTests.confirm
    def test_confirmation_is_required_and_a_later_correction_invalidates_old_review(self):
        first=self.chat(TEXT+CODES)[1]
        self.assertTrue(first['ready']);self.assertFalse(first['unresolved']);self.assertFalse(self.engine.prepared)
        second=self.chat('伝説レア＋4')[1]
        self.assertEqual(self.confirm(first)[0],409)
        self.assertEqual(self.confirm(second)[1]['kind'],'started')
        self.state.worker.shutdown(wait=True)
        self.assertEqual(self.engine.calls[0]['plus_only_rules']['rarity:5'],[4,4])

if __name__=='__main__':unittest.main(verbosity=2)
