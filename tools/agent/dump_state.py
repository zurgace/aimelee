#!/usr/bin/env python3
"""Connect to a running game's agent bridge and print what it exports.

Start the game with the bridge on, then run this in another terminal:

    MELEE_AGENT_SOCKET=/tmp/melee-agent.sock build/melee <disc.iso>
    python3 tools/agent/dump_state.py --socket /tmp/melee-agent.sock

It never sends input, so the game does not wait on it. Options:

    --every N     print one tick in N (default 30, i.e. twice a second)
    --fight-only  skip menu ticks
    --json        one JSON object per printed tick instead of the table
    --record F    also write every state to F (read back with bridge.read_record)
    --ticks N     stop after N states
    --check       track basic invariants and print PASS/FAIL lines at exit
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge  # noqa: E402
import names  # noqa: E402

SLOT_TYPES = {0: "HMN", 1: "CPU", 2: "DEMO", 3: "--", 4: "BOSS"}


def motion_name(motion_id):
    # Character-specific states (>= 0x155) have per-fighter names we do not table.
    return names.MOTION.get(motion_id, f"special+{motion_id - 0x155}" if motion_id >= 0x155 else "?")


def pad_str(p):
    buttons = "".join(ch for bit, ch in ((bridge.BUTTON_A, "A"), (bridge.BUTTON_B, "B"),
                                        (bridge.BUTTON_X, "X"), (bridge.BUTTON_Y, "Y"),
                                        (bridge.BUTTON_Z, "Z"), (bridge.BUTTON_L, "L"),
                                        (bridge.BUTTON_R, "R"), (bridge.BUTTON_START, "S"))
                      if p.button & bit) or "-"
    if p.err:
        return "unplugged"
    return (f"{buttons:<3} st({p.stick_x:+4d},{p.stick_y:+4d}) c({p.cstick_x:+4d},{p.cstick_y:+4d}) "
            f"LR({p.trigger_l:3d},{p.trigger_r:3d})")


def fighter_lines(st):
    out = []
    for port, f in enumerate(st.fighters):
        tag = f"P{port + 1}{'*' if port == st.agent_port else ' '}"
        if not f.present:
            if st.in_fight and f.slot_type != 3:
                out.append(f"  {tag} {SLOT_TYPES.get(f.slot_type, '?'):4} (no fighter)")
            elif not st.in_fight and f.input.err == 0:
                out.append(f"  {tag} pad {pad_str(f.input)}")
            continue
        char = names.CHARACTER_KIND.get(f.ckind, f"ckind {f.ckind}")
        flags = "".join(c for bit, c in ((bridge.FT_IN_AIR, "air "), (bridge.FT_IN_HITSTUN, "stun "),
                                          (bridge.FT_IN_HITLAG, "lag "), (bridge.FT_ASLEEP, "asleep "))
                        if f.flags & bit).strip() or "ground"
        out.append(
            f"  {tag} {SLOT_TYPES.get(f.slot_type, '?'):4} {char:<9} stk {f.stocks} "
            f"{f.percent:3d}% ({f.percent_f:6.2f}) "
            f"as 0x{f.motion_id:03X} {motion_name(f.motion_id):<16} fr {f.action_frame:6.2f} "
            f"pos ({f.pos_x:8.3f},{f.pos_y:8.3f}) face {f.facing:+.0f} [{flags}]")
        out.append(
            f"       jumps {f.jumps_used}/{f.max_jumps} hitlag {f.hitlag:4.1f} "
            f"mv0 {f.mv0_float:9.3g} shield {f.shield:5.2f} "
            f"v self ({f.self_vx:+.3f},{f.self_vy:+.3f}) kb ({f.kb_vx:+.3f},{f.kb_vy:+.3f}) "
            f"gr {f.ground_vx:+.3f} body {f.body_state}/{f.body_state_move} smash {f.smash_state} | "
            f"pad {pad_str(f.input)}")
    return out


def header_line(st):
    scene = names.SCENE_KIND.get(st.scene_kind, str(st.scene_kind))
    stage = names.STAGE_KIND.get(st.stage, str(st.stage)) if st.in_fight else "-"
    flags = " ".join(n for bit, n in ((bridge.ST_MATCH_START, "START"), (bridge.ST_FIGHTERS_RAN, "ran"),
                                      (bridge.ST_AGENT_INPUT, "agent-in"), (bridge.ST_INPUT_LATE, "LATE"))
                     if st.flags & bit)
    return (f"tick {st.tick:7d} match {st.match_tick:6d} vs {st.vs_frame:6d} scene {scene} "
            f"stage {stage} result {st.match_result} late {st.late_inputs} dropped {st.dropped_states} {flags}")


class Checker:
    """Invariants that must hold on any run; printed as PASS/FAIL at exit."""

    def __init__(self):
        self.prev = None
        self.fails = {}
        self.counts = {"states": 0, "fight_states": 0, "matches": 0}

    def fail(self, name, msg):
        self.fails.setdefault(name, msg)

    def feed(self, st):
        self.counts["states"] += 1
        p = self.prev
        if p is not None:
            if st.tick != p.tick + 1:
                self.fail("tick_monotonic", f"tick {p.tick} -> {st.tick}")
            if st.in_fight and p.in_fight and not st.match_start and st.match_tick != p.match_tick + 1:
                self.fail("match_tick_monotonic", f"match_tick {p.match_tick} -> {st.match_tick}")
        if st.match_start:
            self.counts["matches"] += 1
            if st.match_tick != 0:
                self.fail("match_start_zero", f"match start with match_tick {st.match_tick}")
        if st.in_fight:
            self.counts["fight_states"] += 1
            for port, f in enumerate(st.fighters):
                if not f.present:
                    continue
                if f.facing not in (1.0, -1.0):
                    self.fail("facing_unit", f"P{port + 1} facing {f.facing}")
                if not 0 <= f.percent <= 999 or abs(f.percent - int(f.percent_f)) > 1:
                    self.fail("percent_mirror", f"P{port + 1} percent {f.percent} vs {f.percent_f}")
                if f.motion_id > 0x200:
                    self.fail("motion_range", f"P{port + 1} action state 0x{f.motion_id:X}")
                if not f.asleep and (abs(f.pos_x - f.cur_x) > 1e-4 or abs(f.pos_y - f.cur_y) > 1e-4):
                    self.fail("pos_mirror", f"P{port + 1} nametag pos ({f.pos_x},{f.pos_y}) "
                                            f"vs cur_pos ({f.cur_x},{f.cur_y})")
        self.prev = st

    def report(self):
        print(f"checked {self.counts['states']} states, {self.counts['fight_states']} in a fight, "
              f"{self.counts['matches']} match start(s)")
        for name in ("tick_monotonic", "match_tick_monotonic", "match_start_zero", "facing_unit",
                     "percent_mirror", "motion_range", "pos_mirror"):
            status = "FAIL " + self.fails[name] if name in self.fails else "PASS"
            print(f"  {name:22} {status}")
        return not self.fails


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--socket", default=bridge.default_socket_path())
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--fight-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record", type=Path)
    ap.add_argument("--ticks", type=int, default=0)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--connect-timeout", type=float, default=30.0)
    args = ap.parse_args()

    client = bridge.BridgeClient(args.socket, connect_timeout=args.connect_timeout)
    h = client.hello
    print(f"connected: melee-pc {h.build}, protocol v{h.proto_version}, agent port P{h.agent_port + 1}, "
          f"{'async' if h.sync_mode else 'lockstep'}", file=sys.stderr)
    rec = None
    if args.record:
        rec = open(args.record, "wb")
        bridge.write_record_header(rec)
    checker = Checker() if args.check else None
    n = 0
    try:
        for st in client.states():
            n += 1
            if rec is not None:
                rec.write(client.last_payload)
            if checker:
                checker.feed(st)
            show = (not args.fight_only or st.in_fight) and (st.match_start or n % max(1, args.every) == 0)
            if show:
                if args.json:
                    print(json.dumps(st.to_dict()))
                else:
                    print(header_line(st))
                    for line in fighter_lines(st):
                        print(line)
            if args.ticks and n >= args.ticks:
                break
    except (ConnectionError, KeyboardInterrupt) as e:
        if isinstance(e, ConnectionError):
            print(f"bridge closed: {e}", file=sys.stderr)
    finally:
        client.close()
        if rec is not None:
            rec.close()
    if checker:
        sys.exit(0 if checker.report() else 1)


if __name__ == "__main__":
    main()
