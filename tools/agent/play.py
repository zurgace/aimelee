#!/usr/bin/env python3
"""One command: start melee-pc with the agent bridge on and Phillip on a port.

    python3 tools/agent/play.py              # Linux, from the repository
    py ai-melee\\play.py                      # Windows, from the AI-Melee folder
                                             # (or double-click Play AI-Melee.bat)

Phillip plays P2 as whichever character P2 has. The newer Phillip
(vladfi1/slippi-ai, the bot on Slippi) plays every character its model
covers; for the rest the 2017 agents do: Captain Falcon, Fox, Falco, Marth,
Peach or Sheik get their own, Ganondorf and Roy borrow Falcon's and Marth's,
anything else stands still (set it to CPU instead).
Take P1 (the keyboard, or a gamepad on port 1), set P2 to HMN and pick its
character (P2 reads as plugged in; with P1's cursor: toggle P2's door to
CPU, pick the character, toggle on to HMN), choose a stage (most agents
trained on Final Destination), and play. The agent takes P2 once the match
starts and lets go when it ends. On the results screen P2 counts as ready,
so one Start takes you back to the character select. The game never waits on
the agent for more than the timeout, and carries on if it dies (play.py
restarts it).

What it finds by itself:
  the game      build/melee in a checkout, or melee.exe beside the ai-melee
                folder of a Windows download (--melee to point elsewhere)
  Phillip       ../phillip beside the checkout, or phillip / phillip-master
                inside the AI-Melee folder (--phillip)
  your disc     --iso, else $MELEE_DISC, else the one picked last time, else
                a file dialog asks (the choice is kept in settings.json)
  slippi-ai     on first use: its own Python 3.12 environment (slippi-env/,
                about 2.5 GB, from slippi-requirements.txt) and the medium-v2
                model; the model is timed once (weights/slippi/*.probe.txt)
  the weights   every agent's, exported from Phillip's checkpoints on first
                use with TensorFlow 2.13 through uv (a throwaway Python
                3.11); after that only numpy is needed

Options:
  --brain B          auto (default): slippi-ai where its model plays the
                     character, the 2017 agents elsewhere; slippi: slippi-ai
                     only; classic: the 2017 agents only
  --slippi-model F   a slippi-ai model file (default: medium-v2, downloaded on
                     first use into weights/slippi/)
  --reaction N       0 (default): each character's strongest agent; 1 or 2:
                     prefer agents trained to react 3 or 6 frames late, where
                     they exist (Marth, Peach, Sheik)
  --agent NAME       play this one agent whatever P2 picks (any agent under
                     <phillip>/agents; list them with list_agents.py)
  --port N           the port the agent plays (1-4, default 2)
  --delay N          with --agent: run it with N network steps of action
                     delay, as Phillip's --delay did (its weights are padded)
  --epsilon E        random-action rate (default 0, as Phillip's README plays)
  --sync MODE        lockstep (default) or async
  --timeout-ms MS    lockstep wait per tick (default 4)
  --frame-lag K      hold each pad K extra ticks (emulate a slower pipe)
  --box-controller on|off
                     on: read every controller's sticks as Dolphin does, for a
                     box controller (HayBox, B0XX, Frame1) whose firmware sends
                     Dolphin's values; without it a box in XInput mode can't
                     dash. DInput-mode boxes are recognised by name anyway.
                     Remembered (settings.json) until you say off.
  --random-stages S  legal (default): Random on the stage select picks one of
                     Battlefield, Final Destination, Pokemon Stadium, Yoshi's
                     Story, Dream Land N64 or Fountain of Dreams; all: the
                     game's own Random Stage Switch list
  --p2-pick on|off   on (default): opening the AI's door on the character
                     select seats it as HMN with a random character an AI
                     plays (Sheik aside: hold A on Zelda for her)
  --gomi             Gomihyu, a language model through Ollama, takes over P2
                     as Mario or Fox (slippi-ai and the 2017 agents stay
                     off): she picks the game plan, and learns after each
                     match (gomi_brain.py; tools/agent/gomi/ keeps her lessons)
  --quick            skip the menus: boot straight into a Falcon match (or
                     --agent's matchup): debug VS, both ports human
  --record FILE      record every state the agent sees (dump_state format;
                     verify_model.py --record replays it through Phillip)
  --tcp              use loopback TCP instead of a Unix socket (always on
                     Windows, whose Python has no Unix sockets)

The game runs with MELEE_PREWARM=0 (melee-pc's background disc prewarm can
crash the boot; see NOTES.md) unless you set MELEE_PREWARM yourself.

The bridge serves one client at a time and a new connection replaces the
old one, so record through --record rather than running dump_state.py
next to a playing agent.
"""

