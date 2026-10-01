#!/usr/bin/env python3
"""Training Mode: the real TrainingMode-CommunityEdition (TM-CE), in Dolphin.

    python3 tools/agent/tmce.py                    # TM-CE as set up last time
    python3 tools/agent/tmce.py --iso TM-CE.iso    # a TM-CE disc image you already have
    python3 tools/agent/tmce.py --patch TM-CE.xdelta [--disc GALE01.iso]
                                                   # apply TM-CE's patch to your disc first
    python3 tools/agent/tmce.py --dolphin /path/to/dolphin

TM-CE (https://github.com/AlexanderHarrison/TrainingMode-CommunityEdition)
replaces Melee's 1P Event Match with training events: the Training Lab,
ledgedash, L-cancel, edgeguard and more. It is a patch to the GameCube disc
(PowerPC code and data), so it runs in Dolphin, not in AI-Melee's game, and
none of AI-Melee's AIs play in it. AI-Melee ships neither: download TM-CE
from its releases page and Dolphin (or Slippi Dolphin, which Slippi
Launcher installs) yourself.

What it finds by itself (and remembers in settings.json, "tmce"):
  Dolphin   dolphin-emu on PATH, the Dolphin flatpak, Dolphin in Program
            Files, or Slippi Launcher's Dolphin (--dolphin to point elsewhere)
  TM-CE     the disc image picked or patched last time
  your disc for --patch: --disc, else the one AI-Melee plays (settings.json)

--patch needs xdelta3 (Arch/CachyOS: sudo pacman -S xdelta3); without it,
patch with TM-CE's own instructions and pass the result to --iso. The
patched image goes beside the patch, as <patch name>.iso.

Dolphin runs with -b (it closes when you stop the game), and this waits for
it, so the launcher comes back when you close Dolphin.
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import play  # noqa: E402  (no numpy needed at import)

WINDOWS = os.name == "nt"
FLATPAK_ID = "org.DolphinEmu.dolphin-emu"
RELEASES = "https://github.com/AlexanderHarrison/TrainingMode-CommunityEdition/releases/latest"
PATCH_SUFFIXES = (".xdelta", ".vcdiff", ".xdelta3")


def say(msg):
    print(f"tmce: {msg}", flush=True)


def fail(msg):
    print(f"\ntmce: {msg}\n", file=sys.stderr, flush=True)
    sys.exit(1)


def dolphin_candidates(environ=os.environ, home=None, windows=WINDOWS):
    """Where Dolphin usually is, most likely first: plain Dolphin, then Slippi Launcher's (its netplay
    build plays any disc image; its playback build is for replays)."""
    home = Path(home or Path.home())
    if windows:
        out = []
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            if environ.get(var):
                out += [Path(environ[var]) / "Dolphin" / "Dolphin.exe",
                        Path(environ[var]) / "Dolphin-x64" / "Dolphin.exe"]
        if environ.get("LOCALAPPDATA"):
            out.append(Path(environ["LOCALAPPDATA"]) / "Programs" / "Dolphin" / "Dolphin.exe")
        if environ.get("APPDATA"):
            slippi = Path(environ["APPDATA"]) / "Slippi Launcher"
            out += [slippi / "netplay" / "Slippi Dolphin.exe", slippi / "playback" / "Slippi Dolphin.exe"]
        return out
    config = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
    slippi = config / "Slippi Launcher"
    return [slippi / "netplay" / "Slippi_Online-x86_64.AppImage",
            slippi / "playback" / "Slippi_Playback-x86_64.AppImage"]


def flatpak_dolphin(path=None, run=subprocess.run):
    """flatpak's path when Dolphin's flatpak is installed, else None."""
    flatpak = shutil.which("flatpak", path=path)
    if not flatpak:
        return None
    try:
        ok = run([flatpak, "info", FLATPAK_ID], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None
    return flatpak if ok else None


def find_dolphin(tmce, environ=os.environ, home=None, windows=WINDOWS, run=subprocess.run):
    """The command that starts Dolphin (a list), or None: the one picked before if it's still there,
    else dolphin-emu on PATH, the flatpak, then the usual install places."""
    saved = tmce.get("dolphin")
    if saved and Path(saved).is_file():
        return [saved]
    if not windows:
        on_path = shutil.which("dolphin-emu", path=environ.get("PATH"))
        if on_path:
            return [on_path]
        flatpak = flatpak_dolphin(environ.get("PATH"), run)
        if flatpak:
            return [flatpak, "run", FLATPAK_ID]
    for p in dolphin_candidates(environ, home, windows):
        if p.is_file():
            return [str(p)]
    return None


def find_iso(tmce):
    iso = tmce.get("iso")
    return Path(iso) if iso and Path(iso).is_file() else None


def is_patch(path):
    return Path(path).suffix.lower() in PATCH_SUFFIXES


def patched_path(patch):
    return Path(patch).with_suffix(".iso")


def patch_command(xdelta3, patch, disc, out):
    """xdelta3 decoding the patch against your disc (-f: an older attempt is overwritten)."""
    return [str(xdelta3), "-d", "-f", "-s", str(disc), str(patch), str(out)]


def launch_command(dolphin, iso):
    """-b: Dolphin closes when the game stops; -e: boot this disc."""
    return [*dolphin, "-b", "-e", str(iso)]


def apply_patch(patch, disc, xdelta3=None):
    """TM-CE's patch applied to your disc; the patched image's path."""
    xdelta3 = xdelta3 or shutil.which("xdelta3")
    if not xdelta3:
        fail("applying TM-CE's patch needs xdelta3"
             + (" (Arch/CachyOS: sudo pacman -S xdelta3)" if not WINDOWS else "")
             + ". Or patch your disc as TM-CE's release says, and pick the patched .iso instead.")
    if Path(disc).suffix.lower() in (".rvz", ".ciso", ".gcz", ".wia"):
        fail(f"TM-CE's patch applies to a plain .iso of your disc, and {Path(disc).name} is compressed. "
             "Convert it to .iso in Dolphin (right-click it, Convert File...) and pick that.")
    out = patched_path(patch)
    say(f"applying the TM-CE patch {Path(patch).name} to your disc {Path(disc).name}...")
    r = subprocess.run(patch_command(xdelta3, patch, disc, out), capture_output=True, text=True)
    if r.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        why = (r.stderr or r.stdout).strip().splitlines()
        fail("the TM-CE patch didn't apply to your disc"
             + (f" ({why[-1]})" if why else "")
             + ". It needs the NTSC-U 1.02 disc AI-Melee plays (GALE01), as a plain .iso.")
    say(f"TM-CE ready: {out}")
    return out


def run_dolphin(cmd):
    """Dolphin until it closes; its exit code. SIGTERM (the launcher's Quit) closes it too."""
    game = subprocess.Popen(cmd)

    def stop(*_):
        if game.poll() is None:
            game.terminate()
    old = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        game.wait()
    finally:
        stop()
        try:
            game.wait(timeout=10)
        except subprocess.TimeoutExpired:
            game.kill()
        for sig, handler in old.items():
            signal.signal(sig, handler)
    return game.returncode or 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", help="a TM-CE disc image (remembered)")
    ap.add_argument("--patch", help="TM-CE's .xdelta patch: applied to your disc, the result remembered")
    ap.add_argument("--disc", help="with --patch: your NTSC-U 1.02 disc (default: the one AI-Melee plays)")
    ap.add_argument("--dolphin", help="the Dolphin program (remembered)")
    args = ap.parse_args(argv)

    settings = play.load_settings()
    tmce = settings.setdefault("tmce", {})
    if args.dolphin:
        if not Path(args.dolphin).is_file():
            fail(f"no Dolphin at {args.dolphin}")
        tmce["dolphin"] = str(Path(args.dolphin).resolve())
    if args.iso:
        if not Path(args.iso).is_file():
            fail(f"no disc image at {args.iso}")
        if is_patch(args.iso):
            args.patch, args.iso = args.iso, None
        else:
            tmce["iso"] = str(Path(args.iso).resolve())
    dolphin = find_dolphin(tmce)
    if dolphin is None:
        fail("TM-CE runs in Dolphin, and no Dolphin was found. Install Dolphin (dolphin-emu.org) or "
             "Slippi Launcher (slippi.gg), or pass --dolphin <the Dolphin program>.")
    if args.patch:
        if not Path(args.patch).is_file():
            fail(f"no patch at {args.patch}")
        disc = args.disc or play.find_disc(None, settings)
        tmce["iso"] = str(apply_patch(Path(args.patch), Path(disc)).resolve())
    play.save_settings(settings)
    iso = find_iso(tmce)
    if iso is None:
        fail(f"no TM-CE disc image yet: download TM-CE ({RELEASES}) and pass --patch <its .xdelta> "
             "or --iso <a patched TM-CE .iso>.")
    say(f"TM-CE in {' '.join(dolphin)}: {iso}")
    return run_dolphin(launch_command(dolphin, iso))


if __name__ == "__main__":
    sys.exit(main())
