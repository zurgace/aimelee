#!/usr/bin/env python3
"""AI-Melee's launcher: a small window to start play.py without a terminal.

    python3 tools/agent/launcher.py              open the launcher
    python3 tools/agent/launcher.py --install    add AI-Melee to the app menu and the desktop
    python3 tools/agent/launcher.py --uninstall  remove those shortcuts again

The window offers play.py's common options (which AI, P2's random character,
tournament random stages, the disc) and remembers them in settings.json.
Play runs play.py and shows its progress -- first-run setup, "AI ready", who
plays P2 -- while the game is open; the full log is one click away and in
ai-melee.log beside this file. Closing the game brings the launcher back.

Needs Tk (Arch/CachyOS: sudo pacman -S tk). play.py keeps working from a
terminal as before.
"""

import argparse
import importlib.util
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAY = HERE / "play.py"
LOG = HERE / "ai-melee.log"
ICON = ROOT / "platforms" / "linux" / "melee.png"
APP_ID = "ai-melee"
sys.path.insert(0, str(HERE))

import play  # noqa: E402  (no numpy needed at import)

BRAINS = [
    ("auto", "Best available (newer Phillip, 2017 agents for the rest)"),
    ("slippi", "Newer Phillip only (slippi-ai)"),
    ("classic", "2017 agents only"),
]
DEFAULTS = {"brain": "auto", "p2_pick": True, "legal_stages": True}


# ---------------------------------------------------------------- pure parts

def load_options(settings, environ=os.environ):
    """The launcher's choices from settings.json, plus the disc: the one
    remembered there, else $MELEE_DISC (a desktop launch does not see a
    shell's variables, so it is remembered from then on)."""
    opts = dict(DEFAULTS)
    opts.update({k: v for k, v in settings.get("launcher", {}).items() if k in DEFAULTS})
    if opts["brain"] not in {b for b, _ in BRAINS}:
        opts["brain"] = DEFAULTS["brain"]
    disc = settings.get("disc") or environ.get("MELEE_DISC")
    opts["disc"] = disc if disc and Path(disc).is_file() else None
    return opts


def store_options(settings, opts):
    settings["launcher"] = {k: opts[k] for k in DEFAULTS}
    if opts.get("disc"):
        settings["disc"] = str(Path(opts["disc"]).resolve())
    return settings


def play_args(opts):
    """play.py's command-line arguments for the launcher's choices."""
    args = ["--brain", opts["brain"],
            "--p2-pick", "on" if opts["p2_pick"] else "off",
            "--random-stages", "legal" if opts["legal_stages"] else "all"]
    if opts.get("disc"):
        args = ["--iso", str(opts["disc"])] + args
    return args


_STAMP = re.compile(r"^\[\s*[\d.]+\]\s*")  # the game's own log lines carry a timestamp


def status_for(line, brain="auto"):
    """(slot, text) for a log line worth showing in the status list, else
    None. A later line replaces an earlier one in the same slot."""
    line = _STAMP.sub("", line.strip())
    rules = [
        ("play: exporting", "setup", "Converting the 2017 agents (first time only: a few minutes)..."),
        ("play: setting up the newer Phillip", "setup",
         "Setting up the newer Phillip (first time only: several minutes, about 2.5 GB)..."),
        ("play: downloading slippi-ai", "setup", "Downloading the newer Phillip's model (first time only)..."),
        ("play: timing", "setup", "Timing the model on this computer (first time only)..."),
        ("play: slippi-ai is not available", "model", "Newer Phillip unavailable: the 2017 agents play"),
        ("slippi: model ready", "ai", "AI ready: pick your characters and play"),
        ("agent: client dropped", "ai", "The AI disconnected; it restarts by itself..."),
        ("play: agent exited", "ai", "The AI stopped; restarting it..."),
    ]
    for prefix, slot, text in rules:
        if line.startswith(prefix):
            return slot, text
    if line.startswith("play: agents: "):
        return "agents", "2017 agents: " + line[len("play: agents: "):]
    m = re.match(r"play: slippi-ai type: .*?characters: (.*)$", line)
    if m:
        return "model", "Newer Phillip plays: " + m.group(1)
    if line.startswith("play: ") and " with " in line and "; disc " in line:
        return "game", "Game running. Close the game window to stop."
    if line.startswith("agent: client connected"):
        if brain == "classic":
            return "ai", "AI ready: pick your characters and play"
        return "ai", "Loading the AI model..."
    m = re.match(r"agent: P(\d) opens as (.+?) \(", line)
    if m:
        return "match", f"P{m.group(1)} opens as {m.group(2)}"
    m = re.match(r"slippi: (.+: slippi-ai plays P\d)$", line)
    if m:
        return "match", "This match: " + m.group(1).replace("slippi-ai", "the newer Phillip")
    m = re.match(r"(?:agent|slippi): (no (?:Phillip agent|AI) plays .+?);", line)
    if m:
        return "match", "This match: " + m.group(1)
    m = re.match(r"agent: ([A-Z][\w .&]+): (?:no Phillip agent, .+ stands in|[\w/]+)$", line)
    if m and "slippi-ai" not in line:
        return "match", "This match: the 2017 agent plays " + m.group(1)
    return None


