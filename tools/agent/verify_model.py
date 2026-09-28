#!/usr/bin/env python3
"""Verify the numpy port against Phillip itself (TensorFlow 2.13 oracle).

For each agent:

1. network: numpy's action probabilities against Phillip's TF graph on
   random observation histories (max |difference|);
2. pipeline, same policy: Phillip's real agent.Agent.act() driving a fake
   Pad that records the exact pipe commands it would send to Dolphin, against
   phillip_agent.PhillipAgent with the TF policy plugged in and the same
   numpy seed. Every frame's commands must be identical -- this checks the
   delay queue, action chains, history, banned rules and sampling;
3. pipeline, numpy policy: the same run with the numpy network. Float32
   noise can flip a sample whose random draw lands within ~1e-5 of a
   probability boundary; those are counted, not failed.

Observations come from a synthetic trajectory, or from a recording made
with dump_state.py --record on a real match (--record, --agent-port).

    uv run --python 3.11 --with tensorflow-cpu==2.13.* --with attrs \\
        tools/agent/verify_model.py --phillip ../phillip --agents FalconFalconBF delay0/FoxFD
"""

import argparse
import dataclasses
import os
import sys
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

import bridge  # noqa: E402
import export_weights  # noqa: E402
import phillip_agent  # noqa: E402
import phillip_model  # noqa: E402
import phillip_obs as po  # noqa: E402

FIELDS = [f.name for f in dataclasses.fields(po.PlayerObs)]


def synthetic_trajectory(frames, seed):
    """Plausible, varied observations: players moving, changing states, taking damage."""
    rng = np.random.default_rng(seed)
    traj = []
    ps = [po.PlayerObs(x=-40.0, facing=1.0, action_state=14, shield_size=60.0),
          po.PlayerObs(x=40.0, facing=-1.0, action_state=14, shield_size=60.0, character=7)]
    for _ in range(frames):
        for p in ps:
            p.x = float(np.clip(p.x + rng.normal(0, 2.0), -180, 180))
            p.y = float(max(-60.0, p.y + rng.normal(0, 1.5))) if rng.random() < 0.3 else p.y * 0.9
            if rng.random() < 0.08:
                p.action_state = int(rng.integers(0, 0x160))
                p.action_frame = 0.0
            p.action_frame += 1.0
            if rng.random() < 0.02:
                p.percent = min(p.percent + int(rng.integers(1, 20)), 400)
                p.hitlag_frames_left = float(rng.integers(3, 12))
                p.hitstun_frames_left = float(rng.integers(5, 40))
            p.hitlag_frames_left = max(0.0, p.hitlag_frames_left - 1)
            p.hitstun_frames_left = max(0.0, p.hitstun_frames_left - 1)
            if rng.random() < 0.05:
                p.facing = -p.facing
            p.in_air = p.y > 1.0
            p.jumps_used = int(rng.integers(0, 3)) if p.in_air else 0
            p.invulnerable = bool(rng.random() < 0.05)
            p.charging_smash = bool(rng.random() < 0.03)
            p.shield_size = float(np.clip(p.shield_size + rng.normal(0, 0.5), 0, 60))
            p.speed_air_x_self = float(rng.normal(0, 1))
            p.speed_ground_x_self = float(rng.normal(0, 1))
            p.speed_y_self = float(rng.normal(0, 1))
            p.speed_x_attack = float(rng.normal(0, 0.5)) if p.hitstun_frames_left else 0.0
            p.speed_y_attack = float(rng.normal(0, 0.5)) if p.hitstun_frames_left else 0.0
        traj.append([dataclasses.replace(ps[0]), dataclasses.replace(ps[1])])
    return traj


def recorded_trajectory(path, agent_port):
    """[opponent, agent] per frame the agent would act on, from a recording."""
    traj = []
    last_frame = None
    prev = {}
    opp_port = None
    for st in bridge.read_record(path):
        if not st.in_fight or not st.fighters[agent_port].present:
            continue
        if st.scene_frame == last_frame:
            continue  # paused: Phillip saw no new frame
        last_frame = st.scene_frame
        if opp_port is None:
            opp_port = next((i for i, f in enumerate(st.fighters) if f.present and i != agent_port), None)
            if opp_port is None:
                continue
        obs = []
        for port in (opp_port, agent_port):
            o = po.player_obs(st.fighters[port], prev.get(port))
            prev[port] = o
            obs.append(o)
        traj.append(obs)
    return traj


def tf_policy_fn(actor):
    def policy(history, delayed, hidden):
        hist = [[dataclasses.asdict(p) for p in players] for players, _ in history]
        prev = [a for _, a in history]
        probs, new_hidden = export_weights.tf_policy(actor, hist, prev, delayed,
                                                     tuple(hidden) if hidden else [])
        return probs, list(new_hidden) if hidden else []
    return policy


class FakePad:
    """phillip.pad.Pad without the fifo: records what each frame writes."""

    def __init__(self, pad_mod):
        pad = pad_mod.Pad.__new__(pad_mod.Pad)
        pad.tcp = True  # so __del__ does not look for a pipe
        pad.message = ""
        self.frames = []
        pad.flush = lambda: (self.frames.append(pad.message), setattr(pad, "message", ""))
        self.pad = pad

    def take(self):
        out = "".join(self.frames)
        self.frames = []
        return out


def ours_text(controller):
    if controller is po.REPEAT:
        return ""
    return "".join(line + "\n" for line in po.pipe_commands(controller))


def game_memory(ssbm, players):
    state = ssbm.GameMemory()
    types = dict(ssbm.PlayerMemory._fields_)
    for k in range(2):
        for name in FIELDS:
            v = getattr(players[k], name)
            t = types[name].__name__
            setattr(state.players[k], name, bool(v) if t == "c_bool" else int(v) if t == "c_uint" else float(v))
    return state


