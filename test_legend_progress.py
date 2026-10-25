"""Exact crown reduction through native containers, APIs and persisted saves."""
import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock

from test_advanced_requests import fixture, core
from test_legend_crowns import native


def engine():
    env, metadata = native()
    env['core'] = core
    names = {'compile_legend_progress', '_legend_progress_container',
             '_ensure_legend_progress_slots', 'apply_legend_progress', '_ensure_zero_map'}
    for node in ast.parse(Path('main13.py').read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'native-exact-progress', 'exec'), env)
    return env, metadata


def cleared_save(env, metadata):
    module, save = fixture()
    from local_chat.targeted_engine import ensure_slots
    ensure_slots(module, save, [dict(metadata['legend']['maps'][0], kind='event', group=0, id=2),
                               dict(metadata['legend']['maps'][0], kind='event', group=1, id=0)])
    lib = core.game.map
    save.uncanny.chapters.chapters = [lib.chapters.ChaptersStars.init(2, 4) for _ in range(3)]
    save.uncanny.unknown = [11, 12, 13]
    save.zero_legends.chapters = [lib.zero_legends.ChaptersStars.init() for _ in range(3)]
    for item in save.zero_legends.chapters:
        item.unknown = 17
        item.chapters = [lib.zero_legends.Chapter.init() for _ in range(4)]
        for chapter in item.chapters:
            chapter.stages = [lib.zero_legends.Stage.init() for _ in range(2)]
    for series in metadata:
        container = env['_legend_progress_container'](save, series)
        for item in container.chapters:
            for chapter in item.chapters:
                chapter.selected_stage = 1
                chapter.clear_progress = 2
                chapter.chapter_unlock_state = 3
                if series == 'zero_legend':
                    chapter.unlock_state = 3
                for stage in chapter.stages:
                    stage.clear_stage(7)
    return save