import argparse
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # the repository, when run from a checkout
BUNDLE = HERE.parent    # the AI-Melee folder, when run from a Windows download
SETTINGS = HERE / "settings.json"
WINDOWS = os.name == "nt"
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import phillip_obs  # noqa: E402  (no numpy needed at import)
import roster  # noqa: E402

UV_EXPORT = ["--python", "3.11", "--with", "tensorflow-cpu==2.13.*", "--with", "attrs"]
PIP = "py -m pip install" if WINDOWS else "python3 -m pip install"


def fail(msg):
    print(f"\nplay: {msg}\n", file=sys.stderr)
    sys.exit(1)


def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        SETTINGS.write_text(json.dumps(settings, indent=1))
    except OSError:
        pass  # a read-only folder just means asking again next time


def find_melee(explicit):
    if explicit:
        return Path(explicit)
    names = ["melee.exe", "melee"] if WINDOWS else ["melee", "melee.exe"]
    for d in (ROOT / "build", BUNDLE, BUNDLE.parent):
        for n in names:
            if (d / n).is_file():
                return d / n
    fail("cannot find the game. From a checkout build it first (cmake -B build -G Ninja && "
         "ninja -C build); on Windows keep the ai-melee folder inside the unzipped AI-Melee "
         "folder, next to melee.exe. Or pass --melee <path to melee(.exe)>.")


def find_phillip(explicit, agent):
    candidates = [Path(explicit)] if explicit else [
        ROOT.parent / "phillip", BUNDLE / "phillip", BUNDLE / "phillip-master",
        BUNDLE.parent / "phillip", BUNDLE.parent / "phillip-master"]
    # Windows' Extract All makes phillip-master\phillip-master.
    candidates += [c / c.name for c in candidates]
    for c in candidates:
        if (c / "agents" / agent / "params").is_file():
            return c
    where = "\n  ".join(str(c) for c in candidates)
    fail(f"cannot find Phillip's agent '{agent}'. Looked in:\n  {where}\n"
         "Get it from https://github.com/vladfi1/phillip (git clone, or Code > Download ZIP and "
         "extract it next to this folder), or pass --phillip <folder>.")


def pick_disc_dialog():
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="Choose your Super Smash Bros. Melee disc image (NTSC-U 1.02)",
            filetypes=[("Disc images", "*.iso *.gcm *.ciso *.rvz"), ("All files", "*.*")])
        root.destroy()
    except Exception:  # noqa: BLE001 - no display, no dialog
        return None
    return path or None


def find_disc(explicit, settings):
    for src in (explicit, os.environ.get("MELEE_DISC"), settings.get("disc")):
        if src and Path(src).is_file():
            return Path(src)
    print("play: which disc image? (a file dialog is open; it may be behind this window)", flush=True)
    picked = pick_disc_dialog()
    if picked and Path(picked).is_file():
        settings["disc"] = str(Path(picked).resolve())
        save_settings(settings)
        return Path(picked)
    fail("no disc image. Pass --iso <path to your NTSC-U 1.02 .iso>"
         + (" (or set MELEE_DISC)." if not WINDOWS else "."))


