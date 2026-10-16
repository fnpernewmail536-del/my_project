"""Offline regressions for ordinary units appearing in collaboration banners."""
import ast
import base64
import copy
import gzip
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace as NS
import unittest

from local_chat.characters import collaboration_ids
from local_chat.details import apply_details, compile_details
from local_chat.engine import Engine
from local_chat.parser import ParseError, parse_request
from test_detailed_requests import fixture_module, fixture_save


REPAIR = '''【レベル変更】
レア全てレベル50+MAX
(コラボ系は+20〜30)
激レア全てレベル50+MAX
(コラボ系は+10〜20)'''


class CollaborationLevelTests(unittest.TestCase):
    def test_common_banner_fillers_are_not_collaboration_characters(self):
        metadata = fixture_module().get_character_metadata()
        self.assertEqual(collaboration_ids(metadata), {5, 7})
        self.assertEqual(metadata['groups'][0]['cat_ids'], [4, 5, 6, 7])

    def test_regular_plus_stays_90_and_only_collaboration_uses_ranges(self):
        module = fixture_module()
        save = fixture_save()
        compiled = compile_details(module, parse_request(REPAIR))
        apply_details(module, save, compiled)
        self.assertEqual((save.cats.cats[3].upgrade.base + 1, save.cats.cats[3].upgrade.plus), (50, 90))
        self.assertEqual((save.cats.cats[5].upgrade.base + 1, save.cats.cats[5].upgrade.plus), (50, 90))
        self.assertTrue(20 <= save.cats.cats[4].upgrade.plus <= 30)
        self.assertTrue(10 <= save.cats.cats[6].upgrade.plus <= 20)
        self.assertEqual([item['id'] for item in compiled['levels'] if item['collab']], [5, 7])

    def test_boolean_character_flag_overrides_mixed_pool_membership(self):
        metadata = fixture_module().get_character_metadata()
        metadata['characters'][3]['collab'] = True
        metadata['characters'][4]['collab'] = False
        self.assertEqual(collaboration_ids(metadata), {4, 7})

    def test_string_false_and_other_truthy_values_do_not_make_units_collab(self):
        for value in ['false', 'true', '0', 1, {'source': 'banner'}]:
            with self.subTest(value=value):
                metadata = fixture_module().get_character_metadata()
                metadata['characters'][3]['collab'] = value
                self.assertNotIn(4, collaboration_ids(metadata))

    def test_individual_flags_can_resolve_exceptions_without_banner_data(self):
        module = fixture_module()
        metadata = module.get_character_metadata()
        metadata['groups'] = []
        for info in metadata['characters']:
            info['collab'] = info['id'] in {5, 7}
        module.get_character_metadata = lambda: metadata
        compiled = compile_details(module, parse_request(REPAIR))
        self.assertEqual([item['id'] for item in compiled['levels'] if item['collab']], [5, 7])

    def test_incomplete_pool_comparison_stops_before_transfer(self):
        for kinds in [{'collab'}, {'gacha'}, set()]:
            with self.subTest(kinds=kinds), tempfile.TemporaryDirectory() as temp:
                module = fixture_module()
                metadata = module.get_character_metadata()
                metadata['groups'] = [group for group in metadata['groups'] if group['kind'] in kinds]
                module.get_character_metadata = lambda: metadata
                calls = []
                module.run_job_daiko = lambda *args, **kwargs: calls.append(args)
                engine = Engine(temp, temp)
                engine.module = module
                plan = parse_request(REPAIR + '\n引き継ぎコード:abc123456\n認証番号:0123')
                with self.assertRaisesRegex(ParseError, '所属定義が不足'):
                    engine.execute('job', 'operation', plan)
                self.assertFalse(calls)
                self.assertIsNone(engine.detail_context.compiled)

    def test_no_collab_exception_does_not_require_pool_metadata(self):
        module = fixture_module()
        metadata = module.get_character_metadata()
        metadata['groups'] = []
        module.get_character_metadata = lambda: metadata
        compiled = compile_details(module, parse_request('【レベル変更】\nレア50+MAX'))
        self.assertTrue(all(item['plus'] == 90 for item in compiled['levels']))

    def test_repair_does_not_change_other_rarities_or_resources(self):
        module = fixture_module()
        save = fixture_save()
        save.catfood, save.xp, save.np = 58999, 99999999, 5000
        before = copy.deepcopy(save)
        plan = parse_request(REPAIR)
        self.assertFalse(any(plan['selected'].values()))
        apply_details(module, save, compile_details(module, plan))
        for index in [0, 1, 2, 7, 8]:
            self.assertEqual(vars(save.cats.cats[index].upgrade), vars(before.cats.cats[index].upgrade))
        self.assertEqual((save.catfood, save.xp, save.np), (before.catfood, before.xp, before.np))
        self.assertEqual(save.labyrinth_medals, before.labyrinth_medals)
        self.assertEqual(save.talent_orbs.orbs, before.talent_orbs.orbs)
        self.assertFalse(save.gamatoto.helpers.helpers)

    def test_pool_and_character_order_do_not_change_classification(self):
        metadata = fixture_module().get_character_metadata()
        before = collaboration_ids(metadata)
        metadata['groups'].reverse()
        metadata['characters'].reverse()
        for group in metadata['groups']:
            group['cat_ids'] = list(reversed(group['cat_ids'])) * 2
        self.assertEqual(collaboration_ids(metadata), before)


