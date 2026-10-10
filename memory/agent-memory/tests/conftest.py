"""Shared pytest configuration for the agent-memory test suite.

Besides the HOME isolation fixture this module bootstraps imports so the
suite runs unchanged in both layouts:

* repo checkout:      <repo>/memory/agent-memory/{src,tests} + <repo>/plugin
* installed copy:     ~/.hermes/agent-memory/{src,tests} + plugin under
                      ~/.hermes/plugins/agent-memory-plugin
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_PKG_DIR = Path(__file__).resolve().parent.parent  # agent-memory/ in both layouts
_SRC_DIR = _PKG_DIR / "src"                        # memory.py / text_norm.py

if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

_HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))

# Candidate locations of the plugin package, in resolution order.
_PLUGIN_CANDIDATES = (
    _PKG_DIR.parents[1] / "plugin",                  # <repo>/plugin
    _HERMES_HOME / "plugins" / "agent-memory-plugin",  # installed copy
)


def ensure_plugin_importable() -> None:
    """Make `from plugin import ...` work in either layout.

    The repo checkout ships a package literally named `plugin`, so it is
    resolved through sys.path. The installed copy is named
    `agent-memory-plugin` and is loaded under the module name `plugin`.
    """
    if "plugin" in sys.modules:
        return

    for directory in _PLUGIN_CANDIDATES:
        init = directory / "__init__.py"
        if not init.is_file():
            continue
        if directory.name == "plugin":
            parent = str(directory.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)
            return
        spec = importlib.util.spec_from_file_location("plugin", init)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load plugin from {init}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["plugin"] = module
        spec.loader.exec_module(module)
        return

    raise ModuleNotFoundError(
        "agent-memory plugin not found; looked at: "
        + ", ".join(str(d) for d in _PLUGIN_CANDIDATES)
    )


ensure_plugin_importable()


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
