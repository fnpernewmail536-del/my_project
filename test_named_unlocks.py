"""Named unlock requests, reviewed identities, save effects and HTTP confirmation."""
import copy
import tempfile
from types import SimpleNamespace as NS
import unittest
from local_chat.interpreter import interpret_request
from local_chat.parser import ParseError
from local_chat.named_characters import NameIndex
from local_chat.details import compile_details,apply_details
from local_chat.engine import Engine
from test_collaboration_levels import original_metadata
import test_local_chat as originals

CODES='\n引き継ぎコード:abc123456\n認証番号:0123'

def metadata():
    units=[(1,['ネコ','ネコビルダー']),(881,['志々雄真実','志々雄真実CC']),
           (882,['比古清十郎','比古清十郎CC']),(536,['初音ミク']),(537,['桜ミク'])]
    return {'characters':[{'id':cid,'names':names,'rarity':4 if cid!=1 else 0,'selectable':True,'collab':cid!=1} for cid,names in units]}

def engine_module(meta=None):
    return NS(get_character_metadata=lambda:meta or metadata(),_apply_all_segments_unlocked=lambda *a,**k:[])

def save_data():
    cats={};calls=[]
    for cid in (1,881,882,536,537):
        cat=NS(id=cid-1,unlocked=False,upgrade=NS(base=20,plus=7),current_form=0,unlocked_forms=0,fourth_form=0)
        def unlock(save,current=cat):current.unlocked=True;calls.append(current.id+1)
        cat.unlock=unlock;cats[cid]=cat
    return NS(cats=NS(get_cat_by_id=lambda cid:cats.get(cid+1)),catfood=123,xp=456,np=789),cats,calls

def parse(text,previous=None,meta=None):return interpret_request(text,previous,meta or metadata())

