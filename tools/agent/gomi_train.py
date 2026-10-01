#!/usr/bin/env python3
"""Train Gomihyu's Falco on the user's own replays: a slippi-ai network fine-tuned to play like them.

Clips of their inputs (gomi_clips) look nothing like them: a player's style is reactive, each input
depending on what the opponent just did. This is slippi-ai's own recipe instead (behavior cloning):
its network, which predicts a player's next controller input from the game state, trained on the
replay owner's Falco games, starting from medium-v2 (slippi-ai's trained network, the one the newer
Phillip plays with). Tested on the user's July replays from an untrained network of the same shape:
on games it never trained on, it predicted their inputs with 39% less error than one trained on
everyone else's games.

Runs in slippi-ai's environment (tools/agent/slippi-env; the launcher's "Train Gomi's Falco" button
starts it), on the CPU, on the user's machine: the replays never leave it.

    slippi-env/bin/python gomi_train.py --replays ~/Slippi      # a folder (searched recursively) or a .zip

1. Parse: each replay not parsed before goes through slippi-ai's parser (slippi_db.parse_local);
   the Falco games are kept in <gomi>/falco/train/Parsed.
2. Whose: the player in the most replays is the owner (they're their games, as in gomi_clips).
3. Train: from the base network (--base, else <gomi>/falco/base-model, else weights/slippi/
   medium-v2, which play.py downloads for the newer Phillip), or from her last model when there
   are new games, a few passes over the owner's Falco games.
   One game in five (by its hash, so it stays put as games are added) is held out; every EVAL_EVERY
   steps the error on those is measured, and each time it's the best so far the model is saved as
   <gomi>/falco/model (with model.json: whose, how many games, the error before and now). It stops
   when the error stops improving, or when stopped: the best so far is kept either way.

slippi-ai plays that model as her Falco (slippi_agent.py --gomi-falco) while Gomi herself watches,
reviews and posts (gomi_brain's watching mode).
"""

import argparse
import dataclasses
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FALCO = 22              # slippi-ai's parsed rows use the internal character ids (melee.Character.FALCO)
FALCO_NAME = "falco"    # the same, as slippi-ai's dataset filters name it
BASE_NAMES = ("medium-v2",)    # in weights/slippi/
EPOCHS = 3              # passes over the owner's games
MIN_STEPS = 200
BATCH, UNROLL = 16, 64  # sequences of 64 frames, 16 at a time: ~1.3 s a step on 4 cores
LEARNING_RATE = 3e-5
EVAL_EVERY = 100        # steps
EVAL_BATCHES = 20
PATIENCE = 3            # evaluations without a new best before it stops
HELD_OUT = 5            # one game in HELD_OUT is held out
MIN_DAMAGE = 100        # slippi_db's make_local_dataset: a game where hardly anything happened is left out


def log(msg):
    print(f"gomi-train: {msg}", flush=True)


# ---------------------------------------------------------------- the files

def paths(gomi_dir):
    falco = Path(gomi_dir) / "falco"
    train = falco / "train"
    return {"falco": falco, "train": train, "parsed": train / "Parsed", "rows": train / "parsed.json",
            "meta": train / "meta.json", "latest": train / "latest.pkl", "model": falco / "model",
            "info": falco / "model.json", "base": falco / "base-model"}


def find_base(gomi_dir, explicit=None):
    """The network to start from: the first that exists, else None."""
    p = paths(gomi_dir)
    candidates = [Path(explicit)] if explicit else []
    candidates += [p["base"]] + [HERE / "weights" / "slippi" / n for n in BASE_NAMES]
    return next((c for c in candidates if c.is_file()), None)


def jsonable(v):
    """numpy scalars and tuples, as JSON takes them."""
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        return v.item()
    return v


def code_of(player):
    """A parsed row's player: their connect code (else netplay name), '' when neither."""
    net = player.get("netplay") or {}
    return (net.get("code") or net.get("name") or "").replace("＃", "#")


def owner_of(rows):
    """The code in the most replays (any character): whose replays these are."""
    counts = Counter()
    for row in rows.values():
        for code in {code_of(p) for p in row.get("players") or []} - {""}:
            counts[code] += 1
    return max(counts, key=lambda c: (counts[c], c)) if counts else None


def name_of(rows, code):
    for row in rows.values():
        for p in row.get("players") or []:
            if code_of(p) == code:
                return (p.get("netplay") or {}).get("name") or code
    return code


def falco_game(row, code):
    """Why this parsed replay can't teach `code`'s Falco, or None when it can."""
    if not row.get("valid"):
        return "unreadable"
    if not row.get("is_training"):
        return row.get("not_training_reason") or "not a training replay"
    if not row.get("data_ok"):
        return "failed slippi-ai's data check"
    if not any(code_of(p) == code and p.get("character") == FALCO for p in row.get("players") or []):
        return "not their Falco"
    damage = [p.get("damage_taken") for p in row["players"]]
    if None not in damage and sum(damage) < MIN_DAMAGE:
        return "hardly anything happened"
    return None


