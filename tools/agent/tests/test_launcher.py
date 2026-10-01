"""launcher.py: play.py's arguments, the status lines, the shortcuts, and
(with Tk and Xvfb) the window running a fake play.py to the end."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
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
                                    "box": False, "disc": str(disc), "replays": None})
            self.assertEqual(launcher.play_args(opts), ["--iso", str(disc), "--brain", "auto",
                                                        "--p2-pick", "on", "--random-stages", "legal",
                                                        "--box-controller", "off"])
            opts.update(brain="classic", p2_pick=False, legal_stages=False)
            settings = launcher.store_options({"other": 1}, opts)
            self.assertEqual(settings["launcher"], {"brain": "classic", "p2_pick": False, "legal_stages": False,
                                                 "gomi": False, "box": False})
            self.assertEqual(settings["disc"], str(disc.resolve()))  # the key play.py reads too
            again = launcher.load_options(settings, environ={})
            self.assertEqual(launcher.play_args(again)[2:], ["--brain", "classic", "--p2-pick", "off",
                                                             "--random-stages", "all", "--box-controller", "off"])

    def test_replay_folder_is_remembered(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = launcher.load_options({}, environ={})
            opts["replays"] = tmp
            settings = launcher.store_options({}, opts)
            self.assertEqual(settings["gomi_replays"], str(Path(tmp).resolve()), "the key play.py reads")
            self.assertEqual(launcher.load_options(settings, environ={})["replays"], str(Path(tmp).resolve()))
        self.assertIsNone(launcher.load_options(settings, environ={})["replays"], "gone: forgotten")

    def test_gomi_option(self):
        opts = launcher.load_options({"launcher": {"gomi": True}}, environ={})
        self.assertIn("--gomi", launcher.play_args(opts))
        opts = launcher.load_options({"launcher": {"box": True}}, environ={})
        self.assertEqual(launcher.play_args(opts)[-2:], ["--box-controller", "on"])

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
                   ("game", "Game running. Press Esc or close the game window to stop."))
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
        self.check("play: Gomihyu is finishing her thoughts about the session...",
                   ("game", "Game closed; Gomi is finishing her thoughts about the session..."))
        self.check("gomi: session over: 3 matches; her Discord bot posts about it now",
                   ("discord", "Discord: session over (3 played); her bot posts about it now"))
        self.check('gomi: next match she works on: "Never fall again." (recovery: recover earlier and higher)',
                   ("goal", 'Gomi is working on: "Never fall again."'))
        self.check('gomi: her practice: "Never fall again." (stocks lost offstage: 2 -> 0, better)',
                   ("goal", 'Her practice: "Never fall again." (stocks lost offstage: 2 -> 0, better)'))
        self.check("gomi: 3 of her Discord posts are still waiting: is her gomihyu bot running? (it posts from /x)",
                   ("discord", "Discord: 3 of her posts are waiting. Is her gomihyu bot running?"))
        self.check("gomi: Gomihyu plays Fox vs Marth (gemma4:e4b; 0 lessons, 0 matches played)",
                   ("match", "This match: Gomihyu plays Fox vs Marth"))
        self.check("agent: Gomihyu doesn't play Peach; the port stands still this match. Pick Mario, Fox or Falco",
                   ("match", "This match: Gomihyu doesn't play Peach (pick Mario, Fox or Falco for P2)"))
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


class TrainedTest(unittest.TestCase):
    """Her Falco trained on the user's replays (gomi_train.py): the launcher's line and command."""

    def test_the_line(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(launcher.trained_text(tmp), "not trained yet: she plays Falco on her own rules")
            (Path(tmp) / "falco").mkdir()
            info = {"player": "ILYJ#309", "name": "Uni", "games": 17, "loss": 3.94, "base_loss": 18.61}
            (Path(tmp) / "falco" / "model.json").write_text(json.dumps(info))
            self.assertEqual(launcher.trained_text(tmp), "plays like Uni (17 games, 79% closer); more training helps")
            (Path(tmp) / "falco" / "model.json").write_text(json.dumps(dict(info, done=True)))
            self.assertEqual(launcher.trained_text(tmp), "plays like Uni (17 games, 79% closer)")
            self.assertEqual(launcher.train_button_text(tmp), "Train on my replays")
            # Paused (or cut off): where it is, and the button picks up there.
            train = Path(tmp) / "falco" / "train"
            train.mkdir()
            (train / "latest.pkl").write_bytes(b"x")
            (train / "progress.json").write_text(json.dumps({"step": 1000, "steps": 4480, "seconds_per_step": 6.5,
                                                             "paused": True}))
            self.assertEqual(launcher.trained_text(tmp), "paused at 22% (step 1000/4480), about 6 h 17 min left: "
                                                         "Resume training picks up there")
            self.assertEqual(launcher.train_button_text(tmp), "Resume training")
            (train / "latest.pkl").unlink()
            self.assertEqual(launcher.train_button_text(tmp), "Train on my replays", "nothing to pick up")

    def test_hours(self):
        self.assertEqual(launcher.duration(45.4), "45 min")
        self.assertEqual(launcher.duration(380), "6 h 20 min")
        self.assertEqual(launcher.training_text((140, 4480, 470), "x", 5, 1),
                         "Training: 3% (step 140/4480), about 7 h 50 min left, running 0:05")

    def test_status_while_it_plays(self):
        self.assertEqual(launcher.status_for("play: her Falco is the network trained on Uni's replays (17 games); "
                                             "she watches it play", "gomi"),
                         ("gomi", "Her Falco plays like Uni (trained on 17 games)"))
        self.assertEqual(launcher.status_for("gomi: Gomihyu's Falco vs Fox is the network trained on Uni's replays "
                                             "(17 games); she watches", "gomi"),
                         ("match", "This match: Falco vs Fox, played like Uni; Gomi watches"))
        self.assertEqual(launcher.status_for("slippi: Gomihyu's Falco ready (warm-up 6.1 s)", "gomi")[0], "ai")

    def test_progress_and_the_live_line(self):
        self.assertEqual(launcher.train_progress("gomi-train: step 140/4480 (3%), about 95 min to go"), (140, 4480, 95))
        self.assertEqual(launcher.train_progress("gomi-train: step 200/4480: 0.98 error on your held-out games "
                                                 "(best 0.98, before 1.05); about 90 min to go"), (200, 4480, 90))
        self.assertIsNone(launcher.train_progress("gomi-train: checking her on your held-out games..."))
        self.assertEqual(launcher.training_text((140, 4480, 95), "x", 754, 5),
                         "Training: 3% (step 140/4480), about 1 h 35 min left, running 12:34")
        self.assertEqual(launcher.training_text((140, 4480, 95), "x", 754, 5, pausing=True),
                         "Pausing at 3%: saving where it is, running 12:34")
        self.assertEqual(launcher.training_text(None, "parsed 50 new replays (13s)", 3725, 1),
                         "Training: parsed 50 new replays (13s), running 1:02:05")
        self.assertEqual(launcher.training_text((140, 4480, 95), "x", 900, 200),
                         "Training: 3% (step 140/4480), about 1 h 35 min left, running 15:00 (no word from it for 3 min)")

    def test_tensorflow_chatter_is_left_out(self):
        for line in ("I0000 00:00:1790853862.785505   24807 cudart_stub.cc:31] Could not find cuda drivers on your "
                     "machine, GPU will not be used.",
                     "I0000 00:00:1790854716.245037     842 port.cc:153] oneDNN custom operations are on.",
                     "WARNING: All log messages before absl::InitializeLog() is called are written to STDERR",
                     "WARNING:tensorflow:From x.py:48: The name tf.losses.x is deprecated.", ""):
            self.assertTrue(launcher.TF_CHATTER.match(line), line)
        for line in ("gomi-train: before: 1.05 error on your held-out games", "Traceback (most recent call last):",
                     "E0000 00:00:1790853862.1 24807 cuda_platform.cc:52] failed call to cuInit",
                     "ValueError: Batch size 16 is not divisible by minibatch size 128"):
            self.assertFalse(launcher.TF_CHATTER.match(line), line)

    def test_the_command(self):
        cmd = launcher.train_command("/env/python", "/replays", "/gomi")
        self.assertEqual(cmd[:3], ["/env/python", "-u", str(AGENT / "gomi_train.py")])
        self.assertEqual(cmd[3:], ["--replays", "/replays", "--gomi-dir", "/gomi"])


class ShortcutTest(unittest.TestCase):
    def test_entry_quotes_paths(self):
        entry = launcher.desktop_entry("/usr/bin/python3", "/home/a b/ai-melee/tools/agent/launcher.py", "/i.png")
        self.assertIn('Exec=/usr/bin/python3 "/home/a b/ai-melee/tools/agent/launcher.py"\n', entry)
        self.assertIn("Terminal=false\n", entry)
        self.assertIn("StartupWMClass=ai-melee\n", entry)

    @unittest.skipIf(os.name == "nt", "the Linux desktop entries (Windows makes .lnk shortcuts: WindowsTest)")
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


class WindowsTest(unittest.TestCase):
    """The Windows parts that can be checked anywhere."""

    def test_shortcut_script(self):
        script = launcher.windows_shortcut_script(r"C:\Py\pythonw.exe", '"C:\\Users\\O\'Neil\\launcher.py"',
                                                  r"C:\Users\O'Neil", r"C:\Users\O'Neil\melee.ico")
        self.assertIn("GetFolderPath('Desktop')", script)
        self.assertIn("GetFolderPath('Programs')", script)
        self.assertIn(r"$s.TargetPath = 'C:\Py\pythonw.exe'", script)
        self.assertIn(r"$s.WorkingDirectory = 'C:\Users\O''Neil'", script, "a ' in a path is doubled")
        removal = launcher.windows_shortcut_script("", "", "", "", remove=True)
        self.assertIn("Remove-Item -LiteralPath $lnk", removal)
        self.assertNotIn("CreateShortcut", removal)

    def test_pythons_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            py, pyw = Path(tmp) / "python.exe", Path(tmp) / "pythonw.exe"
            py.write_bytes(b"")
            pyw.write_bytes(b"")
            self.assertEqual(launcher.windowless_python(py), pyw)
            self.assertEqual(launcher.console_python(pyw), py)
            self.assertEqual(launcher.console_python(py), py)
        self.assertEqual(launcher.windowless_python("/usr/bin/python3"), Path("/usr/bin/python3"))
        self.assertEqual(launcher.stop_command(42), ["taskkill", "/PID", "42", "/T", "/F"])
        # Quit first asks the windows to close, as the game's X does, so Gomihyu's session still ends.
        self.assertEqual(launcher.stop_command(42, force=False), ["taskkill", "/PID", "42", "/T"])
        self.assertGreater(launcher.quit_grace_s(gomi=True), launcher.play.GOMI_WRAP_UP_S)
        self.assertEqual(launcher.quit_grace_s(gomi=False), 15)

    @unittest.skipUnless(os.name == "nt" and importlib.util.find_spec("tkinter"), "Windows, with Tk")
    def test_quit_closes_the_game_like_its_x(self):
        """On a real Windows machine: Quit's first taskkill (no /F) closes the game's window the way its X
        does, the game exits by itself, and play.py, waiting on it, finishes on its own; nothing is
        force-killed."""
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game.py"
            game.write_text(textwrap.dedent(f"""
                import tkinter as tk
                root = tk.Tk()
                def closed():
                    open(r"{tmp}\\game-closed", "w").close()
                    root.destroy()
                root.protocol("WM_DELETE_WINDOW", closed)
                root.after(100, lambda: open(r"{tmp}\\game-up", "w").close())
                root.mainloop()
            """))
            play_py = Path(tmp) / "play.py"
            play_py.write_text(textwrap.dedent(f"""
                import subprocess, sys
                game = subprocess.Popen([sys.executable, r"{game}"])
                game.wait()
                open(r"{tmp}\\play-done", "w").write(str(game.returncode))
            """))
            proc = subprocess.Popen([sys.executable, str(play_py)], creationflags=launcher.CREATE_NO_WINDOW)
            for _ in range(200):
                if (Path(tmp) / "game-up").exists():
                    break
                time.sleep(0.1)
            subprocess.run(launcher.stop_command(proc.pid, force=False), capture_output=True)
            proc.wait(timeout=30)
            self.assertTrue((Path(tmp) / "game-closed").exists(), "the window was asked to close")
            self.assertEqual((Path(tmp) / "play-done").read_text(), "0", "play.py finished by itself")

    def test_shortcut_prefers_the_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "AI-Melee.exe"
            with mock.patch.object(launcher, "EXE", exe), mock.patch.object(launcher, "BUNDLE", Path(tmp)):
                target, args, workdir, _ = launcher.shortcut_target("/py/pythonw.exe")
                self.assertEqual((args, workdir), (f'"{Path(launcher.__file__).resolve()}"', launcher.HERE))
                exe.write_bytes(b"MZ")
                self.assertEqual(launcher.shortcut_target("/py/pythonw.exe"), (exe, "", Path(tmp), exe))

    def test_old_top_level_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp)
            (b / "game").mkdir()
            (b / "ai-melee").mkdir()
            (b / "resources").mkdir()
            for n in ("melee.exe", "SDL3.dll", "notes.txt"):
                (b / n).write_bytes(b"")
            (b / "game" / "old-top-level.txt").write_text(
                "melee.exe\r\nSDL3.dll\r\nresources\r\nmissing.dll\r\nai-melee\r\ngame\r\n")
            old = launcher.old_top_level(b)
            self.assertEqual(sorted(p.name for p in old), ["SDL3.dll", "melee.exe", "resources"])
            launcher.remove_paths(old)
            self.assertEqual(sorted(p.name for p in b.iterdir()), ["ai-melee", "game", "notes.txt"])
            self.assertEqual(launcher.old_top_level(b), [])
            self.assertEqual(launcher.old_top_level(b / "nowhere"), [])

    def test_old_top_level_names_are_whole_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp)
            (b / "game").mkdir()
            for n in ("old game.dll", "old", "Play AI-Melee.bat"):
                (b / n).write_bytes(b"")
            (b / "game" / "old-top-level.txt").write_text("old game.dll\r\nPlay AI-Melee.bat\r\n")
            self.assertEqual([p.name for p in launcher.old_top_level(b)], ["old game.dll"],
                             "not 'old' or 'Play' from splitting on spaces, never the .bat")

    def test_exe_source(self):
        c = (AGENT / "windows" / "ai_melee_exe.c").read_text()
        self.assertIn('L"%ls\\\\ai-melee\\\\launcher.py"', c)
        self.assertIn("pyw.exe", c)


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


TRAIN_RUN = textwrap.dedent("""
    import sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    tmp, stop = Path(sys.argv[2]), sys.argv[3] == "stop"
    import os
    os.environ["GOMI_DIR"] = str(tmp / "gomi")
    import play, launcher, gomi_train
    play.SETTINGS = tmp / "settings.json"
    play.ensure_slippi_env = lambda: sys.executable
    gomi_train.find_base = lambda gomi, explicit=None: tmp / "base"
    launcher.train_command = lambda python, replays, gomi: [python, "-u", str(tmp / "fake_train.py")]
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    l = launcher.Launcher(tk, ttk, filedialog, messagebox, scrolledtext)
    l.opts["replays"] = str(tmp)
    orig = l.trained_label.configure
    l.trained_label.configure = lambda **kw: (print("LABEL", kw.get("text"), flush=True), orig(**kw))
    heard = l.heard_from_training
    def watch(line):
        heard(line)
        print("BAR", l.train_bar.winfo_ismapped(), str(l.train_bar.cget("mode")), round(float(l.train_bar.cget("value"))),
              "TITLE", l.root.title(), flush=True)
    l.heard_from_training = watch
    def info(title, text, parent=None):
        print("INFO", text.replace(chr(10), " "), flush=True)
        print("BUTTON", l.train_button.cget("text"), flush=True)
        print("AFTER", l.train_bar.winfo_ismapped(), l.root.title(), flush=True)
        l.root.after(100, l.root.destroy)
    messagebox.showinfo = info
    l.root.after(200, l.train)
    if stop:
        l.root.after(2500, l.train)          # pressed again: Pause training
        over = l.training_over
        def paused(text, paused=False):
            print("PAUSING", l.train_button.cget("text"), str(l.train_button.cget("state")), flush=True)
            over(text, paused)
            print("OVER", paused, text, "|", l.train_button.cget("text"), "|", l.trained_label.cget("text"),
                  flush=True)
            l.root.after(100, l.root.destroy)
        l.training_over = paused
    l.root.after(20000, l.root.destroy)
    l.root.mainloop()
