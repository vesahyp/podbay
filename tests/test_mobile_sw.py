"""The phone app's shell cache version must change with the shell."""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("stamp_sw", ROOT / "scripts" / "stamp_sw.py")
stamp_sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stamp_sw)


def test_committed_cache_version_matches_the_shell():
    source = (stamp_sw.APP / "sw.js").read_text()
    version = re.search(r'var CACHE = "podbay-shell-([0-9a-f]+)";', source).group(1)
    assert version == stamp_sw.shell_hash(), "run: python3 scripts/stamp_sw.py"


def test_changing_a_shell_file_changes_the_version(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(stamp_sw.APP, app)
    before = stamp_sw.shell_hash(app)
    (app / "app.js").write_text((app / "app.js").read_text() + "\n// edit\n")
    assert stamp_sw.shell_hash(app) != before


def test_sw_and_config_do_not_change_the_version(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(stamp_sw.APP, app)
    before = stamp_sw.shell_hash(app)
    (app / "config.js").write_text("window.PODBAY = {};\n")
    (app / "sw.js").write_text("// other\n")
    assert stamp_sw.shell_hash(app) == before
