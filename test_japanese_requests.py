import copy
import unittest
from local_chat.interpreter import interpret_request
from local_chat.parser import ParseError


class JapaneseRequests(unittest.TestCase):
    def test_conversational_quantities(self):
        examples={
            '猫缶を45000個にしてもらえますか？':('custom_catfood',45000),
            'ねこ缶は四万五千個にしてほしいです':('custom_catfood',45000),
            '猫缶は4万5千個にしてほしい':('custom_catfood',45000),
            'すみませんが、猫カンを42000個にしていただけますか？':('custom_catfood',42000),
            'えっと、にゃんこ缶は三万八千個にしてほしいです':('custom_catfood',38000),
            'あと、エヌピーは二千で頼む':('custom_np',2000),
            'リーダーシップは三百個にしてほしいです':('custom_leadership',300),
            '経験値を八千万にしてもらえますか？':('custom_xp',80000000),
            '銀チケットは二百枚で頼む':('custom_normal_tickets',200),
            '金チケットは三百枚にしてほしいです':('custom_rare_tickets',300),
            'NPは0万5000でお願い':('custom_np',5000),
        }
        for text,(key,value) in examples.items():
            with self.subTest(text=text):
                p=interpret_request(text)
                self.assertEqual(p['unresolved'],[])
                self.assertEqual(p['custom_amounts'][key],value)

    def test_multiclause_and_shared_subject_quantities(self):
        p=interpret_request('猫缶は45000で、XPはMAXにして、NPは1000で頼む')
        self.assertEqual(p['custom_amounts']['custom_catfood'],45000)
        self.assertEqual(p['custom_amounts']['custom_np'],1000)
        self.assertFalse(p['unresolved'])
        shared=interpret_request('猫缶、XPを30000')
        self.assertEqual(shared['custom_amounts'],{'custom_catfood':30000,'custom_xp':30000})

    def test_range_expressions(self):
        for text in ('猫缶は1000から2000の間でランダム','猫缶は1000個から2000個の間でランダム',
                     '猫缶は1000以上2000以下でランダム','猫缶は1000から2000までランダムにして'):
            with self.subTest(text=text):
                p=interpret_request(text)
                self.assertFalse(p['unresolved'])
                self.assertEqual(p['quantity_ranges']['4'],[1000,2000])

    def test_base_plus_grammar_preserves_two_fields(self):
        p=interpret_request('レアは基本レベル50で、プラスは30にして')
        self.assertFalse(p['unresolved'])
        self.assertEqual(p['level_rules']['2']['base'],50)
        self.assertIn(p['level_rules']['2']['plus'],(30,[30,30]))

    def test_explicit_predicates_apply_only_to_matching_original_values(self):
        from test_advanced_requests import fixture
        from local_chat.conditional_engine import compile_conditional,apply_conditional
        from bcsfe import core
        m,s=fixture()
        s.cats.cats[6].upgrade.plus=2;s.cats.cats[7].upgrade.plus=25
        for text in ('レアの＋値が20未満なら＋値30','現在＋値が20未満のレアキャラは＋値30'):
            with self.subTest(text=text):
                p=interpret_request(text,character_metadata=m.get_character_metadata())
                self.assertFalse(p['unresolved'])
                self.assertEqual(len(p['conditional_rules']),1)
                apply_conditional(m,s,compile_conditional(m,p))
                loaded=core.SaveFile(core.Data(s.to_data().data),cc=s.cc)
                self.assertEqual(loaded.cats.cats[6].upgrade.plus,30)
                self.assertEqual(loaded.cats.cats[7].upgrade.plus,25)
                self.assertEqual(loaded.cats.cats[6].upgrade.base,19)
                s.cats.cats[6].upgrade.plus=2

    def test_new_threshold_word_order_routes_to_conditional(self):
        p=interpret_request('現在レベルが30未満の超激レアはレベル50')
        self.assertEqual(p['conditional_rules'][0]['expr'],{'op':'set','value':50})
        self.assertEqual(p['conditional_rules'][0]['when'],[{'field':'base','op':'lt','value':30}])

    def test_guesses_are_explained_and_ambiguities_block(self):
        p=interpret_request('猫カンを45000個にしてもらえますか？')
        self.assertTrue(p['inferences'])
        for text in ('それを同じ数にして','強いやつだけいい感じに'):
            p=interpret_request(text)
            self.assertFalse(p['ready'])
        with self.assertRaises(ParseError):interpret_request('猫缶は5万99999')
        p=interpret_request('キャツアイ：120')
        if p['unresolved']:
            self.assertTrue(p['unresolved'][0]['suggestions'])

    def test_invalid_values_do_not_mutate_previous_plan(self):
        p=interpret_request('NP5000');before=copy.deepcopy(p)
        for text in ('猫缶を300000万にして','NP500〜400','プラチナチケット15〜100'):
            with self.assertRaises(ParseError):interpret_request(text,p)
        self.assertEqual(p,before)


if __name__=='__main__':unittest.main()