class NamedUnlockTests(unittest.TestCase):
    def test_phrasing_corpus_resolves_exactly_the_two_requested_characters(self):
        for text in ['比古清十郎と志々雄真実を解放して','比古清十郎、志々雄真実を開放してください',
                     '指定キャラ解放：比古清十郎、志々雄真実','指定キャラ開放:\n・比古清十郎\n・志々雄真実',
                     '指定キャラ解放\n比古清十郎\n志々雄真実','【指定キャラ解放】\n比古清十郎\n志々雄真実',
                     '【指定キャラ開放】比古清十郎と志々雄真実','解放してほしいキャラは比古清十郎と志々雄真実',
                     '比古清十郎と志々雄真実の解放をお願いします','指定キャラとして比古清十郎と志々雄真実を解放してください',
                     'メニュー27=比古清十郎/志々雄真実','指定キャラ1体（比古清十郎、志々雄真実）：300円',
                     '指定キャラ解放：比古 清十郎・志々雄 真実','指定キャラ解放：No.882とNo.881',
                     '指定キャラ解放：881、882','指定キャラ解放：比古清十郎 志々雄真実',
                     '「比古清十郎」と「志々雄真実」を解放して','指定キャラ解放：比古清十郎と志々雄真実お願いします']:
            with self.subTest(text=text):
                p=parse(text+CODES)
                self.assertTrue(p['ready']);self.assertFalse(p['unresolved']);self.assertFalse(p['blockers'])
                self.assertEqual({x['id'] for x in p['unlock_resolved']},{881,882})
                self.assertFalse(p['selected']);self.assertFalse(p['level_rules'])
                self.assertFalse(p.get('_review_confirmed'))

    def test_literal_names_containing_separators_are_kept_as_one_character(self):
        m=metadata();m['characters'][3]['names']=['妖精と仲間たち'];m['characters'][4]['names']=['妖精','仲間たち']
        p=parse('妖精と仲間たちと比古清十郎を解放して',meta=m)
        self.assertEqual({x['id'] for x in p['unlock_resolved']},{536,882})

    def test_changing_target_removes_stale_named_inference(self):
        p=parse('比古清十朗を解放して');self.assertTrue(p['inferences'])
        p=parse('指定キャラ解放をNo.881に変更して',p)
        self.assertFalse(any('比古' in i['input'] for i in p['inferences']))

    def test_form_names_and_quotes_deduplicate_the_same_character(self):
        p=parse('「比古清十郎CC」と比古清十郎を解放してください')
        self.assertEqual([x['id'] for x in p['unlock_resolved']],[882])
        self.assertEqual(p['unlock_resolved'][0]['matched_name'],'比古清十郎CC')

    def test_hiragana_katakana_and_fullwidth_normalization(self):
        self.assertEqual(parse('ねこを解放して')['unlock_resolved'][0]['id'],1)
        self.assertEqual(parse('指定キャラ解放：Ｎｏ．８８２')['unlock_resolved'][0]['id'],882)

    def test_unique_short_name_and_typo_are_visible_proposals_not_approval(self):
        for text in ['比古を解放して','比古清十朗を解放してください','志々雄真美を開放して']:
            with self.subTest(text=text):
                p=parse(text+CODES)
                self.assertTrue(p['ready']);self.assertTrue(p['inferences'])
                self.assertTrue(any('推測候補' in x['label'] for x in p['labels']))
                self.assertNotIn('_review_confirmed',p)

    def test_ambiguous_abbreviation_blocks_even_with_codes(self):
        p=parse('指定キャラ解放：ミク'+CODES)
        self.assertFalse(p['ready']);self.assertEqual(len(p['unlock_issues'][0]['candidates']),2)
        self.assertFalse(p['unlock_resolved']);self.assertIn('No.536',p['blockers'][0])

    def test_exact_duplicate_names_block_instead_of_selecting_first(self):
        m=metadata();m['characters'][3]['names'].append('同じ名前');m['characters'][4]['names'].append('同じ名前')
        p=parse('指定キャラ解放：同じ名前'+CODES,meta=m)
        self.assertFalse(p['ready']);self.assertFalse(p['unlock_resolved'])

    def test_unknown_name_invalid_id_and_negative_modifiers_cannot_execute(self):
        for text in ['指定キャラ解放：存在しないキャラ名','指定キャラ解放：No.0','指定キャラ解放：No.999999',
                     '比古清十郎を解放しないでください','指定キャラ解放：比古清十郎以外','指定キャラ解放：比古清十郎だけ',
                     '指定キャラ解放：比古清十郎Lv50','指定キャラ解放：比古清十郎+10',
                     '指定キャラ解放：比古清十郎\nその他不要']:
            with self.subTest(text=text):
                p=parse(text+CODES)
                self.assertFalse(p['ready']);self.assertTrue(p['blockers'])

    def test_price_does_not_become_character_id_and_target_is_required(self):
        p=parse('指定キャラ1体：300円'+CODES)
        self.assertFalse(p['ready']);self.assertFalse(p['unlock_resolved'])
        self.assertEqual(p['quoted_prices'][0]['price_jpy'],300)

    def test_missing_metadata_stops_before_any_target_is_approved(self):
        p=interpret_request('比古清十郎を解放して'+CODES)
        self.assertFalse(p['ready']);self.assertFalse(p['unlock_resolved']);self.assertTrue(p['menu_pending'])

    def test_later_codes_and_unrelated_quantities_keep_resolved_targets(self):
        first=parse('比古清十郎を解放して');next_plan=parse('NP5000'+CODES,first)
        self.assertTrue(next_plan['ready']);self.assertEqual(next_plan['unlock_resolved'],first['unlock_resolved'])
        self.assertEqual(next_plan['custom_amounts']['custom_np'],5000)

    def test_unknown_target_can_be_replaced_with_explicit_choice(self):
        p=parse('指定キャラ解放：ミク\nNP5000')
        p=parse('指定キャラ解放をNo.536に変更して'+CODES,p)
        self.assertTrue(p['ready']);self.assertEqual([x['id'] for x in p['unlock_resolved']],[536])
        self.assertEqual(p['custom_amounts']['custom_np'],5000)

    def test_more_named_targets_append_and_cancel_removes_them_and_fee(self):
        p=parse('指定キャラ1体（比古清十郎）：150円')
        p=parse('追加で志々雄真実を解放して',p)
        self.assertEqual({x['id'] for x in p['unlock_resolved']},{881,882})
        p=parse('NP5000\nメニュー27不要',p)
        self.assertFalse(p['unlock_queries']);self.assertFalse(p['unlock_resolved']);self.assertFalse(p['quoted_prices'])
        self.assertEqual(p['selected']['s1'],['custom_np'])

    def test_following_service_is_not_misclassified_as_another_character(self):
        p=parse('指定キャラ解放\n比古清十郎\n志々雄真実\nレジェンド全ステージクリア\nNP5000'+CODES)
        self.assertTrue(p['ready']);self.assertEqual({x['id'] for x in p['unlock_resolved']},{881,882})
        self.assertIn('legend_clear',p['selected']['s2']);self.assertEqual(p['custom_amounts']['custom_np'],5000)

    def test_only_named_units_are_unlocked_and_levels_forms_items_are_kept(self):
        p=parse('比古清十郎と志々雄真実を解放して');save,cats,calls=save_data()
        logs=apply_details(engine_module(),save,compile_details(engine_module(),p))
        self.assertEqual(calls,[882,881]);self.assertEqual({cid for cid,c in cats.items() if c.unlocked},{881,882})
        for cat in cats.values():
            self.assertEqual((cat.upgrade.base,cat.upgrade.plus),(20,7))
            self.assertEqual((cat.current_form,cat.unlocked_forms,cat.fourth_form),(0,0,0))
        self.assertEqual((save.catfood,save.xp,save.np),(123,456,789));self.assertIn('比古清十郎',logs[0])

    def test_level_changes_apply_after_named_unlocks_only_when_requested(self):
        p=parse('比古清十郎を解放して\n超激レアレベル50+10')
        save,cats,_=save_data();apply_details(engine_module(),save,compile_details(engine_module(),p))
        self.assertEqual((cats[882].upgrade.base+1,cats[882].upgrade.plus),(50,10))
        self.assertEqual((cats[881].unlocked,cats[881].upgrade.base+1,cats[881].upgrade.plus),(False,21,7))

    def test_named_and_rarity_unlocks_are_deduplicated_and_both_reported(self):
        p=parse('比古清十郎を解放して\nメニュー24')
        save,cats,calls=save_data();logs=apply_details(engine_module(),save,compile_details(engine_module(),p))
        self.assertEqual(set(calls),{881,882,536,537});self.assertEqual(len(calls),4)
        self.assertTrue(any('合計4体' in line for line in logs));self.assertFalse(cats[1].unlocked)

    def test_unselectable_named_target_is_not_replaced_by_a_similar_selectable_unit(self):
        m=metadata();m['characters'][1]['selectable']=False
        p=parse('志々雄真実を解放して'+CODES,meta=m)
        self.assertFalse(p['ready']);self.assertFalse(p['unlock_resolved'])

    def test_duplicate_or_bad_metadata_ids_fail_transactionally(self):
        previous=parse('NP5000');before=copy.deepcopy(previous)
        for kind in ['duplicate','fractional_id','invalid_names','string_selectable']:
            with self.subTest(kind=kind):
                m=metadata()
                if kind=='duplicate':m['characters'].append(copy.deepcopy(m['characters'][0]))
                elif kind=='fractional_id':m['characters'][0]['id']=1.5
                elif kind=='invalid_names':m['characters'][0]['names']='ネコ'
                else:m['characters'][0]['selectable']='false'
                with self.assertRaises(ParseError):parse('比古清十郎を解放して',previous,m)
                self.assertEqual(previous,before)

    def test_changed_identity_or_new_ambiguous_name_stops_before_transfer(self):
        for change in ['identity','ambiguity','unselectable']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                p=parse('比古を解放して'+CODES);p['_review_confirmed']=True
                m=metadata()
                if change=='identity':m['characters'][2]['id']=883
                elif change=='ambiguity':m['characters'][1]['names'].append('比古別キャラ')
                else:m['characters'][2]['selectable']=False
                module=engine_module(m);calls=[];module.run_job_daiko=lambda *a,**k:calls.append(a)
                engine=Engine(temp,temp);engine.module=module
                with self.assertRaisesRegex(ParseError,'引き継ぎ取得前'):engine.execute('jid','oid',p)
                self.assertFalse(calls)

    def test_missing_save_target_raises_instead_of_reporting_success(self):
        p=parse('比古清十郎を解放して');save,cats,_=save_data();del cats[882]
        with self.assertRaisesRegex(RuntimeError,'解放対象'):apply_details(engine_module(),save,compile_details(engine_module(),p))

    def test_engine_bridge_applies_unlocks_after_original_worker_without_bulk_unlock(self):
        p=parse('比古清十郎を解放して'+CODES);p['_review_confirmed']=True
        save,cats,calls=save_data();module=engine_module()
        def original(current,selected):self.assertNotIn('unlock_all_cats',selected.get('s3',[]));return []
        module._apply_all_segments_unlocked=original
        module.run_job_daiko=lambda *args,**kwargs:module._apply_all_segments_unlocked(save,args[4])
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=module;engine.install_detail_adapter(module);engine.execute('jid','oid',p)
        self.assertEqual(calls,[882]);self.assertFalse(cats[881].unlocked)

