#!/usr/bin/env python3
"""Training Lab check (src/pc/lab.h), on your machine.

Boots Captain Falcon vs Captain Falcon on Battlefield with MELEE_LAB=1 and
drives P1 through the keyboard path (MELEE_KEY_FIFO), pressing the Lab's
D-pad keys (H F G T) while the bridge only watches. P1 saves state, records
P2 dashing and jumping (P1's keys drive P2), plays it back on a loop, and
loads the state.

The match's own frame counter is part of the saved state, so a load or a
playback loop sends it back: that is how ticks are lined up. Checks: each
action's "lab:" line; P1 stands still and P2 moves while recording; every
playback loop is the recording again, both fighters, field for field
(position, action, action frame, percent); loading the state gives the tick
it was saved on.

    python3 tools/agent/check_lab.py --iso /path/to/GALE01.iso
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge  # noqa: E402
import harness  # noqa: E402

FRAMES = 2400
P1, P2 = 0, 1
# (play frame, FIFO line): T F G H are the Lab's D-pad on the keyboard.
KEYS = [(10, "H 100"),        # save state
        (60, "G 100"),        # record P2
        (90, "Right 600"),    # P1's keys drive P2: dash right...
        (140, "V 80"),        # ...and jump
        (200, "G 100"),       # stop recording
        (260, "T 100"),       # play it back, on a loop
        (780, "F 100")]       # load state (which stops the playback mid-loop)
END = 860


def fighter_key(f):
    return (round(f.cur_x, 4), round(f.cur_y, 4), f.motion_id, round(f.action_frame, 4), round(f.percent_f, 4))


def segments(states):
    """The fight's fighters-ran states, split wherever the match frame went back (a load or a loop)."""
    segs, cur, last = [], [], None
    for st in states:
        if not (st.in_fight and st.fighters_ran):
            continue
        if last is not None and st.vs_frame <= last:
            segs.append(cur)
            cur = []
        cur.append(st)
        last = st.vs_frame
    if cur:
        segs.append(cur)
    return segs


def analyze(report, states, log, rec_frames=None):
    for line in ("lab: Training Lab on", "lab: saved state", "lab: recording P2", "lab: recorded ",
                 "lab: playing back P2's recording", "lab: loaded state"):
        report.check(f"log: {line.strip()}", line in log)
    if rec_frames is None:
        import re
        m = re.search(r"lab: recorded (\d+) frames", log)
        rec_frames = int(m.group(1)) if m else 0
    report.check("recorded a few seconds", 60 <= rec_frames <= 400, f"{rec_frames} frames")
    segs = segments(states)
    report.check("loads and loops sent the match frame back", len(segs) >= 4, f"{len(segs)} stretches")
    if len(segs) < 4 or rec_frames <= 0:
        return report
    first = {st.vs_frame: st for st in segs[0]}
    loops = [s for s in segs[1:-1]]
    load = segs[-1]
    starts = {s[0].vs_frame for s in loops}
    report.check("every loop starts at the recording's start", len(starts) == 1, f"starts {sorted(starts)}")
    v0 = min(starts)
    rec = [first.get(v) for v in range(v0, v0 + rec_frames)]
    rec = [st for st in rec if st is not None]
    report.check("the recording's frames are in the first stretch", len(rec) >= rec_frames - 2,
                 f"{len(rec)} of {rec_frames}")
    if not rec:
        return report
    p1 = {fighter_key(st.fighters[P1])[:2] for st in rec[5:]}
    report.check("P1 stands still while recording", len(p1) == 1, f"{len(p1)} positions")
    xs = [st.fighters[P2].cur_x for st in rec]
    report.check("P2 moves while recording (your keys drive it)", max(xs) - min(xs) > 10,
                 f"x {min(xs):.1f} .. {max(xs):.1f}")
    full = [s for s in loops if len(s) >= rec_frames]
    report.check("at least two whole playback loops", len(full) >= 2, f"{len(full)} of {len(loops)}")
    mismatches, compared = [], 0
    for n, seg in enumerate(loops):
        for st in seg[:rec_frames]:
            want = first.get(st.vs_frame)
            if want is None:
                continue
            for port in (P1, P2):
                compared += 1
                a, b = fighter_key(want.fighters[port]), fighter_key(st.fighters[port])
                if a != b:
                    mismatches.append(f"loop {n + 1} frame {st.vs_frame} P{port + 1}: recorded {a}, played {b}")
    report.check("every playback loop is the recording, field for field", compared > 0 and not mismatches,
                 f"{compared} fighter-frames, {len(mismatches)} differ"
                 + (f"; first: {mismatches[0]}" if mismatches else ""))
    saved = first.get(load[0].vs_frame)
    report.check("loading goes back to the saved tick", saved is not None and load[0].vs_frame < v0,
                 f"match frame {load[0].vs_frame}")
    if saved is not None:
        same = all(fighter_key(saved.fighters[p]) == fighter_key(load[0].fighters[p]) for p in (P1, P2))
        report.check("the loaded tick is the saved one", same,
                     f"saved {fighter_key(saved.fighters[P1])}, loaded {fighter_key(load[0].fighters[P1])}")
    return report


def run(args):
    report = harness.Report("Training Lab: savestates, record and playback")
    keys = list(KEYS)
    with harness.GameRun(args.melee, args.iso, FRAMES, keep=args.keep, label="lab",
                         extra_env={"MELEE_LAB": "1"}) as game:
        client = game.connect()
        clock = harness.PlayClock()
        states = []
        try:
            while True:
                st = client.recv_state(timeout=15)
                if st is None:
                    break
                frame = clock.feed(st)
                states.append(st)
                while keys and frame is not None and frame >= keys[0][0]:
                    game.keys(keys.pop(0)[1])
                client.send_input(st.tick + 1, bridge.Pad.neutral(), release=True)   # the bridge only watches
                if frame is not None and frame >= END:
                    break
        except ConnectionError:
            pass
        finally:
            client.close()
        game.stop()
        log = game.log_text()
    return analyze(report, states, log)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    harness.base_args(ap)
    args = ap.parse_args()
    harness.require_disc(args)
    ok = run(args).print()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
