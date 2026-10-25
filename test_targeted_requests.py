"""Review scopes, history exclusions, removal and actual stage serialization."""
import ast,copy,datetime,re,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from local_chat.interpreter import interpret_request
from local_chat.structured_requests import parse_document
from local_chat.targeted_engine import review_targets,compile_targets
from local_chat.targeted_requests import ownership_exclusions
from local_chat.details import compile_details,apply_details
from local_chat.parser import ParseError
from test_advanced_requests import fixture,core

MAPPING=[0,1,2,4,5,6,7,8,9]


def prepared(fuji=False):
    m,s=fixture();m._apply_all_segments_unlocked=Mock();m.MAIN_STORY_REAL_INDEX=MAPPING
    source=Path('main13.py').read_text();tree=ast.parse(source)
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='EVENT_STAGE_FAMILIES' for t in n.targets))
    m.EVENT_STAGE_FAMILIES=ast.literal_eval(assignment.value)
    if fuji:
        s.cats.cats.extend(core.Cat.init(i) for i in range(len(s.cats.cats),623))
        m.get_character_metadata()['characters'].append({'id':623,'names':['ジャガンドーJr','破壊神ジャガンドーJr'],'rarity':1,'selectable':True,'collab':False})
        m.get_legend_metadata()['aku_ex']={'chapters':[{'id':0,'name':'富士山EX','stages':['富士山']}]}
        sub=core.game.map.item_reward_stage.SubChapterStars
        s.item_reward_stages.sub_chapters=[sub.init(1,1) for _ in range(43)]
    return m,s


def plan(text,m):
    p=interpret_request(text,character_metadata=m.get_character_metadata())
    review_targets(m,p)
    return p


