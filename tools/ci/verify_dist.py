"""Statically verify built distributions contain the expected package payload.

Run from the repository root after `python -m build`. Exits nonzero on failure.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

DIST = Path("dist")

EXPECTED_MODULES = {
    "black_market_sdk/__init__.py",
    "black_market_sdk/addresses.py",
    "black_market_sdk/abis.py",
    "black_market_sdk/abyss.py",
    "black_market_sdk/abyss_abis.py",
    "black_market_sdk/auction.py",
    "black_market_sdk/client.py",
    "black_market_sdk/format.py",
    "black_market_sdk/launch.py",
    "black_market_sdk/launch_api.py",
    "black_market_sdk/lifecycle.py",
    "black_market_sdk/lifecycle_abis.py",
    "black_market_sdk/lifecycle_rpc.py",
    "black_market_sdk/live.py",
    "black_market_sdk/py.typed",
}


def fail(message: str) -> None:
    print(f"dist verification failed: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))
    if not wheels:
        fail("no wheel found in dist/")
    if not sdists:
        fail("no sdist found in dist/")

    wheel = wheels[-1]
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    missing = EXPECTED_MODULES - names
    if missing:
        fail(f"wheel {wheel.name} is missing: {sorted(missing)}")
    metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
    if not metadata:
        fail(f"wheel {wheel.name} has no dist-info/METADATA")
    license_files = [n for n in names if ".dist-info/licenses/" in n or n.endswith("LICENSE")]
    if not license_files:
        fail(f"wheel {wheel.name} ships no LICENSE file")
    with zipfile.ZipFile(wheel) as zf:
        meta = zf.read(metadata[0]).decode()
    for field in ("Name: black-market-sdk", "License-Expression: MIT", "Requires-Python: >=3.10"):
        if field not in meta:
            fail(f"wheel METADATA missing {field!r}")

    sdist = sdists[-1]
    with tarfile.open(sdist) as tf:
        sdist_names = tf.getnames()
    for required in ("LICENSE", "README.md", "pyproject.toml", "docs/launch-guide.md", "assets/abyss-social.webp"):
        if not any(n.endswith(f"/{required}") for n in sdist_names):
            fail(f"sdist {sdist.name} is missing {required}")

    print(f"dist verification ok: {wheel.name}, {sdist.name}")


if __name__ == "__main__":
    main()
