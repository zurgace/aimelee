#!/usr/bin/env python3
"""List Phillip's agents: the matchup each expects and whether its weights load cleanly.

Agents are trained for one character on one stage (and against particular
opponents); this prints, for every agent directory under <phillip>/agents:
character, stage, act_every, delay (network steps and frames), memory,
action set, whether the character is part of its input, who it trained
against, and -- with TensorFlow available -- whether Phillip's own restore()
fits the checkpoint to the graph its params describe:

  ok         every variable matched
  delay pad  the first actor layer gained zero rows for delayed-action
             inputs the checkpoint predates (harmless: Phillip ran it so)
  MISMATCH   variables missing, unused or padded elsewhere: the params do
             not describe the network that was trained, and Phillip itself
             would play with misaligned weights

Without TensorFlow only the params columns are shown.

    python3 tools/agent/list_agents.py --phillip ../phillip
    uv run --python 3.11 --with tensorflow-cpu==2.13.* --with attrs \\
        tools/agent/list_agents.py --phillip ../phillip --check
"""

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ACTION_SET_SIZES = {"old": 30, "cardinal": 30, "diagonal": 54, "custom": 35, "custom_sh_jc": 37,
                    "custom_sh2_wd": 38}
# Defaults of the options that matter here (phillip RL/RLConfig/CPU/embed).
DEFAULTS = {"act_every": 3, "delay": 0, "memory": 0, "action_type": "diagonal", "stage": "final_destination",
            "omit_char": False, "predict": False}


def load_params(path):
    with open(path / "params") as f:
        p = json.load(f)
    if "agent" in p:  # util.load_params: the agent section wins
        p.update(p["agent"])
    return p


def find_agents(root):
    for params in sorted(root.rglob("params")):
        d = params.parent
        if any((d / n).exists() for n in ("snapshot", "snapshot.index")):
            yield d
        else:
            yield d  # listed, marked as missing weights


def weights_state(d):
    if (d / "snapshot.index").exists():
        return "v2"
    if (d / "snapshot").exists():
        head = (d / "snapshot").read_bytes()[:40]
        return "lfs-pointer" if head.startswith(b"version https://git-lfs") else "v1"
    return "missing"


def restore_check(phillip, d):
    """Compare Phillip's graph for these params with the checkpoint."""
    import export_weights
    import tensorflow as tf

    actor, _ = export_weights.load_actor(phillip, d)
    snap = str(d / "snapshot")
    reader = tf.train.load_checkpoint(snap)
    shapes = reader.get_variable_to_shape_map()
    used = set()
    problems = []
    delay_pad = False
    for var in actor.variables:
        name = var.name[:-2] if var.name.endswith(":0") else var.name
        if name.startswith("train/") or "Adam" in name:
            continue
        if name not in shapes:
            if name != "global_step":
                problems.append(f"{name} not in checkpoint")
            continue
        used.add(name)
        want, have = var.get_shape().as_list(), shapes[name]
        if want != list(have):
            is_actor0 = name.endswith("actor/layer_0/weight")
            extra = want[0] - have[0]
            n_actions = actor.embedAction.size
            if (is_actor0 and want[1:] == list(have[1:]) and extra > 0
                    and extra == actor.config.delay * n_actions):
                delay_pad = True
            else:
                problems.append(f"{name} {have} -> {want}")
    unused = [n for n in shapes if n not in used and not n.startswith("train/") and "Adam" not in n
              and n != "global_step" and "critic" not in n and "model/" not in n]
    problems += [f"{n} unused" for n in unused]
    if problems:
        return "MISMATCH: " + "; ".join(problems[:3]) + (" ..." if len(problems) > 3 else "")
    return "delay pad" if delay_pad else "ok"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phillip", default=str(HERE.parents[2] / "phillip"))
    ap.add_argument("--check", action="store_true", help="also check restore() fit (needs TensorFlow 2.13)")
    args = ap.parse_args()
    root = Path(args.phillip) / "agents"
    if not root.is_dir():
        sys.exit(f"{root}: not found (pass --phillip)")
    weights_dir = HERE / "weights"
    rows = []
    for d in find_agents(root):
        p = {**DEFAULTS, **load_params(d)}
        name = str(d.relative_to(root))
        a = p["action_type"]
        opp = p.get("enemies") or p.get("enemy") or "-"
        if isinstance(opp, list):
            opp = ",".join(opp)
        w = weights_state(d)
        status = ""
        if args.check and w in ("v1", "v2"):
            if p.get("predict"):
                status = "predictive model (not supported)"
            else:
                try:
                    status = restore_check(args.phillip, d)
                except Exception as e:  # noqa: BLE001 - report and go on
                    status = f"error: {type(e).__name__}: {str(e).splitlines()[0][:80]}"
        exported = (weights_dir / (name.replace("/", "_") + ".npz")).exists()
        rows.append((name, p.get("char") or "?", p["stage"], p["act_every"],
                     f"{p['delay']} ({p['delay'] * p['act_every']}f)", p["memory"],
                     f"{a} ({ACTION_SET_SIZES.get(a, '?')})", "no" if p["omit_char"] else "yes", opp, w,
                     "yes" if exported else "no", status))
    head = ("agent", "char", "stage", "act_every", "delay", "memory", "actions", "char input", "trained vs",
            "weights", "exported", "restore")
    widths = [max(len(str(r[i])) for r in rows + [head]) for i in range(len(head))]
    for r in [head] + rows:
        print("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)).rstrip())


if __name__ == "__main__":
    main()
