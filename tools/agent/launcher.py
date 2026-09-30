#!/usr/bin/env python3
"""AI-Melee's launcher: a small window to start play.py without a terminal.

    python3 tools/agent/launcher.py              open the launcher
    python3 tools/agent/launcher.py --install    add AI-Melee to the app menu and the desktop
    python3 tools/agent/launcher.py --uninstall  remove those shortcuts again

On Windows, double-click AI-Melee.exe in the AI-Melee folder (no console
window); the launcher's "Add desktop shortcut" button, or --install, puts
AI-Melee on the desktop and in the Start menu.

The window offers play.py's common options (which AI, P2's random character,
tournament random stages, the disc) and remembers them in settings.json.
Play runs play.py and shows its progress -- first-run setup, "AI ready", who
plays P2 -- while the game is open; the full log is one click away and in
ai-melee.log beside this file. Closing the game brings the launcher back.

Needs Tk (Arch/CachyOS: sudo pacman -S tk; python.org's Windows installer
includes it). play.py keeps working from a terminal as before.
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
WINDOWS = os.name == "nt"
# The icon: beside this file in the Windows download, in platforms/ in a checkout.
ICON = next((p for p in (HERE / "melee.png", ROOT / "platforms" / "linux" / "melee.png") if p.exists()),
            ROOT / "platforms" / "linux" / "melee.png")
ICO = next((p for p in (HERE / "melee.ico", ROOT / "platforms" / "windows" / "melee.ico") if p.exists()),
           HERE / "melee.ico")
APP_ID = "ai-melee"
BUNDLE = HERE.parent  # the AI-Melee folder, in the Windows download
EXE = BUNDLE / "AI-Melee.exe"
CREATE_NO_WINDOW = 0x08000000  # Windows: run play.py (and what it starts) without a console window
sys.path.insert(0, str(HERE))

import play  # noqa: E402  (no numpy needed at import)

BRAINS = [
    ("auto", "Best available (newer Phillip, 2017 agents for the rest)"),
    ("slippi", "Newer Phillip only (slippi-ai)"),
    ("classic", "2017 agents only"),
]
DEFAULTS = {"brain": "auto", "p2_pick": True, "legal_stages": True, "gomi": False, "box": False}


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
    replays = settings.get("gomi_replays")
    opts["replays"] = replays if replays and Path(replays).exists() else None
    return opts


def store_options(settings, opts):
    settings["launcher"] = {k: opts[k] for k in DEFAULTS}
    if opts.get("disc"):
        settings["disc"] = str(Path(opts["disc"]).resolve())
    if opts.get("replays"):
        settings["gomi_replays"] = str(Path(opts["replays"]).resolve())
    return settings


def play_args(opts):
    """play.py's command-line arguments for the launcher's choices."""
    args = ["--brain", opts["brain"],
            "--p2-pick", "on" if opts["p2_pick"] else "off",
            "--random-stages", "legal" if opts["legal_stages"] else "all"]
    if opts.get("gomi"):
        args.append("--gomi")
    args += ["--box-controller", "on" if opts.get("box") else "off"]
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
        return "game", "Game running. Press Esc or close the game window to stop."
    if line.startswith("agent: client connected"):
        if brain in ("classic", "gomi"):
            return "ai", "AI ready: pick your characters and play"
        return "ai", "Loading the AI model..."
    m = re.match(r"gomi: Gomihyu plays (\w+) vs (.+?) \(", line)
    if m:
        return "match", f"This match: Gomihyu plays {m.group(1)} vs {m.group(2)}"
    m = re.match(r"agent: (Gomihyu doesn't play .+?);", line)
    if m:
        return "match", "This match: " + m.group(1) + " (pick Mario, Fox or Falco for P2)"
    m = re.match(r'gomi: (?:Gomi|after the match): "(.+)"$', line)
    if m:
        return "gomi", f'Gomi: "{m.group(1)}"'
    if line.startswith("play: Gomihyu plays Mario, Fox and Falco on her rules"):
        return "gomi", "Gomihyu: Ollama isn't answering, so she plays on her rules (see log)"
    m = re.match(r"gomi: (\d+) of her Discord posts are still waiting", line)
    if m:
        return "discord", f"Discord: {m.group(1)} of her posts are waiting. Is her gomihyu bot running?"
    if line.startswith("gomi: sent the match to her Discord bot"):
        return "discord", "Discord: match sent to her bot (she posts when the session is over, or on GG)"
    if line.startswith("play: Gomihyu is finishing her thoughts"):
        return "game", "Game closed; Gomi is finishing her thoughts about the session..."
    m = re.match(r"gomi: session over: (\d+) match", line)
    if m:
        return "discord", f"Discord: session over ({m.group(1)} played); her bot posts about it now"
    m = re.match(r"gomi: match over: (.+)$", line)
    if m:
        return "result", "Gomihyu " + m.group(1)
    m = re.match(r'gomi: (?:next match she works on|practising this match): "(.+?)" \(', line)
    if m:
        return "goal", f'Gomi is working on: "{m.group(1)}"'
    m = re.match(r"gomi: her practice: (.+)$", line)
    if m:
        return "goal", "Her practice: " + m.group(1)
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
    if WINDOWS:
        return windows_install(python)
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
    if WINDOWS:
        return windows_uninstall()
    removed = []
    for p in shortcut_paths(home):
        if p.exists():
            p.unlink()
            removed.append(p)
    return removed


# ---------------------------------------------------------------- Windows

def windowless_python(python):
    """pythonw.exe beside python.exe: the launcher without a console window."""
    p = Path(python)
    w = p.with_name("pythonw.exe")
    return w if p.name.lower() == "python.exe" and w.exists() else p


def console_python(python):
    """python.exe beside pythonw.exe: play.py's output needs a real stdout."""
    p = Path(python)
    c = p.with_name("python.exe")
    return c if p.name.lower() == "pythonw.exe" and c.exists() else p


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def windows_shortcut_script(target, arguments, workdir, icon, remove=False):
    """PowerShell that puts AI-Melee.lnk on the desktop and in the Start menu
    (or removes them), printing each path; WScript.Shell makes the .lnk."""
    lines = ["$places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))",
             "foreach ($d in $places) {",
             "  $lnk = Join-Path $d 'AI-Melee.lnk'"]
    if remove:
        lines += ["  if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk; Write-Output $lnk }"]
    else:
        lines += ["  $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)",
                  f"  $s.TargetPath = {_ps_quote(target)}",
                  f"  $s.Arguments = {_ps_quote(arguments)}",
                  f"  $s.WorkingDirectory = {_ps_quote(workdir)}",
                  f"  $s.IconLocation = {_ps_quote(icon)}",
                  "  $s.Description = 'Play Super Smash Bros. Melee against the Phillip AI'",
                  "  $s.Save()",
                  "  Write-Output $lnk"]
    lines.append("}")
    return "\n".join(lines)