def run_phillip(phillip, path, traj, epsilon, seed):
    from phillip import agent as pagent, pad as ppad, ssbm, util

    params = util.load_params(str(path), "agent")
    params.update(gpu=False, epsilon=epsilon, reload=0, swap=0, dump=None, disk=0, tb=False, real_delay=0)
    agent = pagent.Agent(**params)
    fake = FakePad(ppad)
    np.random.seed(seed)
    out = []
    for players in traj:
        agent.act(game_memory(ssbm, players), fake.pad)
        out.append(fake.take())
    return out, params.get("char")


def run_ours(model, char, traj, epsilon, seed, policy_fn=None):
    agent = phillip_agent.PhillipAgent(model, char, epsilon=epsilon, seed=seed, policy_fn=policy_fn)
    return [ours_text(agent.act(players)) for players in traj], agent.steps


TOL = 5e-4  # on action probabilities: float32 noise of 1798-wide layers reaches ~2e-4


def numeric(actor, model, cases, seed):
    """max |TF - numpy|, and for scale max |TF - numpy float64| and
    max |numpy float32 - numpy float64|: TF's and numpy's float32 rounding
    both sit about as far from the float64 result as from each other."""
    import copy

    m64 = copy.deepcopy(model)
    for layer in m64.trunk + m64.actor + [x for x in (m64.action_fc, m64.player_fc) if x]:
        layer.w = layer.w.astype(np.float64)
        layer.b = layer.b.astype(np.float64)
    rng = np.random.default_rng(seed)
    names = [f["field"] for f in model.fields]
    worst = [0.0, 0.0, 0.0]
    for _ in range(cases):
        history = []
        for _ in range(model.memory + 1):
            players = [dict(zip(names, export_weights.random_player(rng, names))) for _ in range(2)]
            history.append((players, int(rng.integers(0, model.num_actions))))
        delayed = [int(a) for a in rng.integers(0, model.num_actions, size=model.delay)]
        hidden = [rng.uniform(-1, 1, size=s).astype(np.float32) for s in model.hidden_sizes]
        hist_tf = [[p for p in players] for players, _ in history]
        p_tf, _ = export_weights.tf_policy(actor, hist_tf, [a for _, a in history], delayed,
                                           tuple(hidden) if hidden else [])
        p_np, _ = model.policy(history, delayed, hidden)
        p_64, _ = m64.policy(history, delayed, hidden)
        for k, d in enumerate((p_tf - p_np, p_tf - p_64, p_np - p_64)):
            worst[k] = max(worst[k], float(np.max(np.abs(d))))
    return worst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phillip", default=str(HERE.parents[2] / "phillip"))
    ap.add_argument("--agents", nargs="+", default=["FalconFalconBF", "delay0/FoxFD", "delay0/FalcoFD"])
    ap.add_argument("--weights", type=Path, default=export_weights.DEFAULT_OUT)
    ap.add_argument("--record", type=Path, help="a dump_state.py --record file to take observations from")
    ap.add_argument("--agent-port", type=int, default=2, help="the agent's port in --record (1-4)")
    ap.add_argument("--frames", type=int, default=2400)
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--epsilon", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    if args.record:
        traj = recorded_trajectory(args.record, args.agent_port - 1)
        source = f"{len(traj)} recorded frames from {args.record}"
    else:
        traj = synthetic_trajectory(args.frames, args.seed)
        source = f"{len(traj)} synthetic frames"
    all_ok = True
    for name in args.agents:
        path = export_weights.agent_dir(args.phillip, name)
        npz = args.weights / (name.replace("/", "_") + ".npz")
        actor, _ = export_weights.load_actor(args.phillip, path, {"epsilon": args.epsilon})
        if not npz.exists():
            arrays, meta = export_weights.resolve(actor)
            print(f"{name}: no {npz}; export it first with export_weights.py")
            all_ok = False
            continue
        model = phillip_model.PhillipModel(npz)
        model.epsilon = args.epsilon  # the TF graph was built with it
        err, err64, noise = numeric(actor, model, args.cases, args.seed)
        theirs, char = run_phillip(args.phillip, path, traj, args.epsilon, args.seed)
        same_policy, steps = run_ours(model, char, traj, args.epsilon, args.seed, tf_policy_fn(actor))
        numpy_policy, _ = run_ours(model, char, traj, args.epsilon, args.seed)
        diff_same = [i for i, (a, b) in enumerate(zip(theirs, same_policy)) if a != b]
        diff_np = [i for i, (a, b) in enumerate(zip(theirs, numpy_policy)) if a != b]
        ok = err <= TOL and not diff_same
        all_ok &= ok
        print(f"== {name} ({model.action_type}, act_every {model.act_every}, delay {model.delay}, "
              f"memory {model.memory}; {source}, {steps} network steps)")
        print(f"  [{'PASS' if err <= TOL else 'FAIL'}] network: max |numpy - TF| = {err:.2e} "
              f"over {args.cases} random histories (float32 noise: |TF - float64| {err64:.1e}, "
              f"|numpy - float64| {noise:.1e})")
        print(f"  [{'PASS' if not diff_same else 'FAIL'}] pipeline (TF policy): pad commands identical to "
              f"phillip.agent.Agent on {len(theirs) - len(diff_same)}/{len(theirs)} frames"
              + (f", first difference at frame {diff_same[0]}" if diff_same else ""))
        print(f"  [INFO] pipeline (numpy policy): {len(theirs) - len(diff_np)}/{len(theirs)} frames identical"
              + (f"; first sampling flip at frame {diff_np[0]}" if diff_np else ""))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
