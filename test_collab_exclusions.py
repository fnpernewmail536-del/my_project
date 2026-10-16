"""Scoped collaboration plus values and named level exclusions with save effects."""
import copy
import tempfile
from types import SimpleNamespace as NS
import unittest
from local_chat.interpreter import interpret_request
from local_chat.details import compile_details,apply_details
from local_chat.engine import Engine
from local_chat.parser import ParseError
from test_collaboration_levels import original_metadata
import test_local_chat as originals

CODES='\n引き継ぎコード:abc123456\n認証番号:0123'
TEXT='''レアチケット：999
通常チケット：999
基本レベルは変更せず、＋値だけ変更
コラボキャラ：＋10〜15の範囲でおまかせ
ただし、コラボの超激レア：＋1
伝説レア：＋3
変更対象から除外：
・チビガウ
・メルストのノノ
上記2体は基本レベル・＋値ともに現在のまま'''

def metadata():
    entries=[(1,'ネコ',0,False),(11,'普通EX',1,False),(10,'普通レア',2,False),
             (68,'チビガウ',2,True),(112,'ノノ',2,True),(120,'他コラボレア',2,True),
             (150,'普通激レア',3,False),(200,'普通超激レア',4,False),(201,'コラボ超激キャラ',4,True),
             (300,'普通伝説',5,False),(301,'コラボ伝説',5,True)]
    return {'characters':[{'id':cid,'names':[name],'rarity':rarity,'collab':collab,'selectable':True} for cid,name,rarity,collab in entries],
            'groups':[{'kind':'collab','name':'「メルクストーリア」コラボガチャ','cat_ids':[112]},
                      {'kind':'collab','name':'「ケリ姫スイーツ」コラボガチャ','cat_ids':[68]}]}

def module(meta=None):return NS(get_character_metadata=lambda:meta or metadata(),_apply_all_segments_unlocked=lambda *a:[])
def parse(text,old=None,meta=None):return interpret_request(text,old,meta or metadata())
def save_data():
    cats={}
    for info in metadata()['characters']:
        cid=info['id'];cats[cid]=NS(id=cid-1,unlocked=True,upgrade=NS(base=cid%20+9,plus=5 if cid==201 else 9 if cid in (300,301) else 80))
        cats[cid].unlock=lambda save,c=cats[cid]:setattr(c,'unlocked',True)
    return NS(cats=NS(get_cat_by_id=lambda cid:cats.get(cid+1)),catfood=40000,xp=99999999,np=5000),cats
def snapshot(cats):return {cid:(c.upgrade.base,c.upgrade.plus,c.unlocked) for cid,c in cats.items()}

