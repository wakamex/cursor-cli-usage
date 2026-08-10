#!/usr/bin/env python3
"""Cursor account usage monitor using Cursor-owned local authentication."""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_USAGE_FILE = Path.home() / ".cursor" / "usage-limits.json"
USAGE_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
CACHE_MAX_AGE = 300
DAEMON_INTERVAL = 300


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _format_reset(value: object) -> str:
    if not isinstance(value, str | int | float):
        return ""
    try:
        reset = datetime.fromtimestamp(float(value) / 1000, UTC)
    except (TypeError, ValueError, OSError):
        parsed = _parse_iso(value)
        if parsed is None:
            return ""
        reset = parsed
    minutes = max(0, int((reset - datetime.now(UTC)).total_seconds()) // 60)
    if minutes >= 1440:
        return f"{minutes // 1440}d{(minutes % 1440) // 60}h"
    if minutes >= 60:
        return f"{minutes // 60}h{minutes % 60}m"
    return f"{minutes}m"


def get_state_db() -> Path:
    override = os.environ.get("CURSOR_USAGE_STATE_DB")
    if override:
        return Path(override).expanduser()
    if platform.system() == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Cursor"
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )
    if platform.system() == "Windows":
        app_data = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return app_data / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    return Path.home() / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def get_usage_file() -> Path:
    override = os.environ.get("CURSOR_USAGE_FILE")
    return Path(override).expanduser() if override else DEFAULT_USAGE_FILE


def get_access_token() -> str:
    """Read the current access token without refreshing or modifying Cursor state."""
    path = get_state_db()
    if not path.is_file():
        raise RuntimeError(f"Cursor state database not found at {path}")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                ("cursorAuth/accessToken",),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Could not read Cursor authentication from {path}") from exc
    if not row or not isinstance(row[0], str) or not row[0]:
        raise RuntimeError("Cursor is not logged in")
    return row[0]


def _number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _usage_bucket(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {
        key: parsed
        for key, source in (
            ("limit", "limit"),
            ("used", "used"),
            ("remaining", "remaining"),
            ("percent_used", "totalPercentUsed"),
        )
        if (parsed := _number(value.get(source))) is not None
    }


def fetch_usage() -> dict:
    token = get_access_token()
    request = urllib.request.Request(
        USAGE_URL,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "User-Agent": "cursor-usage/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(
                "Cursor access token is no longer accepted; sign in with Cursor again"
            ) from exc
        raise RuntimeError(f"Cursor usage API returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Cursor usage API is unavailable") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Cursor usage API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Cursor usage API returned an unexpected response")
    return payload


def _normalize(payload: dict) -> dict:
    models = payload.get("autoBucketModels")
    return {
        "billing_cycle": {
            "start": payload.get("billingCycleStart"),
            "end": payload.get("billingCycleEnd"),
        },
        "plan_usage": _usage_bucket(payload.get("planUsage")),
        "spend_limit_usage": _usage_bucket(payload.get("spendLimitUsage")),
        "auto_models": models if isinstance(models, list) else [],
    }


def build_usage_json() -> dict:
    updated_at = _iso_now()
    try:
        usage = _normalize(fetch_usage())
    except Exception as exc:
        return {
            "provider": "cursor",
            "status": "unavailable",
            "source": "cursor_dashboard_api",
            "retrieved_at": updated_at,
            "error": str(exc),
        }
    return {
        "provider": "cursor",
        "status": "live",
        "source": "cursor_dashboard_api",
        "retrieved_at": updated_at,
        **usage,
    }


def write_usage_file(data: dict) -> None:
    path = get_usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _read_cache() -> dict | None:
    try:
        data = json.loads(get_usage_file().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def get_cached_usage(max_age: int = CACHE_MAX_AGE, force_refresh: bool = False) -> dict:
    cached = _read_cache()
    if not force_refresh and cached:
        retrieved = _parse_iso(cached.get("retrieved_at"))
        if retrieved and (datetime.now(UTC) - retrieved).total_seconds() < max_age:
            return {**cached, "status": "cached"}
    fresh = build_usage_json()
    if fresh["status"] == "live":
        write_usage_file(fresh)
        return fresh
    if cached:
        return {**cached, "status": "stale", "refresh_error": fresh.get("error")}
    return fresh


def _summary_usage(data: dict) -> dict | None:
    for key in ("plan_usage", "spend_limit_usage"):
        bucket = data.get(key)
        if isinstance(bucket, dict) and bucket.get("percent_used") is not None:
            return bucket
    return None


def _statusline_text(data: dict) -> str:
    bucket = _summary_usage(data)
    if not bucket:
        return "cursor:q:unavailable"
    used = float(bucket["percent_used"])
    reset = _format_reset((data.get("billing_cycle") or {}).get("end"))
    parts = [f"cursor:q:{used:.1f}%used"]
    if reset:
        parts.append(f"reset:{reset}")
    if data.get("status") in {"cached", "stale"}:
        parts.append(str(data["status"]))
    return " ".join(parts)


def _print_status(data: dict) -> None:
    print("Cursor usage")
    print(f"Status: {data['status']}")
    bucket = data.get("plan_usage")
    if isinstance(bucket, dict):
        if bucket.get("percent_used") is not None:
            print(f"Plan: {float(bucket['percent_used']):g}% used")
        if bucket.get("used") is not None and bucket.get("limit") is not None:
            print(f"Usage: {bucket['used']} of {bucket['limit']}")
    spend = data.get("spend_limit_usage")
    if isinstance(spend, dict) and spend.get("percent_used") is not None:
        print(f"Spend limit: {float(spend['percent_used']):g}% used")
    cycle = data.get("billing_cycle") or {}
    if cycle.get("end"):
        print(f"Resets: {cycle['end']}")
    if data.get("error"):
        print(f"Error: {data['error']}")
    if data.get("refresh_error"):
        print(f"Refresh error: {data['refresh_error']}")


def cmd_status(_args: argparse.Namespace) -> None:
    _print_status(build_usage_json())


def cmd_json(_args: argparse.Namespace) -> None:
    print(json.dumps(build_usage_json(), indent=2))


def cmd_statusline(args: argparse.Namespace) -> None:
    print(_statusline_text(get_cached_usage(args.max_age, args.refresh)))


def cmd_refresh(_args: argparse.Namespace) -> None:
    data = build_usage_json()
    if data["status"] == "live":
        write_usage_file(data)
    _print_status(data)


def cmd_daemon(args: argparse.Namespace) -> None:
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"cursor-usage daemon started (refreshing every {args.interval}s)")
    print(f"Writing to {get_usage_file()}")
    while True:
        data = build_usage_json()
        if data["status"] == "live":
            write_usage_file(data)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {_statusline_text(data)}")
        time.sleep(args.interval)


def cmd_install(_args: argparse.Namespace) -> None:
    print("Install with:\n  uv tool install cursor-cli-usage\n\nThen run:\n  cursor-usage")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cursor account usage monitor")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "json", "daemon", "statusline", "refresh", "install"],
    )
    parser.add_argument("-i", "--interval", type=int, default=DAEMON_INTERVAL)
    parser.add_argument("--max-age", type=int, default=CACHE_MAX_AGE)
    parser.add_argument("--refresh", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    commands = {
        "status": cmd_status,
        "json": cmd_json,
        "daemon": cmd_daemon,
        "statusline": cmd_statusline,
        "refresh": cmd_refresh,
        "install": cmd_install,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