def held_out(md5):
    """One game in HELD_OUT, by its hash: the same games stay out as more are added."""
    return int(hashlib.md5(md5.encode()).hexdigest(), 16) % HELD_OUT == 0


def split(rows):
    """(train, test) rows; each gets at least one (one game: the same one for both)."""
    test = [r for r in rows if held_out(r["slp_md5"])]
    train = [r for r in rows if not held_out(r["slp_md5"])]
    if not test and train:
        test = [train.pop()] if len(train) > 1 else list(train)
    if not train and test:
        train = [test.pop()] if len(test) > 1 else list(test)
    return train, test


def steps_for(rows):
    """A few passes over the owner's games (each frame is in UNROLL / BATCH-sized pieces)."""
    frames = sum(max(0, r.get("lastFrame") or 0) for r in rows)
    return max(MIN_STEPS, math.ceil(EPOCHS * frames / (BATCH * UNROLL)))


# ---------------------------------------------------------------- 1. parse

class _Replay:
    """What slippi_db.parse_local.parse_slp reads of a file: its name and bytes."""

    def __init__(self, name, read):
        self.name, self._read = name, read

    def read(self):
        return self._read()


def parse_new(source, p):
    """Parse the replays in `source` not parsed before; keep the Falco games. Returns the rows."""
    import gomi_replays
    from slippi_db import parse_local

    p["parsed"].mkdir(parents=True, exist_ok=True)
    try:
        rows = json.loads(p["rows"].read_text())
    except (OSError, ValueError):
        rows = {}
    new = 0
    started = time.monotonic()
    for key, read in gomi_replays.replay_files(source):
        if key in rows:
            continue
        with_tmp = p["train"] / "tmp"
        with_tmp.mkdir(exist_ok=True)
        row = parse_local.parse_slp_safe(_Replay(key, read), str(p["parsed"]), str(with_tmp))
        row = jsonable(row)
        row.setdefault("raw", "gomi")
        md5 = row.get("slp_md5")
        players = row.get("players") or []
        if md5 and not any(pl.get("character") == FALCO for pl in players):
            (p["parsed"] / md5).unlink(missing_ok=True)     # only Falco games are trained on
        rows[key] = row
        new += 1
        if new % 50 == 0:
            p["rows"].write_text(json.dumps(rows))
            log(f"parsed {new} new replays ({time.monotonic() - started:.0f}s)")
    if new:
        p["rows"].write_text(json.dumps(rows))
        log(f"parsed {new} new replay{'s' if new != 1 else ''} in {time.monotonic() - started:.0f}s")
    return rows


# ---------------------------------------------------------------- 3. train

