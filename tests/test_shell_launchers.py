"""Exercise real launcher boundaries using disposable, offline Python fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = [["run_scraper.sh","scrape_images.py",[]],["setup.sh","pip.py",["install","-r","requirements.txt"]]]


@unittest.skipIf(os.name == "nt", "POSIX launchers are exercised on Linux")
class LauncherContractTests(unittest.TestCase):
    def test_paths_arguments_and_exit_status(self):
        for wrapper_name, entry_name, expected_prefix in WRAPPERS:
            for exit_code in (0, 37):
                with self.subTest(wrapper=wrapper_name, exit_code=exit_code):
                    if wrapper_name.endswith(".sh") and os.name == "nt":
                        continue
                    if wrapper_name.endswith(".bat") and os.name != "nt":
                        continue
                    with tempfile.TemporaryDirectory(prefix="launcher contract ") as temp:
                        root = Path(temp) / "repo with spaces"
                        root.mkdir()
                        wrapper = root / wrapper_name
                        shutil.copyfile(ROOT / wrapper_name, wrapper)
                        fixture = (
                            "import json, os, sys\n"
                            "from pathlib import Path\n"
                            "print(json.dumps({'cwd': str(Path.cwd()), 'args': sys.argv[1:]}))\n"
                            "raise SystemExit(int(os.environ['FIXTURE_EXIT']))\n"
                        )
                        (root / entry_name).write_text(fixture, encoding="utf-8")
                        env = dict(os.environ, PYTHON=sys.executable,
                                   PYTHONPATH=str(root), FIXTURE_EXIT=str(exit_code))
                        args = ["argument with spaces", "literal-$value"]
                        if wrapper_name.endswith(".bat"):
                            command = ["cmd", "/d", "/c", str(wrapper), *args]
                        else:
                            command = ["sh", str(wrapper), *args]
                        result = subprocess.run(command, cwd=temp, env=env,
                                                capture_output=True, text=True, timeout=15)
                        self.assertEqual(result.returncode, exit_code, result.stderr)
                        observed = json.loads(result.stdout)
                        self.assertEqual(Path(observed["cwd"]), root)
                        self.assertEqual(observed["args"], expected_prefix + args)


if __name__ == "__main__":
    unittest.main()
