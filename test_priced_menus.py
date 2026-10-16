"""Sales prices and round brackets are separate from game edit values."""
import copy
from pathlib import Path
import unittest
from local_chat.interpreter import interpret_request
from local_chat.parser import ParseError, split_clauses
from local_chat.engine import resolve_maximums
from local_chat.menus import MENU_BY_NUMBER
from local_chat.details import compile_details, apply_details
from test_detailed_requests import fixture_module, fixture_save
import test_local_chat as originals

CODES='\n引き継ぎコード:abc123456\n認証番号:0123'
SAMPLE=Path(__file__).with_name('sample_priced_menu.txt').read_text(encoding='utf-8')


class PricedMenuTests(unittest.TestCase):
    def test_entire_supplied_price_list_is_recognized_without_fragmented_items(self):
        plan=interpret_request(SAMPLE+CODES)
        self.assertFalse(plan['unresolved'])
        self.assertEqual(len(plan['quoted_prices']),33)
        self.assertEqual(set(plan['menu_pending']),{'3','26','27','35','36','67','68','name:未来編採点スコア指定'})
        self.assertFalse(plan['ready'])
        self.assertIn('gold_membership',plan['selected']['s4'])
        self.assertIn('user_rank_rewards_claimed',plan['selected']['s4'])
        self.assertIn('catguide_rewards_unclaimed',plan['selected']['s4'])
        self.assertIn('true_legend_clear',plan['selected']['s2'])
        self.assertIn('zero_legend_clear',plan['selected']['s2'])
        self.assertTrue(plan['backup_requested'])

    def test_quoted_prices_never_become_item_quantities_or_levels(self):
        plan=interpret_request(SAMPLE)
        _,amounts=resolve_maximums(plan)
        self.assertEqual(amounts['custom_catfood'],58999)
        self.assertEqual(amounts['custom_np'],9999)
        self.assertEqual(amounts['custom_catseyes'],999)
        self.assertEqual(amounts['custom_talent_orbs'],998)
        self.assertEqual(amounts['custom_rare_tickets'],999)
        self.assertEqual(amounts['custom_platinum_tickets'],99)
        self.assertEqual(amounts['custom_legend_tickets'],99)
        self.assertEqual(plan['fruit_filters'],{'matatabi':998,'stones':999})
        self.assertTrue(all(rule=={'base':'max','plus':'max'} for rule in plan['level_rules'].values()))
        self.assertEqual(plan['custom_playtime'],'9999:00')
        self.assertIsNone(plan['gamatoto_level'])

    def test_price_does_not_overwrite_catalogue_or_guess_people_or_series(self):
        before=copy.deepcopy(MENU_BY_NUMBER)
        plan=interpret_request(SAMPLE)
        self.assertEqual(MENU_BY_NUMBER,before)
        self.assertFalse(plan['menu_target_values'])
        self.assertFalse(plan.get('helpers_legend'))
        self.assertIsNone(plan['future_score_settings'])
        catseyes=next(row for row in plan['quoted_prices'] if row['numbers']==[9])
        self.assertEqual(catseyes['price_jpy'],250)
        self.assertEqual(MENU_BY_NUMBER[9]['price_jpy'],100)

    def test_fullwidth_and_ascii_quantity_brackets_with_prices(self):
        for text in ['猫缶指定（40000）：50円','猫缶指定(数量:40,000):50円','猫缶指定(40000)(50円)',
                     '猫缶40000：50円','猫缶：40000（50円）']:
            with self.subTest(text=text):
                plan=interpret_request(text)
                self.assertFalse(plan['unresolved']);self.assertFalse(plan['blockers'])
                self.assertEqual(plan['custom_amounts']['custom_catfood'],40000)
                self.assertEqual(plan['quoted_prices'][0]['price_jpy'],50)

    def test_parenthesized_price_only_uses_maximum(self):
        for text in ['猫缶指定（50円）','猫缶指定(50円)','猫缶指定50円','4. 猫缶指定：50円','メニュー4：50円']:
            with self.subTest(text=text):
                plan=interpret_request(text)
                self.assertEqual(plan['selected']['s1'],['catfood'])
                self.assertFalse(plan['custom_amounts']);self.assertFalse(plan['unresolved'])

    def test_ticket_item_and_level_arguments_have_concrete_values(self):
        plan=interpret_request('レアチケット指定（300枚）：100円\nNP指定（5000）：50円\nキャッツアイ全種（200個）：250円\n全キャラレベル変更（50+10）：100円')
        self.assertFalse(plan['unresolved'])
        self.assertEqual(plan['custom_amounts']['custom_rare_tickets'],300)
        self.assertEqual(plan['custom_amounts']['custom_np'],5000)
        self.assertEqual(plan['custom_amounts']['custom_catseyes'],200)
        self.assertTrue(all(rule=={'base':50,'plus':[10,10]} for rule in plan['level_rules'].values()))

    def test_numeric_base_inside_brackets_preserves_plus(self):
        plan=interpret_request('全キャラレベル変更（50）')
        save=fixture_save();save.cats.cats[3].upgrade.plus=23
        apply_details(fixture_module(),save,compile_details(fixture_module(),plan))
        self.assertEqual((save.cats.cats[3].upgrade.base+1,save.cats.cats[3].upgrade.plus),(50,23))

    def test_category_level_brackets_and_inline_collab_exception(self):
        plan=interpret_request('【レベル変更】\n基本全てレベル（20+90）\nレア全てレベル50+MAX（コラボのみ+20〜30）')
        self.assertFalse(plan['unresolved']);self.assertFalse(plan['blockers'])
        self.assertEqual(plan['level_rules']['0'],{'base':20,'plus':[90,90]})
        self.assertEqual(plan['level_rules']['2']['collab_plus'],[20,30])
        save=fixture_save();apply_details(fixture_module(),save,compile_details(fixture_module(),plan))
        self.assertEqual(save.cats.cats[3].upgrade.plus,90)
        self.assertTrue(20<=save.cats.cats[4].upgrade.plus<=30)

    def test_names_inside_brackets_are_retained_but_pending_execution(self):
        plan=interpret_request('指定キャラ1体（比古清十郎）：300円\nコラボ別開放（るろうに剣心）：450円\n隊員追加・クラスアップ（10人レジェンド）：1500円'+CODES)
        self.assertFalse(plan['unresolved']);self.assertFalse(plan['ready'])
        self.assertEqual(plan['menu_target_values']['27'],'比古清十郎')
        self.assertEqual(plan['menu_target_values']['26'],'るろうに剣心')
        self.assertEqual(plan['menu_target_values']['67'],'10人レジェンド')
        self.assertEqual(plan['menu_target_values']['68'],'10人レジェンド')

    def test_future_scope_is_not_guessed_from_200_yen_and_can_be_resolved(self):
        plan=interpret_request('未来編採点スコア指定：200円'+CODES)
        self.assertFalse(plan['ready']);self.assertIsNone(plan['future_score_settings'])
        plan=interpret_request('メニュー46=9000',plan)
        self.assertTrue(plan['ready']);self.assertFalse(plan['blockers'])
        self.assertEqual(plan['future_score_settings'],{'3':9000,'4':9000,'5':9000})
        self.assertEqual(plan['quoted_prices'][0]['price_jpy'],200)

    def test_future_scope_and_score_inside_two_brackets(self):
        plan=interpret_request('未来編採点スコア指定（全ステージ）（9000）：200円'+CODES)
        self.assertTrue(plan['ready']);self.assertFalse(plan['unresolved'])
        self.assertEqual(plan['future_score_settings'],{'3':9000,'4':9000,'5':9000})

    def test_future_scope_can_be_resolved_in_same_message(self):
        plan=interpret_request('未来編採点スコア指定：200円\nメニュー46=9000'+CODES)
        self.assertTrue(plan['ready']);self.assertFalse(plan['blockers'])
        self.assertEqual(plan['quoted_prices'][0]['numbers'],[46])

    def test_range_in_parentheses_is_chosen_once_and_keeps_price(self):
        plan=interpret_request('プレイ時間変更（8000〜8200）：500円')
        self.assertTrue(8000<=int(plan['custom_playtime'].split(':')[0])<=8200)
        self.assertEqual(plan['quoted_prices'][0]['price_jpy'],500)
        updated=interpret_request(CODES,plan)
        self.assertEqual(updated['custom_playtime'],plan['custom_playtime'])

    def test_omitted_items_and_superseded_reward_modes_remove_stale_prices(self):
        plan=interpret_request('NP指定：50円\nNP不要\nユーザーランク報酬全受取：200円\nユーザーランク報酬全未受取化：300円')
        self.assertEqual([row['numbers'] for row in plan['quoted_prices']],[[78]])
        self.assertNotIn('np',plan.get('selected',{}).get('s1',[]))

    def test_ambiguous_scope_can_be_omitted_without_omitting_other_edits(self):
        plan=interpret_request('猫缶指定：50円\n未来編採点スコア指定：200円'+CODES)
        plan=interpret_request('未来編採点スコア指定不要',plan)
        self.assertTrue(plan['ready']);self.assertFalse(plan['blockers'])
        self.assertEqual(plan['selected']['s1'],['catfood'])
        self.assertEqual(len(plan['quoted_prices']),1)

    def test_plain_shared_quantities_are_not_changed_by_menu_preprocessing(self):
        plan=interpret_request('猫缶、XPを30000')
        self.assertEqual(plan['custom_amounts'],{'custom_catfood':30000,'custom_xp':30000})

    def test_thousands_do_not_split_unknown_prices_or_parenthesis_contents(self):
        self.assertEqual(split_clauses('上級者垢：3,000円、NP（5,000）'),['上級者垢：3,000円','NP（5,000）'])
        self.assertEqual(split_clauses('仕様72,73,74'),['仕様72','73','74'])
        plan=interpret_request('未登録の商品：3,000円'+CODES)
        self.assertEqual(len(plan['unresolved']),1)
        self.assertEqual(plan['unresolved'][0]['input'],'未登録の商品：3,000円')

    def test_invalid_brackets_and_values_never_change_previous_draft(self):
        previous=interpret_request('猫缶40000'+CODES);before=copy.deepcopy(previous)
        for text in ['猫缶指定（40000：50円','猫缶指定（2147483648）：50円']:
            with self.subTest(text=text),self.assertRaises(ParseError):interpret_request(text,previous)
        plan=interpret_request('メニュー4=50円',previous)
        self.assertFalse(plan['ready']);self.assertTrue(plan['unresolved'])
        self.assertEqual(previous,before)

    def test_negative_parenthesis_is_not_silently_ignored(self):
        plan=interpret_request('猫缶指定（40000にしない）：50円'+CODES)
        self.assertFalse(plan['ready']);self.assertTrue(plan['unresolved'])


class PricedMenuHttpTests(unittest.TestCase):
    setUp=originals.HttpTests.setUp;tearDown=originals.HttpTests.tearDown
    request=originals.HttpTests.request;chat=originals.HttpTests.chat;confirm=originals.HttpTests.confirm

    def test_full_list_blocks_approval_but_recognizes_all_quoted_prices(self):
        draft=self.chat(SAMPLE+CODES)[1]
        self.assertFalse(draft['ready']);self.assertFalse(draft['unresolved'])
        self.assertEqual(len(draft['quoted_prices']),33)
        self.assertEqual(self.confirm(draft)[0],400);self.assertFalse(self.engine.prepared)

    def test_quantity_in_parentheses_runs_only_after_approval(self):
        draft=self.chat('猫缶指定（40000）：50円'+CODES)[1]
        self.assertTrue(draft['ready']);self.assertFalse(self.engine.prepared)
        self.assertEqual(draft['quoted_prices'][0]['price_jpy'],50)
        self.assertEqual(self.confirm(draft)[1]['kind'],'started')
        self.state.worker.shutdown(wait=True)
        self.assertEqual(self.engine.calls[0]['custom_amounts']['custom_catfood'],40000)


if __name__=='__main__':unittest.main(verbosity=2)
