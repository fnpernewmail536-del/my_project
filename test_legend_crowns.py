"""Native crown-limited selection, admission and save/reload checks."""
import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock

from test_advanced_requests import fixture, core


def native():
    tree=ast.parse(Path('main13.py').read_text())
    env={'copy':copy,'MAX_LEGEND_SELECTIONS':5000}
    names={'validate_legend_crowns_shape','resolve_legend_crowns','full_legend_selection',
           'safe_legend_stages','apply_legend_stages','validate_legend_stages_shape'}
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('LEGEND_SERIES','LEGEND_SELECTED_VALUES') for t in node.targets):
            exec(compile(ast.Module(body=[node],type_ignores=[]),'legend-constants','exec'),env)
        if isinstance(node,ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module(body=[node],type_ignores=[]),'native-legend','exec'),env)
    metadata={key:{'maps':[{'id':0,'name':'four crowns','crowns':4,'stages':['a','b']},
                         {'id':1,'name':'one crown','crowns':1,'stages':['c']}]} for key in env['LEGEND_SERIES']}
    env['get_legend_metadata']=lambda:metadata
    env['_ensure_zero_map']=lambda save,mid:True
    env['_sync_idi_clear']=Mock()
    return env,metadata


class LegendCrownTests(unittest.TestCase):
    def test_invalid_limit_is_rejected_before_api_key_or_quota_is_consumed(self):
        from test_render_chat import ChatTests
        harness=ChatTests();harness.setUp()
        env=harness.env
        env.update(MAX_CHAR_LIST=1000,MAX_CHARACTER_SETTINGS=1000,VIP_ITEM_GROUPS={},FACILITY_NAMES=[])
        for name in ('validate_legend_stages_shape','validate_detailed_stage_settings_shape',
                     'validate_labyrinth_character_settings_shape','validate_lineup_settings_shape','validate_score_settings_shape'):
            env[name]=lambda data:True
        node=next(n for n in ast.parse(Path('main13.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='validate_common_input')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'real-common-validation','exec'),env)
        env['validate_and_consume_api_key']=Mock(return_value=True)
        for limit in (0,5,True,'2',2.5):
            with harness.app.test_request_context('/api/run_daiko',method='POST',json={
                'selected':{'s1':['legend_clear']},'legend_crowns':{'legend':limit},'api_key':'test-api-key'}):
                response=harness.app.make_response(harness.m.api_run_daiko())
            self.assertEqual(response.status_code,400)
        env['validate_and_consume_api_key'].assert_not_called()
        env['_reserve_site_vip_job'].assert_not_called()
        harness.executor.submit.assert_not_called()

    def test_all_stage_dispatch_does_not_run_the_unlimited_parent_again(self):
        env,_=native()
        selected={'s1':['all_stage_clear']}
        selections=env['resolve_legend_crowns']({'selected':selected,'legend_crowns':{
            'legend':1,'true_legend':2,'zero_legend':3}},None)
        node=next(n for n in ast.parse(Path('main13.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_apply_all_segments_unlocked')
        env['apply_legend_stages']=Mock(return_value=[])
        env['apply_daiko_segment']=Mock(return_value=[])
        for name in ('apply_vip_items','apply_vip_facilities','apply_vip_talent_orbs','apply_special_stages',
                     'apply_main_story_stages','apply_event_stage_settings','apply_lineup_settings','apply_ototo_settings',
                     'apply_dojo_score_settings','apply_future_score_settings','_apply_user_rank_reward_actions'):
            env[name]=lambda *args:[]
        exec(compile(ast.Module(body=[node],type_ignores=[]),'real-stage-dispatch','exec'),env)
        env['_apply_all_segments_unlocked'](object(),selected,[],{},legend_stages=selections)
        env['apply_legend_stages'].assert_called_once()
        self.assertEqual(env['apply_legend_stages'].call_args.args[1],selections)
        items=env['apply_daiko_segment'].call_args.args[1]
        self.assertIn('main_story_clear',items)
        self.assertFalse(set(items)&{'legend_clear','true_legend_clear','zero_legend_clear'})

    def test_validate_exact_types_bounds_and_known_series(self):
        env,_=native();validate=env['validate_legend_crowns_shape']
        for raw in ({},{'legend':1,'true_legend':2,'zero_legend':4}):
            self.assertTrue(validate({'legend_crowns':raw}))
        for raw in (None,[],{'other':2},{'legend':True},{'legend':2.0},{'legend':'2'},{'legend':0},{'legend':5}):
            self.assertFalse(validate({'legend_crowns':raw}),raw)

    def test_each_limit_selects_lower_crowns_and_only_real_definitions(self):
        env,_=native()
        for key,parent in env['LEGEND_SELECTED_VALUES'].items():
            for limit in range(1,5):
                result=env['resolve_legend_crowns']({'selected':{'s1':[parent]},'legend_crowns':{key:limit}},None)
                self.assertEqual(set(result),{key})
                self.assertEqual(set(result[key][0]),set(range(limit)))
                self.assertEqual(result[key][1],{0:[0]})
                self.assertTrue(env['validate_legend_stages_shape']({'legend_stages':result}))

    def test_all_stage_clear_combines_independent_limits(self):
        env,_=native()
        limits={'legend':1,'true_legend':2,'zero_legend':3}
        result=env['resolve_legend_crowns']({'selected':{'s1':['all_stage_clear']},'legend_crowns':limits},None)
        for key,n in limits.items():self.assertEqual(set(result[key][0]),set(range(n)))

    def test_no_unselected_parent_or_missing_definitions_can_fall_back_to_all(self):
        env,meta=native()
        with self.assertRaises(ValueError):env['resolve_legend_crowns']({'selected':{},'legend_crowns':{'legend':2}},None)
        meta['legend']['maps']=[]
        with self.assertRaises(ValueError):env['resolve_legend_crowns']({'selected':{'s1':['legend_clear']},'legend_crowns':{'legend':2}},None)

    def test_legacy_default_and_explicit_individual_deselection_are_preserved(self):
        env,_=native();chosen={'legend':{0:{0:[1],2:[0]}}};before=copy.deepcopy(chosen)
        self.assertIs(env['resolve_legend_crowns']({},None),None)
        self.assertEqual(set(env['full_legend_selection']('legend')['legend'][0]),{0,1,2,3})
        result=env['resolve_legend_crowns']({'selected':{'s1':['legend_clear']},'legend_crowns':{'legend':2}},chosen)
        self.assertEqual(result,{'legend':{0:{0:[1]}}})
        self.assertEqual(chosen,before)
        result=env['resolve_legend_crowns']({'selected':{'s1':['legend_clear']},'legend_crowns':{'legend':2}},{'legend':{}})
        self.assertEqual(result,{'legend':{}})

    def test_three_native_save_formats_keep_upper_crowns_and_existing_counts(self):
        env,metadata=native();module,save=fixture()
        from local_chat.targeted_engine import ensure_slots
        targets=[]
        for spec in metadata['legend']['maps']:
            targets.append({**spec,'kind':'event','family':'legend','group':0})
        ensure_slots(module,save,targets)
        lib=core.game.map
        save.uncanny.chapters.chapters=[lib.chapters.ChaptersStars.init(2,4) for _ in range(2)]
        save.uncanny.unknown=[0,0]
        save.zero_legends.chapters=[lib.zero_legends.ChaptersStars.init() for _ in range(2)]
        for stars in save.zero_legends.chapters:
            stars.chapters=[lib.zero_legends.Chapter.init() for _ in range(4)]
            for chapter in stars.chapters:
                chapter.stages=[lib.zero_legends.Stage.init() for _ in range(2)]
        # These are native BCSFE containers, matching the original three paths.
        env['apply_legend_stages'](save,{'legend':{0:{3:[0]}},'true_legend':{0:{3:[0]}},'zero_legend':{0:{3:[0]}}})
        groups={'legend':save.event_stages.chapters[0], 'true_legend':save.uncanny.chapters,
                'zero_legend':save.zero_legends}
        for group in groups.values():group.chapters[0].chapters[0].stages[0].clear_stage(7)
        before={(key,star):copy.deepcopy(group.chapters[0].chapters[star].serialize())
                for key,group in groups.items() for star in range(4)}
        selections=env['resolve_legend_crowns']({'selected':{'s1':['all_stage_clear']},
              'legend_crowns':{'legend':1,'true_legend':2,'zero_legend':3}},None)
        env['apply_legend_stages'](save,selections)
        loaded=core.SaveFile(core.Data(save.to_data().data),cc=save.cc)
        after={'legend':loaded.event_stages.chapters[0], 'true_legend':loaded.uncanny.chapters,
               'zero_legend':loaded.zero_legends}
        for key,limit in (('legend',1),('true_legend',2),('zero_legend',3)):
            group=after[key]
            for star in range(limit):
                self.assertTrue(all(getattr(stage,'clear_amount',getattr(stage,'clear_times',0))>=1 for stage in group.chapters[0].chapters[star].stages[:2]))
            stage=group.chapters[0].chapters[0].stages[0]
            self.assertEqual(getattr(stage,'clear_amount',getattr(stage,'clear_times',0)),7)
            for star in range(limit,4):
                # Snapshot the unselected crowns, including an existing clear.
                old=before[key,star]
                self.assertEqual(group.chapters[0].chapters[star].serialize(),old)
        self.assertEqual(loaded.game_version.game_version,150501)
        env['_sync_idi_clear'].assert_not_called()

    def test_api_daiko_and_creation_pass_limited_selection_to_worker(self):
        from test_render_chat import ChatTests
        for create in (False,True):
            harness=ChatTests();harness.setUp()
            _,metadata=native();harness.m.get_legend_metadata=lambda:metadata
            data={'selected':{'s1':['legend_clear']},'legend_crowns':{'legend':2},
                  'api_key':'test-api-key','operation_id':'a'*30,
                  'transfer_code':'test-code','auth_code':'0000'}
            with harness.app.test_request_context('/api/run_create' if create else '/api/run_daiko',method='POST',json=data):
                response=harness.app.make_response(harness.m.api_run_create() if create else harness.m.api_run_daiko())
            self.assertEqual(response.status_code,200,response.json)
            args,_=harness.executor.submit.call_args
            # Locate the actual stage-selection argument without depending on
            # unrelated native worker parameters ahead of it.
            selections=next(arg for arg in args if isinstance(arg,dict) and set(arg)=={'legend'})
            self.assertEqual(selections,{'legend':{0:{0:[0,1],1:[0,1]},1:{0:[0]}}})


if __name__=='__main__':unittest.main()