def train(p, rows, owner, base_path, max_minutes=None):
    """Fine-tune on the owner's Falco games; the best model so far is saved as it goes."""
    import pickle

    import numpy as np
    from slippi_ai import data as data_lib, flag_utils, saving as generic_saving
    from slippi_ai.tf import learner as learner_lib, saving, train_lib

    games = [r for r in rows.values() if falco_game(r, owner) is None]
    train_rows, test_rows = split(games)
    p["meta"].write_text(json.dumps(games))
    try:
        info = json.loads(p["info"].read_text())
    except (OSError, ValueError):
        info = {}
    start = p["model"] if p["model"].is_file() and info.get("player") == owner else base_path
    if start == p["model"] and info.get("games") == len(games) and info.get("done"):
        log(f"her Falco has already learned from all {len(games)} of your Falco games")
        return
    state = generic_saving.load_state_from_disk(str(start))    # older models (medium-v2) need its unpickler
    log(f"learning from {len(train_rows)} of your Falco games ({len(test_rows)} held out to check her on), "
        f"starting from {'her last model' if start == p['model'] else Path(start).name}")

    cfg = generic_saving.upgrade_config(state["config"])
    try:
        config = flag_utils.dataclass_from_dict(train_lib.Config, cfg)
    except (TypeError, ValueError, KeyError):
        config = train_lib.Config()                  # an RL model's config: only the policy's own matters
        if "observation" in cfg:
            config.observation = flag_utils.dataclass_from_dict(type(config.observation), cfg["observation"])
    d = config.dataset
    d.data_dir, d.meta_path = str(p["parsed"]), str(p["meta"])
    d.allowed_characters, d.allowed_opponents, d.allowed_names = FALCO_NAME, "all", owner
    d.banned_names, d.swap, d.mirror = "none", True, False
    config.data.batch_size, config.data.unroll_length, config.data.num_workers = BATCH, UNROLL, 0
    config.data.shared_memory = False    # its worker processes need "forkserver", which Windows lacks

    policy = saving.load_policy_from_state(state)
    learner = learner_lib.Learner(policy=policy, value_function=None,
                                  **dict(dataclasses.asdict(config.learner), learning_rate=LEARNING_RATE))
    if not learner.value_vars:                        # no value function: nothing for its optimizer to do
        learner.value_optimizer.apply = lambda grads, params: None
    replays = data_lib.replays_from_meta(d)           # the owner's side of each game
    test_md5 = {r["slp_md5"] for r in test_rows}
    train_md5 = {r["slp_md5"] for r in train_rows}
    kw = dict(name_map=state.get("name_map") or {"": 0}, extra_frames=policy.delay + 1,
              observation_config=config.observation, **dataclasses.asdict(config.data))
    train_src = data_lib.make_source(replays=[r for r in replays if r.meta.slp_md5 in train_md5], **kw)
    test_src = data_lib.make_source(replays=[r for r in replays if r.meta.slp_md5 in test_md5], **kw)

    def evaluate():
        m = train_lib.TrainManager(learner, test_src, dict(train=False))
        try:
            return float(np.mean([np.mean(m.step()[0]["policy"]["loss"]) for _ in range(EVAL_BATCHES)]))
        finally:
            m.stop()

    def save(where, loss, done=False):
        out = {k: v for k, v in state.items() if k not in ("rl_config", "agent_config", "opponent")}
        out["config"] = dict(state["config"])
        out["config"]["dataset"] = dict(out["config"].get("dataset") or {}, allowed_characters=FALCO_NAME,
                                        allowed_opponents="all")
        out["state"] = dict(policy=tuple(v.numpy() for v in policy.variables))
        tmp = where.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(out, f)
        tmp.replace(where)
        if where == p["model"]:
            info.update(player=owner, name=name_of(rows, owner), games=len(games), held_out=len(test_rows),
                        loss=round(loss, 3), base=info.get("base") or Path(base_path).name,
                        base_loss=info.get("base_loss", round(first, 3)), done=done,
                        trained=time.strftime("%Y-%m-%d %H:%M:%S"))
            p["info"].write_text(json.dumps(info, indent=1))

    first = best = evaluate()
    log(f"before: {first:.2f} error on your held-out games")
    steps = steps_for(train_rows)
    deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
    manager = train_lib.TrainManager(learner, train_src, dict(train=True))
    stale, t0, done = 0, time.monotonic(), False
    try:
        for i in range(1, steps + 1):
            manager.step(compiled=False if i == 1 else None)     # the first creates Adam's slots
            if i % EVAL_EVERY and i != steps:
                continue
            loss = evaluate()
            if loss < best:
                best, stale = loss, 0
                save(p["model"], loss)
            else:
                stale += 1
            left = (steps - i) * (time.monotonic() - t0) / i
            log(f"step {i}/{steps}: {loss:.2f} error on your held-out games (best {best:.2f}, "
                f"before {first:.2f}); about {left / 60:.0f} min to go")
            if stale >= PATIENCE:
                log("it stopped getting better: done")
                done = True
                break
            if deadline and time.monotonic() > deadline:
                log("out of time for now; train again to go on")
                break
        else:
            done = True
    finally:
        manager.stop()
    if p["model"].is_file() and done:
        info["done"] = True
        p["info"].write_text(json.dumps(info, indent=1))
    if best < first:
        log(f"her Falco learned from your games: {first:.2f} -> {best:.2f} error on games she never saw "
            f"({(first - best) / first:.0%} less); saved as {p['model']}")
    else:
        log("no better than where it started: nothing saved this time")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replays", required=True, help="a folder of .slp replays (searched recursively) or a .zip")
    ap.add_argument("--gomi-dir", default=os.environ.get("GOMI_DIR") or str(HERE / "gomi"))
    ap.add_argument("--base", help="the slippi-ai network to start from (default: see above)")
    ap.add_argument("--minutes", type=float, help="stop after this long (the best model so far is kept)")
    args = ap.parse_args()

    p = paths(args.gomi_dir)
    base = find_base(args.gomi_dir, args.base)
    if base is None and not p["model"].is_file():
        log("no slippi-ai network to start from: play against slippi-ai once (it downloads medium-v2), "
            f"or save a slippi-ai network as {p['base']}")
        sys.exit(2)
    rows = parse_new(args.replays, p)
    owner = owner_of(rows)
    if owner is None:
        log("no Slippi netplay replays there (their connect codes say whose they are)")
        sys.exit(2)
    games = [r for r in rows.values() if falco_game(r, owner) is None]
    theirs = sum(1 for r in rows.values() if owner in {code_of(q) for q in r.get("players") or []})
    log(f"your replays are {name_of(rows, owner)}'s ({owner}: in {theirs} of {len(rows)}); "
        f"{len(games)} of their Falco games to learn from")
    if not games:
        sys.exit(2)
    train(p, rows, owner, base or p["model"], args.minutes)
    os._exit(0)      # slippi-ai's data workers can hold the process open


if __name__ == "__main__":
    main()
