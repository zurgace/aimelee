"""launcher.py: play.py's arguments, the status lines, the shortcuts, and
(with Tk and Xvfb) the window running a fake play.py to the end."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))

import launcher  # noqa: E402


class OptionsTest(unittest.TestCase):
    def test_defaults_and_play_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            disc = Path(tmp) / "GALE01.iso"
            disc.write_bytes(b"")
            opts = launcher.load_options({}, environ={"MELEE_DISC": str(disc)})
            self.assertEqual(opts, {"brain": "auto", "p2_pick": True, "legal_stages": True, "gomi": False,
                                    "disc": str(disc)})
            self.assertEqual(launcher.play_args(opts), ["--iso", str(disc), "--brain", "auto",
                                                        "--p2-pick", "on", "--random-stages", "legal"])
            opts.update(brain="classic", p2_pick=False, legal_stages=False)
            settings = launcher.store_options({"other": 1}, opts)
            self.assertEqual(settings["launcher"], {"brain": "classic", "p2_pick": False, "legal_stages": False,
                                                 "gomi": False})
            self.assertEqual(settings["disc"], str(disc.resolve()))  # the key play.py reads too
            again = launcher.load_options(settings, environ={})
            self.assertEqual(launcher.play_args(again)[2:], ["--brain", "classic", "--p2-pick", "off",
                                                             "--random-stages", "all"])

    def test_gomi_option(self):
        opts = launcher.load_options({"launcher": {"gomi": True}}, environ={})
        self.assertEqual(launcher.play_args(opts)[-1], "--gomi")

    def test_missing_disc_and_bad_brain(self):
        opts = launcher.load_options({"disc": "/nonexistent.iso", "launcher": {"brain": "x"}}, environ={})
        self.assertIsNone(opts["disc"])
        self.assertEqual(opts["brain"], "auto")
        self.assertNotIn("--iso", launcher.play_args(opts))


class StatusTest(unittest.TestCase):
    def check(self, line, want, brain="auto"):
        self.assertEqual(launcher.status_for(line, brain), want, line)

    def test_log_lines(self):
        self.check("play: exporting FalconFalconBF from Phillip's checkpoints with TensorFlow 2.13.",
                   ("setup", "Converting the 2017 agents (first time only: a few minutes)..."))
        self.check("play: agents: Captain Falcon, Peach (+ Ganondorf as stand-ins)",
                   ("agents", "2017 agents: Captain Falcon, Peach (+ Ganondorf as stand-ins)"))
        self.check("play: slippi-ai type: rl, delay 21 frames, 2 characters: Falco, Fox",
                   ("model", "Newer Phillip plays: Falco, Fox"))
        self.check("play: /x/build/melee with Phillip's agents on P2; disc /x/GALE01.iso",
                   ("game", "Game running. Close the game window to stop."))
        self.check("[    1.950] agent: client connected (#1)", ("ai", "Loading the AI model..."))
        self.check("[    1.950] agent: client connected (#1)",
                   ("ai", "AI ready: pick your characters and play"), brain="classic")
        self.check("slippi: model ready (warm-up 4.2 s)", ("ai", "AI ready: pick your characters and play"))
        self.check("[   20.113] agent: P2 opens as Ice Climbers (random among the 13 characters an AI plays)",
                   ("match", "P2 opens as Ice Climbers"))
        self.check("slippi: Falco: slippi-ai plays P2", ("match", "This match: Falco: the newer Phillip plays P2"))
        self.check("slippi: no AI plays Mario; P2 stands still this match. slippi-ai plays: ...",
                   ("match", "This match: no AI plays Mario"))
        self.check("agent: no Phillip agent plays Mario; the port stands still this match.",
                   ("match", "This match: no Phillip agent plays Mario"))
        self.check("agent: Fox: delay0/FoxFD", ("match", "This match: the 2017 agent plays Fox"))
        self.check("agent: Ganondorf: no Phillip agent, Captain Falcon's (FalconFalconBF) stands in",
                   ("match", "This match: the 2017 agent plays Ganondorf"))

    def test_gomi_lines(self):
        self.check("gomi: Gomihyu plays Mario vs Fox (gemma4:e4b; 3 lessons, 2 matches played)",
                   ("match", "This match: Gomihyu plays Mario vs Fox"))
        self.check('gomi: Gomi: "Kneel before my fireballs!"', ("gomi", 'Gomi: "Kneel before my fireballs!"'))
        self.check('gomi: after the match: "I let you win."', ("gomi", 'Gomi: "I let you win."'))
        self.check("gomi: match over: lost 0-2 stocks vs Fox; best plan so far fireball, worst approach",
                   ("result", "Gomihyu lost 0-2 stocks vs Fox; best plan so far fireball, worst approach"))
        self.assertIsNone(launcher.status_for("gomi: plan: fireball"))
        self.check("gomi: 3 of her Discord posts are still waiting: is her gomihyu bot running? (it posts from /x)",
                   ("discord", "Discord: 3 of her posts are waiting. Is her gomihyu bot running?"))
        self.check("gomi: Gomihyu plays Fox vs Marth (gemma4:e4b; 0 lessons, 0 matches played)",
                   ("match", "This match: Gomihyu plays Fox vs Marth"))
        self.check("agent: Gomihyu doesn't play Peach; the port stands still this match. Pick Mario or Fox",
                   ("match", "This match: Gomihyu doesn't play Peach (pick Mario or Fox for P2)"))
        self.check("[    1.950] agent: client connected (#1)",
                   ("ai", "AI ready: pick your characters and play"), brain="gomi")

    def test_noise_is_ignored(self):
        for line in ("agent: roster: Fox, Falco", "[    3.0] agent: driving port 2 from tick 812",
                     "agent: WARNING: FoxFD trained on final_destination; expect odd play",
                     "agent: match start on battlefield, agent on P2", "some TensorFlow warning"):
            self.assertIsNone(launcher.status_for(line), line)

    def test_failure_text(self):
        self.assertEqual(launcher.failure_text(["x", "play: no disc image.", "", "Traceback"]), "no disc image.")
        self.assertIsNone(launcher.failure_text(["nothing"]))


class ShortcutTest(unittest.TestCase):
    def test_entry_quotes_paths(self):
        entry = launcher.desktop_entry("/usr/bin/python3", "/home/a b/ai-melee/tools/agent/launcher.py", "/i.png")
        self.assertIn('Exec=/usr/bin/python3 "/home/a b/ai-melee/tools/agent/launcher.py"\n', entry)
        self.assertIn("Terminal=false\n", entry)
        self.assertIn("StartupWMClass=ai-melee\n", entry)

    def test_install_and_uninstall(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "Desktop").mkdir()
            env = {"XDG_DATA_HOME": str(Path(home) / "data"), "PATH": "/nonexistent"}  # no xdg-user-dir, gio
            with mock.patch.dict(os.environ, env):
                written = launcher.install(home, python="/usr/bin/python3")
                self.assertEqual(written, [Path(home) / "data" / "applications" / "ai-melee.desktop",
                                           Path(home) / "Desktop" / "ai-melee.desktop"])
                for p in written:
                    self.assertTrue(os.access(p, os.X_OK))
                    self.assertIn(f"Icon={launcher.ICON}", p.read_text())
                if shutil.which("desktop-file-validate", path="/usr/bin:/bin"):
                    r = subprocess.run(["/usr/bin/desktop-file-validate", str(written[0])],
                                       capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(launcher.uninstall(home), written)
                self.assertFalse(any(p.exists() for p in written))


WINDOW_RUN = textwrap.dedent("""
    import sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    tmp = Path(sys.argv[2])
    import play, launcher
    play.SETTINGS = tmp / "settings.json"
    launcher.PLAY = tmp / "fake_play.py"
    launcher.LOG = tmp / "ai-melee.log"
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    l = launcher.Launcher(tk, ttk, filedialog, messagebox, scrolledtext)
    l.opts["disc"] = str(tmp / "GALE01.iso")
    seen = []
    orig = l.set_status
    l.set_status = lambda slot, text: (seen.append(text), orig(slot, text))
    def done():
        orig_finished()
        l.root.update()
        print("STATUS", seen, flush=True)
        print("OPTIONS_BACK", l.options.winfo_ismapped(), flush=True)
        l.root.destroy()
    orig_finished = l.finished
    l.finished = done
    l.root.after(200, l.play)
    l.root.after(20000, l.root.destroy)
    l.root.mainloop()
""")


@unittest.skipIf(importlib.util.find_spec("tkinter") is None or not shutil.which("xvfb-run"),
                 "needs tkinter and xvfb-run")
class WindowTest(unittest.TestCase):
    def test_play_shows_status_then_returns_to_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "GALE01.iso").write_bytes(b"")
            (Path(tmp) / "fake_play.py").write_text(
                "import sys\nassert '--iso' in sys.argv\n"
                "print('play: /x/melee with Phillip\\'s agents on P2; disc /x/GALE01.iso')\n"
                "print('slippi: model ready (warm-up 1.0 s)')\n")
            r = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", WINDOW_RUN, str(AGENT), tmp],
                               capture_output=True, text=True, timeout=60)
            self.assertIn("AI ready: pick your characters and play", r.stdout, r.stderr[-2000:])
            self.assertIn("OPTIONS_BACK 1", r.stdout)
            self.assertIn("model ready", (Path(tmp) / "ai-melee.log").read_text())


if __name__ == "__main__":
    unittest.main()
