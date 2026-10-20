"""Test fixtures shared across the camas-kernel suite.

Ensure HMAC_SECRET is present so token-derivation helpers (which now fail loud
when it is unset in production) work deterministically in the focused suite.
"""
import os

os.environ.setdefault(
    "HMAC_SECRET", "test-only-hmac-secret-deterministic-0123456789abcdef"
)

# D1: offline pytest harness. Standalone scripts under tools/ are manual jobs.
import functools
import inspect
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest


def pytest_configure(config):
    config.option.asyncio_mode = "auto"
    config.option.ignore = list(config.option.ignore or []) + [
        str(Path(__file__).resolve().parents[1] / "tools")
    ]


def pytest_collection_modifyitems(items):
    external = {
        "test_keyword_extraction": "Requires real LLM API; offline suite does not call paid APIs",
        "test_ghl_client_basic": "Requires real GHL API and may create custom fields",
        "test_agent_dry_run": "Requires real GHL contacts; dry_run still reads external services",
    }
    for item in items:
        if item.name in external:
            item.add_marker(pytest.mark.skip(reason=external[item.name]))
        # Legacy script tests return bool. Pytest ignores False unless asserted.
        fn = item.obj
        if inspect.signature(fn).return_annotation in (bool, "bool"):
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def checked(*args, __fn=fn, **kwargs):
                    assert await __fn(*args, **kwargs) is True
            else:
                @functools.wraps(fn)
                def checked(*args, __fn=fn, **kwargs):
                    assert __fn(*args, **kwargs) is True
            item.obj = checked


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    # No ambient credentials, even if a legacy module loaded a nearby .env.
    for name in tuple(os.environ):
        if name.endswith(("_API_KEY", "_TOKEN", "_DATABASE_URL")) or name in {
            "DATABASE_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN", "HANGOS_ENABLE_BEDROCK", "HANGOS_ALLOW_PAID_LLM",
        }:
            monkeypatch.delenv(name, raising=False)
    attempts = []

    def deny_network(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("Offline pytest forbids network/DB connections")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    yield
    assert not attempts, "Test attempted external I/O; use a scoped mock or explicit integration skip"


@pytest.fixture
def agent(monkeypatch):
    """Real cron agent; exercise event rejection and no-DB dry-run fallback only."""
    from agents.cron_amem_weekly import CronAmemWeekly

    monkeypatch.setenv("CRON_AMEM_WEEKLY_DRY_RUN", "1")
    return CronAmemWeekly(
        llm=SimpleNamespace(ready=False), memory=SimpleNamespace(dsn=None)
    )


@pytest.fixture
def dsn():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("Requires TEST_DATABASE_URL for an isolated test database")
    pytest.skip("Offline suite: DB integration requires a separately authorized test-DB run")


@pytest.fixture
def memory(dsn):
    pytest.skip("Requires isolated test DB plus Voyage API; no fake semantic-search pass")


@pytest.fixture
def memory_ids(dsn):
    pytest.skip("Cleanup belongs to the separately authorized DB integration lifecycle")
