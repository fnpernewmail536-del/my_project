"""Orders must preserve operation roles, section boundaries and real save values."""
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

from local_chat.interpreter import interpret_request
from local_chat.structured_requests import parse_document
from local_chat.named_characters import NameIndex
from local_chat.details import compile_details,apply_details
from local_chat.parser import ParseError
from test_advanced_requests import fixture,core

ORDER='''# 代行依頼内容
## ① アイテム・所持数変更
4.猫缶：5789
10.ネコビタン全種類：各201〜235
## ② キャラ解放
【恒常ガチャ】
超激レア：80%解放
## ③ キャラレベル変更
### Aグループ：基本Lv50、プラス値+5〜+20
対象キャラ：
にゃんま（鬼にゃんま）、ノノ
※コラボキャラのプラス値は+5〜+7
### Bグループ：基本Lv30、プラス値+2〜+6
ネコカンカン
## ⑧ 本能解放
以下のキャラは、本能の80%を解放してください。
鬼にゃんま、ノノ
## ⑨ 全体の処理ルール
2. 「全種類」「全種」は該当カテゴリの全アイテムに適用する。
8. 数値が未指定の項目は未指定のままにする。
9. 処理前にキャラ数・レベル・アイテム数・除外対象を一覧表示する。
'''

class StructuredTests(unittest.TestCase):
    def test_supplied_log_no_headings_become_ids_or_unlocks(self):
        module,save=fixture()
        p=interpret_request(ORDER,character_metadata=module.get_character_metadata())
        self.assertEqual(p['unlock_queries'],[])
        self.assertFalse(any(r['domain']=='cat' and r['field']=='unlocked' for r in p['document_rules']))
        self.assertFalse(p['ready'])
        self.assertEqual(p['custom_amounts']['custom_catfood'],5789)
        self.assertEqual(p['quantity_ranges']['10'],[201,235])
        self.assertEqual(p['document_summary']['characters_in_level_groups'],3)
        self.assertEqual(p['document_summary']['talent_characters'],2)
        self.assertEqual(len(p['document_rules']),7)
        self.assertEqual(len(p['unresolved']),1)

    def test_pasted_full_document_has_all_groups_and_no_numeric_targets(self):
        text=Path('testdata/structured_order.txt').read_text()
        d=parse_document(text)
        self.assertEqual([g['name'] for g in d['level_groups']],['A','B','C','B+','B'])
        self.assertEqual(d['summary']['characters_in_level_groups'],286)
        self.assertEqual(d['summary']['talent_characters'],51)
        self.assertEqual(d['collections']['catamins'],{'range':[201,235]})
        self.assertEqual(d['collections']['materials'],{'range':[85,285]})
        self.assertEqual(d['resources']['normal_tickets'],124)
        p=interpret_request(text)
        self.assertFalse(p['unlock_queries'])
        self.assertFalse(p['ready'])
        self.assertFalse(any(x['input'] in ('2','8','9','##') for x in p['unresolved']))
        self.assertTrue(any(r['domain']=='aku_ex' for r in p['document_rules']))
        self.assertTrue(any('コラボ取得対象' in x['input'] for x in p['unresolved']))

    def test_preview_prices_negation_max_and_unspecified(self):
        for value in ['猫缶指定：50円 45000','猫缶は変更しないでください。45000は例です。']:
            d=parse_document(value)
            self.assertFalse(d['operations']);self.assertTrue(d['issues'])
        self.assertEqual(parse_document('5.XP指定：MAX')['resources']['xp'],'MAX')
        self.assertFalse(parse_document('18.レジェチケ：未指定')['operations'])

    def test_annotated_formal_names_and_conflicts(self):
        module,_=fixture();index=NameIndex(module.get_character_metadata())
        self.assertEqual(set(index.exact('にゃんま（鬼にゃんま）')),{4})
        self.assertEqual(set(index.exact('ノノ（鬼にゃんま）')),{4,7})
        self.assertFalse(index.exact('にゃんま（存在しない名前）'))
        matches,issues=index.resolve_all(['にゃんま（鬼にゃんま）'])
        self.assertEqual([x['id'] for x in matches],[4]);self.assertFalse(issues)

    def test_missing_group_rule_and_reversed_range_block(self):
        module,_=fixture()
        text='## レベル変更\n### Aグループ：基本Lv40、プラス値+2〜+4\n鬼にゃんま\n【B+ランク】\nノノ'
        p=interpret_request(text,character_metadata=module.get_character_metadata())
        self.assertFalse(p['ready'])
        self.assertFalse(any('No.7' in r['selector'].get('names',[]) for r in p['document_rules']))
        p=interpret_request(text.replace('+2〜+4','+9〜+4'),character_metadata=module.get_character_metadata())
        self.assertFalse(p['document_rules'])

    def test_save_reload_and_collab_exception_keep_unlocks_other_units(self):
        module,save=fixture();module._apply_all_segments_unlocked=Mock()
        text='''## キャラレベル変更
### Aグループ：基本Lv50、プラス値+5〜+20
にゃんま（鬼にゃんま）、ノノ
※コラボキャラのプラス値は+5〜+7
### その他のレベル指定
レア：基本Lv30、プラス値+2〜+6'''
        p=interpret_request(text,character_metadata=module.get_character_metadata())
        self.assertFalse(p['blockers'],p['blockers'])
        before=copy.deepcopy(save.cats.serialize())
        compiled=compile_details(module,p)
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):apply_details(module,save,compiled)
        loaded=core.SaveFile(core.Data(save.to_data().data),cc=save.cc)
        self.assertEqual(loaded.cats.cats[3].upgrade.base,49)
        self.assertTrue(5<=loaded.cats.cats[3].upgrade.plus<=20)
        self.assertEqual(loaded.cats.cats[6].upgrade.base,49)  # named rule wins over rarity
        self.assertTrue(5<=loaded.cats.cats[6].upgrade.plus<=7)
        self.assertEqual(loaded.cats.cats[7].upgrade.base,29)
        self.assertTrue(2<=loaded.cats.cats[7].upgrade.plus<=6)
        self.assertEqual(loaded.cats.cats[0].upgrade.base,19)
        self.assertEqual(loaded.cats.favourites,save.cats.favourites)
        self.assertEqual(loaded.order_ids,save.order_ids)
        self.assertTrue(all(c.unlocked for c in loaded.cats.cats))

    def test_actual_cap_violation_stops_before_save_write(self):
        module,save=fixture();module._apply_all_segments_unlocked=Mock()
        p=interpret_request('## レベル変更\n### Aグループ：基本Lv50、プラス値+1〜+3\n暁のイザナギ',character_metadata=module.get_character_metadata())
        before=copy.deepcopy(save.cats.serialize())
        with self.assertRaisesRegex(ParseError,'上限は0'):
            compile_details(module,p)
        self.assertEqual(save.cats.serialize(),before)