class CollabExclusionTests(unittest.TestCase):
    def test_entire_previously_failed_text_is_ready_with_no_uninterpreted_clauses(self):
        p=parse(TEXT+CODES)
        self.assertTrue(p['ready']);self.assertFalse(p['unresolved']);self.assertFalse(p['blockers'])
        self.assertEqual(p['plus_only_rules'],{'collab':[10,15],'collab:4':[1,1],'rarity:5':[3,3]})
        self.assertEqual({x['id'] for x in p['level_exclusion_resolved']},{68,112})
        self.assertIn('custom_rare_tickets',p['selected']['s1']);self.assertIn('custom_normal_tickets',p['selected']['s1'])

    def test_exclusions_win_and_normal_units_and_base_levels_are_preserved(self):
        p=parse(TEXT);save,cats=save_data();before=snapshot(cats)
        compiled=compile_details(module(),p);logs=apply_details(module(),save,compiled)
        self.assertFalse({68,112}&{x['id'] for x in compiled['levels']})
        for cid,c in cats.items():
            self.assertEqual(c.upgrade.base,before[cid][0])
            if cid in (68,112,1,11,10,150,200):self.assertEqual(c.upgrade.plus,before[cid][1])
            elif cid==201:self.assertEqual(c.upgrade.plus,1)
            elif cid in (300,301):self.assertEqual(c.upgrade.plus,3)
            else:self.assertTrue(10<=c.upgrade.plus<=15)
        self.assertEqual((save.catfood,save.xp,save.np),(40000,99999999,5000))
        self.assertTrue(any('チビガウ' in line and 'ノノ' in line for line in logs))

    def test_collab_ultra_only_does_not_change_normal_ultra_or_other_collab_rarities(self):
        p=parse('コラボの超激レア：＋1');save,cats=save_data();before=snapshot(cats)
        apply_details(module(),save,compile_details(module(),p))
        for cid,c in cats.items():self.assertEqual(c.upgrade.plus,1 if cid==201 else before[cid][1])

    def test_scoped_collab_exception_wins_in_either_order(self):
        for text in ['コラボ＋10〜15\nコラボ超激レア＋1','コラボ超激レア＋1\nコラボ＋10〜15']:
            with self.subTest(text=text):
                save,cats=save_data();apply_details(module(),save,compile_details(module(),parse(text)))
                self.assertEqual(cats[201].upgrade.plus,1);self.assertTrue(10<=cats[120].upgrade.plus<=15)

    def test_phrasing_and_reverse_scope_variants(self):
        for text in ['コラボの超激レア＋1','超激レアのコラボ＋1','コラボ系の超激レアのプラス値を1でお願いします',
                     'ただし、コラボの超激レア：＋1','コラボキャラの超激レアプラス1']:
            with self.subTest(text=text):self.assertEqual(parse(text)['plus_only_rules']['collab:4'],[1,1])

    def test_natural_named_preservation_and_heading_have_same_targets(self):
        for text in ['チビガウとメルストのノノだけそのままでお願いします',
                     'チビガウとメルストのノノだけそのままでお願いします追加失礼します',
                     'レベル除外=チビガウとメルストのノノ','【変更対象から除外】\n・チビガウ\n・メルストのノノ']:
            with self.subTest(text=text):
                p=parse('コラボ＋10〜15\n'+text)
                self.assertFalse(p['blockers']);self.assertEqual({x['id'] for x in p['level_exclusion_resolved']},{68,112})

    def test_exclusions_also_prevent_legacy_all_max_from_overwriting_levels(self):
        p=parse('キャラMAX\nレベル除外=チビガウとメルストのノノ')
        self.assertNotIn('max_all_cats_plus',p['selected']['s3']);self.assertEqual(len(p['level_rules']),6)
        save,cats=save_data();before=snapshot(cats);apply_details(module(),save,compile_details(module(),p))
        for cid,c in cats.items():
            self.assertEqual((c.upgrade.base,c.upgrade.plus),before[cid][:2] if cid in (68,112) else (59,90))

    def test_excluded_locked_character_is_not_unlocked_by_level_change(self):
        p=parse('コラボ＋10〜15\nレベル除外=チビガウ');save,cats=save_data();cats[68].unlocked=False
        apply_details(module(),save,compile_details(module(),p));self.assertFalse(cats[68].unlocked);self.assertEqual(cats[68].upgrade.plus,80)

    def test_exclusion_only_is_not_an_executable_edit(self):
        p=parse('レベル除外=チビガウ'+CODES)
        self.assertFalse(p['ready']);self.assertIn('代行内容',p['missing'])

    def test_later_codes_quantities_and_global_plus_changes_keep_exclusions(self):
        p=parse('コラボ＋10〜15\nレベル除外=チビガウとメルストのノノ')
        p=parse('コラボ＋20\nNP5000'+CODES,p)
        self.assertTrue(p['ready']);self.assertEqual({x['id'] for x in p['level_exclusion_resolved']},{68,112})
        save,cats=save_data();apply_details(module(),save,compile_details(module(),p))
        self.assertEqual(cats[68].upgrade.plus,80);self.assertEqual(cats[112].upgrade.plus,80);self.assertEqual(cats[120].upgrade.plus,20)

    def test_explicit_full_rarity_plus_replaces_old_scoped_collab_rule(self):
        p=parse('コラボ超激レア＋1');p=parse('超激レアレベル50+30',p)
        self.assertNotIn('collab:4',p['plus_only_rules']);save,cats=save_data();apply_details(module(),save,compile_details(module(),p))
        self.assertEqual(cats[201].upgrade.plus,30);self.assertEqual(cats[200].upgrade.plus,30)

    def test_removing_level_exclusions_is_explicit_and_keeps_other_requests(self):
        p=parse('NP5000\nコラボ＋10〜15\nレベル除外=チビガウ')
        p=parse('レベル除外なし',p)
        self.assertFalse(p['level_exclusion_queries']);self.assertFalse(p['level_exclusion_resolved'])
        self.assertEqual(p['custom_amounts']['custom_np'],5000);self.assertFalse(p['blockers'])

    def test_all_scope_members_can_be_excluded_without_false_missing_definition_error(self):
        p=parse('コラボレア＋10\nレベル除外=チビガウとノノと他コラボレア')
        self.assertEqual(compile_details(module(),p)['levels'],[])

    def test_invalid_ranges_and_count_mismatch_block_without_mutating_prior_plan(self):
        p=parse('NP5000');before=copy.deepcopy(p)
        with self.assertRaises(ParseError):parse('コラボ超激レア＋15〜10',p)
        self.assertEqual(p,before)
        q=parse('コラボ＋10\nレベル除外=チビガウ\n上記2体は基本レベル・＋値ともに現在のまま'+CODES)
        self.assertFalse(q['ready']);self.assertTrue(q['blockers'])

    def test_unknown_ambiguous_exclusions_and_conditional_modifiers_do_not_execute(self):
        m=metadata();m['characters'].append({'id':113,'names':['ノノ'],'rarity':2,'collab':True,'selectable':True})
        for text in ['レベル除外=存在しない対象名','レベル除外=No.999999','レベル除外=ノノ',
                     'ただし、日本編だけ','コラボ超激レア＋1にしない','コラボ超激レア＋1以外']:
            with self.subTest(text=text):
                p=parse('NP5000\n'+text+CODES,meta=m);self.assertFalse(p['ready']);self.assertTrue(p['blockers'])
                if text=='レベル除外=ノノ':self.assertNotIn('指定キャラ解放を',p['blockers'][0])

    def test_changed_exclusion_identity_stops_before_account_transfer(self):
        p=parse(TEXT+CODES);p['_review_confirmed']=True;m=metadata();m['characters'][4]['id']=114
        engine_calls=[];mod=module(m);mod.run_job_daiko=lambda *a,**k:engine_calls.append(a)
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=mod
            with self.assertRaisesRegex(ParseError,'引き継ぎ取得前'):engine.execute('jid','oid',p)
        self.assertFalse(engine_calls)