def _powershell(script):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or "PowerShell failed")
    return [Path(ln) for ln in r.stdout.splitlines() if ln.strip()]


def shortcut_target(python=sys.executable):
    """(target, arguments, working folder, icon) for the Windows shortcut: AI-Melee.exe when
    it's there, else the windowless Python running this file."""
    if EXE.exists():
        return EXE, "", BUNDLE, EXE
    launcher = Path(__file__).resolve()
    return windowless_python(python), f'"{launcher}"', launcher.parent, ICO


def windows_install(python=sys.executable):
    return _powershell(windows_shortcut_script(*shortcut_target(python)))


def old_top_level(bundle=BUNDLE):
    """Game files a new zip extracted over an old folder left at the top: the names in
    game/old-top-level.txt (where they used to be) that are still there. Only those."""
    try:
        text = (Path(bundle) / "game" / "old-top-level.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    names = [line.strip() for line in text.splitlines() if line.strip()]  # one name per line
    keep = {"game", "ai-melee", "ai-melee.exe", "play ai-melee.bat", "readme-ai-melee.txt"}
    return [Path(bundle) / n for n in names
            if n.lower() not in keep and "/" not in n and "\\" not in n and n not in (".", "..")
            and (Path(bundle) / n).exists()]


def remove_paths(paths):
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def windows_uninstall():
    return _powershell(windows_shortcut_script("", "", "", "", remove=True))


def windows_shortcut_exists():
    desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop" / "AI-Melee.lnk"
    appdata = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return desktop.exists() or (appdata / "AI-Melee.lnk").exists()


def stop_command(pid):
    """Windows can't ask play.py to stop (no SIGTERM): end it, the game and the AI together."""
    return ["taskkill", "/PID", str(pid), "/T", "/F"]


# ---------------------------------------------------------------- the window

def no_tk_message():
    if WINDOWS:
        msg = ("AI-Melee's launcher needs Tk, which this Python doesn't have.\n"
               "Run the python.org installer again, choose Modify, tick 'tcl/tk and IDLE', "
               "and open AI-Melee again. (Play AI-Melee.bat works without it.)")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "AI-Melee", 0x10)
        except (ImportError, AttributeError, OSError):
            pass
        print(msg, file=sys.stderr)
        return
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
    """KDE's or GNOME's own file picker when there is one (Tk's is the native one on Windows)."""
    start = str(start or Path.home())
    if WINDOWS:
        return False, None
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
        self.stopping = False
        self.lines = []
        self.lines_q = queue.Queue()
        self.status = {}

        if WINDOWS:
            try:  # sharp text on scaled displays
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (ImportError, AttributeError, OSError):
                pass
        root = self.root = tk.Tk(className=APP_ID)
        root.title("AI-Melee")
        root.resizable(True, True)
        root.minsize(460, 0)
        try:
            if WINDOWS and ICO.exists():
                root.iconbitmap(default=str(ICO))
            elif ICON.exists():
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
        self.brain_buttons = [ttk.Radiobutton(ai, text=text, value=value, variable=self.brain)
                              for value, text in BRAINS]
        for button in self.brain_buttons:
            button.pack(anchor="w")
        self.gomi = tk.BooleanVar(value=self.opts["gomi"])
        ttk.Checkbutton(ai, text="Gomihyu takes over P2, as Mario, Fox or Falco (needs Ollama with gemma4:e4b)",
                        variable=self.gomi, command=self.gomi_changed).pack(anchor="w", pady=(6, 0))
        replays = ttk.Frame(ai)
        replays.pack(fill="x", pady=(4, 0))
        ttk.Label(replays, text="Her Falco learns from replays:").pack(side="left")
        self.replays_label = ttk.Label(replays, text="")
        self.replays_label.pack(side="left", padx=6)
        ttk.Button(replays, text="Teach Gomi from replays...", command=self.choose_replays).pack(side="right")
        self.show_replays()
        self.gomi_changed()
        game = ttk.LabelFrame(self.options, text="Game", padding=8)
        game.pack(fill="x", pady=(8, 0))
        self.p2_pick = tk.BooleanVar(value=self.opts["p2_pick"])
        ttk.Checkbutton(game, text="Opening P2's door gives it a random character the AI plays",
                        variable=self.p2_pick).pack(anchor="w")
        self.legal = tk.BooleanVar(value=self.opts["legal_stages"])
        ttk.Checkbutton(game, text="Random stage picks tournament stages only", variable=self.legal).pack(anchor="w")
        self.box = tk.BooleanVar(value=self.opts["box"])
        ttk.Checkbutton(game, text="I play on a box controller (HayBox, B0XX, Frame1)",
                        variable=self.box).pack(anchor="w")
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
        if WINDOWS and not windows_shortcut_exists():
            self.shortcut_button = ttk.Button(bar, text="Add desktop shortcut", command=self.add_shortcut)
            self.shortcut_button.pack(side="left", padx=(8, 0))
        self.play_button = ttk.Button(bar, text="Play", style="Play.TButton", command=self.play)
        self.play_button.pack(side="right")
        self.stop_button = ttk.Button(bar, text="Quit game", command=self.stop)

        self.log_view = scrolledtext.ScrolledText(outer, height=14, width=90, wrap="word",
                                                  font=("TkFixedFont", 9), state="disabled")
        self.log_shown = False
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.pump)
        if WINDOWS:
            root.after(300, self.offer_tidy_up)

    # ---- options ---------------------------------------------------------

    def gomi_changed(self):
        """With Gomihyu on, the other AIs are off: their choice doesn't apply."""
        state = "disabled" if self.gomi.get() else "!disabled"
        for button in self.brain_buttons:
            button.state([state])

    def offer_tidy_up(self):
        """Extracted over an old folder: the old melee.exe at the top starts the game without
        the AI. Offer once to remove the old game files there (the game lives in game\\ now)."""
        old = old_top_level()
        if not old or self.settings.get("tidy_up_declined"):
            return
        names = ", ".join(p.name for p in old[:6]) + (" ..." if len(old) > 6 else "")
        if self.messagebox.askyesno(
                "AI-Melee", "The game now lives in the game folder, and the old game files are still at the "
                f"top of the AI-Melee folder ({names}). Double-clicking that old melee.exe starts the game "
                "without the AI.\n\nRemove the old game files from the top of the folder?", parent=self.root):
            remove_paths(old)
        else:
            self.settings["tidy_up_declined"] = True
            play.save_settings(self.settings)

    def add_shortcut(self):
        try:
            made = install(Path.home())
        except OSError as e:
            self.messagebox.showerror("AI-Melee", f"Couldn't make the shortcut:\n\n{e}", parent=self.root)
            return
        self.shortcut_button.pack_forget()
        self.messagebox.showinfo("AI-Melee", "AI-Melee is on your desktop and in the Start menu now:\n\n"
                                 + "\n".join(str(p) for p in made), parent=self.root)

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

    def show_replays(self):
        self.replays_label.configure(text=Path(self.opts["replays"]).name if self.opts.get("replays")
                                     else "(none: pick your Slippi replay folder)")

    def choose_replays(self):
        """Her Falco imitates every Falco player in these replays (gomi_replays.py): read them now,
        and new ones each time she starts."""
        start = self.opts.get("replays") or str(Path.home() / "Slippi")
        folder = self.filedialog.askdirectory(parent=self.root, title="Your Slippi replay folder",
                                              initialdir=start if Path(start).is_dir() else str(Path.home()))
        if not folder:
            return
        self.opts["replays"] = folder
        self.show_replays()
        self.save()
        if not self.log_shown:
            self.toggle_log()

        def learn():
            import gomi_replays
            lines = []

            def log(msg):
                lines.append(msg)
                self.lines_q.put(f"gomi: {msg}")
            gomi_replays.learn(folder, os.environ.get("GOMI_DIR") or HERE / "gomi", log=log)
            taught = gomi_replays.Teacher(Path(os.environ.get("GOMI_DIR") or HERE / "gomi") / "falco" /
                                          "teacher.json").lines()
            self.lines_q.put(("replays", "\n".join(lines + taught) or "No new replays with a Falco in them."))
        threading.Thread(target=learn, daemon=True).start()

    def save(self):
        self.opts.update(brain=self.brain.get(), p2_pick=self.p2_pick.get(), legal_stages=self.legal.get(),
                         gomi=self.gomi.get(), box=self.box.get())
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
        cmd = [str(console_python(sys.executable)), "-u", str(PLAY), *play_args(self.opts)]
        try:
            self.logfile = open(LOG, "w", encoding="utf-8", errors="replace")
        except OSError:
            self.logfile = None
        self.append(f"$ {' '.join(cmd)}")
        self.stopping = False
        self.proc = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                     bufsize=1, creationflags=CREATE_NO_WINDOW if WINDOWS else 0,
                                     env=dict(os.environ, PYTHONIOENCODING="utf-8"))
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
                if isinstance(line, tuple):              # the replays are read (choose_replays)
                    self.messagebox.showinfo("AI-Melee", "Gomi's Falco, from your replays:\n\n" + line[1],
                                             parent=self.root)
                    continue
                self.lines.append(line)
                self.append(line)
                st = status_for(line, "gomi" if self.opts["gomi"] else self.opts["brain"])
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
        if code not in (0, -signal.SIGTERM) and not self.stopping:
            why = failure_text(self.lines) or f"it stopped with code {code}"
            if not self.log_shown:
                self.toggle_log()
            self.messagebox.showerror("AI-Melee", f"AI-Melee could not keep going:\n\n{why}\n\n"
                                      f"The full log is below and in {LOG}.", parent=self.root)

    def end_play(self):
        """play.py closes the game and the AI on SIGTERM; on Windows they end together."""
        self.stopping = True
        if WINDOWS:
            subprocess.run(stop_command(self.proc.pid), capture_output=True, creationflags=CREATE_NO_WINDOW)
        else:
            self.proc.terminate()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.end_play()

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.end_play()
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
        for key in ("start", "setup", "agents", "model", "game", "ai", "match", "gomi", "result", "goal", "discord"):
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
        print("launcher: open AI-Melee from the " + ("Start menu" if WINDOWS else "app menu (Games)")
              + " or the desktop")
        if importlib.util.find_spec("tkinter") is None:
            print("launcher: this Python has no Tk yet, which the window needs"
                  + ("" if WINDOWS else ": sudo pacman -S tk"))
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
