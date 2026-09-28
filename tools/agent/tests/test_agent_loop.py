"""agent.py's live loop against a scripted fake game speaking the bridge protocol.

A tiny generated model stands in for Phillip's weights. The fake game plays
some menu ticks, a match (with a paused stretch) and a second match, waits
for each tick's input like lockstep does, and records what came back. The
reply stream must be: release outside fights, neutral for the first 120
frames of each match, then exactly the pads phillip_agent.PhillipAgent picks
offline for the same observations and seed -- held while paused, restarted
per match.
"""

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))

try:
    import numpy as np
except ImportError:
    np = None

import bridge  # noqa: E402

FIELDS = ["percent", "facing", "x", "y", "action_state", "action_frame", "character", "invulnerable",
          "hitlag_frames_left", "hitstun_frames_left", "jumps_used", "charging_smash", "shield_size",
          "in_air", "speed_air_x_self", "speed_ground_x_self", "speed_y_self", "speed_x_attack",
          "speed_y_attack"]


def make_model(path, char="falcon", seed=5, sees_char=False):
    rng = np.random.default_rng(seed)
    spec = []
    for f in FIELDS:
        if f == "action_state":
            spec.append({"field": f, "kind": "onehot", "size": 383})
        elif f == "character" and not sees_char:
            spec.append({"field": f, "kind": "null"})
        else:
            spec.append({"field": f, "kind": "float", "scale": 0.1, "bias": None, "lower": -10.0, "upper": 10.0})
    width = 2 * (383 + 17 + (1 if sees_char else 0)) + 30 + 30  # players, prev action, one delayed
    arrays = {"actor/0/W": rng.normal(0, 0.3, size=(width, 16)).astype(np.float32),
              "actor/0/b": np.zeros(16, np.float32),
              "actor/1/W": rng.normal(0, 1.0, size=(16, 30)).astype(np.float32),
              "actor/1/b": np.zeros(30, np.float32)}
    meta = {"name": "FalconFalconTiny", "player_fields": spec, "trunk_layers": 0, "gru_layers": 0,
            "actor_layers": 2, "num_actions": 30, "action_type": "old", "memory": 0, "delay": 1,
            "act_every": 2, "epsilon": 0.0, "predict": False,
            "params": {"char": char, "stage": "battlefield"},
            "layers": {"actor/0": {"nl": "leaky_relu", "alpha": 0.01}, "actor/1": {"nl": "linear", "alpha": 0.0}}}
    np.savez(path, meta=np.array(json.dumps(meta)), **arrays)


