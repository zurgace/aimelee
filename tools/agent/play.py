#!/usr/bin/env python3
"""One command: start melee-pc with the agent bridge on and Phillip on a port.

    python3 tools/agent/play.py --iso /path/to/GALE01.iso

That plays FalconFalconBF on P2: pick Captain Falcon for yourself (P1, the
keyboard, or a gamepad on port 1), set P2 to HMN Captain Falcon (P2 reads as
plugged in; with P1's cursor: toggle P2's door to CPU, pick Falcon, toggle
on to HMN), choose Battlefield, and play. The agent takes P2 once the match
starts and lets go when it ends; the game never waits on it for more than
the timeout, and carries on if it dies (play.py restarts it).

Options:
  --agent NAME       any agent under <phillip>/agents (default FalconFalconBF);
                     list them with tools/agent/list_agents.py
  --port N           the port the agent plays (1-4, default 2)
  --delay N          run the agent with N network steps of action delay, as
                     Phillip's --delay did (its weights are padded to fit)
  --epsilon E        random-action rate (default 0, as Phillip's README plays)
  --sync MODE        lockstep (default) or async
  --timeout-ms MS    lockstep wait per tick (default 4)
  --frame-lag K      hold each pad K extra ticks (emulate a slower pipe)
  --quick            skip the menus: boot straight into the agent's matchup
                     (debug VS, both ports human, the agent's stage)
  --record FILE      record every state the agent sees (dump_state format;
                     verify_model.py --record replays it through Phillip)

The bridge serves one client at a time and a new connection replaces the
old one, so record through --record rather than running dump_state.py
next to a playing agent.

Weights: the first time, the agent is exported from Phillip's checkpoint with
TensorFlow 2.13 through uv (a throwaway Python 3.11 environment); after that
only numpy is needed. Without uv, run export_weights.py yourself.
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import phillip_obs  # noqa: E402  (no numpy needed at import)

UV_EXPORT = ["--python", "3.11", "--with", "tensorflow-cpu==2.13.*", "--with", "attrs"]


def weights_path(agent, delay):
    stem = agent.replace("/", "_") + (f"_delay{delay}" if delay is not None else "")
    return HERE / "weights" / f"{stem}.npz"


def ensure_weights(args):
    out = weights_path(args.agent, args.delay)
    if out.exists():
        return out
    cmd = [str(HERE / "export_weights.py"), "--phillip", args.phillip, "--agent", args.agent, "--out", str(out)]
    if args.delay is not None:
        cmd += ["--delay", str(args.delay)]
    uv = shutil.which("uv")
    if uv is None:
        sys.exit(f"{out} not found and uv is not installed. Export it once with TensorFlow 2.13 "
                 f"(Python 3.8-3.11):\n  python3.11 {' '.join(cmd)}")
    print(f"play: exporting {args.agent} with TensorFlow 2.13 (first run only; downloads ~200 MB) ...",
          flush=True)
    subprocess.run([uv, "run", *UV_EXPORT, *cmd], check=True)
    return out


def agent_params(args):
    import json
    p = Path(args.phillip) / "agents" / args.agent / "params"
    with open(p) as f:
        params = json.load(f)
    params.update(params.get("agent", {}))
    return params


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default=os.environ.get("MELEE_DISC"), help="your NTSC-U 1.02 disc image")
    ap.add_argument("--melee", default=str(ROOT / "build" / "melee"))
    ap.add_argument("--phillip", default=str(ROOT.parent / "phillip"), help="phillip checkout (default ../phillip)")
    ap.add_argument("--agent", default="FalconFalconBF")
    ap.add_argument("--port", type=int, default=2, choices=[1, 2, 3, 4])
    ap.add_argument("--delay", type=int)
    ap.add_argument("--epsilon", type=float, default=0.0)
    ap.add_argument("--sync", choices=["lockstep", "async"], default="lockstep")
    ap.add_argument("--timeout-ms", type=float, default=4.0)
    ap.add_argument("--frame-lag", type=int, default=0)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--record", type=Path)
    ap.add_argument("--opponent", help="with --quick: the other port's character (Phillip name, "
                                       "default the agent's own)")
    args = ap.parse_args()

    if not args.iso or not Path(args.iso).exists():
        sys.exit("pass --iso <your disc image> (or set MELEE_DISC)")
    if not Path(args.melee).exists():
        sys.exit(f"{args.melee} not found: cmake -B build -G Ninja && ninja -C build")
    try:
        import numpy  # noqa: F401
    except ImportError:
        sys.exit("the agent needs numpy: your distribution's package (Arch/CachyOS: "
                 "pacman -S python-numpy), or python3 -m venv .venv && .venv/bin/pip install numpy "
                 "and run play.py with .venv/bin/python")
    weights = ensure_weights(args)

    sock_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    sock = str(Path(sock_dir) / f"melee-agent-{os.getpid()}.sock")
    env = dict(os.environ)
    env.update(MELEE_AGENT_SOCKET=sock, MELEE_AGENT_PORT=str(args.port), MELEE_AGENT_SYNC=args.sync,
               MELEE_AGENT_TIMEOUT_MS=str(args.timeout_ms))
    if args.quick:
        if args.port > 2:
            sys.exit("--quick seats ports 1 and 2 only; use --port 1 or 2")
        params = agent_params(args)
        me = phillip_obs.CKIND_BY_PHILLIP_NAME.get(params.get("char"), 0)
        them = phillip_obs.CKIND_BY_PHILLIP_NAME.get(args.opponent or params.get("char"), me)
        chars = [them, them]
        chars[args.port - 1] = me
        env.update(MELEE_BOOT_SCENE="vs", MELEE_DEBUG_VS_CHARS=f"{chars[0]},{chars[1]}")
        stage = phillip_obs.STKIND_BY_PHILLIP_NAME.get(params.get("stage", "final_destination"))
        if stage is not None and stage != 0x20:  # the debug match is on Final Destination already
            env["MELEE_DEBUG_VS_STAGE"] = str(stage)

    game = subprocess.Popen([args.melee, args.iso], cwd=str(ROOT), env=env)
    agent_cmd = [sys.executable, str(HERE / "agent.py"), "--weights", str(weights), "--socket", sock,
                 "--epsilon", str(args.epsilon), "--frame-lag", str(args.frame_lag)]
    if args.seed is not None:
        agent_cmd += ["--seed", str(args.seed)]
    if args.record is not None:
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
