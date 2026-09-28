#!/usr/bin/env python3
"""Check that the bridge changes nothing while it is off.

Runs the four-CPU debug match (MELEE_DEBUG_VS=cpu4: busy, and needs no
input) with a fixed MELEE_SEED on an upstream melee-pc build twice and on
this branch's build once, bridge env unset, and compares the Slippi
recorder's frame events byte for byte:

  upstream run 1 vs upstream run 2   -> the match is deterministic here
  upstream run 1 vs this branch      -> the branch plays identically

It also reports (INFO, not pass/fail) how the branch plays with
MELEE_AGENT_SOCKET set but no agent connected, and runs the smoke test.

Build upstream beside this checkout first, e.g.:

    git worktree add ../melee-pc-upstream upstream/master
    cmake -S ../melee-pc-upstream -B ../melee-pc-upstream/build -G Ninja
    ninja -C ../melee-pc-upstream/build melee

    python3 tools/agent/check_vanilla.py --iso /path/to/GALE01.iso \\
        --upstream ../melee-pc-upstream/build/melee
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402
import slp_read  # noqa: E402

FRAMES = 1800


def replay(melee, iso, label, keep, bridge_on=False):
    with harness.GameRun(melee, iso, FRAMES, chars=None, stage=None, seed=7, bridge_on=bridge_on,
                         key_fifo=False, extra_env={"MELEE_DEBUG_VS": "cpu4"}, keep=keep,
                         label=label) as game:
        code = game.wait(timeout=180)
        slp = game.slp_file()
        events = slp_read.frame_events(slp) if slp is not None else []
    return code, events


def compare(report, name, a, b, informational=False):
    n = min(len(a), len(b))
    first = next((i for i in range(n) if a[i] != b[i]), None)
    ok = n >= 1000 and first is None
    detail = f"{n} frame events compared" + ("" if first is None else f", first difference at event {first}")
    if informational:
        report.info(name, ("identical, " if ok else "differs, ") + detail)
    else:
        report.check(name, ok, detail)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    harness.base_args(ap)
    ap.add_argument("--upstream", required=True, help="an upstream melee-pc build's melee binary")
    ap.add_argument("--no-smoke", action="store_true", help="skip tools/smoke_test.py")
    args = ap.parse_args()
    harness.require_disc(args)
    report = harness.Report("Bridge off = vanilla")

    runs = {}
    for label, melee, bridge_on in (("upstream-1", args.upstream, False), ("upstream-2", args.upstream, False),
                                    ("branch-off", args.melee, False), ("branch-idle", args.melee, True)):
        print(f"running {label} ...", flush=True)
        code, events = replay(melee, args.iso, label, args.keep, bridge_on)
        report.check(f"{label} exits cleanly with a replay", code == 0 and events, f"exit {code}, "
                     f"{len(events)} frame events")
        runs[label] = events

    deterministic = compare(report, "upstream is deterministic run to run", runs["upstream-1"], runs["upstream-2"])
    same = compare(report, "branch with the bridge off plays identically", runs["upstream-1"], runs["branch-off"])
    if not deterministic and not same:
        report.info("note", "upstream differs from itself, so the branch comparison is inconclusive")
    compare(report, "branch with MELEE_AGENT_SOCKET set, no agent", runs["upstream-1"], runs["branch-idle"],
            informational=True)

    if not args.no_smoke:
        env_disc = {"MELEE_DISC": args.iso}
        import os
        proc = subprocess.run([sys.executable, str(harness.ROOT / "tools/smoke_test.py")],
                              env={**os.environ, **env_disc}, capture_output=True, text=True)
        tail = proc.stdout.strip().splitlines()[-1:] or [""]
        report.check("tools/smoke_test.py", proc.returncode == 0, tail[0])
    sys.exit(0 if report.print() else 1)


if __name__ == "__main__":
    main()
