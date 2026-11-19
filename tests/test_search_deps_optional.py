"""The weather workflow (update.yml) installs only requirements.txt and then runs every test; a failed
test stops the weather data from being published. A test that needs a search-only package must skip
there (pytest.importorskip), not fail. On 2026-11-18 tests/routing/test_routing_main.py did not, and the
weather data stopped for four GFS cycles: every local run had all packages installed, so nothing
noticed. This guard hides the search-only packages and runs the suite again, so the same mistake fails
locally first."""

from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
# PyPI name -> import name of every package that only requirements-search.txt installs.
SEARCH_ONLY_IMPORTS = {"osmium": "osmium", "zstandard": "zstandard"}

HIDE_AND_RUN = """
import importlib.abc
import sys

HIDDEN = set(sys.argv[1:])


class Hide(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in HIDDEN:
            raise ModuleNotFoundError(f'No module named {name!r}', name=name)
        return None


sys.meta_path.insert(0, Hide())
import pytest

sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', '--no-header', 'tests']))
"""


def _locked_names(path: Path) -> set[str]:
    return {m.group(1).lower() for m in re.finditer(r"^([A-Za-z0-9_.-]+)==", path.read_text(encoding="utf-8"), re.M)}


def test_the_list_of_search_only_packages_is_current():
    search_only = _locked_names(ROOT / "requirements-search.txt") - _locked_names(ROOT / "requirements.txt")
    assert search_only == set(SEARCH_ONLY_IMPORTS)


def test_suite_passes_without_the_search_only_packages():
    for module in SEARCH_ONLY_IMPORTS.values():
        # Without them, this run already is the weather workflow's, and so is the re-run below,
        # where this test skips itself here instead of starting another one.
        pytest.importorskip(module)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", HIDE_AND_RUN, *SEARCH_ONLY_IMPORTS.values()],
        cwd=ROOT, capture_output=True, text=True, timeout=600, check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
