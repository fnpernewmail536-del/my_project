"""Legend updates and native BCSFE save roundtrips, without production access."""

import contextlib
import copy
import io
import json
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
FIXTURE = json.loads((ROOT / "tests/legend_fixture.json").read_text(encoding="utf-8"))


def base36(value):
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while value:
        value, digit = divmod(value, 36)
        result = alphabet[digit] + result
    return result or "0"


def published_tsv():
    # Names, indices and crown counts are from the pinned public fixture.
    maps = ["id\tname_jp\tstars"]
    stages = ["id\tname_jp"]
    for group, key in ((0, "legend"), (9, "true_legend"), (16, "zero_legend")):
        for item in FIXTURE[key]["maps"]:
            full_id = group * 1000 + item["id"]
            multipliers = ",".join(["100"] * item["crowns"]) if item["crowns"] > 1 else ""
            maps.append(f"{base36(full_id)}\t{item['name']}\t{multipliers}")
            for index, name in enumerate(item["stages"]):
                stages.append(f"{base36(full_id * 1000 + index)}\t{name}")
    return "\n".join(maps), "\n".join(stages)


class LegendUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = contextlib.ExitStack()
        cls.addClassCleanup(cls.context.close)
        temporary = cls.context.enter_context(tempfile.TemporaryDirectory())
        cls.root = Path(temporary)
        cls.context.enter_context(patch.dict(os.environ, {
            "TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": "",
            "REQUIRE_PERSISTENT_DB": "0", "RENDER": "",
            "FLASK_SECRET_KEY": "test-stable-secret", "IDENTITY_HASH_KEY": "test-stable-secret",
            "ACCOUNT_IDENTITY_HASH_KEY": "test-stable-secret", "ADMIN_SNAPSHOT_KEY": "test-stable-secret",
            "PROXY_URL": "", "AUTO_GAME_UPDATE_ENABLED": "0", "DISABLE_BACKGROUND_UPDATER": "1",
            "GAME_UPDATE_CACHE_PATH": str(cls.root / "game_updates.json"),
        }))
        cls.context.enter_context(patch("dotenv.load_dotenv", return_value=False))
        cls.context.enter_context(patch.object(threading.Thread, "start"))
        cls.context.enter_context(patch("requests.get", side_effect=AssertionError("Network disabled")))
        cls.context.enter_context(patch("requests.post", side_effect=AssertionError("Network disabled")))
        script = cls.root / "main13.py"
        shutil.copy2(ROOT / "main13.py", script)
        with contextlib.redirect_stdout(io.StringIO()):
            loaded = runpy.run_path(str(script))
        cls.app = loaded["_ensure_game_update_state"].__globals__

    def setUp(self):
        self.root.joinpath("game_updates.json").unlink(missing_ok=True)
        self.app["_game_update_state"] = None
        self.state = self.app["_ensure_game_update_state"]()

    def older_legend(self):
        result = copy.deepcopy(FIXTURE)
        result.update(data_version="15.6.0", revision="0" * 40)
        result["zero_legend"]["maps"] = result["zero_legend"]["maps"][:34]
        return result

    def save(self):
        return core.SaveFile(cc=core.CountryCode.from_code("jp"), gv=core.GameVersion(150501))

    def reload(self, save):
        data = save.to_data()
        return core.SaveFile(core.Data(data.data), cc=core.CountryCode.from_code("jp"))

    def test_verified_bundle_and_current_selection(self):
        self.assertEqual(self.app["_bundled_legend_metadata"](), FIXTURE)
        with patch.dict(self.app, {"_get_base_legend_metadata": Mock(return_value={"data_version": "15.6.0"})}):
            metadata = self.app["get_legend_metadata"]()
            self.assertEqual([len(metadata[key]["maps"]) for key in self.app["LEGEND_SERIES"]], [49, 49, 37])
            self.assertEqual(metadata["zero_legend"]["maps"][36]["name"], "理想郷ビバラメン")
            selections = self.app["safe_legend_stages"]({"legend_stages": {"zero_legend": {
                "36": {"0": [5, 6], "1": [0]}, "37": {"0": [0]}, "14": {"1": [5]},
            }}})
            self.assertEqual(selections, {"zero_legend": {36: {0: [5]}, 14: {1: [5]}}})
            self.assertIn(36, self.app["full_legend_selection"]("zero_legend")["zero_legend"])
        status = self.app["get_game_update_status"]()
        self.assertEqual(status["legend_data_version"], "15.7.1")
        self.assertEqual(status["legend_map_counts"]["zero_legend"], 37)

    def test_published_fetch_pins_config_maps_and_stages_to_one_commit(self):
        maps, stages = published_tsv()
        revision = FIXTURE["revision"]
        calls = []
        japan = FIXTURE["data_version"]

        def get(url):
            calls.append(url)
            if url.endswith("/commits/master"):
                return json.dumps({"sha": revision}).encode()
            self.assertIn("/" + revision + "/data/", url)
            if url.endswith("config.json"):
                return json.dumps({"japan": japan}).encode()
            return (maps if url.endswith("map.tsv") else stages).encode()

        with patch.dict(self.app, {"_game_update_get": get}):
            self.assertEqual(self.app["_fetch_published_legend_stages"](self.older_legend()), FIXTURE)
            self.assertEqual(len(calls), 4)
            calls.clear()
            self.assertEqual(self.app["_fetch_published_legend_stages"](FIXTURE), FIXTURE)
            self.assertEqual(len(calls), 1)
            japan = "15.8"
            self.assertEqual(self.app["_fetch_published_legend_stages"](self.older_legend())["data_version"], "15.8.0")

    def test_missing_stages_names_crowns_and_old_versions_are_rejected(self):
        for change in ("old", "missing_map", "missing_stage", "missing_crown", "empty_name", "wrong_id"):
            with self.subTest(change=change):
                candidate = copy.deepcopy(FIXTURE)
                if change == "old":
                    candidate["data_version"] = "15.6.0"
                elif change == "missing_map":
                    candidate["zero_legend"]["maps"].pop()
                elif change == "missing_stage":
                    candidate["zero_legend"]["maps"][36]["stages"].pop()
                elif change == "missing_crown":
                    candidate["zero_legend"]["maps"][14]["crowns"] = 1
                elif change == "empty_name":
                    candidate["zero_legend"]["maps"][36]["stages"][5] = ""
                else:
                    candidate["zero_legend"]["maps"][36]["id"] = 37
                with self.assertRaises(ValueError):
                    self.app["_validate_legend_update"](candidate, FIXTURE)
        maps, stages = published_tsv()
        for broken_stages in (stages.rsplit("\n", 1)[0], stages + "\n" + stages.splitlines()[-1]):
            with self.assertRaises(ValueError):
                self.app["_parse_published_legend_stages"](maps, broken_stages, "15.7.1", FIXTURE, FIXTURE["revision"])

    def test_background_update_survives_bcdata_failure_and_cache_restart(self):
        self.state["legend_stages"] = self.older_legend()
        replacements = {
            "_fetch_latest_jp_app_version": Mock(return_value=150701),
            "_fetch_published_characters": Mock(side_effect=lambda current: current),
            "_fetch_published_legend_stages": Mock(return_value=copy.deepcopy(FIXTURE)),
            "_game_update_get": Mock(side_effect=OSError("Offline BCData")),
        }
        with patch.dict(self.app, replacements), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.app["_refresh_game_updates"]())
        self.assertEqual(self.state["legend_stages"], FIXTURE)
        self.assertEqual(self.state["status"], "partial")
        self.assertEqual(self.state["errors"], ["bcdata:OSError"])
        # A different, simulated valid commit proves reload uses the cache,
        # rather than silently falling back to the identical bundled snapshot.
        self.state["legend_stages"]["revision"] = "f" * 40
        self.app["_persist_game_update_state"]()
        expected = copy.deepcopy(self.state["legend_stages"])
        self.app["_game_update_state"] = None
        self.assertEqual(self.app["_ensure_game_update_state"]()["legend_stages"], expected)
        replacements["_fetch_published_legend_stages"] = Mock(side_effect=OSError("Offline stages"))
        with patch.dict(self.app, replacements), contextlib.redirect_stdout(io.StringIO()):
            self.app["_refresh_game_updates"]()
        state = self.app["_ensure_game_update_state"]()
        self.assertEqual(state["legend_stages"], expected)
        self.assertIn("legend_stages:OSError", state["errors"])

    def test_old_cache_and_invalid_legend_cache_preserve_other_definitions(self):
        cached = copy.deepcopy(self.state)
        cached["characters"]["data_version"] = "15.8.0"
        cached.pop("legend_stages")
        for corrupted in (False, True):
            if corrupted:
                cached["legend_stages"] = {"data_version": "15.8.0"}
            self.root.joinpath("game_updates.json").write_text(json.dumps(cached), encoding="utf-8")
            self.app["_game_update_state"] = None
            with contextlib.redirect_stdout(io.StringIO()):
                state = self.app["_ensure_game_update_state"]()
            self.assertEqual(state["characters"]["data_version"], "15.8.0")
            self.assertEqual(state["legend_stages"], FIXTURE)

    def test_latest_overlay_does_not_wait_for_old_base_cache_ttl(self):
        base = {"data_version": "15.6.0", "event_stage_families": {"event": {"maps": [{"id": 5}]}},
                "zero_legend": {"maps": []}}
        with patch.dict(self.app, {"_get_base_legend_metadata": Mock(return_value=base)}):
            self.state["legend_stages"] = self.older_legend()
            self.assertEqual(len(self.app["get_legend_metadata"]()["zero_legend"]["maps"]), 34)
            self.state["legend_stages"] = copy.deepcopy(FIXTURE)
            metadata = self.app["get_legend_metadata"]()
            self.assertEqual(len(metadata["zero_legend"]["maps"]), 37)
            self.assertEqual(metadata["event_stage_families"], base["event_stage_families"])
            self.assertEqual(metadata["data_version"], "15.6.0")
            self.assertEqual(metadata["legend_data_version"], "15.7.1")
            metadata["zero_legend"]["maps"].clear()
            self.assertEqual(len(self.state["legend_stages"]["zero_legend"]["maps"]), 37)

    def test_new_chapter_and_crown_clear_survive_native_save_reload(self):
        save = self.save()
        self.assertEqual(len(save.zero_legends.chapters), 0)
        self.app["_ensure_zero_map"](save, 33)
        old = save.zero_legends.chapters[0]
        old.unknown = 7
        old.chapters[0].stages[0].clear_times = 9
        old.chapters[0].unlock_state = 3
        save.zero_legends.chapters[14].chapters.pop()
        self.app["_ensure_latest_stage_slots"](save, FIXTURE)
        self.assertEqual(len(save.zero_legends.chapters), 37)
        self.assertEqual(len(save.zero_legends.chapters[36].chapters[0].stages), 6)
        self.assertEqual(len(save.zero_legends.chapters[14].chapters), 2)
        logs = self.app["apply_legend_stages"](save, {"zero_legend": {36: {0: [5]}, 14: {1: [5]}}})
        self.assertEqual(logs, ["零レジェンドストーリー(2ステージ冠)"])
        loaded = self.reload(save)
        self.assertEqual(loaded.game_version.game_version, 150501)
        self.assertEqual(len(loaded.zero_legends.chapters), 37)
        latest = loaded.zero_legends.chapters[36].chapters[0]
        self.assertEqual((latest.clear_progress, latest.unlock_state, latest.stages[5].clear_times), (6, 3, 1))
        self.assertEqual(loaded.zero_legends.chapters[14].chapters[1].stages[5].clear_times, 1)
        self.assertEqual(loaded.zero_legends.chapters[14].chapters[0].stages[5].clear_times, 0)
        self.assertEqual(loaded.zero_legends.chapters[0].chapters[0].stages[0].clear_times, 9)
        self.assertEqual(loaded.zero_legends.chapters[0].unknown, 7)
        self.assertEqual(loaded.zero_legends.chapters[0].chapters[1].stages[0].clear_times, 0)

    def test_newer_existing_slots_are_preserved_without_guessing_more_definitions(self):
        save = self.save()
        self.app["_ensure_zero_map"](save, 36)
        for _ in range(3):
            save.zero_legends.chapters.append(copy.deepcopy(save.zero_legends.chapters[36]))
        save.zero_legends.chapters[39].chapters[0].stages[5].clear_times = 8
        self.app["_ensure_latest_stage_slots"](save, FIXTURE)
        self.assertFalse(self.app["_ensure_zero_map"](save, 40))
        loaded = self.reload(save)
        self.assertEqual(len(loaded.zero_legends.chapters), 40)
        self.assertEqual(loaded.zero_legends.chapters[39].chapters[0].stages[5].clear_times, 8)


if __name__ == "__main__":
    unittest.main()