""")

FAKE_TRAIN = textwrap.dedent("""
    import json, os, sys, time
    from pathlib import Path
    print("gomi-train: before: 18.61 error on your held-out games", flush=True)
    time.sleep(0.3)
    print("gomi-train: step 50/300 (17%), about 6 min to go", flush=True)
    print("gomi-train: checking her on your held-out games...", flush=True)
    print("gomi-train: step 100/300: 5.13 error on your held-out games (best 5.13, before 18.61); "
          "about 5 min to go", flush=True)
    falco = Path(os.environ["GOMI_DIR"]) / "falco"
    falco.mkdir(parents=True, exist_ok=True)
    (falco / "model").write_bytes(b"x")
    (falco / "model.json").write_text(json.dumps({"name": "Uni", "games": 17, "loss": 5.13, "base_loss": 18.61}))
    pause = Path(os.environ["GOMI_DIR"]) / "falco" / "train" / "pause"
    for _ in range(300 if PAUSABLE else 16):          # the real one checks after every step
        if pause.exists():
            pause.unlink()
            (pause.parent / "latest.pkl").write_bytes(b"x")
            (pause.parent / "progress.json").write_text(json.dumps(
                {"step": 101, "steps": 300, "seconds_per_step": 3.0, "paused": True}))
            print("gomi-train: paused at step 101/300 (34%); train again to pick up from here", flush=True)
            sys.exit(0)
        time.sleep(0.1)
    print("gomi-train: her Falco learned from your games: 18.61 -> 5.13 error on games she never saw (72% less)",
          flush=True)
