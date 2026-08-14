from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import cursor_cli_usage as cursor_usage


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class CursorUsageTests(unittest.TestCase):
    def test_linux_state_database_follows_xdg_config_home(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(
                cursor_usage.os.environ,
                {"XDG_CONFIG_HOME": temporary},
                clear=True,
            ),
            mock.patch.object(cursor_usage.platform, "system", return_value="Linux"),
        ):
            self.assertEqual(
                cursor_usage.get_state_db(),
                Path(temporary)
                / "Cursor"
                / "User"
                / "globalStorage"
                / "state.vscdb",
            )

    def test_access_token_is_read_from_database_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.vscdb"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
                connection.execute(
                    "INSERT INTO ItemTable VALUES (?, ?)",
                    ("cursorAuth/accessToken", "local-token"),
                )
                connection.commit()
            finally:
                connection.close()
            before = database.read_bytes()
            with mock.patch.dict(
                cursor_usage.os.environ, {"CURSOR_USAGE_STATE_DB": str(database)}
            ):
                with mock.patch.object(
                    cursor_usage, "_get_cli_access_token", return_value=None
                ):
                    self.assertEqual(cursor_usage.get_access_token(), "local-token")
            self.assertEqual(database.read_bytes(), before)

    def test_cli_status_is_preferred_and_only_access_token_is_returned(self):
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "authenticated": True,
                    "auth": {
                        "accessToken": "cli-access",
                        "refreshToken": "must-not-be-returned",
                    },
                }
            ),
        )
        with (
            mock.patch.object(cursor_usage, "_find_cursor_cli", return_value="agent"),
            mock.patch.object(
                cursor_usage.subprocess, "run", return_value=completed
            ) as run,
            mock.patch.object(cursor_usage, "_get_ide_access_token") as ide,
        ):
            self.assertEqual(cursor_usage.get_access_token(), "cli-access")
        run.assert_called_once_with(
            ["agent", "status", "--format", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        ide.assert_not_called()

    def test_fetch_uses_bearer_token_and_connect_protocol(self):
        payload = {"planUsage": {"limit": 2000, "used": 500, "totalPercentUsed": 25}}

        def fake_urlopen(request, timeout):
            self.assertEqual(request.get_header("Authorization"), "Bearer token")
            self.assertEqual(request.get_header("Connect-protocol-version"), "1")
            self.assertEqual(timeout, 15)
            return FakeResponse(payload)

        with (
            mock.patch.object(cursor_usage, "get_access_token", return_value="token"),
            mock.patch.object(
                cursor_usage.urllib.request, "urlopen", side_effect=fake_urlopen
            ),
        ):
            self.assertEqual(cursor_usage.fetch_usage(), payload)

    def test_build_normalizes_current_period_usage(self):
        payload = {
            "billingCycleStart": "1000",
            "billingCycleEnd": "2000",
            "planUsage": {"limit": 2000, "remaining": 1500, "totalPercentUsed": 25},
            "spendLimitUsage": {"limit": "1000", "used": "100"},
            "autoBucketModels": ["auto"],
        }
        with mock.patch.object(cursor_usage, "fetch_usage", return_value=payload):
            data = cursor_usage.build_usage_json()
        self.assertEqual(data["status"], "live")
        self.assertEqual(data["plan_usage"]["percent_used"], 25)
        self.assertEqual(data["spend_limit_usage"]["used"], 100)
        self.assertEqual(data["auto_models"], ["auto"])

    def test_unauthorized_never_attempts_refresh(self):
        error = urllib.error.HTTPError(cursor_usage.USAGE_URL, 401, "", {}, None)
        with (
            mock.patch.object(cursor_usage, "get_access_token", return_value="expired"),
            mock.patch.object(
                cursor_usage.urllib.request, "urlopen", side_effect=error
            ),
        ):
            data = cursor_usage.build_usage_json()
        self.assertEqual(data["status"], "unavailable")
        self.assertIn("sign in", data["error"])

    def test_stale_cache_is_returned_when_refresh_fails(self):
        cached = {
            "provider": "cursor",
            "status": "live",
            "source": "cursor_dashboard_api",
            "retrieved_at": "2020-01-01T00:00:00+00:00",
            "plan_usage": {"percent_used": 50},
        }
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "usage.json"
            cache.write_text(json.dumps(cached))
            with (
                mock.patch.object(cursor_usage, "DEFAULT_USAGE_FILE", cache),
                mock.patch.object(
                    cursor_usage,
                    "build_usage_json",
                    return_value={"status": "unavailable", "error": "offline"},
                ),
            ):
                result = cursor_usage.get_cached_usage()
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["refresh_error"], "offline")


if __name__ == "__main__":
    unittest.main()