def uv_command():
    uv = shutil.which("uv")
    if uv:
        return [uv]
    if importlib.util.find_spec("uv") is not None:  # pip-installed, Scripts not on PATH
        return [sys.executable, "-m", "uv"]
    return None


def weights_path(agent, delay):
    stem = agent.replace("/", "_") + (f"_delay{delay}" if delay is not None else "")
    return HERE / "weights" / f"{stem}.npz"


def export(agents, phillip, delay=None, out=None):
    """Export these agents' weights in one TensorFlow run."""
    cmd = [str(HERE / "export_weights.py"), "--phillip", str(phillip)]
    for a in agents:
        cmd += ["--agent", a]
    if out is not None:
        cmd += ["--out", str(out)]
    if delay is not None:
        cmd += ["--delay", str(delay)]
    uv = uv_command()
    if uv is None:
        fail(f"the agents' weights are not exported yet, and that needs uv: {PIP} uv"
             + ("" if WINDOWS else "  (Arch/CachyOS: sudo pacman -S uv)"))
    print(f"play: exporting {', '.join(agents)} from Phillip's checkpoints with TensorFlow 2.13.\n"
          "      First run only: uv downloads Python 3.11 and TensorFlow (~250 MB), "
          "which takes a few minutes ...", flush=True)
    try:
        subprocess.run([*uv, "run", *UV_EXPORT, *cmd], check=True)
    except subprocess.CalledProcessError:
        fail("the export failed (see the error above). If it was a download error, check your internet "
             "connection and run play again; otherwise please report the error.")


def ensure_weights(args):
    out = weights_path(args.agent, args.delay)
    if not out.exists():
        export([args.agent], args.phillip, args.delay, out)
    return out


def ensure_roster(args):
    """Export whatever the roster lacks, write the roster file, return its path."""
    chosen, skipped = roster.choose(args.phillip, args.reaction)
    if not chosen:
        fail(f"no usable agents in {args.phillip}/agents")
    missing = [a for a in roster.agents_in(chosen) if not weights_path(a, None).exists()]
    if missing:
        export(missing, args.phillip)
    path = HERE / "weights" / "roster.json"
    path.parent.mkdir(exist_ok=True)
    roster.write(path, chosen, lambda a: weights_path(a, None))
    print(f"play: agents: {roster.summary(chosen)}", flush=True)
    if skipped:
        print(f"play: not in your phillip checkout: {', '.join(a for a, _ in skipped)} (Phillip's "
              "Google Drive zip has them; see tools/agent/NOTES.md)", flush=True)
    return path


SLIPPI_ENV = HERE / "slippi-env"
SLIPPI_REQUIREMENTS = HERE / "slippi-requirements.txt"
SLIPPI_MODELS = {
    # The model slippi-ai's README offers for local play (12 characters).
    "medium-v2": "https://www.dropbox.com/scl/fi/lpi9krfei1knfvfw7up7v/medium-v2"
                 "?rlkey=qmah3qfz5anwva93x48zcx01k&st=sxo8hbeb&dl=1",
}
SLIPPI_FOLDER = ("https://www.dropbox.com/scl/fo/mg916t9exid4stqmx2bjf/"
                 "AD2oysY7SbTa6N0u7j75-SA?rlkey=baqxnfxg2uytvcz62w9o8mwzt&st=eil5kcql&dl=0")


