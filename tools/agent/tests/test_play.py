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
import time
import unittest
from unittest import mock
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
            (bundle / "game").mkdir()
            (bundle / "game" / "melee.exe").write_bytes(b"MZ")
            (bundle / "melee.exe").write_bytes(b"MZ")  # left over from an older zip: game\ wins
            make_phillip(bundle / "phillip-master")
            disc = Path(tmp) / "Melee.iso"
            disc.write_bytes(b"\0")
            (bundle / "ai-melee" / "settings.json").write_text(json.dumps({"disc": str(disc)}))
            got = self.probe(bundle / "ai-melee")
            self.assertEqual(Path(got["melee"]), bundle / "game" / "melee.exe")
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


class SuperviseTest(unittest.TestCase):
    """play.supervise with stand-in processes: the game and the agent are python one-liners."""

    def setUp(self):
        sys.path.insert(0, str(AGENT))
        import play
        self.play = play
        self.logs = []
        self.started = 0

    def proc(self, seconds, code=0):
        return subprocess.Popen([sys.executable, "-c", f"import sys, time; time.sleep({seconds}); sys.exit({code})"])

    def agents(self, *specs):
        """start_agent for supervise: each call starts the next (seconds, code) stand-in."""
        def start():
            self.started += 1
            return self.proc(*specs[min(self.started - 1, len(specs) - 1)])
        return start

    def supervise(self, game, start, **kw):
        t = time.monotonic()
        code = self.play.supervise(game, start, log=self.logs.append, restart_delay=0.05, **kw)
        return code, time.monotonic() - t

    def test_her_agent_leaving_first_means_the_game_is_closing(self):
        code, took = self.supervise(self.proc(1.5), self.agents((0.3, 0)), gomi=True)
        self.assertEqual((code, self.started), (0, 1), "never restarted")
        self.assertLess(took, 4)
        self.assertFalse(any("restarting" in m or "finishing her thoughts" in m for m in self.logs), self.logs)

    def test_a_crash_is_restarted(self):
        code, _ = self.supervise(self.proc(1.5), self.agents((0.2, 1), (5, 0)), gomi=True)
        self.assertEqual(self.started, 2)
        self.assertIn("play: agent exited (1); restarting it", self.logs)

    def test_without_gomi_any_exit_is_restarted_until_it_gives_up(self):
        self.supervise(self.proc(4), self.agents((0.05, 0)))
        self.assertEqual(self.started, 6, "the first and 5 restarts")
        self.assertIn("play: the agent keeps dying; the game carries on without it", self.logs)

    def test_waits_for_her_to_finish_her_thoughts(self):
        code, took = self.supervise(self.proc(0.3), self.agents((1.5, 0)), gomi=True)
        self.assertIn("play: Gomihyu is finishing her thoughts about the session...", self.logs)
        self.assertGreater(took, 1.2, "waited for her")
        self.assertLess(took, 4)

    def test_but_not_forever(self):
        code, took = self.supervise(self.proc(0.3), self.agents((60, 0)), gomi=True, wrap_up=0.5)
        self.assertLess(took, 5, "stopped after the wrap-up time")


class CssCharsTest(unittest.TestCase):
    """MELEE_AGENT_CSS_CHARS: the characters the AI's door opens with."""

    def setUp(self):
        sys.path.insert(0, str(AGENT))
        import play
        import roster
        self.play, self.roster = play, roster

    # medium-v2's probe: Falcon, Fox, Luigi, Marth, Peach, Pikachu, Popo,
    # Jigglypuff, Samus, Yoshi, Sheik, Falco.
    MEDIUM_V2 = "model: medium-v2\ntype: rl, delay 21 frames, 12 characters: ...\n" \
                "ckinds: 0,2,7,9,12,13,14,15,16,17,19,20\nload: 3.0 s; ...\n"

    def test_model_and_roster_minus_sheik(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "medium-v2"
            model.write_bytes(b"")
            model.with_name("medium-v2.probe.txt").write_text(self.MEDIUM_V2)
            ckinds = self.play.probe_slippi(None, model)  # cached: nothing is run
            for a in ("FalconFalconBF", "delay0/FoxFD", "delay0/FalcoFD", "MarthFD0", "PeachFD", "SheikFD"):
                d = Path(tmp) / "phillip" / "agents" / a
                d.mkdir(parents=True)
                (d / "params").write_text("{}")
                (d / "snapshot").write_bytes(b"\0")
            chosen, _ = self.roster.choose(Path(tmp) / "phillip")
            classic = self.roster.entries(chosen)
        names = lambda css: sorted(self.roster.display(int(c)) for c in css.split(","))  # noqa: E731
        self.assertEqual(names(self.play.css_chars(ckinds, classic)), sorted([
            "Fox", "Falco", "Peach", "Jigglypuff", "Ice Climbers", "Captain Falcon", "Ganondorf",
            "Marth", "Roy", "Pikachu", "Luigi", "Samus", "Yoshi"]))
        self.assertEqual(names(self.play.css_chars(roster_ckinds=classic)), sorted([
            "Fox", "Falco", "Peach", "Captain Falcon", "Ganondorf", "Marth", "Roy"]))
        self.assertEqual(self.play.css_chars([0x13]), "")

    def test_gomi_status_without_ollama(self):
        with mock.patch.dict(os.environ, {"GOMI_OLLAMA_URL": "http://127.0.0.1:9/api/chat"}):
            self.assertIn("on her rules", self.play.gomi_status())
        self.assertEqual(sorted(self.play.GOMI_CKINDS), [0x02, 0x08, 0x14])  # Fox, Mario, Falco

    def test_old_probe_without_ckinds_is_redone(self):
        if os.name == "nt":
            self.skipTest("a shell script stands in for the slippi-env python")
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "m"
            model.with_name("m.probe.txt").write_text("model: m\ntype: rl\nload: 1 s\n")
            fake = Path(tmp) / "python"
            fake.write_text("#!/bin/sh\necho 'ckinds: 2'\n")
            fake.chmod(0o755)
            self.assertEqual(self.play.probe_slippi(fake, model), [2])


if __name__ == "__main__":
    unittest.main()
