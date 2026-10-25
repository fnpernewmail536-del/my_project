"""Third-form selection must preserve ownership, evolution rights and save state."""
import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from local_chat.character_forms import REQUEST, is_third_form_request
from local_chat.interpreter import interpret_request
from local_chat.details import compile_details, apply_details
from local_chat.conditional_engine import compile_conditional, apply_conditional
from local_chat.parser import ParseError
from test_advanced_requests import fixture, core


def prepared():
    module, save = fixture()
    metadata = module.get_character_metadata()
    for i, info in enumerate(metadata['characters']):
        info.update(form_count=[1, 2, 3, 4, 3, 4, 3, 3][i], true_form_state=0 if i == 6 else 2)
    save.cats.cats[5].unlocked = False
    save.cats.cats[7].unlocked = False
    save.cats.cats[3].current_form = 3
    save.cats.cats[3].unlocked_forms = 3
    save.cats.cats[3].fourth_form = 2
    save.cats.cats[2].unlocked_forms = 1
    module._apply_all_segments_unlocked = Mock()
    tree = ast.parse(Path('main13.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'character_third_form_state')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'native-third-form', 'exec'), namespace)
    module.character_third_form_state = namespace['character_third_form_state']
    return module, save, metadata


class ThirdFormTests(unittest.TestCase):
    def execute(self, text, module, save):
        plan = interpret_request(text, character_metadata=module.get_character_metadata())
        self.assertFalse(plan['unresolved'], plan['unresolved'])
        compiled = compile_details(module, plan) if not plan.get('conditional_mode') else compile_conditional(module, plan)
        with patch.object(core.core_data, 'get_chara_drop', return_value=Mock()):
            logs = apply_details(module, save, compiled) if not plan.get('conditional_mode') else apply_conditional(module, save, compiled)
        loaded = core.SaveFile(core.Data(save.to_data().data), cc=save.cc)
        return loaded, plan, logs

    def test_user_phrase_and_supported_variants_parse_to_selection(self):
        for text in (REQUEST, '第三形態にできるキャラクターは第三形態にしたい',
                     '第３形態にできるキャラを全て第３形態にしてください',
                     '第3形態に対応しているキャラは第3形態にしてください。'):
            with self.subTest(text=text):
                module, save, _ = prepared()
                loaded, plan, _ = self.execute(text, module, save)
                self.assertEqual(loaded.cats.cats[2].current_form, 2)
                self.assertEqual(next(r for r in plan['conditional_rules'] if r['field'] == 'form')['expected_ids'], [3, 4, 5, 6, 7, 8])
                self.assertTrue(any('第4形態の解放権は保持' in label['label'] for label in plan['labels']))
                self.assertFalse(any('編成・使用形態・お気に入り' in label['label'] for label in plan['labels']))

    def test_negation_other_form_and_extra_conditions_are_not_guessed(self):
        for text in ('第3形態にはしないでください', '第4形態にできるキャラは第4形態にしてください',
                     REQUEST + 'ただしコラボは除外', '第3形態にできるレアキャラは第3形態にしてください'):
            self.assertFalse(is_third_form_request(text), text)

    def test_roundtrip_updates_exact_fields_only_and_keeps_fourth_rights(self):
        module, save, _ = prepared()
        expected = copy.deepcopy(save.cats.serialize())
        lineups = copy.deepcopy(save.lineups.serialize())
        for i in (2, 3, 4, 6):
            expected['cats'][i]['current_form'] = 2
            expected['cats'][i]['unlocked_forms'] = max(expected['cats'][i]['unlocked_forms'], 0 if i == 6 else 2)
        loaded, _, logs = self.execute(REQUEST, module, save)
        self.assertEqual(loaded.cats.serialize(), expected)
        self.assertEqual(loaded.lineups.serialize(), lineups)
        self.assertEqual(loaded.game_version.game_version, 150501)
        self.assertEqual(loaded.cats.cats[3].fourth_form, 2)
        self.assertTrue(any('第3形態へ切替：4体' in log for log in logs))

    def test_same_order_unlock_applies_form_and_explicit_removal_wins(self):
        module, save, _ = prepared()
        text = '## キャラ解放\n【恒常ガチャ】\n伝説レア:100%解放\n漆黒の魔女ダークキャスリィも削除\n## キャラレベル変更\n' + REQUEST
        loaded, _, _ = self.execute(text, module, save)
        self.assertTrue(loaded.cats.cats[5].unlocked)
        self.assertEqual(loaded.cats.cats[5].current_form, 2)
        self.assertFalse(loaded.cats.cats[4].unlocked)
        self.assertEqual(loaded.cats.cats[4].current_form, 0)
        self.assertFalse(loaded.cats.cats[7].unlocked)
        self.assertEqual(loaded.cats.cats[7].current_form, 0)

    def test_basic_unlock_level_then_third_form_from_initial_state(self):
        module, save, metadata = prepared()
        for i in range(3):
            metadata['characters'][i].update(rarity=0, form_count=3, true_form_state=0)
            save.cats.cats[i].unlocked = i == 0
            save.cats.cats[i].upgrade.base = 0
            save.cats.cats[i].upgrade.plus = 0
        original_others = copy.deepcopy(save.cats.serialize()['cats'][3:])
        text = '## キャラ解放\n【その他】\n基本キャラ:全員解放\n## キャラレベル変更\n基本キャラ:基本Lv20、プラス値+90\n' + REQUEST
        # Embed verified caps for the synthetic basic definition; never use the
        # fixture's pre-change super-rare row as a substitute basic definition.
        for info in metadata['characters']:
            info['level_caps'] = {'base':20,'plus':90} if info['rarity']==0 else {'base':60,'plus':70}
        metadata.update(source='bcsfe', data_version='15.5.1',
                        level_caps_provenance={'source':'bcsfe','region':'jp','data_version':'15.5.1'})
        loaded, plan, _ = self.execute(text, module, save)
        for i in range(3):
            cat = loaded.cats.cats[i]
            self.assertTrue(cat.unlocked)
            self.assertEqual((cat.upgrade.base+1,cat.upgrade.plus,cat.current_form), (20,90,2))
        # The form request still selects eligible owned units outside basic,
        # while their ownership and levels remain exactly as requested before.
        for old, cat in zip(original_others, loaded.cats.serialize()['cats'][3:]):
            self.assertEqual(cat['unlocked'], old['unlocked'])
            self.assertEqual(cat['upgrade'], old['upgrade'])
        unlock = next(r for r in plan['document_rules'] if r['field']=='unlocked')
        self.assertEqual(unlock['selector'], {'rarity':0})
        self.assertEqual(unlock['expected_ids'], [1,2,3])

    def test_basic_unlock_does_not_acquire_other_rarities(self):
        module, save, metadata = prepared()
        for cat in save.cats.cats:cat.unlocked=False
        loaded, _, _ = self.execute('## キャラ解放\n基本キャラ:全員解放', module, save)
        self.assertEqual([c.id+1 for c in loaded.cats.cats if c.unlocked], [1])

    def test_no_decrease_keeps_level_policy_but_allows_fourth_to_third(self):
        module, save, metadata = prepared()
        plan = interpret_request(REQUEST, character_metadata=metadata)
        plan.update(conditional_no_decrease=True, conditional_cat_no_decrease=True)
        apply_conditional(module, save, compile_conditional(module, plan))
        self.assertEqual(save.cats.cats[3].current_form, 2)
        self.assertEqual(save.cats.cats[3].fourth_form, 2)

    def test_additional_sentence_preserves_entire_document_and_credentials(self):
        module, _, metadata = prepared()
        plan = interpret_request('## アイテム・所持数変更\n4.猫缶:5789\n## キャラレベル変更\n### Aグループ:基本Lv50、プラス値+5〜+7\n鬼にゃんま', character_metadata=metadata)
        plan.update(transfer_code='test-code', auth_code='0000', _review_confirmed=True)
        original = copy.deepcopy(plan)
        added = interpret_request(REQUEST, plan, metadata)
        self.assertEqual(plan, original)
        self.assertEqual(added['custom_amounts'], plan['custom_amounts'])
        self.assertEqual(added['selected'], plan['selected'])
        self.assertEqual(added['document_rules'][:-1], plan['document_rules'])
        self.assertEqual(added['transfer_code'], 'test-code')
        self.assertEqual(added['auth_code'], '0000')
        self.assertNotIn('_review_confirmed', added)
        self.assertIn(REQUEST, added['_document_source'])
        repeated = interpret_request(REQUEST, added, metadata)
        self.assertEqual(repeated['document_rules'], added['document_rules'])
        self.assertEqual(repeated['_document_source'].count(REQUEST), 1)

    def test_changed_evolution_definition_stops_before_apply(self):
        for change in ({'true_form_state':0}, {'form_count':2}, {'form_count':4}):
            module, save, metadata = prepared()
            plan = interpret_request(REQUEST, character_metadata=metadata)
            metadata['characters'][2].update(change)
            before = copy.deepcopy(save.cats.serialize())
            with self.assertRaises(ParseError):compile_conditional(module, plan)
            self.assertEqual(save.cats.serialize(), before)

    def test_add_after_conditional_amendment_preserves_new_resource_rule(self):
        module, _, metadata = prepared()
        plan = interpret_request('## キャラレベル変更\n### Aグループ:基本Lv50\n鬼にゃんま', character_metadata=metadata)
        plan = interpret_request('NPは現在値の2倍', plan, metadata)
        self.assertTrue(plan['conditional_mode'])
        before = copy.deepcopy(plan['conditional_rules'])
        added = interpret_request(REQUEST, plan, metadata)
        self.assertFalse(added['unresolved'], added['unresolved'])
        self.assertEqual(added['conditional_rules'][:-1], before)
        self.assertEqual(added['conditional_rules'][-1]['field'], 'form')
        self.assertTrue(any(r['domain']=='resource' and r['field']=='np' for r in added['conditional_rules']))

    def test_existing_form_label_does_not_duplicate_and_year_review_survives(self):
        module, _, metadata = prepared()
        text = '## キャラ解放\n【恒常ガチャ】\n超激レア:80%解放\n2020年以前を最後に開催されたコラボシリーズは、全レアリティで取得対象から除外\n## キャラレベル変更\n' + REQUEST
        plan = interpret_request(text, character_metadata=metadata)
        self.assertTrue(plan['_pending_document_ownership'])
        added = interpret_request(REQUEST, plan, metadata)
        self.assertTrue(added['_pending_document_ownership'])
        self.assertFalse(added['ready'])
        self.assertEqual(sum('→ 使用形態' in label['label'] for label in added['labels']), 1)
        omitted = interpret_request('未解釈項目1不要', added, metadata)
        self.assertFalse(omitted['unresolved'], omitted['unresolved'])
        self.assertEqual(sum(r['domain']=='cat' and r['field']=='form' for r in omitted['document_rules']), 1)
        self.assertTrue(any(r['domain']=='ownership' for r in omitted['document_rules']))

    def test_unknown_evolution_state_or_missing_hook_stops(self):
        module, _, metadata = prepared()
        del metadata['characters'][2]['true_form_state']
        plan = interpret_request(REQUEST, character_metadata=metadata)
        self.assertTrue(plan['unresolved'])
        module, _, metadata = prepared()
        plan = interpret_request(REQUEST, character_metadata=metadata)
        del module.character_third_form_state
        with self.assertRaisesRegex(ParseError, '実行接続'):compile_conditional(module, plan)

    def test_render_creation_confirmation_keeps_form_operation(self):
        import test_render_chat
        harness = test_render_chat.ChatTests(); harness.setUp()
        module, save, metadata = prepared()
        for key, value in vars(module).items():
            if key != '_apply_all_segments_unlocked':setattr(harness.m, key, value)
        harness.post('settings', {'mode':'create','account_type':'new','count':1})
        response = harness.post('chat', {'message':'## キャラレベル変更\n' + REQUEST, 'request_id':'third-form-draft'})
        self.assertTrue(response.json['ready'], response.json)
        result = harness.post('confirm', {'confirmation_id':response.json['confirmation_id'],'request_id':'third-form-confirm'})
        self.assertEqual(result.status_code, 200, result.json)
        harness.m.run_job_create = lambda *args, **kwargs:harness.m._apply_all_segments_unlocked(save, {}, [], {})
        args, kwargs = harness.executor.submit.call_args
        args[0](*args[1:], **kwargs)
        self.assertEqual(save.cats.cats[2].current_form, 2)


if __name__ == '__main__':
    unittest.main()
