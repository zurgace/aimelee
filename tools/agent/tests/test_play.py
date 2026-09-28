"""play.py's discovery of the game, Phillip and the disc, in both layouts.

The Windows download is a folder with melee.exe at the top, the agent tools
in ai-melee/ and Phillip extracted next to them (GitHub's "Download ZIP"
names it phillip-master). A checkout has build/melee and ../phillip. Runs
play.py's helpers from a copy of the tools placed in each layout.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent

PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import play
out = {"melee": str(play.find_melee(None)),
       "phillip": str(play.find_phillip(None, "FalconFalconBF")),
       "disc": str(play.find_disc(None, play.load_settings()))}
print(json.dumps(out))
"""


def copy_tools(dst):
    dst.mkdir(parents=True)
    for f in AGENT.glob("*.py"):
        shutil.copy(f, dst / f.name)


def make_phillip(d):
    (d / "agents" / "FalconFalconBF").mkdir(parents=True)
    (d / "agents" / "FalconFalconBF" / "params").write_text('{"char": "falcon"}')


class PlayDiscoveryTest(unittest.TestCase):
    def probe(self, tools, env_disc=None):
        env = {k: v for k, v in os.environ.items() if k != "MELEE_DISC"}
        if env_disc:
            env["MELEE_DISC"] = env_disc
        r = subprocess.run([sys.executable, "-c", PROBE, str(tools)], capture_output=True, text=True,
                           env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_windows_bundle_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "AI-Melee"
            copy_tools(bundle / "ai-melee")
            (bundle / "melee.exe").write_bytes(b"MZ")
            make_phillip(bundle / "phillip-master")
            disc = Path(tmp) / "Melee.iso"
            disc.write_bytes(b"\0")
            (bundle / "ai-melee" / "settings.json").write_text(json.dumps({"disc": str(disc)}))
            got = self.probe(bundle / "ai-melee")
            self.assertEqual(Path(got["melee"]), bundle / "melee.exe")
            self.assertEqual(Path(got["phillip"]), bundle / "phillip-master")
            self.assertEqual(Path(got["disc"]), disc)  # remembered from last time

    def test_phillip_extracted_with_extract_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "AI-Melee"
            copy_tools(bundle / "ai-melee")
            (bundle / "melee.exe").write_bytes(b"MZ")
            make_phillip(bundle / "phillip-master" / "phillip-master")
            disc = Path(tmp) / "Melee.iso"
            disc.write_bytes(b"\0")
            got = self.probe(bundle / "ai-melee", env_disc=str(disc))
            self.assertEqual(Path(got["phillip"]), bundle / "phillip-master" / "phillip-master")

    def test_checkout_layout_and_disc_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "ai-melee"
            copy_tools(repo / "tools" / "agent")
            (repo / "build").mkdir()
            (repo / "build" / "melee").write_bytes(b"\x7fELF")
            make_phillip(Path(tmp) / "phillip")
            saved = Path(tmp) / "saved.iso"
            saved.write_bytes(b"\0")
            env = Path(tmp) / "env.iso"
            env.write_bytes(b"\0")
            (repo / "tools" / "agent" / "settings.json").write_text(json.dumps({"disc": str(saved)}))
            got = self.probe(repo / "tools" / "agent", env_disc=str(env))
            self.assertEqual(Path(got["melee"]), repo / "build" / "melee")
            self.assertEqual(Path(got["phillip"]), Path(tmp) / "phillip")
            self.assertEqual(Path(got["disc"]), env)  # MELEE_DISC beats the remembered one

    def test_missing_phillip_says_where_to_get_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "AI-Melee"
            copy_tools(bundle / "ai-melee")
            (bundle / "melee.exe").write_bytes(b"MZ")
            r = subprocess.run([sys.executable, "-c",
                                "import sys; sys.path.insert(0, sys.argv[1]); import play; "
                                "play.find_phillip(None, 'FalconFalconBF')", str(bundle / "ai-melee")],
                               capture_output=True, text=True, timeout=30)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("github.com/vladfi1/phillip", r.stderr)
            self.assertIn("Download ZIP", r.stderr)


if __name__ == "__main__":
    unittest.main()