class OriginalCollabExclusionTests(unittest.TestCase):
    def test_exact_user_text_uses_actual_chibigau_and_nono_ids_and_ultra_scope(self):
        m=original_metadata();p=parse(TEXT+CODES,meta=m)
        self.assertTrue(p['ready']);self.assertEqual({x['id'] for x in p['level_exclusion_resolved']},{68,112})
        compiled=compile_details(module(m),p)
        self.assertFalse({68,112}&{x['id'] for x in compiled['levels']})
        self.assertEqual({x['plus'] for x in compiled['levels'] if x['rarity']==4 and x['collab']},{1})

class CollabExclusionHttpTests(unittest.TestCase):
    setUp=originals.HttpTests.setUp;tearDown=originals.HttpTests.tearDown
    request=originals.HttpTests.request;chat=originals.HttpTests.chat;confirm=originals.HttpTests.confirm
    def test_request_requires_review_and_later_correction_invalidates_old_confirmation(self):
        self.engine.character_metadata=metadata
        first=self.chat(TEXT+CODES)[1];self.assertTrue(first['ready']);self.assertFalse(self.engine.calls)
        second=self.chat('コラボ超激レア＋2')[1];self.assertEqual(self.confirm(first)[0],409)
        self.assertEqual(self.confirm(second)[1]['kind'],'started');self.state.worker.shutdown(wait=True)
        self.assertEqual(self.engine.calls[0]['plus_only_rules']['collab:4'],[2,2])
        self.assertEqual({x['id'] for x in self.engine.calls[0]['level_exclusion_resolved']},{68,112})

    def test_unknown_exclusion_prevents_approval_until_corrected(self):
        self.engine.character_metadata=metadata
        first=self.chat('コラボ＋10〜15\nレベル除外=存在しない対象名'+CODES)[1]
        self.assertFalse(first['ready']);self.assertEqual(self.confirm(first)[0],400)
        second=self.chat('レベル除外なし\nレベル除外=No.68とNo.112')[1]
        self.assertTrue(second['ready']);self.assertEqual(self.confirm(first)[0],409)

if __name__=='__main__':unittest.main(verbosity=2)
