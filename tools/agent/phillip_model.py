"""Phillip's policy network in numpy.

Loads the .npz that export_weights.py writes from Phillip's own TensorFlow
Actor (weights after restore(), plus the resolved embedding and layer spec)
and computes the same action probabilities without TensorFlow:

    embed each player (floats scaled and clamped to [-10, 10], action state
    one-hot 383, optional character one-hot 32, optional linear/NL FCs)
    -> game = [P0, P1] (+ prev action one-hot) per history step
    -> trunk FC layers -> GRU layers -> [core, delayed action one-hots]
    -> actor FC layers -> softmax -> (1 - eps) p + eps / A

Mirrors embed.py, core.py, ac.py, actor.py and tf_lib.py of vladfi1/phillip
(MIT). Everything runs in float32 like the TF graph did. On load, the
reference inputs/outputs the exporter recorded are replayed, so a broken
file or a numpy mismatch fails loudly before a match starts.
"""

import json

import numpy as np

PLAYER_FIELD_ORDER = None  # from the file: the spec lists fields in embedding order


def _nl(name, alpha):
    if name in (None, "linear"):
        return lambda x: x
    if name == "leaky_relu":
        a = np.float32(alpha)
        return lambda x: np.maximum(a * x, x)
    if name == "leaky_softplus":
        a = np.float32(alpha)

        def leaky_softplus(x):
            ax = a * x
            m = np.maximum(ax, x)
            return m + np.log(np.exp(ax - m) + np.exp(x - m))
        return leaky_softplus
    if name == "elu":
        return lambda x: np.where(x > 0, x, np.expm1(np.minimum(x, 0))).astype(np.float32)
    if name == "relu":
        return lambda x: np.maximum(x, 0)
    if name == "tanh":
        return np.tanh
    if name == "sigmoid":
        return lambda x: (1 / (1 + np.exp(-x))).astype(np.float32)
    raise ValueError(f"unknown nonlinearity {name}")


class Dense:
    def __init__(self, w, b, nl, alpha):
        self.w = np.asarray(w, np.float32)
        self.b = np.asarray(b, np.float32)
        self.f = _nl(nl, alpha)

    def __call__(self, x):
        return self.f(x @ self.w + self.b)


class GRU:
    """tf_lib.GRUCell."""

    def __init__(self, wru, bru, wc, bc):
        self.wru = np.asarray(wru, np.float32)
        self.bru = np.asarray(bru, np.float32)
        self.wc = np.asarray(wc, np.float32)
        self.bc = np.asarray(bc, np.float32)
        self.size = self.bc.shape[0]

    def __call__(self, x, h):
        ru = 1 / (1 + np.exp(-(np.concatenate([x, h], -1) @ self.wru + self.bru)))
        r, u = ru[..., :self.size], ru[..., self.size:]
        c = np.tanh(np.concatenate([x, r * h], -1) @ self.wc + self.bc)
        new_h = (u * h + (1 - u) * c).astype(np.float32)
        return new_h, new_h


