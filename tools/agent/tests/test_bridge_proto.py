"""bridge.py against the real C side of the bridge.

Builds tests/fake_game.c with src/pc/agent_link.c (skipped without a C
compiler), connects bridge.BridgeClient to it and checks every State field
against the values the C side wrote, then that the inputs the client sent
arrive on the ticks they target.
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
ROOT = AGENT.parents[1]
sys.path.insert(0, str(AGENT))

import bridge  # noqa: E402

CC = shutil.which(os.environ.get("CC", "cc")) or shutil.which("gcc")


def f32(x):
    """Round a Python float to float32, as the C side stores it."""
    import struct
    return struct.unpack("<f", struct.pack("<f", x))[0]


def expected_state(t):
    """Mirror of fill() in fake_game.c."""
    fighters = []
    for i in range(4):
        fighters.append(dict(
            present=True, slot_type=i, ckind=i + 10, fkind=i + 20, stocks=-(i + 1), flags=i + 1,
            jumps_used=i + 2, max_jumps=i + 3, percent=-(i * 100) - (t % 50),
            motion_id=0x100 + i + t, percent_f=f32(1.5 * i + t), facing=-1.0 if i % 2 else 1.0,
            pos_x=f32(t + 0.25 * i), pos_y=f32(-t - 0.5 * i), cur_x=f32(t + 0.125 * i),
            cur_y=f32(-t - 0.0625 * i), action_frame=f32(2.0 * i + 0.5), hitlag=f32(3.0 * i),
            mv0_bits=0x40490FDB ^ i, shield=f32(60.0 - i), self_vx=f32(f32(0.1) * (i + 1)),
            self_vy=f32(f32(-0.2) * (i + 1)), kb_vx=f32(f32(0.3) * (i + 1)),
            kb_vy=f32(f32(-0.4) * (i + 1)), ground_vx=f32(f32(0.5) * (i + 1)), body_state=-i,
            body_state_move=i * 7, smash_state=3,
            input=dict(button=0x1000 | i, stick_x=-80 + i, stick_y=80 - i, cstick_x=-(i + 1),
                       cstick_y=i + 1, trigger_l=140 + i, trigger_r=10 + i, analog_a=200 + i,
                       analog_b=100 + i, err=-1 if i == 3 else 0)))
    return dict(tick=t, match_tick=t * 2, scene_frame=t * 3, vs_frame=t * 5, scene_kind=2,
                game_mode=0x21, flags=0x3F, match_result=7, stage=0x1F, agent_port=1,
                late_inputs=t + 11, dropped_states=t + 13, fighters=fighters)


@unittest.skipIf(CC is None, "no C compiler")
class BridgeProtoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = Path(cls.tmp.name) / "fake_game"
        subprocess.run([CC, "-std=gnu11", "-O1", "-Wall", "-Werror", f"-I{ROOT / 'src'}",
                        str(HERE / "fake_game.c"), str(ROOT / "src/pc/agent_link.c"),
                        "-o", str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    transport = "unix"

    def run_game(self, ticks, timeout_us):
        if self.transport == "tcp":
            sock = bridge.free_tcp_address()
        else:
            sock = Path(self.tmp.name) / f"b{ticks}.sock"
        proc = subprocess.Popen([str(self.exe), str(sock), str(ticks), str(timeout_us)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return sock, proc

    def test_state_layout_and_inputs(self):
        ticks = 50
        sock, proc = self.run_game(ticks, 500000)
        try:
            with bridge.BridgeClient(str(sock), connect_timeout=10) as c:
                self.assertEqual(c.hello.build, "fake-game")
                self.assertEqual(c.hello.agent_port, 1)
                self.assertEqual(c.hello.sync_mode, bridge.SYNC_LOCKSTEP)
                self.assertEqual(c.hello.state_size, bridge.STATE_SIZE)
                # Tick 1's input goes first: the game waits for it before sending state 1.
                c.send_input(1, bridge.Pad(button=1, stick_x=1))
                for _ in range(ticks):
                    st = c.recv_state(timeout=5)
                    self.assertIsNotNone(st)
                    exp = expected_state(st.tick)
                    got = st.to_dict()
                    for key, val in exp.items():
                        if key == "fighters":
                            continue
                        self.assertEqual(got[key], val, key)
                    for i, fexp in enumerate(exp["fighters"]):
                        fgot = got["fighters"][i]
                        for key, val in fexp.items():
                            if isinstance(val, float):
                                self.assertTrue(math.isclose(fgot[key], val, rel_tol=0, abs_tol=0),
                                                f"P{i + 1}.{key}: {fgot[key]} != {val}")
                            else:
                                self.assertEqual(fgot[key], val, f"P{i + 1}.{key}")
                    self.assertAlmostEqual(st.fighters[0].mv0_float, math.pi, places=6)
                    if st.tick < ticks:
                        c.send_input(st.tick + 1, bridge.Pad(button=st.tick + 1, stick_x=(st.tick + 1) % 100))
            out, err = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(proc.returncode, 0, err)
        takes = [ln.split() for ln in out.splitlines() if ln.startswith("take")]
        self.assertEqual(len(takes), ticks)
        for t, kind, button, stick_x in ((int(a[1]), int(a[2]), int(a[3]), int(a[4])) for a in takes):
            self.assertEqual(kind, 1, f"tick {t} not on time")  # AGENT_TAKE_ON_TIME
            self.assertEqual(button, t)
            self.assertEqual(stick_x, t % 100)

    def test_release_and_wrong_port_do_not_drive(self):
        sock, proc = self.run_game(5, 20000)
        try:
            with bridge.BridgeClient(str(sock), connect_timeout=10) as c:
                c.send_input(1, bridge.Pad(button=0x100), port=2)  # not the agent port
                c.send_input(2, bridge.Pad(button=0x100), release=True)
                for _ in range(5):
                    self.assertIsNotNone(c.recv_state(timeout=5))
            out, _ = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        kinds = [int(ln.split()[2]) for ln in out.splitlines() if ln.startswith("take")]
        self.assertEqual(kinds, [0] * 5)  # AGENT_TAKE_NONE: never active

    def test_recording_roundtrip(self):
        path = Path(self.tmp.name) / "rec.bin"
        payloads = []
        for t in (1, 2, 3):
            st = expected_state(t)
            head = bridge.STATE_HEAD.pack(st["tick"], st["match_tick"], st["scene_frame"],
                                          st["vs_frame"], st["scene_kind"], st["game_mode"],
                                          st["flags"], st["match_result"], st["stage"],
                                          st["agent_port"], 0, st["late_inputs"], st["dropped_states"])
            body = b"".join(bytes(bridge.FIGHTER.size) for _ in range(4))
            payloads.append(head + body)
        with open(path, "wb") as f:
            bridge.write_record_header(f)
            for p in payloads:
                f.write(p)
        ticks = [st.tick for st in bridge.read_record(path)]
        self.assertEqual(ticks, [1, 2, 3])


class BridgeProtoTcpTest(BridgeProtoTest):
    """The same over loopback TCP, the transport Windows uses."""

    transport = "tcp"

    def test_address_forms(self):
        import socket
        self.assertEqual(bridge.parse_address("tcp:47101"), (socket.AF_INET, ("127.0.0.1", 47101)))
        self.assertEqual(bridge.parse_address("tcp:127.0.0.1:5"), (socket.AF_INET, ("127.0.0.1", 5)))
        for bad in ("tcp:", "tcp:x", "tcp:70000", "tcp:0"):
            with self.assertRaises(ValueError):
                bridge.parse_address(bad)


if __name__ == "__main__":
    unittest.main()
