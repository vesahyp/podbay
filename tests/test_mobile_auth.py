"""The phone app stays signed in (mobile/app/auth.js): an expired access
token is renewed from the refresh token with no sign-in, and only Cognito's
refusal of the refresh token ends the login. The check runs in Node."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_expired_access_token_is_renewed_without_a_sign_in():
    # conftest stubs subprocess.run, so the check starts through Popen.
    with subprocess.Popen(["node", str(ROOT / "scripts" / "auth_check.mjs")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
        out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 0, out
