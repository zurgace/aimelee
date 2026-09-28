"""Unit tests for the Phillip port that need only numpy (no TensorFlow).

The TensorFlow oracle comparison is verify_model.py; these pin down the
pieces that can be checked on their own: action sets, the Dolphin pad
mapping, the CSS icon table against the decomp, the ring buffers and the
delay/chain/ban semantics of the decision loop, and the embedding math.
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
ROOT = AGENT.parents[1]
sys.path.insert(0, str(AGENT))

try:
    import numpy as np
except ImportError:  # the live agent needs numpy; the bridge tools do not
    np = None

needs_numpy = unittest.skipIf(np is None, "numpy not installed")

if np is not None:
    import phillip_agent
    import phillip_model
    import phillip_obs as po
    import bridge


@needs_numpy
class ActionSetTest(unittest.TestCase):
    def test_sizes_match_checkpoints(self):
        # actor output widths seen in the bundled checkpoints
        self.assertEqual(len(po.ACTION_TYPES["old"]), 30)       # FalconFalconBF
        self.assertEqual(len(po.ACTION_TYPES["custom"]), 35)    # delay0/*
        self.assertEqual(len(po.ACTION_TYPES["diagonal"]), 54)  # FoxFD0 ...

    def test_old_order(self):
        old = po.ACTION_TYPES["old"]
        self.assertEqual(old[0], po.SimpleController("NONE", (0.5, 0.5)))
        self.assertEqual(old[5], po.SimpleController("A", (0.5, 0.5)))
        self.assertEqual(old[9], po.SimpleController("A", (1.0, 0.5)))
        self.assertEqual(old[29], po.SimpleController("L", (1.0, 0.5)))

    def test_custom_ends_with_repeat(self):
        self.assertIs(po.ACTION_TYPES["custom"][-1], po.REPEAT)

    def test_chain_fills_act_every(self):
        self.assertEqual(po.action_chain("old", 5, 2), [po.ACTION_TYPES["old"][5]] * 2)
        sh = po.action_chain("custom_sh2_wd", 35, 3)
        self.assertEqual([c.button for c in sh], ["NONE", "NONE", "Y"])


@needs_numpy
class DolphinPadTest(unittest.TestCase):
    def test_stick_table(self):
        # value in [0, 1] -> s8 the game sees through Dolphin's pipe + GCPad
        cases = {(0.5, 0.5): (0, 0), (1, 0.5): (127, 0), (0, 0.5): (-127, 0), (0.5, 1): (0, 127),
                 (0.5, 0): (0, -127), (0.4, 0.5): (-26, 0), (0.6, 0.5): (25, 0), (1, 1): (127, 127),
                 (0, 0): (-127, -127)}
        for stick, want in cases.items():
            self.assertEqual(po.stick_to_pad(*stick), want, stick)

    def test_buttons(self):
        p = po.dolphin_pad(po.SimpleController("L", (0.5, 0.5)))
        self.assertEqual((p.button, p.trigger_l), (bridge.BUTTON_L, 255))
        p = po.dolphin_pad(po.SimpleController("A", (1, 0.5)))
        self.assertEqual((p.button, p.stick_x, p.analog_a), (bridge.BUTTON_A, 127, 255))
        self.assertEqual(po.dolphin_pad(po.NEUTRAL), bridge.Pad.neutral())

    def test_pipe_commands(self):
        text = po.pipe_commands(po.SimpleController("Y", (0.4, 0.5)))
        self.assertEqual(text[:8], ["RELEASE A", "RELEASE B", "RELEASE X", "PRESS Y", "RELEASE Z",
                                    "RELEASE START", "RELEASE L", "RELEASE R"])
        self.assertEqual(text[8:], ["SET MAIN 0.40 0.50", "SET C 0.50 0.50"])


@needs_numpy
class ObservationTest(unittest.TestCase):
    def test_css_icon_order_matches_decomp(self):
        src = (ROOT / "src/melee/mn/mncharsel.c").read_text()
        start = src.index("ICONHUD_DRMARIO")
        order = re.findall(r"\bCKind_(\w+)\s*,\s*ICONSTATE_", src[start:start + 6000])[:25]
        self.assertEqual(len(order), 25)
        import names
        by_name = {v: k for k, v in names.CHARACTER_KIND.items()}
        for icon, ck in enumerate(order):
            self.assertEqual(po.CSS_ICON_BY_CKIND[by_name[ck]], icon, ck)
        self.assertEqual(po.CSS_ICON_BY_CKIND[0x13], po.CSS_ICON_BY_CKIND[0x12])  # Sheik on Zelda

    def test_non_finite_keeps_previous(self):
        f = bridge.Fighter(True, 0, 0, 2, 4, bridge.FT_IN_AIR, 1, 2, 12, 0x0E, 12.5, 1.0, 10.0, 5.0,
                           10.0, 5.0, 3.0, 0.0, 0x7FC00000, 60.0, 0.1, -0.2, 0.0, 0.0, 0.0, 1, 0, 2)
        prev = po.PlayerObs(hitstun_frames_left=7.0)
        o = po.player_obs(f, prev)
        self.assertEqual(o.hitstun_frames_left, 7.0)  # NaN bits in mv0
        self.assertTrue(o.in_air and o.invulnerable and o.charging_smash)
        self.assertEqual((o.character, o.percent, o.action_state, o.jumps_used), (7, 12, 0x0E, 1))


class _StubModel:
    """A PhillipModel stand-in: fixed shape, policy recorded and scripted."""

    def __init__(self, action_type="old", act_every=2, delay=0, memory=0):
        self.action_type = action_type
        self.act_every = act_every
        self.delay = delay
        self.memory = memory
        self.num_actions = len(po.ACTION_TYPES[action_type])
        self.epsilon = 0.0
        self.calls = []
        self.next_actions = []

    def zero_hidden(self):
        return []

    def policy(self, history, delayed, hidden):
        self.calls.append((history, list(delayed)))
        a = self.next_actions.pop(0) if self.next_actions else 0
        p = np.zeros(self.num_actions, np.float32)
        p[a] = 1
        return p, hidden


@needs_numpy
class AgentLoopTest(unittest.TestCase):
    def players(self, x=0.0):
        return [po.PlayerObs(x=-x), po.PlayerObs(x=x)]

    def test_network_every_act_every_frames(self):
        m = _StubModel(act_every=3)
        m.next_actions = [5, 9, 0]
        a = phillip_agent.PhillipAgent(m, "falcon", seed=1)
        out = [a.act(self.players()) for _ in range(9)]
        self.assertEqual(len(m.calls), 3)
        self.assertEqual(out[:3], [po.ACTION_TYPES["old"][5]] * 3)
        self.assertEqual(out[3:6], [po.ACTION_TYPES["old"][9]] * 3)

    def test_delay_executes_old_actions_and_feeds_the_queue(self):
        m = _StubModel(act_every=1, delay=2)
        m.next_actions = [5, 6, 7, 8]
        a = phillip_agent.PhillipAgent(m, "falcon", seed=1)
        out = [a.act(self.players()) for _ in range(4)]
        old = po.ACTION_TYPES["old"]
        self.assertEqual(out, [old[0], old[0], old[5], old[6]])  # queue starts at action 0
        self.assertEqual([d for _, d in m.calls], [[0, 0], [0, 5], [5, 6], [6, 7]])
        # prev_action is the last *executed* one
        self.assertEqual([h[-1][1] for h, _ in m.calls], [0, 0, 0, 5])

    def test_memory_history_oldest_first(self):
        m = _StubModel(act_every=1, memory=2)
        a = phillip_agent.PhillipAgent(m, "falcon", seed=1)
        for x in (1.0, 2.0, 3.0):
            a.act(self.players(x))
        hist = m.calls[-1][0]
        self.assertEqual([h[0][1].x for h in hist], [1.0, 2.0, 3.0])
        first = m.calls[0][0]
        self.assertEqual([h[0][1].x for h in first], [0.0, 0.0, 1.0])  # zero-filled start

    def test_banned_sends_neutral(self):
        m = _StubModel(action_type="old", act_every=1)
        b_right = po.ACTION_TYPES["old"].index(po.SimpleController("B", (1.0, 0.5)))
        m.next_actions = [b_right, b_right]
        a = phillip_agent.PhillipAgent(m, "fox", seed=1)
        self.assertEqual(a.act(self.players(50.0)), po.NEUTRAL)  # fox side-B off the right
        self.assertEqual(a.act(self.players(-50.0)), po.ACTION_TYPES["old"][b_right])

    def test_repeat_passes_through(self):
        m = _StubModel(action_type="custom", act_every=2)
        m.next_actions = [len(po.ACTION_TYPES["custom"]) - 1]
        a = phillip_agent.PhillipAgent(m, "fox", seed=1)
        self.assertIs(a.act(self.players()), po.REPEAT)

    def test_sampling_matches_numpy_choice(self):
        # Phillip: numpy.random.choice(range(A), p=probs) on the global state.
        probs = np.array([0.1, 0.2, 0.3, 0.4] + [0.0] * 26, np.float32)
        m = _StubModel(act_every=1)
        m.policy = lambda h, d, hid: (probs, hid)
        a = phillip_agent.PhillipAgent(m, "falcon", seed=11)
        ours = []
        for _ in range(50):
            a.act(self.players())
            ours.append(int(a.actions.as_list()[-1]))
        np.random.seed(11)
        theirs = [int(np.random.choice(list(range(30)), p=probs)) for _ in range(50)]
        self.assertEqual(ours, theirs)


@needs_numpy
class ModelMathTest(unittest.TestCase):
    """A tiny hand-made model file: embedding scales, clamps, one-hots, FCs."""

    def make(self, tmp):
        rng = np.random.default_rng(3)
        fields = [
            {"field": "percent", "kind": "float", "scale": 0.01, "bias": None, "lower": -10.0, "upper": 10.0},
            {"field": "x", "kind": "float", "scale": 0.1, "bias": None, "lower": -10.0, "upper": 10.0},
            {"field": "action_state", "kind": "onehot", "size": 383, "fc": "action_fc"},
            {"field": "character", "kind": "null"},
        ]
        per_player = 2 + 4
        arrays = {
            "action_fc/W": rng.normal(size=(383, 4)).astype(np.float32),
            "action_fc/b": rng.normal(size=4).astype(np.float32),
            "actor/0/W": rng.normal(size=(2 * per_player + 30, 8)).astype(np.float32),
            "actor/0/b": rng.normal(size=8).astype(np.float32),
            "actor/1/W": rng.normal(size=(8, 30)).astype(np.float32),
            "actor/1/b": rng.normal(size=30).astype(np.float32),
        }
        meta = {"name": "tiny", "player_fields": fields, "trunk_layers": 0, "gru_layers": 0,
                "actor_layers": 2, "num_actions": 30, "action_type": "old", "memory": 0, "delay": 0,
                "act_every": 2, "epsilon": 0.0, "predict": False,
                "layers": {"action_fc": {"nl": "linear", "alpha": 0.0},
                           "actor/0": {"nl": "leaky_relu", "alpha": 0.01},
                           "actor/1": {"nl": "linear", "alpha": 0.0}}}
        path = Path(tmp) / "tiny.npz"
        np.savez(path, meta=np.array(json.dumps(meta)), **arrays)
        return path, arrays

    def test_forward_by_hand(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, w = self.make(tmp)
            m = phillip_model.PhillipModel(path)
            p0 = {"percent": 250, "x": -300.0, "action_state": 14, "character": 3}
            p1 = {"percent": 1200, "x": 12.5, "action_state": 500, "character": 3}  # clamps, bad id
            probs, _ = m.policy([([p0, p1], 5)], [], [])

            def player(p):
                oh = np.zeros(383, np.float32)
                if p["action_state"] < 383:
                    oh[p["action_state"]] = 1
                return np.concatenate([[min(max(p["percent"] * 0.01, -10), 10)],
                                       [min(max(p["x"] * 0.1, -10), 10)],
                                       oh @ w["action_fc/W"] + w["action_fc/b"]])
            prev = np.zeros(30, np.float32)
            prev[5] = 1
            x = np.concatenate([player(p0), player(p1), prev]).astype(np.float32)
            h = x @ w["actor/0/W"] + w["actor/0/b"]
            h = np.maximum(0.01 * h, h)
            z = h @ w["actor/1/W"] + w["actor/1/b"]
            want = np.exp(z - z.max())
            want /= want.sum()
            np.testing.assert_allclose(probs, want, atol=1e-5)

    def test_self_check_catches_a_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, arrays = self.make(tmp)
            data = dict(np.load(path))
            meta = json.loads(str(data.pop("meta")))
            meta["ref_epsilon"] = 0.0
            data["ref/players"] = np.zeros((1, 1, 2, 4))
            data["ref/field_names"] = np.array(["percent", "x", "action_state", "character"])
            data["ref/prev_action"] = np.zeros((1, 1), np.int64)
            data["ref/delayed_action"] = np.zeros((1, 0), np.int64)
            data["ref/probs"] = np.full((1, 30), 1 / 30, np.float32)  # wrong on purpose
            bad = Path(tmp) / "bad.npz"
            np.savez(bad, meta=np.array(json.dumps(meta)), **data)
            with self.assertRaises(RuntimeError):
                phillip_model.PhillipModel(bad)


if __name__ == "__main__":
    unittest.main()