def slippi_python():
    return SLIPPI_ENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def ensure_slippi_env():
    """slippi-ai's Python 3.12 environment, from slippi-requirements.txt. Rebuilt
    when that file changes. None when it cannot be made."""
    import hashlib

    want = hashlib.sha256(SLIPPI_REQUIREMENTS.read_bytes()).hexdigest()
    stamp = SLIPPI_ENV / "requirements.sha256"
    if slippi_python().exists() and stamp.exists() and stamp.read_text().strip() == want:
        return slippi_python()
    uv = uv_command()
    if uv is None:
        print(f"play: slippi-ai needs uv to set up its environment: {PIP} uv", flush=True)
        return None
    print("play: setting up the newer Phillip (slippi-ai): Python 3.12, TensorFlow and slippi-ai\n"
          "      into tools/agent/slippi-env (about 2.5 GB). First run only; this takes a while ...",
          flush=True)
    try:
        if not slippi_python().exists():
            subprocess.run([*uv, "venv", "-q", "--python", "3.12", str(SLIPPI_ENV)], check=True)
        subprocess.run([*uv, "pip", "install", "--python", str(slippi_python()),
                        "-r", str(SLIPPI_REQUIREMENTS)], check=True)
    except subprocess.CalledProcessError:
        print("play: setting up slippi-ai failed (see above)", flush=True)
        return None
    stamp.write_text(want)
    return slippi_python()


def ensure_slippi_model(explicit):
    """The model file: --slippi-model, else medium-v2 (downloaded once)."""
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    path = HERE / "weights" / "slippi" / "medium-v2"
    if path.is_file():
        return path
    import urllib.request

    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    print("play: downloading slippi-ai's medium-v2 model from Dropbox ...", flush=True)
    try:
        with urllib.request.urlopen(SLIPPI_MODELS["medium-v2"], timeout=60) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        part.replace(path)
    except OSError as e:
        part.unlink(missing_ok=True)
        print(f"play: could not download it ({e}). Get medium-v2 from {SLIPPI_MODELS['medium-v2']}\n"
              f"      (or another model from {SLIPPI_FOLDER})\n"
              f"      and save it as {path}, or pass --slippi-model <file>.", flush=True)
        return None
    return path


def probe_slippi(python, model):
    """Time the model once on this machine; the result is kept beside it.
    Returns the CKinds the model plays, or None when it cannot run."""
    out = model.with_name(model.name + ".probe.txt")
    if not out.exists() or "\nckinds:" not in out.read_text():  # older probes lack the CKinds
        print(f"play: timing {model.name} on this machine (once) ...", flush=True)
        r = subprocess.run([str(python), str(HERE / "slippi_agent.py"), "--probe", str(model),
                            "--async-inference"], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-2000:], file=sys.stderr)
            return None
        out.write_text(r.stdout)
    ckinds = []
    for line in out.read_text().splitlines():
        if line.startswith(("type:", "load:")):
            print(f"play: slippi-ai {line}", flush=True)
        elif line.startswith("ckinds:"):
            ckinds = [int(c) for c in line.split(":", 1)[1].split(",") if c.strip()]
    return ckinds


GOMI_CKINDS = (0x08, 0x02)  # Mario and Fox (gomi_brain.CHARACTERS)


def gomi_status():
    """Whether Gomihyu's model answers, in one line."""
    import gomi_brain

    llm = gomi_brain.Ollama(os.environ.get("GOMI_OLLAMA_URL") or gomi_brain.DEFAULT_URL,
                           os.environ.get("GOMI_MODEL") or gomi_brain.DEFAULT_MODEL)
    why = llm.check()
    if why:
        return f"Gomihyu plays Mario and Fox on her rules for now: {why}"
    return f"Gomihyu plays Mario and Fox ({llm.model} via Ollama)"


SHEIK = 0x13  # picked by holding A on Zelda as the match loads: not a character select icon


def css_chars(model_ckinds=(), roster_ckinds=()):
    """MELEE_AGENT_CSS_CHARS: the characters an AI will play, for the AI's
    door to open with one of them at random. Sheik left out."""
    return ",".join(str(c) for c in sorted(set(model_ckinds) | set(roster_ckinds)) if c != SHEIK)


