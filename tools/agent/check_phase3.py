#!/usr/bin/env python3
"""Phase 3 check: Phillip plays P2 live, survives being killed, comes back.

Needs exported weights (export_weights.py). Boots Captain Falcon vs Captain
Falcon on Battlefield, runs tools/agent/agent.py on P2, and checks:

  - the agent drives P2 every tick after its warm-up, with no late inputs,
    and P2 actually plays (many action states, moves around);
  - latency: network step and reply times, tick-to-tick wall time (stalls);
  - kill -9 of the agent mid-match: the game keeps running, logs the drop,
    and P2 falls back to a neutral pad;
  - a restarted agent reconnects and drives P2 again.

    python3 tools/agent/check_phase3.py --iso /path/to/GALE01.iso
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import harness  # noqa: E402

FRAMES = 5400      # about 90 s
KILL_AT = 1500     # agent frames into the match
OBSERVE = 300      # states the observer reads while no agent drives
P2 = 1


def start_agent(game, weights, tag, seed):
    out = game.dir / f"agent-{tag}.log"
    cmd = [sys.executable, str(HERE / "agent.py"), "--weights", str(weights), "--socket", game.socket,
           "--record", str(game.dir / f"rec-{tag}.bin"), "--stats-json", str(game.dir / f"stats-{tag}.json"),
           "--progress", "60", "--once", "--seed", str(seed)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []

    def pump():
        with open(out, "w") as f:
            for line in proc.stdout:
                lines.append(line)
                f.write(line)
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    return proc, lines


def progress_frame(lines):
    for line in reversed(lines):
        if line.startswith("progress frame "):
            return int(line.split()[2])
    return 0


def run(args):
    report = harness.Report("Phase 3: Phillip live")
    with harness.GameRun(args.melee, args.iso, FRAMES, keep=args.keep, key_fifo=False, label="phase3") as game:
        agent1, lines1 = start_agent(game, args.weights, "1", 1)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and progress_frame(lines1) < KILL_AT and agent1.poll() is None:
            time.sleep(0.05)
        reached = progress_frame(lines1)
        report.check("agent reached the kill point", reached >= KILL_AT,
                     f"frame {reached}; agent log tail: {''.join(lines1[-3:]).strip()}")
        agent1.kill()  # SIGKILL: no goodbye on the socket
        agent1.wait()

        observed = []
        try:
            obs = game.connect(timeout=10)
            for _ in range(OBSERVE):
                st = obs.recv_state(timeout=5)
                if st is None:
                    break
                observed.append(st)
            obs.close()
        except ConnectionError as e:
            report.check("observer connects after the kill", False, str(e))

        agent2, lines2 = start_agent(game, args.weights, "2", 2)
        code = game.wait(timeout=240)
        try:
            agent2.wait(timeout=20)
        except subprocess.TimeoutExpired:
            agent2.kill()
        log = game.log_text()
        rec1 = list(bridge.read_record(game.dir / "rec-1.bin"))
        rec2 = list(bridge.read_record(game.dir / "rec-2.bin")) if (game.dir / "rec-2.bin").exists() else []
        stats1 = json.loads((game.dir / "stats-1.json").read_text()) if (game.dir / "stats-1.json").exists() else {}

    report.check("game exits cleanly", code == 0, f"exit {code}")
    driven = [st for st in rec1 if st.flags & bridge.ST_AGENT_INPUT]
    report.check("agent drove P2", len(driven) >= KILL_AT - 200, f"{len(driven)} ticks with the agent's pad")
    late = (driven[-1].late_inputs - driven[0].late_inputs) if driven else -1
    report.check("no late inputs while the agent played", late == 0, f"{late} late ticks")
    motions = {st.fighters[P2].motion_id for st in driven}
    xs = [st.fighters[P2].cur_x for st in driven]
    report.check("P2 plays (varied action states)", len(motions) >= 8, f"{len(motions)} distinct action states")
    report.check("P2 moves around", xs and max(xs) - min(xs) > 30, f"x range {max(xs) - min(xs):.1f}" if xs else "")
    if stats1:
        report.info("network step latency",
                    f"p50 {stats1['step_us_p50']:.0f} us, p99 {stats1['step_us_p99']:.0f} us, "
                    f"max {stats1['step_us_max']:.0f} us ({stats1['network_steps']} steps)")
        report.info("state -> input reply", f"p50 {stats1['reply_us_p50']:.0f} us, p99 {stats1['reply_us_p99']:.0f} us")
        gaps = stats1["tick_gaps_over_25ms"]
        report.check("no stalls: tick-to-tick wall time", gaps <= max(3, stats1["frames_acted"] // 200),
                     f"p50 {stats1['tick_gap_ms_p50']:.1f} ms, p99 {stats1['tick_gap_ms_p99']:.1f} ms, "
                     f"{gaps} gaps over 25 ms")
    report.check("game kept running after kill -9", len(observed) >= OBSERVE // 2, f"{len(observed)} states observed")
    if observed:
        tail = observed[len(observed) // 2:]
        report.check("P2 neutral while no agent drives", all(
            not st.flags & bridge.ST_AGENT_INPUT and st.fighters[P2].input.button == 0
            and st.fighters[P2].input.stick_x == 0 and st.fighters[P2].input.err == 0 for st in tail))
    report.check("drop logged", "agent: client dropped" in log)
    driven2 = [st for st in rec2 if st.flags & bridge.ST_AGENT_INPUT]
    report.check("restarted agent drives P2 again", len(driven2) >= 300, f"{len(driven2)} ticks")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    harness.base_args(ap)
    ap.add_argument("--weights", type=Path, default=HERE / "weights" / "FalconFalconBF.npz")
    args = ap.parse_args()
    harness.require_disc(args)
    if not args.weights.exists():
        sys.exit(f"{args.weights} not found; export it first:\n  uv run --python 3.11 --with "
                 "tensorflow-cpu==2.13.* --with attrs tools/agent/export_weights.py --phillip ../phillip "
                 "--agent FalconFalconBF")
    ok = run(args).print()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