class PhillipModel:
    def __init__(self, path):
        data = np.load(path, allow_pickle=False)
        self.meta = json.loads(str(data["meta"]))
        m = self.meta
        if m.get("predict"):
            raise NotImplementedError(
                f"{m.get('name')}: predictive-model agents (params 'predict') are not supported yet")

        def dense(key):
            spec = m["layers"][key]
            return Dense(data[key + "/W"], data[key + "/b"], spec["nl"], spec["alpha"])

        self.fields = m["player_fields"]
        self.action_fc = dense("action_fc") if "action_fc" in m["layers"] else None
        self.player_fc = dense("player_fc") if "player_fc" in m["layers"] else None
        self.trunk = [dense(f"trunk/{i}") for i in range(m["trunk_layers"])]
        self.gru = [GRU(data[f"gru/{i}/Wru"], data[f"gru/{i}/bru"], data[f"gru/{i}/Wc"], data[f"gru/{i}/bc"])
                    for i in range(m["gru_layers"])]
        self.actor = [dense(f"actor/{i}") for i in range(m["actor_layers"])]
        self.num_actions = m["num_actions"]
        self.memory = m["memory"]
        self.delay = m["delay"]
        self.act_every = m["act_every"]
        self.action_type = m["action_type"]
        self.epsilon = m["epsilon"]
        self.hidden_sizes = [g.size for g in self.gru]
        self._refs = {k[4:]: data[k] for k in data.files if k.startswith("ref/")}
        self.self_check()

    # ---- embedding (embed.py) -------------------------------------------

    def _embed_player(self, p):
        """p: object/dict with the PlayerObs fields. One player's vector."""
        get = p.get if isinstance(p, dict) else lambda k: getattr(p, k)
        parts = []
        for spec in self.fields:
            kind = spec["kind"]
            v = get(spec["field"])
            if kind == "float":
                t = np.float32(v)
                if spec["bias"]:
                    t = np.float32(t + np.float32(spec["bias"]))
                if spec["scale"]:
                    t = np.float32(t * np.float32(spec["scale"]))
                if spec["lower"]:
                    t = max(t, np.float32(spec["lower"]))
                if spec["upper"]:
                    t = min(t, np.float32(spec["upper"]))
                parts.append(np.array([t], np.float32))
            elif kind == "onehot":
                oh = np.zeros(spec["size"], np.float32)
                i = int(v)
                if 0 <= i < spec["size"]:  # tf.one_hot: out of range -> all zeros
                    oh[i] = 1
                if spec.get("fc"):
                    oh = self.action_fc(oh) if spec["fc"] == "action_fc" else oh
                parts.append(oh)
            elif kind == "null":
                continue
            else:
                raise ValueError(f"unknown embedding {kind}")
        x = np.concatenate(parts).astype(np.float32)
        if self.player_fc is not None:
            x = self.player_fc(x)
        return x

    def embed_game(self, players):
        return np.concatenate([self._embed_player(players[0]), self._embed_player(players[1])])

    def _onehot_action(self, a):
        oh = np.zeros(self.num_actions, np.float32)
        if 0 <= int(a) < self.num_actions:
            oh[int(a)] = 1
        return oh

    # ---- forward (actor.py) ---------------------------------------------

    def zero_hidden(self):
        return [np.zeros(s, np.float32) for s in self.hidden_sizes]

    def policy(self, history, delayed_actions, hidden):
        """Action probabilities.

        history: memory+1 entries, oldest first, each (players, prev_action)
            where players is [P0, P1] (P1 is the agent).
        delayed_actions: the `delay` queued actions, oldest first.
        hidden: list of GRU states.
        Returns (probs float32[A], new hidden).
        """
        x = np.concatenate([np.concatenate([self.embed_game(players), self._onehot_action(prev)])
                            for players, prev in history])
        for layer in self.trunk:
            x = layer(x)
        new_hidden = []
        for cell, h in zip(self.gru, hidden):
            x, h2 = cell(x, h)
            new_hidden.append(h2)
        x = np.concatenate([x] + [self._onehot_action(a) for a in delayed_actions]).astype(np.float32)
        for layer in self.actor:
            x = layer(x)
        z = x - np.max(x)
        e = np.exp(z)
        probs = (e / np.sum(e)).astype(np.float32)
        eps = np.float32(self.epsilon)
        probs = ((np.float32(1) - eps) * probs + eps / np.float32(self.num_actions)).astype(np.float32)
        return probs, new_hidden

    # ---- self check -----------------------------------------------------

    def self_check(self, tol=5e-4):
        """Replay the exporter's reference cases; raise if numpy disagrees.

        TF's float32 kernels (Eigen's exp/log, long dot products) and numpy's
        round differently: up to ~2e-4 on the probabilities of the widest
        agents (1798 inputs), and each is about as far from a float64
        evaluation as from the other, so the gap is float32 noise. A broken
        file or layer mix-up shows up as errors of 1e-2 and more."""
        refs = self._refs
        if "probs" not in refs:
            return
        eps = self.epsilon
        self.epsilon = float(self.meta["ref_epsilon"])
        try:
            for i in range(refs["probs"].shape[0]):
                history = []
                for j in range(self.memory + 1):
                    players = [dict(zip(refs["field_names"], refs["players"][i, j, k])) for k in range(2)]
                    history.append((players, refs["prev_action"][i, j]))
                delayed = list(refs["delayed_action"][i]) if self.delay else []
                hidden = [refs[f"hidden/{g}"][i] for g in range(len(self.gru))]
                probs, _ = self.policy(history, delayed, hidden)
                err = float(np.max(np.abs(probs - refs["probs"][i])))
                if not err <= tol:
                    raise RuntimeError(f"{self.meta.get('name')}: numpy policy differs from the TF "
                                       f"reference by {err:.3g} on case {i}")
        finally:
            self.epsilon = eps