""")


@unittest.skipIf(importlib.util.find_spec("tkinter") is None or not shutil.which("xvfb-run"),
                 "needs tkinter and xvfb-run")
class WindowTest(unittest.TestCase):
    def train(self, stop):
        with tempfile.TemporaryDirectory() as tmp:
            script = FAKE_TRAIN.replace("PAUSABLE", "True" if stop else "False")
            (Path(tmp) / "fake_train.py").write_text(script)
            r = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", TRAIN_RUN, str(AGENT), tmp,
                                "stop" if stop else "run"], capture_output=True, text=True, timeout=60)
            return r

    def test_train_button(self):
        r = self.train(stop=False)
        self.assertIn("LABEL Training: before: 18.61 error on your held-out games, running 0:0", r.stdout,
                      r.stderr[-2000:])
        self.assertRegex(r.stdout, r"BAR 1 indeterminate \d+ TITLE AI-Melee: training Gomi's Falco\n",
                         "the bar moves before there's a step count")
        self.assertIn("LABEL Training: 17% (step 50/300), about 6 min left, running 0:0", r.stdout)
        self.assertIn("BAR 1 determinate 33 TITLE AI-Melee: training Gomi's Falco 33%", r.stdout)
        self.assertIn("LABEL Training: 33% (step 100/300), about 5 min left, running 0:0", r.stdout)
        self.assertIn("LABEL Training: 33% (step 100/300), about 5 min left, running 0:01", r.stdout,
                      "the clock ticks between the trainer's lines")
        self.assertIn("INFO Gomi's Falco: her Falco learned from your games: 18.61 -> 5.13", r.stdout)
        self.assertIn("LABEL plays like Uni (17 games, 72% closer); more training helps", r.stdout)
        self.assertIn("BUTTON Train on my replays", r.stdout)
        self.assertIn("AFTER 0 AI-Melee", r.stdout, "the bar is gone and the title back")

    def test_pause_then_resume(self):
        r = self.train(stop=True)
        self.assertIn("LABEL Pausing at 33%: saving where it is, running 0:0", r.stdout, r.stderr[-2000:])
        self.assertIn("PAUSING Pausing... disabled", r.stdout, "pressed once: it's saving")
        self.assertIn("OVER True paused at step 101/300 (34%); train again to pick up from here | Resume training | "
                      "paused at 34% (step 101/300), about 10 min left: Resume training picks up there", r.stdout)
        self.assertNotIn("INFO", r.stdout, "pausing isn't news: no message box")

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