class TargetedTests(unittest.TestCase):
    def execute(self,text,m,s):
        p=plan(text,m);self.assertFalse(p['unresolved'],p['unresolved'])
        compiled=compile_details(m,p)
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):apply_details(m,s,compiled)
        return core.SaveFile(core.Data(s.to_data().data),cc=s.cc),p

    def test_explicit_removal_keeps_levels_forms_favourites_and_other_units(self):
        m,s=prepared();before=copy.deepcopy(s.cats.serialize());lineups=copy.deepcopy(s.lineups.serialize())
        loaded,p=self.execute('## キャラ解放\n鬼にゃんまも削除',m,s)
        before['cats'][3]['unlocked']=0
        self.assertEqual(loaded.cats.serialize(),before)
        self.assertEqual(loaded.lineups.serialize(),lineups)
        self.assertIn('解放状態：0',p['labels'][0]['label'])

    def test_fuji_menu_can_be_cancelled_without_stale_target_labels(self):
        from local_chat.parser import parse_request
        m,s=prepared(fuji=True);p=parse_request('メニュー54')
        review_targets(m,p)
        self.assertTrue(any(l['key'].startswith('target_review_') for l in p['labels']))
        p=parse_request('メニュー54不要',p);review_targets(m,p)
        self.assertFalse(p['document_rules'])
        self.assertFalse(any('富士山' in l['label'] for l in p['labels']))

    def test_render_review_lists_targets_before_start_and_rechecks_definitions(self):
        import test_render_chat
        h=test_render_chat.ChatTests();h.setUp();m,_=prepared(fuji=True)
        for name in ('get_character_metadata','get_legend_metadata','core','EVENT_STAGE_FAMILIES'):
            setattr(h.m,name,getattr(m,name))
        r=h.post('chat',{'message':'## ステージ・お宝・スコア\n54.富士山EXクリア','request_id':'target-review-01'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertTrue(any('ジャガンドーJr' in l['label'] for l in r.json['labels']))
        self.assertFalse(h.executor.submit.called)
        r=h.post('credentials',{'transfer_code':'abc123456','auth_code':'0123'})
        self.assertTrue(r.json['ready'],r.json)
        m.get_legend_metadata()['aku_ex']['chapters'][0]['stages'][0]='changed'
        r=h.post('confirm',{'confirmation_id':r.json['confirmation_id'],'request_id':'target-confirm-01'})
        self.assertEqual(r.status_code,400,r.json);self.assertFalse(h.executor.submit.called)

    def test_local_review_uses_the_same_target_manifest(self):
        import test_local_chat
        h=test_local_chat.HttpTests();h.setUp()
        try:
            m,_=prepared(fuji=True);h.engine.module=m;h.engine.character_metadata=m.get_character_metadata()
            code,r,_=h.chat('## ステージ・お宝・スコア\n54.富士山EXクリア')
            self.assertEqual(code,200,r)
            self.assertTrue(any('ジャガンドーJr' in l['label'] for l in r['labels']))
            self.assertFalse(h.engine.calls)
        finally:h.tearDown()

    def test_unknown_removal_target_blocks(self):
        m,s=prepared();p=plan('## キャラ解放\n架空キャラも削除',m)
        self.assertTrue(p['unresolved']);self.assertFalse(p['document_rules'])

    def test_removal_is_excluded_from_ownership_quota(self):
        m,s=prepared()
        for c in s.cats.cats:c.unlocked=False
        s.cats.cats[3].unlocked=True
        loaded,p=self.execute('## キャラ解放\n【恒常ガチャ】\n超激レア:100%解放\n鬼にゃんまも削除',m,s)
        self.assertFalse(loaded.cats.cats[3].unlocked);self.assertTrue(loaded.cats.cats[4].unlocked)
        self.assertEqual(next(r for r in p['document_rules'] if r['domain']=='ownership')['expected_ids'],[5])

    def test_explicit_chapters_and_future_scores_roundtrip_individually(self):
        m,s=prepared()
        with patch('local_chat.conditional_engine.roll',side_effect=lambda bounds:bounds[1]) as draw:
            loaded,p=self.execute('## ステージ・お宝・スコア\n42.各ステージのクリア回数:本編9章、各4〜20回\n44.未来編の採点スコア:全3章、各8500〜9000',m,s)
        self.assertEqual(draw.call_count,9*48+3*48)
        for pos,real in enumerate(MAPPING):
            self.assertEqual([stage.clear_times for stage in loaded.story.chapters[real].get_valid_treasure_stages()],[20]*48)
            self.assertEqual([stage.itf_timed_score for stage in loaded.story.chapters[real].get_valid_treasure_stages()],[9000 if pos in (3,4,5) else 0]*48)
        self.assertEqual(loaded.story.chapters[3].serialize(),s.story.chapters[3].serialize())

    def test_missing_scope_and_bad_bounds_do_not_guess_all(self):
        m,s=prepared()
        for row in ('42.各ステージのクリア回数:4〜20回','44.未来編の採点スコア:8500〜9000','59.イベントステージクリア','67.隊員追加:MAX','68.隊員クラスアップ:レジェンド'):
            p=plan('## ステージ・お宝・スコア\n'+row,m)
            self.assertTrue(p['unresolved'],row)
        with self.assertRaises(ParseError):plan('## ステージ・お宝・スコア\n44.未来編の採点スコア:全3章、各9001〜8500',m)

    def test_ten_legend_helpers_preserve_existing_legend_members(self):
        m,s=prepared();Helper=core.game.gamoto.gamatoto.Helper
        s.gamatoto.helpers.helpers=[Helper(104),Helper(202)]+[Helper(-1) for _ in range(8)]
        loaded,_=self.execute('## ガマトト・オトート\n67.隊員追加:合計10人、全員レジェンド\n68.隊員クラスアップ:全10人、レジェンド',m,s)
        ids=[h.id for h in loaded.gamatoto.helpers.helpers]
        self.assertEqual(len(set(ids)),10);self.assertIn(104,ids);self.assertEqual(set(ids),set(range(100,110)))

    def test_fuji_ex_clear_reward_and_character_roundtrip(self):
        m,s=prepared(fuji=True)
        loaded,p=self.execute('## ステージ・お宝・スコア\n54.富士山EXクリア',m,s)
        self.assertTrue(loaded.cats.get_cat_by_id(622).unlocked)
        self.assertTrue(loaded.item_reward_stages.sub_chapters[42].sub_chapters[0].stages[0].claimed)
        self.assertEqual(loaded.event_stages.chapters[1].chapters[42].chapters[0].stages[0].clear_amount,1)
        self.assertTrue(any('報酬キャラ' in l['label'] for l in p['labels']))

    def test_fuji_missing_definition_or_changed_review_stops_before_apply(self):
        m,s=prepared(fuji=True);p=plan('## ステージ・お宝・スコア\n54.富士山EXクリア',m)
        m.get_legend_metadata()['aku_ex']['chapters'][0]['stages'][0]='別のステージ'
        with self.assertRaisesRegex(ParseError,'変更'):compile_details(m,p)
        m.get_legend_metadata().pop('aku_ex');p=plan('## ステージ・お宝・スコア\n54.富士山EXクリア',m)
        self.assertTrue(p['unresolved'])

    def test_fuji_reward_receives_requested_ex_levels_in_the_same_order(self):
        from types import SimpleNamespace
        m,s=prepared(fuji=True)
        getter=m._latest_jp_game_data_getter()
        rows=getter.download('DataLocal','unitbuy.csv').data.decode().splitlines()
        while len(rows)<623:rows.append(','.join(['0']*52))
        row=[0]*52;row[13]=1;row[50]=60;rows[622]=','.join(map(str,row))
        m._latest_jp_game_data_getter=lambda:SimpleNamespace(download=lambda loc,name:core.Data('\n'.join(rows).encode()) if name=='unitbuy.csv' else getter.download(loc,name))
        loaded,_=self.execute('## キャラレベル変更\nEX:各キャラのMAXレベル\n## ステージ・お宝・スコア\n54.富士山EXクリア',m,s)
        cat=loaded.cats.get_cat_by_id(622)
        self.assertTrue(cat.unlocked)
        self.assertEqual((cat.upgrade.base,cat.upgrade.plus),(59,0))

    def test_all_events_all_families_roundtrip_and_keep_existing_counts(self):
        m,s=prepared();families={}
        for key in m.EVENT_STAGE_FAMILIES:
            families[key]={'maps':[{'id':0,'name':'fixture-'+key,'stages':['first','second'],'crowns':1}]}
        m.get_legend_metadata()['event_stage_families']=families
        s.legend_quest.ids=[101,102]
        tree=ast.parse(Path('main13.py').read_text());names={'_labyrinth_value','_set_labyrinth_value','_labyrinth_unlocked_cat_ids','_sync_labyrinth_remaining','apply_labyrinth_stages'}
        env={};exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'labyrinth','exec'),env)
        for name in names:setattr(m,name,env[name])
        s.ush5=80
        from local_chat.targeted_engine import target_manifest,ensure_slots
        targets=target_manifest(m,{'domain':'event_all','families':['all']})
        ensure_slots(m,s,targets)
        s.tower.chapters.chapters[0].chapters[0].stages[0].clear_times=17
        s.event_stages.chapters[1].chapters[0].chapters[0].stages[0].clear_amount=22
        loaded,p=self.execute('## ステージ・お宝・スコア\n59.イベントステージクリア:読み込めた全イベント',m,s)
        from local_chat.targeted_engine import stage_for
        rule=next(r for r in p['document_rules'] if r['domain']=='event_all')
        self.assertEqual(len(rule['reviewed_targets']),13)
        for target in rule['reviewed_targets']:
            if target['kind']=='labyrinth':continue
            self.assertTrue(all(getattr(stage_for(loaded,target,0,i),'clear_amount',getattr(stage_for(loaded,target,0,i),'clear_times',0))>=1 for i in (0,1)),target)
        self.assertEqual(loaded.ush4,2);self.assertEqual(loaded.ush5,80)
        self.assertEqual(loaded.tower.chapters.chapters[0].chapters[0].stages[0].clear_times,17)
        self.assertEqual(loaded.event_stages.chapters[1].chapters[0].chapters[0].stages[0].clear_amount,22)
        self.assertEqual(loaded.cats.serialize(),s.cats.serialize())

    def test_reviewed_all_events_suppresses_native_sweep_without_mutating_order(self):
        from local_chat.engine import Engine
        m,s=prepared();m.get_legend_metadata()['event_stage_families']={'tower':{'maps':[{'id':0,'name':'Tower','stages':['one'],'crowns':1}]}}
        p=plan('## ステージ・お宝・スコア\n38.全ステージクリア\n59.イベントステージクリア:読み込めた全イベント',m)
        compiled=compile_details(m,p);seen=[]
        m._apply_all_segments_unlocked=lambda save,selected,*args:seen.append(copy.deepcopy(selected)) or []
        with tempfile.TemporaryDirectory() as temp:
            e=Engine('.',temp);e.install_detail_adapter(m);e.detail_context.compiled=compiled
            m._apply_all_segments_unlocked(s,p['selected'],[],{})
        selected=set().union(*(set(v) for v in seen[0].values()))
        self.assertNotIn('all_stage_clear',selected);self.assertNotIn('event_clear',selected)
        self.assertTrue({'main_story_clear','zombie_clear','aku_clear','legend_clear','true_legend_clear','zero_legend_clear'}<=selected)
        self.assertTrue(any('all_stage_clear' in v for v in p['selected'].values()))

    def test_unrecognized_event_family_is_not_silently_ignored(self):
        m,s=prepared();m.get_legend_metadata()['event_stage_families']={'new_kind':{'maps':[{'id':0,'name':'unknown','stages':['unknown'],'crowns':1}]}}
        p=plan('## ステージ・お宝・スコア\n59.イベントステージクリア:読み込めた全イベント',m)
        self.assertTrue(p['unresolved']);self.assertNotIn('reviewed_targets',p['document_rules'][0])

    def test_review_is_required_and_metadata_change_invalidates_targets(self):
        m,s=prepared();m.get_legend_metadata()['event_stage_families']={'tower':{'maps':[{'id':0,'name':'Tower','stages':['one'],'crowns':1}]}}
        p=interpret_request('## ステージ・お宝・スコア\n59.イベントステージクリア:読み込めた全イベント',character_metadata=m.get_character_metadata())
        with self.assertRaisesRegex(ParseError,'未確認'):compile_details(m,p)
        review_targets(m,p);m.get_legend_metadata()['event_stage_families']['tower']['maps'][0]['stages'].append('new')
        with self.assertRaisesRegex(ParseError,'変更'):compile_details(m,p)


