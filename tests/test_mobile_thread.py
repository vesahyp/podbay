"""The phone's chat is in time order (mobile/app/thread.js, docs/adr/0002
Update 2026-11-08). The check runs in Node over tests/fixtures/mobile."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_chat_is_in_time_order():
    # conftest stubs subprocess.run, so the check starts through Popen.
    with subprocess.Popen(["node", str(ROOT / "scripts" / "thread_check.mjs")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
        out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 0, out