def failure_text(lines):
    """play.py's own explanation of why it stopped (its fail() line)."""
    for line in reversed(lines):
        if line.startswith("play: "):
            return line[len("play: "):]
    return None


def _quote_exec(arg):
    s = str(arg)
    if re.search(r'[\s"\'\\$`]', s):
        s = '"' + re.sub(r'(["`$\\])', r"\\\1", s) + '"'
    return s


def desktop_entry(python, script, icon):
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=AI-Melee",
        "GenericName=Melee vs Phillip",
        "Comment=Play Super Smash Bros. Melee against the Phillip AI",
        f"Exec={_quote_exec(python)} {_quote_exec(script)}",
        f"Icon={icon}",
        f"Path={Path(script).parent}",
        "Terminal=false",
        "Categories=Game;ActionGame;",
        f"StartupWMClass={APP_ID}",
        "StartupNotify=true",
        "",
    ])


def desktop_dir(home):
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        d = Path(out.stdout.strip())
        if out.returncode == 0 and d.is_dir() and d != Path(home):
            return d
    except (OSError, subprocess.SubprocessError):
        pass
    d = Path(home) / "Desktop"
    return d if d.is_dir() else None


def shortcut_paths(home):
    data = Path(os.environ.get("XDG_DATA_HOME") or Path(home) / ".local" / "share")
    paths = [data / "applications" / f"{APP_ID}.desktop"]
    desk = desktop_dir(home)
    if desk is not None:
        paths.append(desk / f"{APP_ID}.desktop")
    return paths


def install(home, python=sys.executable):
    entry = desktop_entry(python, Path(__file__).resolve(), ICON)
    written = []
    for p in shortcut_paths(home):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(entry)
        p.chmod(0o755)  # Plasma and GNOME only run desktop-folder shortcuts marked executable
        if shutil.which("gio") and p.parent.name != "applications":
            subprocess.run(["gio", "set", str(p), "metadata::trusted", "true"], capture_output=True)
        written.append(p)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(written[0].parent)], capture_output=True)
    return written


def uninstall(home):
    removed = []
    for p in shortcut_paths(home):
        if p.exists():
            p.unlink()
            removed.append(p)
    return removed


# ---------------------------------------------------------------- the window

def no_tk_message():
    msg = ("AI-Melee's launcher needs Tk for its window.\n"
           "Install it (CachyOS/Arch: sudo pacman -S tk) and open AI-Melee again.")
    for cmd in (["kdialog", "--title", "AI-Melee", "--error", msg],
                ["zenity", "--error", "--title=AI-Melee", f"--text={msg}"],
                ["notify-send", "AI-Melee", msg]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd)
            break
    print(msg, file=sys.stderr)


def native_file_dialog(start):
    """KDE's or GNOME's own file picker when there is one."""
    start = str(start or Path.home())
    if shutil.which("kdialog"):
        cmd = ["kdialog", "--title", "Choose your Melee disc image (NTSC-U 1.02)", "--getopenfilename",
               start, "Disc images (*.iso *.gcm *.ciso *.rvz)|All files (*)"]
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--title=Choose your Melee disc image (NTSC-U 1.02)",
               "--file-filter=Disc images | *.iso *.gcm *.ciso *.rvz", "--file-filter=All files | *"]
    else:
        return False, None
    r = subprocess.run(cmd, capture_output=True, text=True)
    return True, (r.stdout.strip() or None) if r.returncode == 0 else None