def fighter_bytes(port, t, present=True, ckind=0):
    x = (-30.0 if port == 0 else 30.0) + 5.0 * np.sin(t / 17.0 + port)
    vals = [1 if present else 0, 0, ckind, 2, 4, 0, 0, 2, int(t // 40) % 50, 0, 0x0E + (t // 25) % 20,
            float(t // 40 % 50), 1.0 if port == 0 else -1.0, x, 0.0, x, 0.0, float(t % 30), 0.0, 0,
            60.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0, 0, 0]
    return bridge.FIGHTER.pack(*vals, *([0] * 11))  # the consumed-pad fields: unused here


class FakeGame(threading.Thread):
    """Plays a script of ticks: (in_fight, match_start, scene_frame, fighters_ran[, P2 ckind])."""

    def __init__(self, path, script):
        super().__init__(daemon=True)
        self.path = path
        self.script = script
        self.replies = {}  # tick -> (target, flags, Pad)
        self.states = []
        self.released = True  # like the bridge: nothing to wait for until the agent drives
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(1)

    def send(self, conn, msg_type, payload):
        conn.sendall(bridge.HEADER.pack(bridge.MAGIC, msg_type, len(payload)) + payload)

    def run(self):
        conn, _ = self.srv.accept()
        hello = bridge.HELLO.pack(bridge.PROTO_VERSION, 1, 0, 4000, 0, bridge.STATE_SIZE, b"fake")
        self.send(conn, bridge.MSG_HELLO, hello)
        buf = b""
        for tick, entry in enumerate(self.script, 1):
            fight, start, scene_frame, ran = entry[:4]
            ck2 = entry[4] if len(entry) > 4 else 0
            flags = (bridge.ST_IN_FIGHT if fight else 0) | (bridge.ST_MATCH_START if start else 0) | \
                    (bridge.ST_FIGHTERS_RAN if ran else 0)
            head = bridge.STATE_HEAD.pack(tick, 0, scene_frame, 0, 2 if fight else 8, 2, flags, 0,
                                          0x1F, 1, 0, 0, 0)
            body = b"".join(fighter_bytes(p, scene_frame, fight and p < 2, ck2 if p == 1 else 0)
                            for p in range(4))
            self.states.append(bridge.State.unpack(head + body))
            self.send(conn, bridge.MSG_STATE, head + body)
            # Lockstep in a fight: wait for this tick's reply. In menus the
            # agent only sends a release now and then: just drain.
            conn.settimeout(5.0 if fight and not (self.released and not start) else 0.005)
            try:
                while len(buf) < 8 + 20:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
            except socket.timeout:
                pass
            while len(buf) >= 28:
                _, _, size = bridge.HEADER.unpack_from(buf, 0)
                target, port, fl, _ = bridge.INPUT_HEAD.unpack_from(buf, 8)
                pad = bridge.Pad.unpack(bridge.PAD.unpack_from(buf, 16))
                self.replies[target - 1] = (target, fl, pad)
                self.released = bool(fl & bridge.IN_RELEASE)
                buf = buf[8 + size:]
        conn.close()
        self.srv.close()


@unittest.skipIf(np is None, "numpy not installed")
class AgentLoopTest(unittest.TestCase):
    def test_reply_stream(self):
        import phillip_agent
        import phillip_model
        import phillip_obs as po

        menu = [(False, False, i, False) for i in range(1, 30)]
        # match 1: 300 frames, with a pause of 40 ticks (scene frame frozen) at frame 200
        m1 = []
        for i in range(1, 301):
            m1.append((True, i == 1, i, True))
            if i == 200:
                m1 += [(True, False, 200, False)] * 40
        m2 = [(True, i == 1, i, True) for i in range(1, 181)]
        script = menu + m1 + menu + m2
        with tempfile.TemporaryDirectory() as tmp:
            npz = Path(tmp) / "tiny.npz"
            make_model(npz)
            sock = str(Path(tmp) / "agent.sock")
            game = FakeGame(sock, script)
            game.start()
            proc = subprocess.run([sys.executable, str(AGENT / "agent.py"), "--weights", str(npz),
                                   "--socket", sock, "--seed", "3", "--once", "--quiet",
                                   "--stats-json", str(Path(tmp) / "stats.json")],
                                  capture_output=True, text=True, timeout=60)
            game.join(10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            stats = json.loads((Path(tmp) / "stats.json").read_text())

        # Offline expectation: the same agent on the same observations.
        with tempfile.TemporaryDirectory() as tmp:
            npz = Path(tmp) / "tiny.npz"
            make_model(npz)
            model = phillip_model.PhillipModel(npz)
        agent = phillip_agent.PhillipAgent(model, "falcon", epsilon=0.0, seed=3)
        expected = {}
        pad = bridge.Pad.neutral()
        frames = 0
        last_scene = None
        prev = {}
        for st in game.states:
            if not st.in_fight:
                last_scene = None
                continue
            if st.match_start:
                agent.reset()
                pad, frames, last_scene, prev = bridge.Pad.neutral(), 0, None, {}
            new = last_scene is None or st.scene_frame > last_scene
            last_scene = st.scene_frame
            if new:
                frames += 1
                if frames > 120:
                    obs = []
                    for p in (0, 1):
                        prev[p] = po.player_obs(st.fighters[p], prev.get(p))
                        obs.append(prev[p])
                    c = agent.act(obs)
                    if c is not po.REPEAT:
                        pad = po.dolphin_pad(c)
            expected[st.tick] = pad

        got_fight = {t: r for t, r in game.replies.items() if t in expected}
        self.assertEqual(len(got_fight), len(expected), "one reply per in-fight tick")
        for tick, pad in expected.items():
            target, flags, got = got_fight[tick]
            self.assertEqual(target, tick + 1)
            self.assertEqual(flags & bridge.IN_RELEASE, 0, f"tick {tick} released in a fight")
            self.assertEqual(got, pad, f"tick {tick}")
        releases = [t for t, r in game.replies.items() if r[1] & bridge.IN_RELEASE]
        self.assertTrue(releases, "the port is released outside fights")
        self.assertTrue(any(p != bridge.Pad.neutral() for p in expected.values()), "the agent did something")
        self.assertGreater(stats["network_steps"], 100)


    def test_roster_follows_the_character(self):
        """P2 as Marth (its agent), Roy (Marth's stands in, seeing itself as
        Marth) and Mario (no agent: released for the match)."""
        import phillip_agent
        import phillip_model
        import phillip_obs as po

        MARTH, ROY, MARIO = 0x09, 0x17, 0x08
        menu = [(False, False, i, False) for i in range(1, 20)]
        script = []
        for ck in (MARTH, ROY, MARIO):
            script += menu + [(True, i == 1, i, True, ck) for i in range(1, 200)]
        script += menu
        with tempfile.TemporaryDirectory() as tmp:
            marth = Path(tmp) / "marth.npz"
            falcon = Path(tmp) / "falcon.npz"
            make_model(marth, char="marth", seed=9, sees_char=True)
            make_model(falcon, char="falcon")
            roster_file = Path(tmp) / "roster.json"
            # The fake game plays Battlefield: Marth's only (FD) agent plays there too.
            roster_file.write_text(json.dumps({
                str(MARTH): {"final_destination": {"agent": "MarthTiny", "weights": str(marth),
                                                   "char": "marth", "stand_in_for": None}},
                str(ROY): {"final_destination": {"agent": "MarthTiny", "weights": str(marth),
                                                 "char": "marth", "stand_in_for": "roy"}},
                "0": {"battlefield": {"agent": "FalconTiny", "weights": str(falcon), "char": "falcon",
                                      "stand_in_for": None}}}))
            sock = str(Path(tmp) / "agent.sock")
            game = FakeGame(sock, script)
            game.start()
            proc = subprocess.run([sys.executable, str(AGENT / "agent.py"), "--roster", str(roster_file),
                                   "--socket", sock, "--seed", "3", "--once"],
                                  capture_output=True, text=True, timeout=60)
            game.join(10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            model = phillip_model.PhillipModel(marth)
        self.assertIn("Marth: MarthTiny", proc.stdout)
        self.assertIn("Roy: no Phillip agent, Marth's (MarthTiny) stands in", proc.stdout)
        self.assertIn("no Phillip agent plays Mario", proc.stdout)

        # Offline: Marth's agent on matches 1 and 2, seeing Marth both times.
        expected = {}
        agent = None
        for st in game.states:
            if not st.in_fight:
                continue
            ck = st.fighters[1].ckind
            if st.match_start:
                agent = phillip_agent.PhillipAgent(model, "marth", epsilon=0.0, seed=3)
                pad, frames, last_scene, prev = bridge.Pad.neutral(), 0, None, {}
            if ck == MARIO:
                continue
            new = last_scene is None or st.scene_frame > last_scene
            last_scene = st.scene_frame
            if new:
                frames += 1
                if frames > 120:
                    obs = []
                    for p in (0, 1):
                        prev[p] = po.player_obs(st.fighters[p], prev.get(p))
                        if p == 1:
                            prev[p].character = po.CSS_ICON_BY_CKIND[MARTH]
                        obs.append(prev[p])
                    c = agent.act(obs)
                    if c is not po.REPEAT:
                        pad = po.dolphin_pad(c)
            expected[st.tick] = pad
        for tick, pad in expected.items():
            target, flags, got = game.replies[tick]
            self.assertEqual(flags & bridge.IN_RELEASE, 0, f"tick {tick} released")
            self.assertEqual(got, pad, f"tick {tick}")
        self.assertTrue(any(p != bridge.Pad.neutral() for p in expected.values()))
        mario = [st.tick for st in game.states if st.in_fight and st.fighters[1].ckind == MARIO]
        drove = [t for t in mario if t in game.replies and not game.replies[t][1] & bridge.IN_RELEASE]
        self.assertEqual(drove, [], "the port is not driven for Mario")
        before = [t for t in game.replies if t < mario[0]]
        self.assertTrue(game.replies[max(before)][1] & bridge.IN_RELEASE,
                        "the port is still released when the Mario match starts")

    def test_gomi_plays_mario(self):
        """--gomi: Mario gets Gomihyu (a fake Ollama here), who answers every
        tick without holding the game up and reflects when the match ends."""
        sys.path.insert(0, str(HERE))
        from test_gomi import FakeOllama

        MARTH, MARIO = 0x09, 0x08
        menu = [(False, False, i, False) for i in range(1, 20)]
        script = menu + [(True, i == 1, i, True, MARIO) for i in range(1, 200)] + menu
        fake = FakeOllama(plan="pressure")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                marth = Path(tmp) / "marth.npz"
                make_model(marth, char="marth")
                roster_file = Path(tmp) / "roster.json"
                roster_file.write_text(json.dumps({str(MARTH): {"final_destination": {
                    "agent": "MarthTiny", "weights": str(marth), "char": "marth", "stand_in_for": None}}}))
                sock = str(Path(tmp) / "agent.sock")
                game = FakeGame(sock, script)
                game.start()
                env = dict(os.environ, GOMI_OLLAMA_URL=fake.url, GOMI_DIR=str(Path(tmp) / "gomi"),
                           GOMI_PLAN_EVERY="0.05")
                proc = subprocess.run([sys.executable, str(AGENT / "agent.py"), "--roster", str(roster_file),
                                       "--socket", sock, "--seed", "3", "--once", "--gomi"],
                                      capture_output=True, text=True, timeout=60, env=env)
                game.join(10)
                lessons = (Path(tmp) / "gomi" / "lessons.md").read_text()
        finally:
            fake.close()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gomi: Gomihyu plays Mario vs Captain Falcon", proc.stdout)
        self.assertIn("gomi: match over:", proc.stdout)
        self.assertIn("Fireballs work on Fox.", lessons)
        fight = [t for t, st in enumerate(game.states, 1) if st.in_fight]
        self.assertTrue(all(t in game.replies and not game.replies[t][1] & bridge.IN_RELEASE for t in fight))
        self.assertTrue(any(game.replies[t][2] != bridge.Pad.neutral() for t in fight), "Mario moves")


if __name__ == "__main__":
    unittest.main()
