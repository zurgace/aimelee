"""slippi_agent.py: libmelee parsing of the bridge's Slippi stream, the pad
mapping, and (with a model) the live loop against a fake game.

Needs slippi-ai's environment (libmelee, slippi-ai, TensorFlow); skipped
elsewhere. The loop test also needs SLIPPI_TEST_MODEL: a slippi-ai model
file, e.g. one made by slippi-ai's slippi_ai/tf/scripts/create_model.py.

data/slp_stream.bin is a synthetic Fox (P1) vs Falco (P2) match on Final
Destination written by melee-pc's own serializer (gen_slp_stream.c).
"""

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

import bridge  # noqa: E402

try:
    import melee
except ImportError:
    melee = None

STREAM = HERE / "data" / "slp_stream.bin"


def chunks():
    data = STREAM.read_bytes()
    i = 0
    while i < len(data):
        (n,) = struct.unpack_from("<I", data, i)
        yield data[i + 4:i + 4 + n]
        i += 4 + n


@unittest.skipIf(melee is None, "libmelee not installed (slippi-ai environment)")
class ParseTest(unittest.TestCase):
    def test_libmelee_reads_the_stream(self):
        import slippi_agent

        console = slippi_agent.make_console()
        states = [gs for gs in (console.feed(c) for c in chunks()) if gs is not None]
        self.assertEqual(len(states), 80 + 123)  # frames -123..79, one state each
        self.assertEqual(states[0].frame, -123)
        last = states[-1]
        self.assertEqual(last.frame, 79)
        self.assertEqual(last.stage, melee.Stage.FINAL_DESTINATION)
        self.assertEqual(last.players[1].character, melee.Character.FOX)
        self.assertEqual(last.players[2].character, melee.Character.FALCO)
        self.assertEqual(last.players[2].facing, False)
        self.assertEqual(last.players[1].jumps_left, 2)
        self.assertAlmostEqual(last.players[1].percent, 7)
        # P2's pre-frame stick: 0.5 of full tilt right, as libmelee's 0..1.
        self.assertAlmostEqual(float(last.players[2].controller_state.main_stick[0]), 0.75)

    def test_slippi_parser_accepts_it(self):
        from slippi_db.parse_libmelee import Parser

        import slippi_agent

        console = slippi_agent.make_console()
        parser = Parser(ports=(2, 1))
        games = [parser.get_game(gs) for gs in (console.feed(c) for c in chunks()) if gs is not None]
        self.assertEqual(int(games[-1].p0.character), 22)  # internal Falco: the agent's side first
        self.assertEqual(int(games[-1].p1.character), 1)   # internal Fox

    def test_pad_mapping(self):
        import slippi_agent

        c = slippi_agent.controller_class()(2)
        c.press_button(melee.Button.BUTTON_A)
        c.press_button(melee.Button.BUTTON_D_UP)
        c.tilt_analog(melee.Button.BUTTON_MAIN, 1.0, 0.5)   # full right
        c.tilt_analog(melee.Button.BUTTON_C, 0.5, 0.0)      # full down
        c.press_shoulder(melee.Button.BUTTON_L, 0.5)
        pad = c.pad()
        self.assertEqual(pad.button, bridge.BUTTON_A | bridge.BUTTON_UP)
        self.assertEqual((pad.stick_x, pad.stick_y, pad.cstick_x, pad.cstick_y), (80, 0, 0, -80))
        self.assertEqual((pad.trigger_l, pad.analog_a, pad.analog_b), (70, 255, 0))
        c.release_button(melee.Button.BUTTON_A)
        c.tilt_analog(melee.Button.BUTTON_MAIN, 0.5 + 23 / 160, 0.5)  # one raw step past the deadzone
        self.assertEqual((c.pad().button, c.pad().stick_x, c.pad().analog_a), (bridge.BUTTON_UP, 23, 0))


class FakeGame(threading.Thread):
    """Streams the fixture like the game: each frame's events, then that tick's STATE."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.replies = {}
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(1)

    @staticmethod
    def msg(msg_type, payload):
        return bridge.HEADER.pack(bridge.MAGIC, msg_type, len(payload)) + payload

    def run(self):
        conn, _ = self.srv.accept()
        conn.sendall(self.msg(bridge.MSG_HELLO, bridge.HELLO.pack(
            bridge.PROTO_VERSION, 1, 0, 4000, 0, bridge.STATE_SIZE, b"fake")))
        frames = list(chunks())
        header, frames = frames[0], frames[1:]
        conn.sendall(self.msg(bridge.MSG_SLP_EVENTS, header))
        buf = b""
        for tick, events in enumerate(frames, 1):
            flags = bridge.ST_IN_FIGHT | bridge.ST_FIGHTERS_RAN | (bridge.ST_MATCH_START if tick == 1 else 0)
            head = bridge.STATE_HEAD.pack(tick, tick - 1, tick, 0, 2, 2, flags, 0, 0x20, 1, 0, 0, 0)
            body = b""
            for p in range(4):
                vals = [1 if p < 2 else 0, 0, (2, 20)[p] if p < 2 else 0, 0, 4, 0, 0, 2, 0, 0, 14,
                        0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 60.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0]
                body += bridge.FIGHTER.pack(*vals, *([0] * 11))
            conn.sendall(self.msg(bridge.MSG_SLP_EVENTS, events) + self.msg(bridge.MSG_STATE, head + body))
            conn.settimeout(10.0)  # lockstep: wait for this tick's reply
            while len(buf) < 28:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            while len(buf) >= 28:
                _, _, size = bridge.HEADER.unpack_from(buf, 0)
                target, _, fl, _ = bridge.INPUT_HEAD.unpack_from(buf, 8)
                self.replies[target - 1] = (fl, bridge.Pad.unpack(bridge.PAD.unpack_from(buf, 16)))
                buf = buf[8 + size:]
        conn.close()
        self.srv.close()


@unittest.skipIf(melee is None or not os.environ.get("SLIPPI_TEST_MODEL"),
                 "needs the slippi-ai environment and SLIPPI_TEST_MODEL")
class LoopTest(unittest.TestCase):
    def test_slippi_ai_plays_the_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = str(Path(tmp) / "agent.sock")
            game = FakeGame(sock)
            game.start()
            proc = subprocess.run([sys.executable, str(AGENT / "slippi_agent.py"),
                                   "--model", os.environ["SLIPPI_TEST_MODEL"], "--socket", sock,
                                   "--once", "--async-inference"],
                                  capture_output=True, text=True, timeout=300)
            game.join(30)
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        self.assertIn("Falco: slippi-ai plays P2", proc.stdout)
        frames = len(list(chunks())) - 1
        self.assertEqual(len(game.replies), frames, "one reply per tick")
        self.assertFalse(any(fl & bridge.IN_RELEASE for fl, _ in game.replies.values()))
        # A random model still moves the sticks once its delay has passed.
        self.assertTrue(any(p != bridge.Pad.neutral() for _, p in game.replies.values()))


if __name__ == "__main__":
    unittest.main()
