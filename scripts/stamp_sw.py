"""Write the shell's content hash into mobile/app/sw.js as its cache version.

A service worker is only replaced when its own bytes change. The shell cache
name lives in sw.js, so a deploy that changed app.js but not sw.js left the
phone on the old shell. The version is now a hash of every file the app ships
except sw.js itself and config.js (written at deploy from Terraform). `make
mobile-deploy` runs this first; tests/test_mobile_sw.py fails when the
committed sw.js is stale.

    python3 scripts/stamp_sw.py          stamp sw.js
    python3 scripts/stamp_sw.py --check  exit 1 when the stamp is stale
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "mobile" / "app"
SKIP = {"sw.js", "config.js"}
LINE = re.compile(r'^var CACHE = "podbay-shell-[0-9a-f]+";$', re.M)


def shell_hash(app: Path = APP) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in app.rglob("*") if p.is_file() and p.name not in SKIP and not p.name.startswith(".")):
        digest.update(str(path.relative_to(app)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def stamped(source: str, version: str) -> str:
    out, n = LINE.subn(f'var CACHE = "podbay-shell-{version}";', source)
    if n != 1:
        raise SystemExit('sw.js has no line: var CACHE = "podbay-shell-<hex>";')
    return out


def main() -> int:
    sw = APP / "sw.js"
    source = sw.read_text()
    new = stamped(source, shell_hash())
    if "--check" in sys.argv:
        return 0 if new == source else 1
    if new != source:
        sw.write_text(new)
    print("sw.js cache", shell_hash())
    return 0


if __name__ == "__main__":
    sys.exit(main())
