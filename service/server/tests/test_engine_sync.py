"""The copy of the engine that ships inside the application.

Nine modules exist twice: in service/server, where the API imports them, and
in app/src/engine, where the Flet packager can reach them. Two engines
deciding differently from the same candles is the failure this file exists to
prevent, and it is the kind that produces no error — just two answers.

These also pin the property the whole application rests on: the engine imports
nothing outside the standard library. The moment one of them grows a pandas
import, the APK gains 55 MB and the argument for embedding it at all
collapses.
"""

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
ROOT = SERVER_DIR.parent.parent
SCRIPT = ROOT / "scripts" / "sync_engine.py"

sys.path.insert(0, str(ROOT / "scripts"))
from sync_engine import MODULES, SOURCE, TARGET, differences  # noqa: E402

# Everything the engine is allowed to import. Standard library only, plus the
# engine's own modules.
ALLOWED = {
    "argparse", "dataclasses", "datetime", "enum", "hashlib", "hmac", "json",
    "math", "os", "random", "time", "typing", "urllib", "collections",
    "functools", "itertools", "statistics", "decimal", "secrets", "re",
    "__future__",
} | {name[:-3] for name in MODULES}


class SyncTests(unittest.TestCase):
    def test_the_application_carries_the_same_engine(self):
        drift = differences()
        self.assertEqual(
            drift,
            [],
            "the app's engine has drifted; run: python scripts/sync_engine.py",
        )

    def test_the_check_mode_agrees(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_check_mode_notices_a_difference(self):
        # Guard against a check that passes because it compares nothing.
        target = TARGET / "indicators.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# drift\n")
            self.assertIn("indicators.py: differs", differences())
        finally:
            target.write_bytes(original)
        self.assertEqual(differences(), [])

    def test_every_module_is_present_on_both_sides(self):
        for name in MODULES:
            with self.subTest(module=name):
                self.assertTrue((SOURCE / name).exists(), f"{name} missing from the server")
                self.assertTrue((TARGET / name).exists(), f"{name} missing from the app")


class PurityTests(unittest.TestCase):
    """The engine imports nothing but the standard library.

    This is not a style rule. It is why the engine weighs 176 KB, why it can be
    embedded in an APK at all, and why the application's dependency list is one
    line long.
    """

    def imports_of(self, path: Path) -> set:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found

    def test_no_module_reaches_outside_the_standard_library(self):
        for name in MODULES:
            with self.subTest(module=name):
                outside = self.imports_of(SOURCE / name) - ALLOWED
                self.assertEqual(
                    outside,
                    set(),
                    f"{name} imports {sorted(outside)}; the APK's size argument "
                    f"depends on this staying empty",
                )

    def test_the_purity_check_would_catch_an_offender(self):
        # The same guard as above: a check that inspects nothing passes
        # everything.
        found = self.imports_of(SOURCE / "auto_trader.py")
        self.assertIn("dataclasses", found)
        self.assertIn("calibration", found)
        self.assertGreaterEqual(len(found), 8)

    def test_the_engine_runs_with_the_standard_library_alone(self):
        # Isolated mode (-I) ignores PYTHONPATH and the user site directory, so
        # this fails if anything is quietly coming from the project's venv.
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from auto_trader import build_auto_trader\n"
            "from market_data import SyntheticSource\n"
            "bot = build_auto_trader(source=SyntheticSource(), equity=1000.0)\n"
            "print(bot.evaluate('BTCUSDT', '5m').stage)\n"
        ) % str(TARGET)
        result = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(result.stdout.strip(), {"strategy", "risk", "calibration", "council", "approved"})


if __name__ == "__main__":
    unittest.main()
