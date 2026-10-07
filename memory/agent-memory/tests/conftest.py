"""Shared pytest configuration for the agent-memory test suite."""

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point the home directory at a temp dir for every test.

    `AgentMemory()` and the plugin default to `~/.hermes/agent-memory/memory.db`.
    Without this, a test that reaches the default path opens the developer's
    real memory database and runs schema migrations against it.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