class LegendProgressTests(unittest.TestCase):
    def test_invalid_input_stops_before_api_key_consumption_or_quota(self):
        from test_render_chat import ChatTests
        harness = ChatTests(); harness.setUp()
        env = harness.env
        env.update(MAX_CHAR_LIST=1000, MAX_CHARACTER_SETTINGS=1000, VIP_ITEM_GROUPS={}, FACILITY_NAMES=[])
        for name in ('validate_legend_stages_shape', 'validate_detailed_stage_settings_shape',
                     'validate_labyrinth_character_settings_shape', 'validate_lineup_settings_shape',
                     'validate_score_settings_shape'):
            env[name] = lambda data: True
        node = next(n for n in ast.parse(Path('main13.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'validate_common_input')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'exact-common-validation', 'exec'), env)
        env['validate_and_consume_api_key'] = Mock(return_value=True)
        for create in (False, True):
            for limit in (-1, 5, True, '0', 1.5):
                with harness.app.test_request_context('/', method='POST', json={
                    'selected': {'s1': ['legend_clear']}, 'legend_progress': {'legend': limit},
                    'api_key': 'test-api-key'}):
                    response = harness.app.make_response(harness.m.api_run_create() if create else harness.m.api_run_daiko())
                self.assertEqual(response.status_code, 400)
        env['validate_and_consume_api_key'].assert_not_called()
        env['_reserve_site_vip_job'].assert_not_called()
        harness.executor.submit.assert_not_called()

    def test_missing_definitions_and_conflicting_targets_never_start_a_job(self):
        from test_render_chat import ChatTests
        for create in (False, True):
            for kind in ('metadata', 'parent', 'individual'):
                harness = ChatTests(); harness.setUp()
                _, metadata = engine()
                harness.m.get_legend_metadata = lambda: {} if kind == 'metadata' else metadata
                data = {'selected': {'s1': ['legend_clear']}, 'legend_progress': {'legend': 0},
                        'api_key': 'test-api-key', 'operation_id': 'c' * 30,
                        'transfer_code': 'test-code', 'auth_code': '0000'}
                if kind == 'parent':
                    data['selected'] = {}
                if kind == 'individual':
                    data['legend_stages'] = {'legend': {'0': {'0': [0]}}}
                with harness.app.test_request_context('/', method='POST', json=data):
                    response = harness.app.make_response(harness.m.api_run_create() if create else harness.m.api_run_daiko())
                self.assertEqual(response.status_code, 400, response.json)
                harness.env['_reserve_site_vip_job'].assert_not_called()
                harness.executor.submit.assert_not_called()

    def test_invalid_exact_types_and_conflicting_modes_rejected(self):
        env, _ = engine()
        for raw in ({'legend': 0}, {'legend': 4, 'true_legend': 1, 'zero_legend': 2}):
            self.assertTrue(env['validate_legend_crowns_shape']({'legend_progress': raw}))
        for raw in (None, [], {'other': 0}, {'legend': True}, {'legend': '1'},
                    {'legend': 1.0}, {'legend': -1}, {'legend': 5}):
            self.assertFalse(env['validate_legend_crowns_shape']({'legend_progress': raw}), raw)
        self.assertFalse(env['validate_legend_crowns_shape']({
            'legend_crowns': {'legend': 1}, 'legend_progress': {'legend': 0}}))
        self.assertTrue(env['validate_legend_crowns_shape']({
            'legend_crowns': {'true_legend': 1}, 'legend_progress': {'legend': 0}}))

    def test_explicit_parent_definitions_and_unmixed_selection_required(self):
        env, meta = engine()
        base = {'legend_progress': {'legend': 0}, 'selected': {'s1': ['legend_clear']}}
        with self.assertRaises(ValueError):
            env['compile_legend_progress']({**base, 'selected': {}})
        with self.assertRaises(ValueError):
            env['compile_legend_progress']({**base, 'legend_stages': {'legend': {0: {0: [0]}}}})
        plan = env['compile_legend_progress'](base)
        self.assertEqual(plan['legend']['crowns'], 0)
        self.assertEqual(env['resolve_legend_crowns'](base, None), {'legend': {}})
        meta['legend']['maps'][0]['name'] = 'changed'
        self.assertNotEqual(plan['legend']['maps'], meta['legend']['maps'])
        save = cleared_save(env, meta)
        before = copy.deepcopy(save.event_stages.serialize())
        with self.assertRaises(ValueError):
            env['apply_legend_progress'](save, plan)
        self.assertEqual(save.event_stages.serialize(), before)
        meta['legend']['maps'] = []
        with self.assertRaises(ValueError):
            env['compile_legend_progress'](base)

    def test_four_to_one_to_zero_and_back_to_four_roundtrip_all_three_series(self):
        env, metadata = engine()
        save = cleared_save(env, metadata)
        other = {name: copy.deepcopy(getattr(save, name).serialize())
                 for name in ('cats', 'lineups', 'medals', 'item_reward_stages')}
        unknown = {}
        for series in metadata:
            container = env['_legend_progress_container'](save, series)
            unknown[series] = (copy.deepcopy(container.chapters[2].serialize()),
                               copy.deepcopy(container.chapters[1].chapters[1].serialize()),
                               container.chapters[1].chapters[0].stages[1].serialize())
        event_other = copy.deepcopy(save.event_stages.chapters[1].serialize())
        for limit in (1, 0, 2, 4):
            with self.subTest(limit=limit):
                data = {'selected': {'s1': ['all_stage_clear']},
                        'legend_progress': {key: limit for key in metadata}}
                env['apply_legend_progress'](save, env['compile_legend_progress'](data))
                save = core.SaveFile(core.Data(save.to_data().data), cc=save.cc)
                self.assertEqual(save.game_version.game_version, 150501)
                for series in metadata:
                    container = env['_legend_progress_container'](save, series)
                    for spec in metadata[series]['maps']:
                        for star in range(spec['crowns']):
                            chapter = container.chapters[spec['id']].chapters[star]
                            cleared = star < limit
                            values = [s.serialize() for s in chapter.stages[:len(spec['stages'])]]
                            self.assertTrue(all(v >= 1 for v in values) if cleared else all(v == 0 for v in values))
                            self.assertEqual(chapter.clear_progress, len(spec['stages']) if cleared else 0)
                            expected = 3 if cleared else int(star == limit and (limit > 0 or spec['id'] == 0))
                            actual = chapter.unlock_state if series == 'zero_legend' else chapter.chapter_unlock_state
                            self.assertEqual(actual, expected)
                            if not cleared:
                                self.assertEqual(chapter.selected_stage, 0)
                    # Unknown maps, real-but-unpublished crowns and padding
                    # stages are left intact rather than guessed or deleted.
                    self.assertEqual(container.chapters[2].serialize(), unknown[series][0])
                    self.assertEqual(container.chapters[1].chapters[1].serialize(), unknown[series][1])
                    self.assertEqual(container.chapters[1].chapters[0].stages[1].serialize(), unknown[series][2])
                self.assertEqual(save.event_stages.chapters[1].serialize(), event_other)
                for name, value in other.items():
                    self.assertEqual(getattr(save, name).serialize(), value, name)
        env['_sync_idi_clear'].assert_not_called()

    def test_kept_crown_counts_and_unselected_series_are_preserved(self):
        env, metadata = engine()
        save = cleared_save(env, metadata)
        before = copy.deepcopy(save.uncanny.serialize()), copy.deepcopy(save.zero_legends.serialize())
        env['apply_legend_progress'](save, env['compile_legend_progress']({
            'selected': {'s1': ['legend_clear']}, 'legend_progress': {'legend': 1}}))
        self.assertEqual(save.event_stages.chapters[0].chapters[0].chapters[0].stages[0].clear_amount, 7)
        self.assertEqual(save.uncanny.serialize(), before[0])
        self.assertEqual(save.zero_legends.serialize(), before[1])

    def test_missing_native_slots_are_extended_without_downgrading_save_version(self):
        env, _ = engine()
        _, save = fixture()
        save.uncanny.chapters.chapters = []
        save.uncanny.unknown = []
        save.zero_legends.chapters = []
        env['apply_legend_progress'](save, env['compile_legend_progress']({
            'selected': {'s1': ['all_stage_clear']},
            'legend_progress': {'legend': 1, 'true_legend': 1, 'zero_legend': 1}}))
        self.assertEqual(save.game_version.game_version, 150501)
        self.assertGreaterEqual(len(save.zero_legends.chapters), 2)
        self.assertEqual(save.zero_legends.chapters[0].chapters[0].unlock_state, 3)

    def test_save_roundtrip_mismatch_stops_processing(self):
        env, metadata = engine()
        save = cleared_save(env, metadata)
        pristine = core.Data(save.to_data().data)
        save.to_data = Mock(return_value=pristine)
        with self.assertRaisesRegex(RuntimeError, '送信を停止'):
            env['apply_legend_progress'](save, env['compile_legend_progress']({
                'selected': {'s1': ['legend_clear']}, 'legend_progress': {'legend': 0}}))

    def test_api_both_modes_freeze_exact_progress_into_worker_amounts(self):
        from test_render_chat import ChatTests
        for create in (False, True):
            harness = ChatTests(); harness.setUp()
            _, metadata = engine()
            harness.m.get_legend_metadata = lambda: metadata
            data = {'selected': {'s1': ['all_stage_clear']}, 'legend_progress': {'legend': 0, 'true_legend': 1},
                    'legend_crowns': {'zero_legend': 2}, 'api_key': 'test-api-key',
                    'operation_id': 'b' * 30, 'transfer_code': 'test-code', 'auth_code': '0000'}
            with harness.app.test_request_context('/', method='POST', json=data):
                response = harness.app.make_response(harness.m.api_run_create() if create else harness.m.api_run_daiko())
            self.assertEqual(response.status_code, 200, response.json)
            args, _ = harness.executor.submit.call_args
            amounts = next(arg for arg in args if isinstance(arg, dict) and '_legend_progress' in arg)
            self.assertEqual(amounts['_legend_progress']['legend']['crowns'], 0)
            self.assertEqual(amounts['_legend_progress']['true_legend']['crowns'], 1)
            stages = next(arg for arg in args if isinstance(arg, dict) and set(arg) == {'legend', 'true_legend', 'zero_legend'})
            self.assertEqual(stages['legend'], {})
            self.assertEqual(stages['true_legend'], {})
            self.assertEqual(set(stages['zero_legend'][0]), {0, 1})

    def test_dispatch_applies_exact_progress_after_all_additive_stage_actions(self):
        env, metadata = engine()
        plans = env['compile_legend_progress']({'selected': {'s1': ['all_stage_clear']}, 'legend_progress': {'legend': 0}})
        calls = []
        for name in ('apply_vip_items', 'apply_legend_stages', 'apply_vip_facilities', 'apply_vip_talent_orbs',
                     'apply_special_stages', 'apply_main_story_stages', 'apply_event_stage_settings',
                     'apply_lineup_settings', 'apply_ototo_settings', 'apply_dojo_score_settings',
                     'apply_future_score_settings', '_apply_user_rank_reward_actions'):
            env[name] = lambda *args, name=name: calls.append(name) or []
        env['apply_daiko_segment'] = Mock(side_effect=lambda *a: calls.append('bulk') or [])
        env['apply_legend_progress'] = Mock(side_effect=lambda *a: calls.append('exact') or ['reset'])
        node = next(n for n in ast.parse(Path('main13.py').read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_apply_all_segments_unlocked')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'exact-dispatch', 'exec'), env)
        logs = env['_apply_all_segments_unlocked'](object(), {'s1': ['all_stage_clear']}, [],
                                                 {'_legend_progress': plans}, legend_stages={'legend': {}})
        self.assertEqual(calls[-1], 'exact')
        self.assertNotIn('legend_clear', env['apply_daiko_segment'].call_args.args[1])
        self.assertIn('true_legend_clear', env['apply_daiko_segment'].call_args.args[1])
        self.assertEqual(logs, ['reset'])


if __name__ == '__main__':
    unittest.main()