class OriginalNamedUnlockTests(unittest.TestCase):
    def test_user_supplied_names_and_form_aliases_match_actual_bundled_metadata(self):
        m=original_metadata()
        for text in ['比古清十郎と志々雄真実を解放して','比古清十郎CCと志々雄真実CCを開放してください',
                     '指定キャラ解放:\n・比古清十郎\n・志々雄真実']:
            with self.subTest(text=text):
                p=interpret_request(text+CODES,character_metadata=m)
                self.assertTrue(p['ready']);self.assertEqual({x['id'] for x in p['unlock_resolved']},{881,882})

class NamedUnlockHttpTests(unittest.TestCase):
    setUp=originals.HttpTests.setUp;tearDown=originals.HttpTests.tearDown
    request=originals.HttpTests.request;chat=originals.HttpTests.chat;confirm=originals.HttpTests.confirm
    def enable_metadata(self):self.engine.character_metadata=metadata
    def test_exact_named_request_requires_confirmation_and_runs_once(self):
        self.enable_metadata();first=self.chat('比古清十郎と志々雄真実を解放して'+CODES)[1]
        self.assertTrue(first['ready']);self.assertFalse(self.engine.prepared);self.assertFalse(self.engine.calls)
        a=self.confirm(first,'named-unlock-confirm')[1];b=self.confirm(first,'named-unlock-confirm')[1]
        self.assertEqual(a['job_id'],b['job_id']);self.state.worker.shutdown(wait=True)
        self.assertEqual(len(self.engine.calls),1);self.assertEqual({x['id'] for x in self.engine.calls[0]['unlock_resolved']},{881,882})

    def test_ambiguous_target_cannot_be_approved_and_choice_gets_new_confirmation(self):
        self.enable_metadata();first=self.chat('指定キャラ解放：ミク'+CODES)[1]
        self.assertFalse(first['ready']);self.assertEqual(self.confirm(first)[0],400)
        second=self.chat('指定キャラ解放をNo.536に変更して')[1]
        self.assertTrue(second['ready']);self.assertEqual(self.confirm(first)[0],409)
        self.assertEqual(self.confirm(second)[1]['kind'],'started');self.state.worker.shutdown(wait=True)
        self.assertEqual([x['id'] for x in self.engine.calls[0]['unlock_resolved']],[536])

    def test_typo_is_reviewed_and_correction_invalidates_old_approval(self):
        self.enable_metadata();first=self.chat('比古清十朗を解放して'+CODES)[1]
        self.assertTrue(first['inferences']);self.assertFalse(self.engine.prepared)
        second=self.chat('指定キャラ解放をNo.881に変更して')[1]
        self.assertEqual(self.confirm(first)[0],409);self.assertEqual(self.confirm(second)[1]['kind'],'started')
        self.state.worker.shutdown(wait=True);self.assertEqual([x['id'] for x in self.engine.calls[0]['unlock_resolved']],[881])

if __name__=='__main__':unittest.main(verbosity=2)
