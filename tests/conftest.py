"""Test plumbing: repo root + analysis/ on sys.path; fresh, restorable script imports."""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "analysis"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture
def load(monkeypatch):
    """Import a repo script fresh with a chosen sys.argv; forget it (and what it pulled in) afterwards."""
    before = set(sys.modules)

    def _load(name, *argv):
        monkeypatch.setattr(sys, "argv", [name, *argv])
        sys.modules.pop(name, None)
        return importlib.import_module(name)

    yield _load
    for k in set(sys.modules) - before:
        if k.split(".")[0] in {"collector", "request_access", "annual_refresh", "city_rain_metrics",
                               "pune_station_series", "deployment_patterns", "bengaluru_flood_road",
                               "rain_event_correlation", "imdlib"}:
            sys.modules.pop(k, None)


def run_script(path, fake_file, argv=(), monkeypatch=None):
    """Execute a top-level script as __main__ with `__file__` pointing at `fake_file`.

    The code object keeps the real path (so coverage is attributed to the real source) while the
    script's BASE = Path(__file__).parent resolves inside tmp_path. `fake_file` must have a
    different basename from the script: coverage prefers __file__ over co_filename when the two
    share a name, and would then ignore the exec'd code."""
    import runpy  # noqa: F401
    src = Path(path).read_text()
    code = compile(src, str(path), "exec")
    monkeypatch.setattr(sys, "argv", [str(path), *argv])
    g = {"__name__": "__main__", "__file__": str(fake_file)}
    exec(code, g)
    return g