def agent_params(phillip, agent):
    with open(Path(phillip) / "agents" / agent / "params") as f:
        params = json.load(f)
    params.update(params.get("agent", {}))
    return params


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", help="your NTSC-U 1.02 disc image")
    ap.add_argument("--melee", help="the game binary (found automatically)")
    ap.add_argument("--phillip", help="phillip checkout (found automatically)")
    ap.add_argument("--agent", help="one agent for every character (default: the roster)")
    ap.add_argument("--brain", choices=["auto", "slippi", "classic"], default="auto")
    ap.add_argument("--slippi-model", help="a slippi-ai model file (default: medium-v2)")
    ap.add_argument("--reaction", type=int, default=0, choices=[0, 1, 2])
    ap.add_argument("--port", type=int, default=2, choices=[1, 2, 3, 4])
    ap.add_argument("--delay", type=int)
    ap.add_argument("--epsilon", type=float, default=0.0)
    ap.add_argument("--sync", choices=["lockstep", "async"], default="lockstep")
    ap.add_argument("--timeout-ms", type=float, default=4.0)
    ap.add_argument("--frame-lag", type=int, default=0)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--random-stages", choices=["legal", "all"], default="legal")
    ap.add_argument("--box-controller", choices=["on", "off"],
                    help="read sticks as Dolphin does, for a box controller (remembered)")
    ap.add_argument("--gomi", action="store_true",
                    help="Gomihyu, a language model through Ollama, takes over P2 as Mario or Fox")
    ap.add_argument("--p2-pick", choices=["on", "off"], default="on",
                    help="opening the AI's door on the character select gives it a random "
                         "character an AI plays (default on)")
    ap.add_argument("--record", type=Path)
    ap.add_argument("--tcp", action="store_true")
    ap.add_argument("--opponent", help="with --quick: the other port's character (Phillip name, "
                                       "default the agent's own)")
    args = ap.parse_args()

    try:
        import numpy  # noqa: F401
    except ImportError:
        fail(f"the agent needs numpy: {PIP} numpy"
             + ("" if WINDOWS else "  (Arch/CachyOS: sudo pacman -S python-numpy)"))
    if args.delay is not None and args.agent is None:
        fail("--delay applies to one agent: give --agent with it")
    settings = load_settings()
    if args.box_controller is not None:
        settings["box_controller"] = args.box_controller == "on"
        save_settings(settings)
    melee = find_melee(args.melee)
    if args.gomi and args.agent is not None:
        fail("--gomi takes over P2 by itself: leave out --agent")
    if not args.gomi or args.quick:  # Gomihyu alone needs no Phillip
        args.phillip = find_phillip(args.phillip, args.agent or "FalconFalconBF")
    disc = find_disc(args.iso, settings)
    python = sys.executable
    agent_script = HERE / "agent.py"
    if args.gomi:
        # She takes over P2: slippi-ai and the 2017 agents aren't loaded at all.
        agent_source = ["--gomi"]
        ai_chars = ",".join(str(c) for c in GOMI_CKINDS)
        print(f"play: {gomi_status()}; slippi-ai and the 2017 agents are off while she plays", flush=True)
    elif args.agent is not None:
        agent_source = ["--weights", str(ensure_weights(args))]
        char = agent_params(args.phillip, args.agent).get("char")
        ai_chars = css_chars(roster_ckinds=[phillip_obs.CKIND_BY_PHILLIP_NAME[char]]
                             if char in phillip_obs.CKIND_BY_PHILLIP_NAME else [])
    else:
        roster_path = ensure_roster(args)
        agent_source = ["--roster", str(roster_path)]
        ai_chars = css_chars(roster_ckinds=roster.read(roster_path))
        if args.brain != "classic":
            slippi = ensure_slippi_env()
            model = ensure_slippi_model(args.slippi_model) if slippi else None
            model_ckinds = probe_slippi(slippi, model) if slippi and model else None
            if model_ckinds is not None:
                python, agent_script = str(slippi), HERE / "slippi_agent.py"
                agent_source = ["--model", str(model), "--brain", args.brain, "--async-inference",
                                *agent_source]
                ai_chars = css_chars(model_ckinds, [] if args.brain == "slippi"
                                     else roster.read(roster_path))
            elif args.brain == "slippi":
                fail("slippi-ai is not available (see above)")
            else:
                print("play: slippi-ai is not available (see above): the 2017 agents play", flush=True)

    if WINDOWS or args.tcp:
        sock = bridge.free_tcp_address()
    else:
        sock_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        sock = str(Path(sock_dir) / f"melee-agent-{os.getpid()}.sock")
    env = dict(os.environ)
    env.update(MELEE_AGENT_SOCKET=sock, MELEE_AGENT_PORT=str(args.port), MELEE_AGENT_SYNC=args.sync,
               MELEE_AGENT_TIMEOUT_MS=str(args.timeout_ms))
    # melee-pc's background prewarm can race the game's own boot-time load of
    # LbRb.dat, which then reads as zeros and stops the game (NOTES.md).
    env.setdefault("MELEE_PREWARM", "0")
    env["MELEE_AGENT_RANDOM_STAGES"] = args.random_stages
    if settings.get("box_controller"):
        env.setdefault("MELEE_BOX_CONTROLLER", "1")
        print("play: box controller: sticks read as Dolphin does (--box-controller off to undo)", flush=True)
    if args.p2_pick == "on" and ai_chars:
        env["MELEE_AGENT_CSS_CHARS"] = ai_chars
    if args.quick:
        if args.port > 2:
            fail("--quick seats ports 1 and 2 only; use --port 1 or 2")
        params = agent_params(args.phillip, args.agent or "FalconFalconBF")
        me = phillip_obs.CKIND_BY_PHILLIP_NAME.get(params.get("char"), 0)
        them = phillip_obs.CKIND_BY_PHILLIP_NAME.get(args.opponent or params.get("char"), me)
        chars = [them, them]
        chars[args.port - 1] = me
        env.update(MELEE_BOOT_SCENE="vs", MELEE_DEBUG_VS_CHARS=f"{chars[0]},{chars[1]}")
        stage = phillip_obs.STKIND_BY_PHILLIP_NAME.get(params.get("stage", "final_destination"))
        if stage is not None and stage != 0x20:  # the debug match is on Final Destination already
            env["MELEE_DEBUG_VS_STAGE"] = str(stage)

    who = "Gomihyu" if args.gomi else args.agent or "Phillip's agents"
    print(f"play: {melee} with {who} on P{args.port}; disc {disc}", flush=True)
    game = subprocess.Popen([str(melee), str(disc)], cwd=str(melee.parent), env=env)
    agent_cmd = [python, str(agent_script), *agent_source, "--socket", sock, "--epsilon", str(args.epsilon)]
    if agent_script.name == "agent.py":
        agent_cmd += ["--frame-lag", str(args.frame_lag)]
    if args.seed is not None:
        agent_cmd += ["--seed", str(args.seed)]
    if args.record is not None:
        if agent_script.name != "agent.py":
            fail("--record works with the 2017 agents only: add --brain classic")
        agent_cmd += ["--record", str(args.record)]
    agent = subprocess.Popen(agent_cmd)
    restarts = 0

    def stop(*_):
        for p in (agent, game):
            if p.poll() is None:
                p.terminate()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while game.poll() is None:
            if agent.poll() is not None:
                if restarts >= 5:
                    print("play: the agent keeps dying; the game carries on without it", flush=True)
                    game.wait()
                    break
                restarts += 1
                print(f"play: agent exited ({agent.returncode}); restarting it", flush=True)
                time.sleep(1.0)
                agent = subprocess.Popen(agent_cmd)
            time.sleep(0.2)
    finally:
        stop()
        for p in (agent, game):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    sys.exit(game.returncode or 0)


if __name__ == "__main__":
    main()
