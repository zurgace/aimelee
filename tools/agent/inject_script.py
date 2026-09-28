#!/usr/bin/env python3
"""Drive the agent port with a fixed script -- no AI -- to see injection work.

With the game running with the bridge on (MELEE_AGENT_SOCKET), start a match
with a human-type fighter on the agent port (P2 by default), then:

    python3 tools/agent/inject_script.py --socket /tmp/melee-agent.sock

From GO! the agent port walks right, walks left, short-hops, jabs and
shields, then repeats every 6 seconds (--once to stop after one pass).
Every input is sent for exactly one tick, lockstep, and the port's action
state is printed as it changes. Your own port keeps working meanwhile.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge  # noqa: E402
import dump_state  # noqa: E402
from harness import PlayClock  # noqa: E402


@dataclass
class Segment:
    start: int  # replay frame the segment starts on (0 = GO!)
    end: int    # first frame after it
    pad: bridge.Pad
    name: str


def pad(button=0, x=0, y=0, trigger_l=0):
    return bridge.Pad(button=button, stick_x=x, stick_y=y, trigger_l=trigger_l,
                      analog_a=255 if button & bridge.BUTTON_A else 0,
                      analog_b=255 if button & bridge.BUTTON_B else 0)


WALK = 40  # half tilt: a walk, not a dash (the game's full tilt is 80)


def demo_script(offset=0):
    """The Phase 2 moves. Frames are relative to `offset`."""
    segs = [
        (30, 70, pad(x=WALK), "walk right"),
        (80, 120, pad(x=-WALK), "walk left"),
        (140, 142, pad(button=bridge.BUTTON_Y), "short hop"),
        (220, 222, pad(button=bridge.BUTTON_A), "jab"),
        (270, 300, pad(button=bridge.BUTTON_L, trigger_l=140), "shield"),
    ]
    return [Segment(a + offset, b + offset, p, n) for a, b, p, n in segs]


def pad_for(script, frame):
    for seg in script:
        if seg.start <= frame < seg.end:
            return seg.pad, seg.name
    return bridge.Pad.neutral(), None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--socket", default=bridge.default_socket_path())
    ap.add_argument("--once", action="store_true", help="stop after one pass of the script")
    args = ap.parse_args()

    client = bridge.BridgeClient(args.socket)
    port = client.hello.agent_port
    print(f"driving P{port + 1}; start a match (P{port + 1} must be HMN)", file=sys.stderr)
    clock = PlayClock()
    period = 360
    last = None
    try:
        for st in client.states():
            frame = clock.feed(st)
            if frame is None or frame < 0:
                # Menus or the countdown: leave the port alone (it reads as a
                # connected neutral pad, so the CSS can seat it).
                client.send_input(st.tick + 1, bridge.Pad.neutral(), release=True)
                continue
            rel = frame % period if not args.once else frame
            p, name = pad_for(demo_script(), rel + 1)  # the input is for the next tick
            client.send_input(st.tick + 1, p)
            f = st.fighters[port]
            now = (name, f.motion_id)
            if now != last:
                last = now
                print(f"frame {frame:5d} {name or '-':<10} P{port + 1} "
                      f"{dump_state.motion_name(f.motion_id):<14} x {f.cur_x:8.2f} y {f.cur_y:7.2f} "
                      f"late {st.late_inputs}")
            if args.once and frame > period:
                break
    except (ConnectionError, KeyboardInterrupt):
        pass
    finally:
        client.close()


if __name__ == "__main__":
    main()