class HistoryTests(unittest.TestCase):
    def setup_history(self):
        m,s=prepared();meta=m.get_character_metadata()
        meta['groups']=[{'id':'regular','kind':'gacha','cat_ids':[1,2,4,5,6]}, {'id':'old','name':'古いコラボ','kind':'collab','cat_ids':[3,7]}, {'id':'recent','name':'新しいコラボ','kind':'collab','cat_ids':[8]}]
        meta['collaboration_history']={'as_of':datetime.date.today().isoformat(),'series':{'old':{'last_held_year':2020,'source':'synthetic test fixture'},'recent':{'last_held_year':2024,'source':'synthetic test fixture'}}}
        return m,s,meta

    def test_year_exclusion_is_applied_before_percentage_at_all_rarities(self):
        m,s,meta=self.setup_history()
        text='## キャラ解放\n【コラボキャラ】\nコラボ系:100%解放\n2020年以前を最後に開催されたコラボシリーズは、全レアリティで取得対象から除外\n## 全体の処理ルール\n6.2020年以前のコラボ除外条件は、他の解放割合より優先する。'
        p=plan(text,m);self.assertFalse(p['unresolved'],p['unresolved'])
        ownership=next(r for r in p['document_rules'] if r['domain']=='ownership');self.assertEqual(ownership['expected_ids'],[8])
        for c in s.cats.cats:c.unlocked=False
        s.cats.cats[6].unlocked=True
        before=copy.deepcopy(s.cats.cats[6].serialize())
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):apply_details(m,s,compile_details(m,p))
        self.assertFalse(s.cats.cats[2].unlocked);self.assertEqual(s.cats.cats[6].serialize(),before);self.assertTrue(s.cats.cats[7].unlocked)

    def test_missing_history_consolidates_year_and_priority_into_one_issue(self):
        m,s,meta=self.setup_history();meta.pop('collaboration_history')
        p=plan('## キャラ解放\nコラボ系:90%解放\n2020年以前を最後に開催されたコラボシリーズは、全レアリティで取得対象から除外\n## 全体の処理ルール\n6.2020年以前のコラボ除外条件は、他の解放割合より優先する。',m)
        self.assertEqual(len(p['unresolved']),1);self.assertIn('履歴が未登録',p['unresolved'][0]['reason'])

    def test_unknown_or_stale_history_never_executes(self):
        m,s,meta=self.setup_history()
        for change in ('stale','missing','no_source'):
            h=copy.deepcopy(meta['collaboration_history'])
            if change=='stale':h['as_of']='2020-01-01'
            elif change=='missing':h['series'].pop('old')
            else:h['series']['old']['source']=''
            doc={'collab_cutoffs':[{'year':2020}],'excluded_collab_names':[]}
            with patch('local_chat.targeted_requests.history_data',return_value=h):self.assertTrue(ownership_exclusions(doc,meta)[2])

    def test_history_change_requires_new_review(self):
        m,s,meta=self.setup_history();p=plan('## キャラ解放\nコラボ系:100%解放\n2020年以前を最後に開催されたコラボシリーズは、全レアリティで取得対象から除外',m)
        meta['collaboration_history']['series']['old']['last_held_year']=2021
        with self.assertRaisesRegex(ParseError,'除外対象が変わり'):compile_details(m,p)

    def test_all_excluded_collabs_have_zero_population_without_deleting_owned(self):
        m,s,meta=self.setup_history()
        p=plan('## キャラ解放\nコラボ系:90%解放\n除外コラボ:古いコラボ、新しいコラボ',m)
        self.assertFalse(p['unresolved'],p['unresolved'])
        self.assertEqual(p['document_rules'][0]['expected_ids'],[])
        before=copy.deepcopy(s.cats.serialize())
        apply_details(m,s,compile_details(m,p));self.assertEqual(s.cats.serialize(),before)

    def test_shared_member_uses_latest_verified_year_across_its_series(self):
        m,s,meta=self.setup_history();meta['groups'][-1]['cat_ids'].append(3)
        p=plan('## キャラ解放\nコラボ系:100%解放\n2020年以前を最後に開催されたコラボシリーズは、全レアリティで取得対象から除外',m)
        self.assertFalse(p['unresolved'],p['unresolved'])
        self.assertEqual(p['document_rules'][0]['expected_ids'],[3,8])

    def test_unknown_history_never_shows_a_provisional_percentage_population(self):
        m,s,meta=self.setup_history();meta.pop('collaboration_history')
        p=plan('## キャラ解放\nコラボ系:90%解放\n2020年以前を最後に開催されたコラボシリーズは、全レアリティで取得対象から除外',m)
        self.assertFalse(any(r['domain']=='ownership' for r in p['document_rules']))
        self.assertFalse(any(re.search(r'対象\d+体・所持目標\d+体',l['label']) for l in p['labels']))
        self.assertTrue(any('確定してから' in l['label'] for l in p['labels']))

    def test_explicit_series_exclusion_needs_no_date_guess(self):
        m,s,meta=self.setup_history();meta.pop('collaboration_history')
        p=plan('## キャラ解放\nコラボ系:100%解放\n除外コラボ:古いコラボ',m)
        self.assertFalse(p['unresolved'],p['unresolved']);self.assertEqual(p['document_rules'][0]['expected_ids'],[8])

    def test_dismissing_history_issue_cannot_execute_an_order_missing_ownership(self):
        from local_chat.conditional_engine import compile_conditional
        m,s,meta=self.setup_history();meta.pop('collaboration_history')
        p=plan('## キャラ解放\nコラボ系:90%解放\n2020年以前を最後に開催されたコラボシリーズは、全レアリティで取得対象から除外\n鬼にゃんまも削除',m)
        p['unresolved']=[];p['blockers']=[]
        for compiler in (compile_details,compile_conditional):
            with self.assertRaisesRegex(ParseError,'注文書を送り直して'):compiler(m,p)

if __name__=='__main__':unittest.main()
