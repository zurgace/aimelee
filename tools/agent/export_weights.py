#!/usr/bin/env python3
"""Export a Phillip agent's weights for the numpy runtime (phillip_model.py).

Runs Phillip's own code under TensorFlow 2.13 once per agent: builds its
Actor from the agent's params, restore()s the checkpoint (which zero-pads
shape mismatches, e.g. an agent run with more delay than it was trained
with), then walks the Python objects -- not variable names -- to write every
layer's weights, nonlinearity and the fully resolved embedding spec into one
.npz, plus reference inputs and the TF graph's outputs on them, which the
numpy side replays on every load.

TF 2.13 needs Python 3.8-3.11; uv provides both without touching the system:

    uv run --python 3.11 --with tensorflow-cpu==2.13.* --with attrs \\
        tools/agent/export_weights.py --phillip ../phillip --agent FalconFalconBF

writes tools/agent/weights/FalconFalconBF.npz. --agent is a path under
<phillip>/agents (e.g. delay0/FoxFD) or an absolute agent directory.
"""

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "weights"
N_REFS = 16


def agent_dir(phillip, agent):
    p = Path(agent)
    if not p.is_absolute():
        p = Path(phillip) / "agents" / agent
    if not (p / "params").exists():
        sys.exit(f"{p}: no params file (is --phillip pointing at a phillip checkout?)")
    return p


def load_actor(phillip, path, overrides=None):
    """Phillip's Actor for an agent directory, restored, epsilon as given."""
    sys.path.insert(0, str(Path(phillip).resolve()))
    import tensorflow.compat.v1 as tf
    tf.disable_eager_execution()
    from phillip import util
    from phillip.actor import Actor

    params = util.load_params(str(path), "agent")
    original = dict(params)
    params["gpu"] = False
    params.update(overrides or {})
    actor = Actor(**params)
    actor.restore()
    return actor, original


def nl_spec(nl):
    """A layer's nonlinearity as (name, alpha)."""
    if nl is None:
        return "linear", 0.0
    name = getattr(nl, "nl", None)
    if name is not None:  # tf_lib.NL
        return name, float(getattr(nl, "alpha", 0.01))
    return getattr(nl, "__name__", str(nl)), 0.0  # a bare tf function (sigmoid/tanh)


def resolve(actor):
    """(arrays, meta) describing the actor completely."""
    from phillip import embed

    sess = actor.sess
    arrays = {}
    layers = {}

    def put_fc(key, fc):
        w, b = sess.run([fc.weight, fc.bias])
        arrays[key + "/W"] = w
        arrays[key + "/b"] = b
        name, alpha = nl_spec(fc.nl)
        layers[key] = {"nl": name, "alpha": alpha, "shape": list(w.shape)}

    ep = actor.embedGame.embedPlayer
    if isinstance(ep, embed.FCEmbedding):
        put_fc("player_fc", ep.fc)
        ep = ep.wrapper
    fields = []
    for fname, op in ep.embedding:
        if op is embed.nullEmbedding:
            fields.append({"field": fname, "kind": "null"})
        elif isinstance(op, embed.FloatEmbedding):
            fields.append({"field": fname, "kind": "float", "scale": op.scale, "bias": op.bias,
                           "lower": op.lower, "upper": op.upper})
        elif isinstance(op, embed.OneHotEmbedding):
            fields.append({"field": fname, "kind": "onehot", "size": op.size})
        elif isinstance(op, embed.FCEmbedding) and isinstance(op.wrapper, embed.OneHotEmbedding):
            if fname != "action_state":
                raise SystemExit(f"unexpected FC embedding on {fname}")
            put_fc("action_fc", op.fc)
            fields.append({"field": fname, "kind": "onehot", "size": op.wrapper.size, "fc": "action_fc"})
        else:
            raise SystemExit(f"unsupported embedding {type(op).__name__} for {fname}")

    trunk = actor.core.trunk.layers
    for i, fc in enumerate(trunk):
        put_fc(f"trunk/{i}", fc)
    cells = list(actor.core.core._cells) if actor.core.core is not None else []
    for i, cell in enumerate(cells):
        wru, bru, wc, bc = sess.run([cell.Wru, cell.bru, cell.Wc, cell.bc])
        arrays.update({f"gru/{i}/Wru": wru, f"gru/{i}/bru": bru, f"gru/{i}/Wc": wc, f"gru/{i}/bc": bc})
    for i, fc in enumerate(actor.policy.net.layers):
        put_fc(f"actor/{i}", fc)

    cfg = actor.config
    meta = {
        "name": getattr(actor, "name", None),
        "player_fields": fields,
        "layers": layers,
        "trunk_layers": len(trunk),
        "gru_layers": len(cells),
        "actor_layers": len(actor.policy.net.layers),
        "num_actions": actor.embedAction.size,
        "action_type": actor.action_type,
        "memory": cfg.memory,
        "delay": cfg.delay,
        "act_every": cfg.act_every,
        "epsilon": actor.policy.epsilon,
        "predict": bool(actor.predict),
        "action_space_embed": bool(actor.action_space_embed),
    }
    if actor.action_space_embed:
        raise SystemExit("agents with action_space_embed are not supported")
    return arrays, meta


