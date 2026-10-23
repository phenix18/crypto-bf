#!/usr/bin/env python3
"""Copy the trading engine into the Flet application.

The nine modules live in ``service/server`` and import each other flat. The
Flet packager only embeds what sits under the app's ``src``, so they have to be
physically present there too.

A symbolic link is not the answer — whether the packager follows one depends on
the platform — and neither is copying by hand, which works until the day
someone edits one side. So: this script copies, and a test compares the two
copies byte for byte and fails when they drift. Two engines deciding
differently from the same candles is the failure worth spending a test on.

    python scripts/sync_engine.py          # copy
    python scripts/sync_engine.py --check  # report drift, change nothing
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "service" / "server"
TARGET = ROOT / "app" / "src" / "engine"

# The closed set: these import only the standard library and each other.
# Adding a module here means checking that it brings no third-party import
# with it, because the application's whole size argument rests on that.
MODULES = (
    "indicators.py",
    "strategy_engine.py",
    "risk_manager.py",
    "calibration.py",
    "market_data.py",
    "broker.py",
    "ai_agents.py",
    "llm_client.py",
    "auto_trader.py",
)

HEADER = '''"""The trading engine, copied from service/server.

Do not edit these files here. Edit them in service/server and run:

    python scripts/sync_engine.py

A test compares the two copies and fails when they differ.
"""
'''


def differences() -> list[str]:
    """Which modules differ between the two trees, or are missing."""
    out = []
    for name in MODULES:
        source = SOURCE / name
        target = TARGET / name
        if not source.exists():
            out.append(f"{name}: missing from {SOURCE}")
        elif not target.exists():
            out.append(f"{name}: missing from {TARGET}")
        elif not filecmp.cmp(source, target, shallow=False):
            out.append(f"{name}: differs")
    return out


def sync() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "__init__.py").write_text(HEADER, encoding="utf-8")
    for name in MODULES:
        shutil.copy2(SOURCE / name, TARGET / name)
    return len(MODULES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift, change nothing")
    args = parser.parse_args()

    if args.check:
        drift = differences()
        if drift:
            print("The application's engine is out of date:", file=sys.stderr)
            for line in drift:
                print(f"  - {line}", file=sys.stderr)
            print("\nRun: python scripts/sync_engine.py", file=sys.stderr)
            return 1
        print(f"{len(MODULES)} modules in sync")
        return 0

    count = sync()
    print(f"copied {count} modules to {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
