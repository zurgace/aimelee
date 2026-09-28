#!/usr/bin/env python3
"""Phase 1 check: the bridge's state export, on your machine with your disc.

Boots straight into Captain Falcon vs Captain Falcon on Battlefield (debug VS,
MELEE_DEBUG_VS_CHARS), records every tick the bridge sends and, through
MELEE_KEY_FIFO (the keyboard path, port 1), makes P1 run right, jump and jab
at fixed points of the match. Then it checks:

  - the tick counter is +1 per tick, the fight is detected once, the match
    tick restarts at 0 and counts up;
  - both ports hold Falcon (CKind 0 / FighterKind 2), human slots, stage 0x1F;
  - Falcon enters (Entry 0x142-0x144) and reaches Wait (0x0E);
  - P1's commands show up on P1 only: Dash/Run with x growing and the raw
    stick at +80, KneeBend then JumpF/JumpB with y rising, then Attack11;
  - the mirrors agree (HUD vs float percent, nametag vs cur_pos, facing ±1);
  - every replay frame agrees field by field with the Slippi recorder, an
    independent serializer of the same fighters;
  - it prints the spawn positions: the regression baseline for NOTES.md.

    python3 tools/agent/check_phase1.py --iso /path/to/GALE01.iso
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge  # noqa: E402
import dump_state  # noqa: E402
import harness  # noqa: E402

FRAMES = 1500
ENTRY = range(0x142, 0x145)
WAIT = 0x0E
RUNNING = {0x0F, 0x10, 0x11, 0x14, 0x15}  # WalkSlow/Middle/Fast, Dash, Run
KNEE_BEND = 0x18
JUMPS = {0x19, 0x1A}
ATTACK11 = 0x2C

# match_tick -> key line for P1 (MELEE_KEY_FIFO, SDL scancode names; arrows = stick,
# V = Y (jump), X = A).
SCRIPT = [(200, "Right 400"), (330, "V 80"), (450, "X 60")]


def run(args):
    report = harness.Report("Phase 1: state export")
    script = list(SCRIPT)
    with harness.GameRun(args.melee, args.iso, FRAMES, keep=args.keep, label="phase1") as game:
        client = game.connect()
        report.check("bridge handshake", client.hello.proto_version == bridge.PROTO_VERSION,
                     f"melee-pc {client.hello.build}, agent port P{client.hello.agent_port + 1}")
        sent = {}

        def on_state(st):
            while script and st.in_fight and st.match_tick >= script[0][0]:
                at, line = script.pop(0)
                game.keys(line)
                sent[line] = st.tick

        states = harness.collect(client, on_state)
        client.close()
        code = game.wait()
        report.check("game exits cleanly", code == 0, f"exit {code}")
        slp = game.slp_file()
        log = game.log_text()

        checker = dump_state.Checker()
        for st in states:
            checker.feed(st)
        in_fight = sum(1 for st in states if st.in_fight)
        report.check("states received", len(states) >= FRAMES // 2 and in_fight >= 600,
                     f"{len(states)} states, {in_fight} in the fight")
        for name in ("tick_monotonic", "match_tick_monotonic", "match_start_zero", "facing_unit",
                     "percent_mirror", "motion_range", "pos_mirror"):
            report.check(f"invariant {name}", name not in checker.fails, checker.fails.get(name, ""))
        starts = [st for st in states if st.match_start]
        report.check("one fight detected", len(starts) == 1, f"{len(starts)} match starts")

        fight = [st for st in states if st.in_fight]
        if not fight:
            report.check("reached the fight", False, "no in-fight states; see the run log")
            return report
        first = next((st for st in fight if st.fighters[0].present and st.fighters[1].present), None)
        if first is None:
            report.check("fighters appear", False, "no fight state with both P1 and P2 present")
            return report
        report.check("stage is Battlefield", first.stage == harness.STKIND_BATTLEFIELD,
                     f"stage 0x{first.stage:X}")
        for port in (0, 1):
            f = first.fighters[port]
            report.check(f"P{port + 1} is Captain Falcon (human)",
                         f.present and f.ckind == 0 and f.fkind == 2 and f.slot_type == 0,
                         f"present {f.present} ckind {f.ckind} fkind {f.fkind} slot {f.slot_type}")
            seq = [st.fighters[port].motion_id for st in fight]
            entry_at = next((i for i, m in enumerate(seq) if m in ENTRY), None)
            wait_at = next((i for i, m in enumerate(seq) if m == WAIT), None)
            report.check(f"P{port + 1} Entry then Wait", entry_at is not None and wait_at is not None
                         and entry_at < wait_at, f"Entry at match tick {entry_at}, Wait at {wait_at}")
        spawn = [(st.fighters[0].cur_x, st.fighters[0].cur_y, st.fighters[1].cur_x, st.fighters[1].cur_y)
                 for st in fight if st.fighters[0].motion_id == WAIT and st.fighters[1].motion_id == WAIT][:1]
        if spawn:
            report.info("spawn positions (P1 x,y / P2 x,y, first shared Wait)",
                        "%.4f,%.4f / %.4f,%.4f" % spawn[0])

        # P1 runs right: P1 moves, P2 does not, and the stick shows up in P1's pad.
        t0 = sent.get("Right 400")
        run_states = [st for st in fight if t0 is not None and t0 <= st.tick <= t0 + 60]
        if run_states:
            p1x = [st.fighters[0].cur_x for st in run_states]
            p2x = [st.fighters[1].cur_x for st in run_states]
            moved = any(st.fighters[0].motion_id in RUNNING for st in run_states)
            report.check("P1 dashes/runs on Right", moved,
                         "action states " + ",".join(sorted({dump_state.motion_name(st.fighters[0].motion_id)
                                                            for st in run_states})[:8]))
            report.check("P1 x grows while running", max(p1x) - p1x[0] > 10, f"dx {max(p1x) - p1x[0]:.2f}")
            report.check("P2 stays put meanwhile", max(p2x) - min(p2x) < 1e-3,
                         f"P2 dx {max(p2x) - min(p2x):.4f}")
            report.check("P1 raw pad shows stick x +80",
                         any(st.fighters[0].input.stick_x >= 79 for st in run_states)
                         and all(st.fighters[1].input.stick_x == 0 for st in run_states))
        else:
            report.check("P1 run command sent", False, "the script never reached match tick 200")

        t1 = sent.get("V 80")
        jump_states = [st for st in fight if t1 is not None and t1 <= st.tick <= t1 + 45]
        if jump_states:
            seq = [st.fighters[0].motion_id for st in jump_states]
            knee = next((i for i, m in enumerate(seq) if m == KNEE_BEND), None)
            air = next((i for i, m in enumerate(seq) if m in JUMPS), None)
            report.check("P1 KneeBend then JumpF/JumpB", knee is not None and air is not None and knee < air,
                         f"KneeBend at +{knee}, jump at +{air}")
            y0 = jump_states[0].fighters[0].cur_y
            report.check("P1 rises and is airborne", max(st.fighters[0].cur_y for st in jump_states) > y0 + 5
                         and any(st.fighters[0].in_air for st in jump_states))
        t2 = sent.get("X 60")
        jab_states = [st for st in fight if t2 is not None and t2 <= st.tick <= t2 + 40]
        if jab_states:
            report.check("P1 jabs (Attack11)", any(st.fighters[0].motion_id == ATTACK11 for st in jab_states)
                         or any(0x41 <= st.fighters[0].motion_id <= 0x45 for st in jab_states),
                         "an aerial counts if the jab landed in the air")

        if slp is not None:
            harness.compare_with_slp(report, states, slp)
        else:
            report.check("slp recorded", False, "no .slp in the run directory")
        report.check("no bridge errors in the log", "agent: client dropped (protocol" not in log
                     and "bridge off" not in log)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    harness.base_args(ap)
    args = ap.parse_args()
    harness.require_disc(args)
    ok = run(args).print()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