class Launcher:
    def __init__(self, tk, ttk, filedialog, messagebox, scrolledtext):
        self.tk, self.ttk = tk, ttk
        self.filedialog, self.messagebox = filedialog, messagebox
        self.settings = play.load_settings()
        self.opts = load_options(self.settings)
        self.proc = None
        self.lines = []
        self.lines_q = queue.Queue()
        self.status = {}

        root = self.root = tk.Tk(className=APP_ID)
        root.title("AI-Melee")
        root.resizable(True, True)
        root.minsize(460, 0)
        if ICON.exists():
            try:
                root.iconphoto(True, tk.PhotoImage(file=str(ICON)))
            except tk.TclError:
                pass
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("TkDefaultFont", 16, "bold"))
        style.configure("Sub.TLabel", foreground="#666")
        style.configure("Play.TButton", font=("TkDefaultFont", 12, "bold"), padding=(24, 8))

        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="AI-Melee", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Super Smash Bros. Melee against the Phillip AI. You are P1; the AI plays P2.",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 12))

        body = ttk.Frame(outer)  # the options, or the status while playing
        body.pack(fill="x")

        # Options
        self.options = ttk.Frame(body)
        self.options.pack(fill="x")
        ai = ttk.LabelFrame(self.options, text="AI", padding=8)
        ai.pack(fill="x")
        self.brain = tk.StringVar(value=self.opts["brain"])
        for value, text in BRAINS:
            ttk.Radiobutton(ai, text=text, value=value, variable=self.brain).pack(anchor="w")
        game = ttk.LabelFrame(self.options, text="Game", padding=8)
        game.pack(fill="x", pady=(8, 0))
        self.p2_pick = tk.BooleanVar(value=self.opts["p2_pick"])
        ttk.Checkbutton(game, text="Opening P2's door gives it a random character the AI plays",
                        variable=self.p2_pick).pack(anchor="w")
        self.legal = tk.BooleanVar(value=self.opts["legal_stages"])
        ttk.Checkbutton(game, text="Random stage picks tournament stages only", variable=self.legal).pack(anchor="w")
        disc = ttk.Frame(game)
        disc.pack(fill="x", pady=(6, 0))
        ttk.Label(disc, text="Disc:").pack(side="left")
        self.disc_label = ttk.Label(disc, text="")
        self.disc_label.pack(side="left", padx=6)
        ttk.Button(disc, text="Change...", command=self.choose_disc).pack(side="right")
        self.show_disc()

        # Status (while playing)
        self.running = ttk.Frame(body)
        self.status_box = ttk.Frame(self.running)
        self.status_box.pack(fill="x")

        # Buttons
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(12, 0))
        self.log_button = ttk.Button(bar, text="Show log", command=self.toggle_log)
        self.log_button.pack(side="left")
        self.play_button = ttk.Button(bar, text="Play", style="Play.TButton", command=self.play)
        self.play_button.pack(side="right")
        self.stop_button = ttk.Button(bar, text="Quit game", command=self.stop)

        self.log_view = scrolledtext.ScrolledText(outer, height=14, width=90, wrap="word",
                                                  font=("TkFixedFont", 9), state="disabled")
        self.log_shown = False
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.pump)

    # ---- options ---------------------------------------------------------

    def show_disc(self):
        d = self.opts.get("disc")
        self.disc_label.configure(text=Path(d).name if d else "(none chosen yet)")

    def choose_disc(self):
        start = Path(self.opts["disc"]).parent if self.opts.get("disc") else None
        had_native, path = native_file_dialog(start)
        if not had_native:
            path = self.filedialog.askopenfilename(
                parent=self.root, title="Choose your Melee disc image (NTSC-U 1.02)",
                initialdir=str(start or Path.home()),
                filetypes=[("Disc images", "*.iso *.gcm *.ciso *.rvz"), ("All files", "*")]) or None
        if path and Path(path).is_file():
            self.opts["disc"] = path
            self.show_disc()
            self.save()
        return bool(self.opts.get("disc"))

    def save(self):
        self.opts.update(brain=self.brain.get(), p2_pick=self.p2_pick.get(), legal_stages=self.legal.get())
        play.save_settings(store_options(self.settings, self.opts))

    # ---- running play.py ------------------------------------------------

    def play(self):
        if not self.opts.get("disc") and not self.choose_disc():
            return
        self.save()
        self.lines = []
        self.status = {}
        for w in self.status_box.winfo_children():
            w.destroy()
        self.set_log("")
        self.set_status("start", "Starting...")
        cmd = [sys.executable, "-u", str(PLAY), *play_args(self.opts)]
        try:
            self.logfile = open(LOG, "w", encoding="utf-8", errors="replace")
        except OSError:
            self.logfile = None
        self.append(f"$ {' '.join(cmd)}")
        self.proc = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, errors="replace", bufsize=1)
        threading.Thread(target=self.read, args=(self.proc,), daemon=True).start()
        self.options.pack_forget()
        self.running.pack(fill="x")
        self.play_button.pack_forget()
        self.stop_button.pack(side="right")

    def read(self, proc):
        for line in proc.stdout:
            self.lines_q.put(line.rstrip("\n"))
        proc.wait()
        self.lines_q.put(None)

    def pump(self):
        try:
            while True:
                line = self.lines_q.get_nowait()
                if line is None:
                    self.finished()
                    continue
                self.lines.append(line)
                self.append(line)
                st = status_for(line, self.opts["brain"])
                if st is not None:
                    self.set_status(*st)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def finished(self):
        code = self.proc.returncode if self.proc else 0
        self.proc = None
        if self.logfile:
            self.logfile.close()
            self.logfile = None
        self.running.pack_forget()
        self.options.pack(fill="x")
        self.stop_button.pack_forget()
        self.play_button.pack(side="right")
        if code not in (0, -signal.SIGTERM):
            why = failure_text(self.lines) or f"it stopped with code {code}"
            if not self.log_shown:
                self.toggle_log()
            self.messagebox.showerror("AI-Melee", f"AI-Melee could not keep going:\n\n{why}\n\n"
                                      f"The full log is below and in {LOG}.", parent=self.root)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()  # play.py closes the game and the AI

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.root.destroy()

    # ---- display ----------------------------------------------------------

    def set_status(self, slot, text):
        if slot != "start":
            self.status.pop("start", None)
        if slot == "game":
            self.status.pop("setup", None)  # the game runs: setup is done
        self.status[slot] = text
        for w in self.status_box.winfo_children():
            w.destroy()
        for key in ("start", "setup", "agents", "model", "game", "ai", "match"):
            if key in self.status:
                mark = "✓" if key in ("agents", "model") or self.status[key].startswith("AI ready") else "•"
                self.ttk.Label(self.status_box, text=f"{mark}  {self.status[key]}", wraplength=520,
                               justify="left").pack(anchor="w", pady=1)

    def append(self, line):
        if self.logfile:
            self.logfile.write(line + "\n")
            self.logfile.flush()
        self.log_view.configure(state="normal")
        self.log_view.insert("end", line + "\n")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def set_log(self, text):
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.insert("end", text)
        self.log_view.configure(state="disabled")

    def toggle_log(self):
        self.log_shown = not self.log_shown
        if self.log_shown:
            self.log_view.pack(fill="both", expand=True, pady=(12, 0))
            self.log_button.configure(text="Hide log")
        else:
            self.log_view.pack_forget()
            self.log_button.configure(text="Show log")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="add AI-Melee to the app menu and the desktop")
    ap.add_argument("--uninstall", action="store_true", help="remove those shortcuts")
    args = ap.parse_args()
    home = Path.home()
    if args.install:
        for p in install(home):
            print(f"launcher: added {p}")
        print("launcher: open AI-Melee from your app menu (Games) or the desktop")
        if importlib.util.find_spec("tkinter") is None:
            print("launcher: this Python has no Tk yet, which the window needs: sudo pacman -S tk")
        return
    if args.uninstall:
        removed = uninstall(home)
        for p in removed:
            print(f"launcher: removed {p}")
        if not removed:
            print("launcher: no AI-Melee shortcuts to remove")
        return
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
    except ImportError:
        no_tk_message()
        sys.exit(1)
    try:
        launcher = Launcher(tk, ttk, filedialog, messagebox, scrolledtext)
    except tk.TclError as e:  # no display
        sys.exit(f"launcher: cannot open a window ({e}); run play.py from a terminal instead")
    launcher.root.mainloop()


if __name__ == "__main__":
    main()
