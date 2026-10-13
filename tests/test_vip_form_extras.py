"""VIP form ranges, preservation, permissions and native save roundtrips."""
import contextlib
import copy
import io
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from bcsfe import core

ROOT = Path(__file__).resolve().parents[1]


class VipFormExtraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = contextlib.ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))
        cls.stack.enter_context(patch.dict(os.environ, {
            "TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": "", "RENDER": "", "REQUIRE_PERSISTENT_DB": "0",
            "FLASK_SECRET_KEY": "test-stable-secret", "IDENTITY_HASH_KEY": "test-stable-secret",
            "ACCOUNT_IDENTITY_HASH_KEY": "test-stable-secret", "ADMIN_SNAPSHOT_KEY": "test-stable-secret",
            "PROXY_URL": "", "AUTO_GAME_UPDATE_ENABLED": "0", "DISABLE_BACKGROUND_UPDATER": "1",
            "GAME_UPDATE_CACHE_PATH": str(cls.root / "game_updates.json"),
        }))
        cls.stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        cls.stack.enter_context(patch.object(threading.Thread, "start"))
        cls.stack.enter_context(patch("requests.get", side_effect=AssertionError("Network disabled")))
        cls.stack.enter_context(patch("requests.post", side_effect=AssertionError("Network disabled")))
        script = cls.root / "main13.py"
        shutil.copy2(ROOT / "main13.py", script)
        with contextlib.redirect_stdout(io.StringIO()):
            loaded = runpy.run_path(str(script))
        cls.app = loaded["compile_vip_form_extras"].__globals__

    def setUp(self):
        # Synthetic metadata isolates ordinary banner fillers from collab-only units.
        self.metadata = {"characters": [
            {"id": index + 1, "rarity": rarity, "selectable": True, "names": [f"fixture-{index + 1}"], "form_count": 1}
            for index, rarity in enumerate([0, 2, 2, 4, 4, 5])
        ], "groups": [{"kind": "collab", "cat_ids": [2, 3, 4]}, {"kind": "gacha", "cat_ids": [2, 5]}]}
        self.legend = {"vip_item_groups": {"battle_items": [f"item-{i}" for i in range(6)]},
                       "talent_orbs": [{"ranks": [{"id": 1, "rank": "A"}, {"id": 2, "rank": "S"}]}]}
        self.context = patch.dict(self.app, {"get_character_metadata": lambda: self.metadata,
                                           "get_legend_metadata": lambda: self.legend})
        self.context.start()
        self.addCleanup(self.context.stop)

    def compile(self, extras):
        return self.app["compile_vip_form_extras"]({"vip_form_extras": extras})

    def save(self):
        save = core.SaveFile(cc=core.CountryCode.from_code("jp"), gv=core.GameVersion(150501))
        save.cats.cats = [core.Cat.init(index) for index in range(6)]
        for index, cat in enumerate(save.cats.cats):
            cat.unlocked = True
            cat.upgrade.base = index + 9
            cat.upgrade.plus = index + 2
        return save

    def reload(self, save):
        return core.SaveFile(core.Data(save.to_data().data), cc=core.CountryCode.from_code("jp"))

    def test_plus_only_collaboration_exceptions_and_exclusions_survive_bulk_and_reload(self):
        extras = self.compile({"levels": {"2": {"plus": [20, 20], "collab_plus": [1, 1]}}, "excluded_ids": [5]})
        save = self.save()
        before = [(cat.upgrade.base, cat.upgrade.plus) for cat in save.cats.cats]
        logs = self.app["_apply_all_segments_unlocked"](save, {"s3": ["max_all_cats_plus"]}, [], {"_vip_form_extras": extras})
        loaded = self.reload(save)
        levels = [(cat.upgrade.base, cat.upgrade.plus) for cat in loaded.cats.cats]
        self.assertEqual(levels[1], (before[1][0], 20))  # ordinary filler in collab banner
        self.assertEqual(levels[2], (before[2][0], 1))   # collaboration-exclusive
        self.assertEqual(levels[4], before[4])          # excluded even from bulk action
        self.assertEqual(levels[0], (59, 90))
        self.assertEqual(loaded.game_version.game_version, 150501)
        self.assertTrue(any("レベル除外" in entry for entry in logs))

    def test_base_only_keeps_plus_and_collab_only_preserves_other_units(self):
        save = self.save()
        before = [(cat.upgrade.base, cat.upgrade.plus) for cat in save.cats.cats]
        extras = self.compile({"levels": {"0": {"base": [25, 25]}, "4": {"collab_plus": [10, 15]}}})
        self.app["_apply_all_segments_unlocked"](save, {"s3": ["max_all_cats_plus"]}, [], {"_vip_form_extras": extras})
        loaded = self.reload(save)
        self.assertEqual((loaded.cats.cats[0].upgrade.base, loaded.cats.cats[0].upgrade.plus), (24, before[0][1]))
        self.assertEqual(loaded.cats.cats[3].upgrade.base, before[3][0])
        self.assertIn(loaded.cats.cats[3].upgrade.plus, range(10, 16))
        self.assertEqual((loaded.cats.cats[4].upgrade.base, loaded.cats.cats[4].upgrade.plus), (59, 90))

    def test_scalar_and_independent_array_draws_use_native_writes_and_reload(self):
        extras = self.compile({"quantities": {"custom_catfood": [42000, 58000], "custom_battle_items": [80, 85]}})
        save = self.save()
        with patch.object(self.app["secrets"], "randbelow", side_effect=[1234, 0, 1, 2, 3, 4, 5]):
            self.app["_apply_all_segments_unlocked"](save, {}, [], {"_vip_form_extras": extras})
        loaded = self.reload(save)
        self.assertEqual(loaded.catfood, 43234)
        self.assertEqual([item.amount for item in loaded.battle_items.items], [80, 81, 82, 83, 84, 85])

    def test_granular_selection_empty_selection_and_s_only_keep_other_types(self):
        extras = self.compile({"quantities": {"custom_battle_items": [50, 50], "custom_talent_orbs": [10, 10]}, "orb_rank": "S"})
        selected, amounts, items, orbs = self.app["_prepare_vip_form_quantities"](
            extras, {"s1": ["battle_items", "talent_orbs"]}, {}, {"battle_items": {1: 999}}, {1: 998, 2: 998})
        self.assertEqual(items, {"battle_items": {1: 50}})
        self.assertEqual(orbs, {2: 10})
        self.assertEqual(extras["quantities"]["custom_talent_orbs"]["ids"], [2])
        save = self.save()
        for item in save.battle_items.items:
            item.amount = 7
        save.talent_orbs.set_orb(1, 4)
        save.talent_orbs.set_orb(2, 2)
        self.app["_apply_all_segments_unlocked"](save, {"s1": ["battle_items", "talent_orbs"]}, [],
                                                {"_vip_form_extras": extras}, vip_items={"battle_items": {1: 999}},
                                                vip_talent_orbs={1: 998, 2: 998})
        loaded = self.reload(save)
        self.assertEqual([item.amount for item in loaded.battle_items.items], [7, 50, 7, 7, 7, 7])
        stored_orbs = {item["id"]: item["value"] for item in loaded.talent_orbs.serialize()}
        self.assertEqual(stored_orbs[1], 4)
        self.assertEqual(stored_orbs[2], 10)
        _, _, empty, _ = self.app["_prepare_vip_form_quantities"](extras, {"s1": ["battle_items"]}, {}, {"battle_items": {}}, None)
        self.assertEqual(empty["battle_items"], {})

    def test_validation_rejects_invalid_and_unknown_definitions_before_work(self):
        cases = [
            {"quantities": {"custom_catfood": [58000, 42000]}},
            {"quantities": {"custom_catfood": [0, 59000]}},
            {"quantities": {"custom_np": [True, 3]}},
            {"quantities": {"unknown": [0, 1]}},
            {"levels": {"9": {"plus": [0, 1]}}},
            {"levels": {"2": {"base": [0, 30]}}},
            {"levels": {"2": {}}},
            {"excluded_ids": [9999]}, {"excluded_ids": [True]}, {"orb_rank": "unknown"},
        ]
        for extras in cases:
            with self.subTest(extras=extras), self.assertRaises(ValueError):
                self.compile(extras)
        self.metadata["groups"] = [{"kind": "collab", "cat_ids": [2, 3]}]
        with self.assertRaisesRegex(ValueError, "所属定義"):
            self.compile({"levels": {"2": {"collab_plus": [1, 1]}}})

    def test_plus_support_uses_verified_unitbuy_and_rejects_missing_rows(self):
        rows = []
        for info in self.metadata["characters"]:
            row = [0] * 52
            row[13] = info["rarity"]
            row[51] = 0 if info["id"] == 2 else 90
            rows.append(",".join(map(str, row)))
        getter = Mock()
        getter.download.return_value = core.Data("\n".join(rows).encode())
        with patch.dict(self.app, {"_latest_jp_game_data_getter": lambda: getter}):
            extras = self.compile({"levels": {"2": {"plus": [15, 15]}}, "plus_supported_only": True})
            self.assertEqual({x["id"]: x["plus"] for x in extras["levels"]}, {2: [0, 0], 3: [15, 15]})
            getter.download.return_value = core.Data(rows[0].encode())
            with self.assertRaisesRegex(ValueError, "対応定義"):
                self.compile({"levels": {"2": {"plus": [15, 15]}}, "plus_supported_only": True})

    def test_both_existing_routes_keep_vip_gate_and_reject_invalid_ranges_before_queue(self):
        executor = Mock()
        for path, handler in (("/api/run_daiko", "api_run_daiko"), ("/api/run_create", "api_run_create")):
            payload = {"transfer_code": "abcde1234", "auth_code": "1234", "api_key": "test",
                       "vip_form_extras": {"quantities": {"custom_catfood": [5, 3]}}}
            for vip, expected in ((False, 403), (True, 400)):
                with self.subTest(path=path, vip=vip), self.app["app"].test_request_context(path, method="POST", json=payload), \
                     patch.dict(self.app, {"validate_and_consume_api_key": lambda _: True, "count_inflight_jobs": lambda: 0,
                                           "site_vip_confirmed": lambda: vip, "executor": executor}):
                    response = self.app[handler]()
                    self.assertEqual(self.app["app"].make_response(response).status_code, expected)
        executor.submit.assert_not_called()

    def test_definition_outage_returns_retryable_error_without_starting_jobs(self):
        executor = Mock()
        for path, handler in (("/api/run_daiko", "api_run_daiko"), ("/api/run_create", "api_run_create")):
            payload = {"transfer_code": "abcde1234", "auth_code": "1234", "api_key": "test",
                       "vip_form_extras": {"quantities": {"custom_battle_items": [5, 10]}}}
            with self.app["app"].test_request_context(path, method="POST", json=payload), \
                 patch.dict(self.app, {"validate_and_consume_api_key": lambda _: True, "count_inflight_jobs": lambda: 0,
                                       "site_vip_confirmed": lambda: True, "executor": executor,
                                       "get_legend_metadata": Mock(side_effect=RuntimeError("Offline"))}), \
                 contextlib.redirect_stdout(io.StringIO()):
                response = self.app[handler]()
                self.assertEqual(self.app["app"].make_response(response).status_code, 503)
        executor.submit.assert_not_called()

    def test_valid_vip_extras_reach_existing_workers_after_trial_reservation(self):
        for path, handler, amounts_index in (("/api/run_daiko", "api_run_daiko", 7), ("/api/run_create", "api_run_create", 4)):
            executor, reserve, operation, create_job = Mock(), Mock(return_value=(None, None)), Mock(), Mock()
            payload = {"transfer_code": "abcde1234", "auth_code": "1234", "api_key": "test", "operation_id": "A" * 30,
                       "vip_form_extras": {"quantities": {"custom_catfood": [42000, 58000]}}}
            with self.app["app"].test_request_context(path, method="POST", json=payload), \
                 patch.dict(self.app, {"validate_and_consume_api_key": lambda _: True, "count_inflight_jobs": lambda: 0,
                                       "site_vip_confirmed": lambda: True, "executor": executor,
                                       "_reserve_site_vip_job": reserve, "_create_operation": operation, "_create_job": create_job,
                                       "register_job_to_session": Mock()}):
                response = self.app[handler]()
                self.assertEqual(self.app["app"].make_response(response).status_code, 200)
            reserve.assert_called_once()
            executor.submit.assert_called_once()
            amounts = executor.submit.call_args.args[amounts_index]
            self.assertEqual(amounts["_vip_form_extras"]["quantities"]["custom_catfood"]["bounds"], [42000, 58000])
            self.assertEqual(executor.submit.call_args.args[-1], "VIP")


if __name__ == "__main__":
    unittest.main()
