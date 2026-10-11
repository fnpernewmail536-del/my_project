"""Persistence and failure tests using the real libSQL SDK and a local primary.

Only libsql.connect's network destination is substituted. The application SQL,
driver, transactions, row adapter, BLOBs and all stores execute unchanged.
No production database, game server, payment service or Discord is contacted.
"""

import contextlib
import io
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import libsql
import db_storage
from ACCOUNT.account_store import AccountStore
from db_storage import clear_connection_pool, connect, storage_config
from free_usage import FreeUsageManager, QuotaExhausted


ROOT = Path(__file__).resolve().parents[1]
NATIVE_CONNECT = libsql.connect


class CloudStorageTests(unittest.TestCase):
    def setUp(self):
        clear_connection_pool()
        self.addCleanup(clear_connection_pool)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.primary = self.root / "cloud.db"
        self.local = self.root / "disposable_app"
        self.env = patch.dict(os.environ, {
            "TURSO_DATABASE_URL": "libsql://test-primary.turso.io",
            "TURSO_AUTH_TOKEN": "dummy-test-token",
            "FLASK_SECRET_KEY": "dummy-stable-secret",
            "IDENTITY_HASH_KEY": "dummy-stable-secret",
            "ACCOUNT_IDENTITY_HASH_KEY": "dummy-stable-secret",
            "ADMIN_SNAPSHOT_KEY": "dummy-stable-secret",
            "PROXY_URL": "",
            "AUTO_GAME_UPDATE_ENABLED": "0",
            "DISABLE_BACKGROUND_UPDATER": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.driver = patch.object(libsql, "connect", side_effect=self.native_primary)
        self.driver.start()
        self.addCleanup(self.driver.stop)

    def native_primary(self, **kwargs):
        self.assertEqual(kwargs["database"], os.environ["TURSO_DATABASE_URL"])
        self.assertEqual(kwargs["auth_token"], "dummy-test-token")
        kwargs["database"] = str(self.primary)
        return NATIVE_CONNECT(**kwargs)

    def load_app(self):
        self.local.mkdir(exist_ok=True)
        script = self.local / "main13.py"
        shutil.copy2(ROOT / "main13.py", script)
        with patch.object(threading.Thread, "start"), \
             patch("requests.get", side_effect=AssertionError("Network disabled")), \
             patch("requests.post", side_effect=AssertionError("Network disabled")), \
             contextlib.redirect_stdout(io.StringIO()):
            return runpy.run_path(str(script))

    def manager(self):
        return FreeUsageManager(str(self.local / "Login/Bonus.db"),
                                str(self.local / "invitation/invitation.db"),
                                "dummy-stable-secret")

    def test_all_stores_survive_deleted_local_directory_and_restart(self):
        app = self.load_app()
        account = app["account_store"]
        user = account.create_account("192.0.2.1", "a" * 64, "testuser", "password-12345")
        account.grant_vip(user["public_id"], 30, "test-admin")
        ticket, created, _ = account.create_purchase_ticket(user["id"], 30)
        self.assertTrue(created)
        self.assertEqual(app["increment_usage_count"](), 1)
        manager = app["free_usage_manager"]
        manager.reserve("192.0.2.1", "a" * 64, "reservation-test")
        manager.settle("reservation-test", success=True)
        invitation = manager.invitation_link("192.0.2.1", "a" * 64, "https://example.test")
        now = time.time()
        job = {"status": "done", "operation_type": "create", "created_at": now,
               "updated_at": now, "started_at": now - 12, "finished_at": now,
               "timing_learnable": True, "timing_count": 1, "timing_profile": "test",
               "timing_marks": [{"stage": "create", "at": now - 12}]}
        app["_persist_job"]("job-test", job)
        app["job_timing_store"].remember("job-test", job)
        operation = "A" * 30
        snapshot = app["_encrypt_snapshot"](b"test-snapshot\x00\xff", operation)
        app["_create_operation"](operation, "job-test", "clone", now)
        app["_update_operation"](operation, snapshot=snapshot, status="error")
        api_key = app["generate_api_key"]()
        self.assertEqual(list(self.local.rglob("*.db")), [])

        # Render discards the whole running instance's local filesystem.
        shutil.rmtree(self.local)
        restarted = self.load_app()
        account2 = restarted["account_store"]
        logged_in = account2.authenticate("testuser", "password-12345")
        self.assertEqual(logged_in["public_id"], user["public_id"])
        self.assertTrue(logged_in["is_paid_vip"])
        self.assertEqual(account2.list_user_tickets(user["id"])[0]["purchase_key"], ticket["purchase_key"])
        self.assertEqual(restarted["get_usage_count"](), 1)
        self.assertEqual(restarted["increment_usage_count"](), 2)
        self.assertEqual(restarted["free_usage_manager"].status("192.0.2.1", "a" * 64)["remaining"], 9)
        self.assertEqual(restarted["free_usage_manager"].invitation_link(
            "192.0.2.1", "a" * 64, "https://example.test")["code"], invitation["code"])
        self.assertEqual(restarted["_load_job"]("job-test")["status"], "done")
        stored = restarted["_load_operation"](operation)
        self.assertEqual(restarted["_decrypt_snapshot"](stored["snapshot"], operation), b"test-snapshot\x00\xff")
        self.assertTrue(restarted["validate_and_consume_api_key"](api_key))
        self.assertFalse(restarted["validate_and_consume_api_key"](api_key))
        self.assertEqual(len(restarted["job_timing_store"]._samples("create", 1, now)), 1)

    def test_failed_transaction_rolls_back_and_integrity_errors_are_preserved(self):
        manager = self.manager()
        manager.reserve("192.0.2.2", "b" * 64, "duplicate")
        with self.assertRaises(sqlite3.IntegrityError):
            manager.reserve("192.0.2.2", "b" * 64, "duplicate")
        self.assertEqual(manager.status("192.0.2.2", "b" * 64)["remaining"], 9)
        manager.settle("duplicate", success=False)
        manager.settle("duplicate", success=False)
        self.assertEqual(manager.status("192.0.2.2", "b" * 64)["remaining"], 10)

    def test_parallel_reservations_cannot_spend_more_than_the_quota(self):
        manager = self.manager()
        manager.status("192.0.2.3", "c" * 64)

        def reserve(index):
            try:
                manager.reserve("192.0.2.3", "c" * 64, f"parallel-{index}")
                return True
            except QuotaExhausted:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            accepted = list(pool.map(reserve, range(14)))
        self.assertEqual(sum(accepted), 10)
        self.assertEqual(manager.status("192.0.2.3", "c" * 64)["remaining"], 0)

    def test_invitation_rewards_are_once_only_in_the_shared_database(self):
        manager = self.manager()
        invitation = manager.invitation_link("192.0.2.4", "d" * 64, "https://example.test")
        manager.redeem_invitation(invitation["code"], "192.0.2.5", "e" * 64)
        manager.reserve("192.0.2.5", "e" * 64, "invited-job")
        self.assertTrue(manager.settle("invited-job", success=True))
        self.assertFalse(manager.settle("invited-job", success=True))
        self.assertEqual(manager.status("192.0.2.4", "d" * 64)["remaining"], 13)
        self.assertEqual(manager.status("192.0.2.5", "e" * 64)["remaining"], 11)
        self.assertEqual(len(manager.pending_vip_trial_events()), 1)

    def test_cloud_failure_never_falls_back_or_exposes_credentials(self):
        path = self.local / "must-not-exist.db"
        with patch.object(libsql, "connect", side_effect=ValueError("dummy-test-token")):
            with self.assertRaises(sqlite3.OperationalError) as captured:
                connect(path)
        self.assertNotIn("dummy-test-token", str(captured.exception))
        self.assertFalse(path.exists())

    def test_reuses_connection_without_caching_remote_rows(self):
        with patch.object(libsql, "connect", side_effect=self.native_primary) as driver:
            for index in range(10):
                with connect(self.local / f"store-{index}.db", isolation_level=None) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("CREATE TABLE IF NOT EXISTS shared_value (value INTEGER)")
            self.assertEqual(driver.call_count, 1)
            with NATIVE_CONNECT(str(self.primary)) as other_app:
                other_app.execute("INSERT INTO shared_value VALUES (42)")
                other_app.commit()
            with connect(self.local / "read.db", isolation_level=None) as conn:
                self.assertEqual(conn.execute("SELECT value FROM shared_value").fetchone()[0], 42)
            self.assertEqual(driver.call_count, 1)

    def test_close_rolls_back_uncommitted_writes_and_preserves_cursor_metadata(self):
        conn = connect(self.local / "write.db")
        conn.execute("CREATE TABLE rollback_check (value INTEGER)")
        conn.commit()
        cursor = conn.execute("INSERT INTO rollback_check VALUES (1)")
        cursor.close()
        conn.close()
        self.assertEqual(cursor.rowcount, 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            cursor.fetchone()
        with connect(self.local / "next.db") as reopened:
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM rollback_check").fetchone()[0], 0)

    def test_expired_or_failed_connections_are_discarded(self):
        with patch.object(libsql, "connect", side_effect=self.native_primary) as driver:
            with connect(self.local / "read.db") as conn:
                conn.execute("SELECT 1").fetchone()
            with patch("db_storage.time.monotonic", return_value=time.monotonic() + 31):
                conn = connect(self.local / "read.db")
                self.assertEqual(driver.call_count, 2)
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("SELECT * FROM missing_table")
                conn.close()
            with connect(self.local / "read.db") as conn:
                conn.execute("SELECT 1").fetchone()
            self.assertEqual(driver.call_count, 3)

    def test_pool_can_be_disabled_and_configuration_avoids_repeat_queries(self):
        with patch.dict(os.environ, {"TURSO_POOL_ENABLED": "0"}), \
             patch.object(libsql, "connect", side_effect=self.native_primary) as driver:
            for _ in range(2):
                with connect(self.local / "read.db") as conn:
                    with patch.object(conn, "_execute", wraps=conn._execute) as execute:
                        conn.execute("PRAGMA foreign_keys=ON")
                        conn.execute("PRAGMA foreign_keys=ON")
                        self.assertEqual(execute.call_count, 1)
            self.assertEqual(driver.call_count, 2)
        self.assertEqual(len(db_storage._IDLE_CONNECTIONS), 0)

    def test_pool_has_bounded_idle_connections_and_exclusive_leases(self):
        connections = [connect(self.local / "lease.db") for _ in range(6)]
        self.assertEqual(len({id(conn._raw) for conn in connections}), 6)
        for conn in connections:
            conn.close()
        self.assertEqual(len(db_storage._IDLE_CONNECTIONS), 4)
        first, second = connect(self.local / "lease.db"), connect(self.local / "lease.db")
        try:
            self.assertIsNot(first._raw, second._raw)
        finally:
            first.close()
            second.close()

    def test_rotated_credentials_do_not_reuse_old_connection(self):
        with patch.object(libsql, "connect", side_effect=lambda **kwargs: NATIVE_CONNECT(
                str(self.primary), isolation_level=kwargs["isolation_level"],
                _check_same_thread=False)) as driver:
            with connect(self.local / "read.db") as conn:
                conn.execute("SELECT 1").fetchone()
            with patch.dict(os.environ, {"TURSO_AUTH_TOKEN": "dummy-rotated-token"}):
                with connect(self.local / "read.db") as conn:
                    conn.execute("SELECT 1").fetchone()
            self.assertEqual(driver.call_count, 2)

    def test_vip_stats_use_one_query_and_keep_results(self):
        account = AccountStore(str(self.local / "ACCOUNT/account.db"), "test-secret")
        user = account.create_account("192.0.2.10", "1" * 64, "statsuser", "password-12345")
        account.grant_vip(user["public_id"], 30, "test-admin")
        original_connect = account._connect
        executions = []
        def tracked_connect():
            conn = original_connect()
            original_execute = conn.execute
            def tracked_execute(sql, parameters=()):
                executions.append(sql)
                return original_execute(sql, parameters)
            conn.execute = tracked_execute
            return conn
        with patch.object(account, "_connect", side_effect=tracked_connect):
            self.assertEqual(account.public_vip_stats(), {
                "active_vip": 1, "total_grants": 1, "grants_30d": 1,
            })
        self.assertEqual(len(executions), 1)


class StorageConfigurationTests(unittest.TestCase):
    def test_missing_token_or_secret_is_rejected_before_connecting(self):
        for variables in (
            {"TURSO_DATABASE_URL": "libsql://test.turso.io"},
            {"TURSO_AUTH_TOKEN": "test"},
            {"TURSO_DATABASE_URL": "libsql://test.turso.io", "TURSO_AUTH_TOKEN": "test"},
            {"REQUIRE_PERSISTENT_DB": "1"},
            {"TURSO_DATABASE_URL": "file:local.db", "TURSO_AUTH_TOKEN": "test", "FLASK_SECRET_KEY": "test"},
        ):
            with self.subTest(variables=list(variables)), patch.dict(os.environ, variables, clear=True):
                with self.assertRaises(RuntimeError):
                    storage_config()

    def test_unconfigured_local_database_keeps_existing_accounts(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True):
            path = str(Path(td) / "ACCOUNT/account.db")
            account = AccountStore(path, "local-secret")
            user = account.create_account("192.0.2.6", "f" * 64, "localuser", "password-12345")
            reopened = AccountStore(path, "local-secret")
            self.assertEqual(reopened.authenticate("localuser", "password-12345")["public_id"], user["public_id"])


if __name__ == "__main__":
    unittest.main()
