import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock

from stage_progress import compile_targets, apply_targets, chapter_for, valid_shape, labyrinth_state
from test_targeted_requests import prepared, MAPPING
from test_advanced_requests import core


def setup():
    m, save = prepared()
    meta = m.get_legend_metadata()
    for family in ('main_story', 'zombie'):
        meta[family] = {'chapters': [{'id': i, 'real_id': real, 'name': f'{family}-{i}',
                                    'stages': [str(n) for n in range(48)]} for i, real in enumerate(MAPPING)]}
    meta['aku'] = {'chapters': [{'id': 0, 'name': 'Aku', 'stages': ['a', 'b', 'c']}]}
    meta['aku_ex'] = {'chapters': [{'id': 0, 'name': 'Fuji', 'stages': ['boss']}]}
    meta['event_stage_families'] = {key: {'label': key, 'maps': [{'id': 0, 'name': key, 'crowns': 2,
        'stages': ['first', 'second']}]} for key in m.EVENT_STAGE_FAMILIES}
    meta['event_stage_families']['labyrinth']['maps'][0]['crowns'] = 1
    return m, save, meta


def request(family, progress, map_id=0, star=0):
    return {'family': family, 'map': map_id, 'star': star, 'progress': progress}


class StageProgressTests(unittest.TestCase):
    def test_invalid_shape_stops_before_api_key_or_quota(self):
        from test_render_chat import ChatTests
        h = ChatTests(); h.setUp(); env = h.env
        env.update(MAX_CHAR_LIST=1000, MAX_CHARACTER_SETTINGS=1000, VIP_ITEM_GROUPS={}, FACILITY_NAMES=[])
        tree = ast.parse(Path('main13.py').read_text())
        for name in ('validate_legend_stages_shape', 'validate_labyrinth_character_settings_shape',
                     'validate_lineup_settings_shape', 'validate_score_settings_shape'):
            env[name] = lambda d: True
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in ('validate_detailed_stage_settings_shape', 'validate_common_input'):
                exec(compile(ast.Module(body=[node], type_ignores=[]), 'progress-common-validation', 'exec'), env)
        env['validate_and_consume_api_key'] = Mock(return_value=True)
        for raw in (None, {}, [request('main_story', True)], [request('main_story', -1)]):
            for create in (False, True):
                with h.app.test_request_context('/', method='POST', json={'api_key':'test-api-key', 'stage_progress':raw}):
                    result = h.app.make_response(h.m.api_run_create() if create else h.m.api_run_daiko())
                self.assertEqual(result.status_code, 400)
        env['validate_and_consume_api_key'].assert_not_called()
        env['_reserve_site_vip_job'].assert_not_called()
        h.executor.submit.assert_not_called()

    def test_single_chapter_keeps_other_chapters_and_save_mismatch_stops(self):
        m, save, _ = setup()
        for chapter in save.story.chapters:
            chapter.clear_chapter()
        other = copy.deepcopy(save.story.chapters[7].serialize())
        apply_targets(m, save, compile_targets(m, [request('main_story', 0)]))
        self.assertEqual(save.story.chapters[7].serialize(), other)
        original = core.Data(save.to_data().data)
        save.to_data = Mock(return_value=original)
        with self.assertRaisesRegex(RuntimeError, '送信を停止'):
            apply_targets(m, save, compile_targets(m, [request('main_story', 10)]))

    def test_exact_progress_dispatch_runs_after_native_bulk_changes(self):
        from unittest.mock import patch
        from test_legend_crowns import native
        env, _ = native(); env.update(core=core)
        node = next(n for n in ast.parse(Path('main13.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_apply_all_segments_unlocked')
        calls = []
        for name in ('apply_vip_items','apply_legend_stages','apply_vip_facilities','apply_vip_talent_orbs',
                     'apply_special_stages','apply_main_story_stages','apply_event_stage_settings',
                     'apply_lineup_settings','apply_ototo_settings','apply_dojo_score_settings',
                     'apply_future_score_settings','_apply_user_rank_reward_actions','apply_daiko_segment'):
            env[name] = lambda *args, name=name: calls.append(name) or []
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'exact-all-stage-dispatch', 'exec'), env)
        plans = [request('main_story', 0)]
        with patch('stage_progress.apply_targets', side_effect=lambda *a: calls.append('exact') or []) as apply:
            env['_apply_all_segments_unlocked'](object(), {'s1':['all_stage_clear']}, [], {'_stage_progress':plans})
        self.assertEqual(calls[-1], 'exact')
        self.assertEqual(apply.call_args.args[2], plans)

    def test_exact_shape_does_not_accept_coercion_or_duplicates(self):
        self.assertTrue(valid_shape([request('main_story', 0)]))
        for bad in (None, {}, [None], [request('main_story', True)], [request('main_story', '0')],
                    [request('main_story', -1)], [request('main_story', 257)],
                    [request('main_story', 0, star=4)], [request('main_story', 0)] * 2):
            self.assertFalse(valid_shape(bad))

    def test_all_nine_story_chapters_reduce_and_preserve_treasures_scores_and_gap(self):
        m, save, _ = setup()
        for chapter in save.story.chapters:
            chapter.progress = 48
            chapter.selected_stage = 47
            for stage in chapter.stages:
                stage.clear_times = 7; stage.treasure = 3; stage.itf_timed_score = 8888
        # Canonicalise the fixture: timed scores only exist in Future chapters,
        # and some padding treasures have no persisted field.
        save = core.SaveFile(core.Data(save.to_data().data), cc=save.cc)
        gap = copy.deepcopy(save.story.chapters[3].serialize())
        cats = copy.deepcopy(save.cats.serialize())
        for n in (23, 0, 48):
            plans = compile_targets(m, [request('main_story', n, i) for i in range(9)])
            apply_targets(m, save, plans)
            save = core.SaveFile(core.Data(save.to_data().data), cc=save.cc)
            for real in MAPPING:
                chapter = save.story.chapters[real]
                self.assertEqual(chapter.progress, n)
                for i, stage in enumerate(chapter.stages[:48]):
                    self.assertEqual(stage.clear_times > 0, i < n)
                    self.assertEqual((stage.treasure, stage.itf_timed_score), (3, 8888 if real in (4, 5, 6) else 0))
                self.assertEqual(chapter.stages[48].clear_times, 7)
            self.assertEqual(save.story.chapters[3].serialize(), gap)
            self.assertEqual(save.cats.serialize(), cats)
            self.assertEqual(save.game_version.game_version, 150501)

    def test_zombie_and_aku_known_stages_reduce_without_changing_other_chapters(self):
        m, save, _ = setup()
        for family in ('zombie', 'aku'):
            apply_targets(m, save, compile_targets(m, [request(family, 48 if family == 'zombie' else 3)]))
            apply_targets(m, save, compile_targets(m, [request(family, 1)]))
            loaded = core.SaveFile(core.Data(save.to_data().data), cc=save.cc)
            if family == 'zombie':
                self.assertEqual([loaded.outbreaks.chapters[0].outbreaks[i].cleared for i in range(48)], [True]+[False]*47)
            else:
                self.assertEqual([s.clear_times for s in loaded.aku.chapters[0].chapters[0].stages[:3]], [1, 0, 0])
                self.assertEqual(loaded.aku.chapters[0].chapters[0].current_stage, 0)
        apply_targets(m, save, compile_targets(m, [request('aku_ex', 1)]))
        apply_targets(m, save, compile_targets(m, [request('aku_ex', 0)]))
        self.assertEqual(save.event_stages.chapters[1].chapters[42].chapters[0].stages[0].clear_amount, 0)

    def test_all_thirteen_event_families_can_clear_and_reduce_roundtrip(self):
        m, save, meta = setup()
        save.legend_quest.ids = [101, 102]
        raw = [request('event:'+key, len(spec['stages']), spec['id'], star)
               for key, family in meta['event_stage_families'].items()
               for spec in family['maps'] for star in range(spec['crowns'])]
        apply_targets(m, save, compile_targets(m, raw))
        raw = [{**row, 'progress': 1 if row['star'] == 0 else 0} for row in raw]
        plans = compile_targets(m, raw)
        apply_targets(m, save, plans)
        loaded = core.SaveFile(core.Data(save.to_data().data), cc=save.cc)
        for row in plans:
            if row['spec']['kind'] == 'labyrinth':
                self.assertEqual(labyrinth_state(m, loaded)[:3], (1, 1, 1))
            else:
                chapter = chapter_for(loaded, row['spec'], row['star'])
                counts = [getattr(s, 'clear_amount', getattr(s, 'clear_times', -1)) for s in chapter.stages[:2]]
                self.assertEqual(counts, [1, 0] if row['star'] == 0 else [0, 0], row['family'])
                self.assertEqual(chapter.clear_progress, row['progress'])
                if row['star'] == 1:
                    self.assertEqual(getattr(chapter, 'unlock_state', getattr(chapter, 'chapter_unlock_state', -1)), 0)
        self.assertEqual(loaded.event_stages.chapter_completion_count[14000], 0)
        apply_targets(m, loaded, compile_targets(m, [request('event:labyrinth', 0)]))
        self.assertEqual(labyrinth_state(m, loaded)[:3], (0, 0, 0))

    def test_missing_changed_or_out_of_range_definitions_stop_before_edits(self):
        m, save, meta = setup()
        for raw in ([request('bad', 0)], [request('main_story', 49)], [request('event:tower', 0, 999)],
                    [request('event:tower', 0, star=3)]):
            with self.assertRaises(ValueError):
                compile_targets(m, raw)
        plans = compile_targets(m, [request('main_story', 0)])
        before = copy.deepcopy(save.story.serialize())
        meta['main_story']['chapters'][0]['stages'][0] = 'changed'
        with self.assertRaises(ValueError):
            apply_targets(m, save, plans)
        self.assertEqual(save.story.serialize(), before)

    def test_native_api_both_modes_pass_frozen_progress_without_parent_selection(self):
        from test_render_chat import ChatTests
        m, _, meta = setup()
        for create in (False, True):
            h = ChatTests(); h.setUp(); h.m.get_legend_metadata = lambda: meta
            data = {'api_key': 'test-api-key', 'operation_id': 'd'*30,
                    'transfer_code': 'test-code', 'auth_code': '0000', 'selected': {},
                    'stage_progress': [request('main_story', 0, 6)]}
            with h.app.test_request_context('/', method='POST', json=data):
                result = h.app.make_response(h.m.api_run_create() if create else h.m.api_run_daiko())
            self.assertEqual(result.status_code, 200, result.json)
            args, _ = h.executor.submit.call_args
            amounts = next(a for a in args if isinstance(a, dict) and '_stage_progress' in a)
            self.assertEqual(amounts['_stage_progress'][0]['spec']['real_id'], 7)
            self.assertEqual(amounts['_stage_progress'][0]['progress'], 0)


if __name__ == '__main__':
    unittest.main()