def original_metadata():
    """Execute only the bundled-data readers, without importing the live engine."""
    source = Path(__file__).resolve().parents[1] / 'source/my_project/main13.py'
    if not source.exists():
        raise unittest.SkipTest('元エンジンは配布ZIPに同梱しません')
    tree = ast.parse(source.read_text())
    constants = {'GACHA_SERIES_NAMES', 'ERROR_CAT_IDS', 'SUMMONED_SPIRIT_CAT_IDS',
                 'BUNDLED_CHARACTER_GROUPS_B64', 'BUNDLED_CHARACTER_DATA_B64',
                 'BUNDLED_CHARACTER_RARITIES_B64', 'BUNDLED_CHARACTER_FORM_COUNTS_B64',
                 'BUNDLED_CHARACTER_TRUE_FORM_STATES_B64', 'BUNDLED_TALENT_DATA_B64'}
    functions = {'_character_groups_from_series', '_bundled_character_groups', '_bundled_character_metadata'}
    env = {'base64': base64, 'gzip': gzip, 'json': json, 're': re}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in constants:
                env[node.targets[0].id] = ast.literal_eval(node.value)
    code = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in functions], type_ignores=[])
    exec(compile(code, str(source), 'exec'), env)
    return env['_bundled_character_metadata']()


class OriginalCollaborationDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = original_metadata()

    def test_actual_neko_esthetician_and_bancho_are_common_banner_fillers(self):
        metadata = self.metadata
        old_ids = {cid for group in metadata['groups'] if group['kind'] == 'collab' for cid in group['cat_ids']}
        fixed_ids = collaboration_ids(metadata)
        by_id = {info['id']: info for info in metadata['characters']}
        for cid, name in [(42, 'ネコエステ'), (32, 'ネコ番長')]:
            with self.subTest(name=name):
                self.assertEqual(by_id[cid]['names'][0], name)
                self.assertIn(cid, old_ids)
                self.assertNotIn(cid, fixed_ids)
        self.assertGreaterEqual(len(old_ids - fixed_ids), 40)

    def test_original_metadata_saves_default_90_for_common_units_and_ranges_for_collab(self):
        metadata = self.metadata
        collab = collaboration_ids(metadata)
        module = NS(get_character_metadata=lambda: metadata, _apply_all_segments_unlocked=lambda *args: [])
        cats = {info['id'] - 1: NS(unlocked=True, upgrade=NS(base=0, plus=7)) for info in metadata['characters']}
        save = NS(cats=NS(get_cat_by_id=lambda cid: cats.get(cid)))
        compiled = compile_details(module, parse_request(REPAIR))
        apply_details(module, save, compiled)
        for info in metadata['characters']:
            if not info.get('selectable', True) or info['rarity'] not in (2, 3):
                continue
            cat = cats[info['id'] - 1]
            self.assertEqual(cat.upgrade.base + 1, 50)
            if info['id'] in collab:
                lo, hi = (20, 30) if info['rarity'] == 2 else (10, 20)
                self.assertTrue(lo <= cat.upgrade.plus <= hi, info['names'][0])
            else:
                self.assertEqual(cat.upgrade.plus, 90, info['names'][0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
