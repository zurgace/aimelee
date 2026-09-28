#!/usr/bin/env python3
"""Phase 2 check: input injection on the agent port, on your machine.

Boots Captain Falcon vs Captain Falcon on Battlefield with the bridge on and
drives P2 (the agent port) through inject_script's moves, one input per tick
in lockstep, while P1 is driven at the same time through the keyboard path
(MELEE_KEY_FIFO). Then P2 walks up to P1 and jabs until it lands hits.

Checks: P2 walks right then left (x moves each way), short-hops (KneeBend,
airborne, lands), jabs (Attack11), shields (GuardOn/Guard, shield health
drops); P1 moves and jumps meanwhile (your port still works); P2's hits land
on P1 (percent rises, damage state, hitlag on both); the pad the tick
consumed is exactly what was sent, and matches the Slippi recorder's raw
pad; no input arrived late. Then the agent disconnects mid-fight: the game
keeps running, a second client connects, and P2 is back to a neutral pad.

    python3 tools/agent/check_phase2.py --iso /path/to/GALE01.iso
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge  # noqa: E402
import dump_state  # noqa: E402
import harness  # noqa: E402
import inject_script  # noqa: E402

FRAMES = 2400
P1, P2 = 0, 1
WALKS = {0x0F, 0x10, 0x11}
TURN = 0x12
KNEE_BEND = 0x18
JUMPS = {0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F}  # jump, aerial jump or falling from one
GUARD = {0xB2, 0xB3}
ATTACK11 = 0x2C
DAMAGE = range(0x4B, 0x5C)

P1_KEYS = [(5, "Left 250"), (160, "V 80")]  # P1 dashes left right after GO!, then jumps
HIT_START = 360    # frame P2 starts walking to P1
HIT_END = 900      # frame the agent disconnects
REJOIN_TICKS = 180  # states the second client reads


def moves_between(states, clock_frames, port, a, b):
    return [st for st, fr in zip(states, clock_frames) if fr is not None and a <= fr < b]


def run(args):
    report = harness.Report("Phase 2: input injection")
    script = inject_script.demo_script()
    sent = {}          # tick -> Pad sent for it
    hit_log = []
    p1_keys = list(P1_KEYS)
    with harness.GameRun(args.melee, args.iso, FRAMES, keep=args.keep, label="phase2") as game:
        client = game.connect()
        port = client.hello.agent_port
        report.check("agent port is P2 in lockstep", port == P2 and client.hello.sync_mode == bridge.SYNC_LOCKSTEP)
        clock = harness.PlayClock()
        states, frames = [], []
        jab_count = 0
        try:
            while True:
                st = client.recv_state(timeout=15)
                if st is None:
                    break
                frame = clock.feed(st)
                states.append(st)
                frames.append(frame)
                while p1_keys and frame is not None and frame >= p1_keys[0][0]:
                    game.keys(p1_keys.pop(0)[1])
                if frame is None or frame < 0:
                    client.send_input(st.tick + 1, bridge.Pad.neutral(), release=True)
                    continue
                nxt = frame + 1
                if nxt < HIT_START:
                    p, _ = inject_script.pad_for(script, nxt)
                else:
                    # Walk to P1, then jab every 24 frames (A for 2) while close.
                    me, them = st.fighters[P2], st.fighters[P1]
                    dx = them.cur_x - me.cur_x
                    if abs(dx) > 14:
                        p = inject_script.pad(x=inject_script.WALK if dx > 0 else -inject_script.WALK)
                        jab_count = 0
                    else:
                        p = inject_script.pad(button=bridge.BUTTON_A if jab_count % 24 < 2 else 0)
                        jab_count += 1
                    hit_log.append((frame, them.percent_f, them.motion_id, them.hitlag, me.hitlag))
                sent[st.tick + 1] = p
                client.send_input(st.tick + 1, p)
                if frame >= HIT_END:
                    break
        except ConnectionError:
            pass  # the game ended the run first; the checks below say what was missed
        finally:
            client.close()  # the agent goes away mid-fight
        # A second client: the game must still be running and P2 neutral again.
        rejoin = []
        try:
            second = game.connect(timeout=10)
            for _ in range(REJOIN_TICKS):
                st = second.recv_state(timeout=5)
                if st is None:
                    break
                rejoin.append(st)
            second.close()
        except ConnectionError as e:
            report.check("second client connects after the first leaves", False, str(e))
        code = game.wait()
        log = game.log_text()
        slp = game.slp_file()

    report.check("game exits cleanly", code == 0, f"exit {code}")
    fight = [(st, fr) for st, fr in zip(states, frames) if fr is not None and fr >= 0]
    report.check("reached GO! and ran the script", len(fight) > HIT_START, f"{len(fight)} frames after GO!")
    if len(fight) <= HIT_START:
        return report

    def window(a, b):
        return [st for st, fr in fight if a <= fr < b]

    # Exactly what was sent is what the tick consumed, on P2 only. (A tick
    # with no fresh pad sample reruns the last one; nothing to inject into.)
    driven = [st for st in states if st.tick in sent and st.flags & bridge.ST_PAD_FRESH]
    mism = [st.tick for st in driven
            and (st.fighters[P2].input.button, st.fighters[P2].input.stick_x, st.fighters[P2].input.stick_y,
                 st.fighters[P2].input.trigger_l, st.fighters[P2].input.err)
            != (sent[st.tick].button, sent[st.tick].stick_x, sent[st.tick].stick_y, sent[st.tick].trigger_l, 0)]
    report.check("P2 consumed exactly the injected pad every tick", not mism and sent,
                 f"{len(sent)} ticks driven" + (f", first mismatch tick {mism[0]}" if mism else ""))
    late = [st for st in states if st.tick in sent and st.flags & bridge.ST_INPUT_LATE]
    report.check("no late inputs (lockstep)", not late, f"{len(late)} late ticks")
    report.check("every driven tick flagged as agent input",
                 driven and all(st.flags & bridge.ST_AGENT_INPUT for st in driven))

    seg = {s.name: s for s in script}
    w = window(seg["walk right"].start + 2, seg["walk right"].end + 1)
    report.check("P2 walks right", any(st.fighters[P2].motion_id in WALKS for st in w)
                 and w[-1].fighters[P2].cur_x > w[0].fighters[P2].cur_x + 5,
                 f"dx {w[-1].fighters[P2].cur_x - w[0].fighters[P2].cur_x:+.2f}, states "
                 + ",".join(sorted({dump_state.motion_name(st.fighters[P2].motion_id) for st in w})))
    w = window(seg["walk left"].start + 2, seg["walk left"].end + 1)
    report.check("P2 turns and walks left", any(st.fighters[P2].motion_id in WALKS | {TURN} for st in w)
                 and w[-1].fighters[P2].cur_x < w[0].fighters[P2].cur_x - 5,
                 f"dx {w[-1].fighters[P2].cur_x - w[0].fighters[P2].cur_x:+.2f}")
    w = window(seg["short hop"].start, seg["short hop"].start + 60)
    seq = [st.fighters[P2].motion_id for st in w]
    knee = next((i for i, m in enumerate(seq) if m == KNEE_BEND), None)
    jump = next((i for i, m in enumerate(seq) if m in JUMPS), None)
    report.check("P2 short hop: KneeBend then airborne", knee is not None and jump is not None and knee < jump
                 and any(st.fighters[P2].in_air for st in w), f"KneeBend +{knee}, jump +{jump}")
    report.check("P2 lands again", not w[-1].fighters[P2].in_air)
    w = window(seg["jab"].start, seg["jab"].start + 30)
    report.check("P2 jabs (Attack11)", any(st.fighters[P2].motion_id == ATTACK11 for st in w))
    w = window(seg["shield"].start, seg["shield"].end + 1)
    report.check("P2 shields (GuardOn/Guard)", any(st.fighters[P2].motion_id in GUARD for st in w)
                 and min(st.fighters[P2].shield for st in w) < w[0].fighters[P2].shield,
                 f"shield {w[0].fighters[P2].shield:.2f} -> {min(st.fighters[P2].shield for st in w):.2f}")

    # P1 through the keyboard path at the same time.
    early = [st for st, fr in zip(states, frames) if fr is not None and 0 <= fr < 60]
    report.check("P1 (keyboard path) moves while P2 is injected",
                 early and min(st.fighters[P1].cur_x for st in early) < early[0].fighters[P1].cur_x - 5)
    w = window(160, 220)
    report.check("P1 (keyboard path) jumps while P2 is injected",
                 any(st.fighters[P1].motion_id == KNEE_BEND for st in w)
                 and any(st.fighters[P1].in_air for st in w))

    # P2's jabs land on P1.
    pct = [h[1] for h in hit_log]
    report.check("P2 hits P1: percent rises", pct and max(pct) > pct[0],
                 f"P1 {pct[0]:.0f}% -> {max(pct):.0f}%" if pct else "no approach frames")
    report.check("P1 enters a damage state with hitlag", any(h[2] in DAMAGE for h in hit_log)
                 and any(h[3] > 0 for h in hit_log))
    report.check("P2 gets hitlag on contact", any(h[4] > 0 for h in hit_log))

    # The agent left mid-fight: the game carried on and P2 is neutral again.
    report.check("game kept running after the agent disconnected", len(rejoin) >= REJOIN_TICKS // 2,
                 f"{len(rejoin)} states on the second connection")
    if rejoin:
        tail = rejoin[len(rejoin) // 2:]
        report.check("P2 back to a neutral, connected pad", all(
            st.fighters[P2].input.button == 0 and st.fighters[P2].input.stick_x == 0
            and st.fighters[P2].input.err == 0 and not st.flags & bridge.ST_AGENT_INPUT for st in tail))
    report.check("drop logged", "agent: client dropped" in log)
    if slp is not None:
        harness.compare_with_slp(report, states, slp)
    else:
        report.check("slp recorded", False, "no .slp in the run directory")
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