class StructuredHttpTests(unittest.TestCase):
    def setUp(self):
        from test_render_chat import ChatTests
        self.h=ChatTests();self.h.setUp()

    def test_preview_protection_invalid_json_and_no_job(self):
        h=self.h
        self.assertEqual(h.client.post('/chat/complex-preview',json={'message':ORDER}).status_code,403)
        headers={'X-CSRF-Token':h.csrf}
        self.assertEqual(h.client.post('/chat/complex-preview',json=['wrong'],headers=headers).status_code,400)
        r=h.client.post('/chat/complex-preview',json={'message':ORDER},headers=headers)
        self.assertEqual(r.status_code,200)
        self.assertFalse(r.json['executable'])
        self.assertEqual(r.json['preview']['summary']['characters_in_level_groups'],3)
        h.executor.submit.assert_not_called()
        h.vip=False
        self.assertEqual(h.client.post('/chat/complex-preview',json={'message':ORDER},headers=headers).status_code,403)

    def test_supported_document_reaches_native_admission_after_confirmation(self):
        h=self.h;module,_=fixture()
        for name in ('get_character_metadata','get_legend_metadata','_latest_jp_game_data_getter','core'):
            setattr(h.m,name,getattr(module,name))
        p=h.post('chat',{'message':'## レベル変更\n### Aグループ：基本Lv50、プラス値+5〜+7\n鬼にゃんま','request_id':'document-1'})
        self.assertEqual(p.status_code,200,p.json)
        h.executor.submit.assert_not_called()
        p=h.post('credentials',{'transfer_code':'abc123456','auth_code':'0123'})
        self.assertTrue(p.json['ready'],p.json)
        result=h.post('confirm',{'confirmation_id':p.json['confirmation_id'],'request_id':'document-confirm'})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(result.json['kind'],'started')
        h.executor.submit.assert_called_once()
        self.assertIn('document_conditional',h.executor.submit.call_args.kwargs['chat_execution']['compiled'])

    def test_unresolved_percentage_never_registers_job(self):
        h=self.h
        p=h.post('chat',{'message':ORDER,'request_id':'document-block'})
        self.assertEqual(p.status_code,200,p.json)
        p=h.post('credentials',{'transfer_code':'abc123456','auth_code':'0123'})
        self.assertFalse(p.json['ready'])
        r=h.post('confirm',{'confirmation_id':p.json['confirmation_id'],'request_id':'document-confirm'})
        self.assertEqual(r.status_code,400)
        h.executor.submit.assert_not_called()

if __name__=='__main__':unittest.main()
