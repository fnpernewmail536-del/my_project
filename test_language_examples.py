"""Example corpus, actual save effects, and multi-turn corrections; no game traffic."""
import json
from pathlib import Path
import unittest
from local_chat.interpreter import interpret_request
from local_chat.parser import ParseError
from local_chat.details import compile_details, apply_details
from test_detailed_requests import fixture_module, fixture_save
import test_local_chat as originals

EXAMPLES=json.loads(Path(__file__).with_name('language_examples.json').read_text(encoding='utf-8'))
CODES='\n引き継ぎコード:abc123456\n認証番号:0123'

def value_at(plan,path):
    value=plan
    for part in path.split('.'):value=value[part]
    return value

class LanguageExampleTests(unittest.TestCase):
    def test_recognized_example_corpus(self):
        for example in EXAMPLES['recognized']:
            with self.subTest(text=example['input']):
                p=interpret_request(example['input'])
                self.assertFalse(p['unresolved'])
                for path,expected in example.get('expected',{}).items():
                    self.assertEqual(value_at(p,path),expected)
                keys={item['key'] for item in p['labels']}
                self.assertTrue(set(example.get('label_keys',[]))<=keys)
                for path,items in example.get('contains',{}).items():
                    self.assertTrue(set(items)<=set(value_at(p,path)))
                for path,items in example.get('absent',{}).items():
                    self.assertFalse(set(items)&set(value_at(p,path)))
                if example.get('pending_numbers'):
                    self.assertFalse(p['ready'])
                    self.assertTrue(set(map(str,example['pending_numbers']))<=set(p['menu_pending']))
                else:self.assertFalse(p['blockers'])
                if example.get('range_hours'):
                    hours=int(p['custom_playtime'].split(':')[0])
                    self.assertTrue(example['range_hours'][0]<=hours<=example['range_hours'][1])

    def test_ambiguous_and_negative_example_corpus_is_not_executable(self):
        for text in EXAMPLES['blocked']:
            with self.subTest(text=text):
                try:p=interpret_request(text+CODES)
                except ParseError:continue
                self.assertFalse(p['ready'])
                self.assertTrue(p['unresolved'] or p['blockers'] or p['missing'])

    def test_rare_only_replaces_previous_ticket_requests_but_preserves_np(self):
        p=interpret_request('NP5000\nプラチケ20\nレジェチケ10\n銀チケ300')
        p=interpret_request('チケットはレアだけ200枚でお願いします',p)
        self.assertFalse(p['unresolved'])
        self.assertEqual(set(p['selected']['s1']),{'custom_np','custom_rare_tickets'})
        self.assertEqual(p['custom_amounts']['custom_np'],5000)
        self.assertEqual(p['custom_amounts']['custom_rare_tickets'],200)

    def test_revised_explicit_level_replaces_old_plus_only_rule(self):
        p=interpret_request('コラボ＋10〜15\n伝説レア＋3')
        p=interpret_request('全てのキャラのレベルを50、プラス値を10にして',p)
        self.assertFalse(p['plus_only_rules'])
        save=fixture_save();apply_details(fixture_module(),save,compile_details(fixture_module(),p))
        self.assertTrue(all((cat.upgrade.base+1,cat.upgrade.plus)==(50,10) for cat in save.cats.cats))

    def test_explicit_rarity_plus_replaces_collab_override_only_for_that_rarity(self):
        p=interpret_request('コラボ＋10〜15')
        p=interpret_request('レアキャラのレベルを50プラス90にして',p)
        self.assertIn(2,p['collab_plus_excluded_rarities'])
        save=fixture_save();apply_details(fixture_module(),save,compile_details(fixture_module(),p))
        self.assertEqual(save.cats.cats[4].upgrade.plus,90)
        self.assertTrue(10<=save.cats.cats[6].upgrade.plus<=15)

    def test_base_only_correction_keeps_pending_plus_change(self):
        p=interpret_request('伝説レア＋3\n基本レベルはそのまま')
        p=interpret_request('伝説レアのレベルを50にして',p)
        save=fixture_save();apply_details(fixture_module(),save,compile_details(fixture_module(),p))
        self.assertEqual((save.cats.cats[8].upgrade.base+1,save.cats.cats[8].upgrade.plus),(50,3))

    def test_omitting_all_level_changes_cancels_pending_plus_only_changes(self):
        p=interpret_request('NP5000\nコラボ＋10〜15\n伝説レア＋3')
        p=interpret_request('メニュー29不要',p)
        self.assertFalse(p['plus_only_rules']);self.assertFalse(p['level_rules'])
        self.assertEqual(p['selected']['s1'],['custom_np'])

    def test_invalid_quantity_and_range_never_mutate_previous_request(self):
        p=interpret_request('NP5000');before=json.loads(json.dumps(p))
        for text in ['猫缶を300000万にして','プレイ時間を8200から8000時間でランダム']:
            with self.subTest(text=text),self.assertRaises(ParseError):interpret_request(text,p)
        self.assertEqual(p,before)

class LanguageHttpTests(unittest.TestCase):
    setUp=originals.HttpTests.setUp;tearDown=originals.HttpTests.tearDown
    request=originals.HttpTests.request;chat=originals.HttpTests.chat;confirm=originals.HttpTests.confirm
    def test_natural_request_is_reviewed_and_old_confirmation_cannot_run_after_correction(self):
        first=self.chat('猫缶の数を4.5万にしてね'+CODES)[1]
        self.assertTrue(first['ready']);self.assertTrue(first['inferences'])
        self.assertFalse(self.engine.prepared)
        second=self.chat('猫缶は4万でお願いします')[1]
        self.assertEqual(self.confirm(first)[0],409)
        self.assertEqual(self.confirm(second)[1]['kind'],'started')
        self.state.worker.shutdown(wait=True)
        self.assertEqual(self.engine.calls[0]['custom_amounts']['custom_catfood'],40000)

if __name__=='__main__':unittest.main(verbosity=2)