# Realistic ranges for random reference inputs, per PlayerObs field.
RANGES = {
    "percent": (0, 300), "facing": (-1, 1), "x": (-250, 250), "y": (-150, 200),
    "action_state": (0, 400), "action_frame": (0, 120), "character": (0, 30),
    "invulnerable": (0, 1), "hitlag_frames_left": (0, 20), "hitstun_frames_left": (0, 60),
    "jumps_used": (0, 6), "charging_smash": (0, 1), "shield_size": (0, 60), "in_air": (0, 1),
    "speed_air_x_self": (-4, 4), "speed_ground_x_self": (-4, 4), "speed_y_self": (-5, 5),
    "speed_x_attack": (-8, 8), "speed_y_attack": (-8, 8),
}
INT_FIELDS = {"percent", "action_state", "character", "jumps_used", "invulnerable", "charging_smash",
              "in_air", "facing"}


def random_player(rng, names):
    vals = []
    for n in names:
        lo, hi = RANGES[n]
        if n == "facing":
            vals.append(float(rng.choice([-1.0, 1.0])))
        elif n in INT_FIELDS:
            vals.append(float(rng.integers(lo, hi + 1)))
        else:
            vals.append(float(rng.uniform(lo, hi)))
    return vals


def tf_policy(actor, players_hist, prev_actions, delayed, hidden):
    """Run Phillip's graph (actor.run_policy) on one input; returns probs, hidden."""
    import numpy as np
    from phillip import ssbm, util, ctype_util as ct

    hist = ((actor.config.memory + 1) * ssbm.SimpleStateAction)()
    for j in range(actor.config.memory + 1):
        for k in range(2):
            p = hist[j].state.players[k]
            for name, v in players_hist[j][k].items():
                ftype = dict(ssbm.PlayerMemory._fields_)[name]
                setattr(p, name, bool(v) if ftype.__name__ == "c_bool" else
                        (int(v) if ftype.__name__ == "c_uint" else float(v)))
        hist[j].prev_action = int(prev_actions[j])
    d = ct.vectorizeCTypes(ssbm.SimpleStateAction, list(hist))
    d["hidden"] = hidden
    d["delayed_action"] = list(delayed)
    feed = dict(util.deepValues(util.deepZip(actor.input, d)))
    policy, new_hidden = actor.sess.run(actor.run_policy, feed)
    return np.asarray(policy, np.float32), new_hidden


def make_refs(actor, meta, seed=1234):
    import numpy as np

    rng = np.random.default_rng(seed)
    names = [f["field"] for f in meta["player_fields"]]
    m1 = meta["memory"] + 1
    players = np.zeros((N_REFS, m1, 2, len(names)), np.float64)
    prev = rng.integers(0, meta["num_actions"], size=(N_REFS, m1))
    delayed = rng.integers(0, meta["num_actions"], size=(N_REFS, max(meta["delay"], 0)))
    hs = actor.core.hidden_size
    hidden_sizes = (list(hs) if isinstance(hs, (list, tuple)) else [hs]) if meta["gru_layers"] else []
    hiddens = [rng.uniform(-1, 1, size=(N_REFS, s)).astype(np.float32) for s in hidden_sizes]
    probs = np.zeros((N_REFS, meta["num_actions"]), np.float32)
    for i in range(N_REFS):
        hist = []
        for j in range(m1):
            both = []
            for k in range(2):
                vals = random_player(rng, names)
                players[i, j, k] = vals
                both.append(dict(zip(names, vals)))
            hist.append(both)
        hidden = tuple(h[i] for h in hiddens) if hiddens else []  # MultiRNNCell state: a tuple
        probs[i], _ = tf_policy(actor, hist, prev[i], delayed[i], hidden)
    refs = {"ref/players": players, "ref/prev_action": prev, "ref/delayed_action": delayed,
            "ref/probs": probs, "ref/field_names": np.array(names)}
    for g, h in enumerate(hiddens):
        refs[f"ref/hidden/{g}"] = h
    return refs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phillip", default=str(HERE.parents[2] / "phillip"),
                    help="phillip checkout (default: ../phillip beside this repo)")
    ap.add_argument("--agent", required=True, help="e.g. FalconFalconBF or delay0/FoxFD")
    ap.add_argument("--out", type=Path, help="output .npz (default tools/agent/weights/<agent>.npz)")
    ap.add_argument("--delay", type=int, help="override the agent's delay (Phillip pads the weights)")
    args = ap.parse_args()

    import numpy as np

    path = agent_dir(args.phillip, args.agent)
    overrides = {"epsilon": 0.0}
    if args.delay is not None:
        overrides["delay"] = args.delay
    actor, params = load_actor(args.phillip, path, overrides)
    arrays, meta = resolve(actor)
    meta["name"] = args.agent
    meta["source"] = str(path)
    meta["params"] = {k: v for k, v in params.items() if isinstance(v, (int, float, str, bool, list))}
    meta["epsilon"] = float(params.get("epsilon") or 0.02)  # the agent's own; play uses --epsilon
    meta["ref_epsilon"] = 0.0
    arrays.update(make_refs(actor, meta))
    out = args.out or DEFAULT_OUT / (args.agent.replace("/", "_") + ".npz")
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, meta=np.array(json.dumps(meta)), **arrays)
    print(f"wrote {out}: {meta['num_actions']} actions ({meta['action_type']}), act_every "
          f"{meta['act_every']}, delay {meta['delay']}, memory {meta['memory']}, "
          f"{meta['gru_layers']} GRU layers")


if __name__ == "__main__":
    main()
