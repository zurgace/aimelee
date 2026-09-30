"""gomi_replays.py: learning Falco from Slippi replays, on synthetic .slp files."""

import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import gomi_replays as gr  # noqa: E402
import mario_moves as mm  # noqa: E402

FALCO, MARTH, FOX = 0x14, 0x09, 0x02
INTERNAL = {FALCO: 22, MARTH: 18, FOX: 1}
PRE, POST, START = 0x44, 0x54, 0x138
STAND, GRAB, GRABBED = 0x0E, 0xD4, 0xE3


def ubj_str(s):
    b = s.encode()
    return b"U" + bytes([len(b)]) + b


def build_slp(stage, chars, players, frames):
    """A .slp: Game Start, then per frame a pre and post event per port. `frames`: a list of
    {port: dict(action, x, y, air, percent, stocks, buttons, raw_x, raw_y)}."""
    events = bytearray([0x35, 1 + 3 * 3])
    for cmd, size in ((0x36, START), (0x37, PRE), (0x38, POST)):
        events += bytes([cmd]) + struct.pack(">H", size)
    start = bytearray(START)
    struct.pack_into(">H", start, 0x12, stage)
    for port in range(4):
        start[0x64 + 0x24 * port] = chars.get(port, 0)
        start[0x65 + 0x24 * port] = 0 if port in chars else 3
    events += b"\x36" + start
    for n, ports in enumerate(frames):
        for port, f in ports.items():
            pre = bytearray(PRE)
            struct.pack_into(">iBBIH", pre, 0, n, port, 0, 0, f.get("action", STAND))
            struct.pack_into(">IH", pre, 0x2C, 0, f.get("buttons", 0))
            pre[0x3A] = f.get("raw_x", 0) & 0xFF
            pre[0x3F] = f.get("raw_y", 0) & 0xFF
            post = bytearray(POST)
            struct.pack_into(">iBBBH", post, 0, n, port, 0, INTERNAL[chars[port]], f.get("action", STAND))
            struct.pack_into(">fffff", post, 0x9, f.get("x", 0.0), f.get("y", 0.0), 1.0, f.get("percent", 0), 60.0)
            post[0x20] = f.get("stocks", 4)
            post[0x2E] = 1 if f.get("air") else 0
            post[0x31] = 2
            events += b"\x37" + pre + b"\x38" + post
    meta = b"U\x08metadata{U\x07players{"
    for port, (code, name) in players.items():
        meta += (ubj_str(str(port)) + b"{U\x05names{U\x07netplayS" + ubj_str(name)
                 + b"U\x04codeS" + ubj_str(code) + b"}}")
    meta += b"}}"
    return b"{U\x03raw[$U#l" + struct.pack(">I", len(events)) + bytes(events) + meta + b"}"


def still(n, ports):
    return [{p: dict(x=x) for p, x in ports.items()} for _ in range(n)]


def falco_game(falco_port=1, other=MARTH):
    """Falco wavedashes twice, then grabs, and the grab is worth 10%."""
    other_port = 1 - falco_port
    frames = still(5, {falco_port: 0.0, other_port: 10.0})
    for _ in range(2):
        for motion, air in [(mm.KNEE_BEND, False)] * 3 + [(mm.JUMPING[0], True), (mm.AIRDODGE, True)] + \
                [(mm.LANDING_SPECIAL, False)] * 5 + [(STAND, False)] * 5:
            frames.append({falco_port: dict(action=motion, air=air), other_port: dict(x=10.0)})
    frames.append({falco_port: dict(action=GRAB), other_port: dict(x=10.0)})
    for i in range(100):
        frames.append({falco_port: dict(), other_port: dict(x=10.0, action=GRABBED, percent=10 if i > 20 else 0)})
    chars = {falco_port: FALCO, other_port: other}
    players = {falco_port: ("ILYJ#309", "Uni"), other_port: ("MRDO#248", "SoulvII")}
    return build_slp(0x20, chars, players, frames)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def teacher(self):
        return gr.Teacher(self.dir / "gomi" / "falco" / "teacher.json")

    def test_reads_a_replay(self):
        r = gr.read_replay(falco_game())
        self.assertEqual((r.stage, r.characters), (0x20, {0: MARTH, 1: FALCO}))
        self.assertEqual(r.players, {1: ("ILYJ#309", "Uni"), 0: ("MRDO#248", "SoulvII")})
        self.assertEqual(len(r.frames), 5 + 2 * 15 + 1 + 100)

    def test_a_falco_player_teaches(self):
        (self.dir / "replays").mkdir()
        (self.dir / "replays" / "Game_1.slp").write_bytes(falco_game())
        self.assertEqual(gr.learn(self.dir / "replays", self.dir / "gomi", log=lambda m: None), (1, 1))
        t = self.teacher()
        self.assertEqual(t.data["techs"].get("wavedash"), 2)
        self.assertEqual(t.options()["close/grounded/center"]["grab"], [1, 10, 100])
        self.assertEqual(t.data["teachers"]["ILYJ#309"]["name"], "Uni")
        self.assertIn("wavedash", t.skills(__import__("falco_moves")))
        self.assertTrue(t.lines()[0].startswith("you learned Falco from replays of Uni (1 games)"))

    def test_dittos_teach_twice_and_others_not_at_all(self):
        (self.dir / "r").mkdir()
        ditto = build_slp(0x1F, {0: FALCO, 1: FALCO}, {0: ("YOMI#762", "Yomiki"), 1: ("ILYJ#309", "Uni")},
                          still(20, {0: -20.0, 1: 20.0}))
        (self.dir / "r" / "ditto.slp").write_bytes(ditto)
        (self.dir / "r" / "sub").mkdir()
        (self.dir / "r" / "sub" / "fox.slp").write_bytes(
            build_slp(0x1F, {0: MARTH, 1: FOX}, {}, still(20, {0: -20.0, 1: 20.0})))
        self.assertEqual(gr.learn(self.dir / "r", self.dir / "gomi", log=lambda m: None), (2, 2))
        self.assertEqual(set(self.teacher().data["teachers"]), {"YOMI#762", "ILYJ#309"})

    def test_zip_and_only_new_files(self):
        zpath = self.dir / "2026-07.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("2026-07/Game_1.slp", falco_game())
            z.writestr("2026-07/notes.txt", "not a replay")
        logs = []
        self.assertEqual(gr.learn(zpath, self.dir / "gomi", log=logs.append), (1, 1))
        self.assertEqual(gr.learn(zpath, self.dir / "gomi", log=logs.append), (0, 0), "read already")
        with zipfile.ZipFile(zpath, "a") as z:
            z.writestr("2026-07/Game_2.slp", falco_game(falco_port=0))
        self.assertEqual(gr.learn(zpath, self.dir / "gomi", log=logs.append), (1, 1), "only the new one")
        self.assertEqual(self.teacher().data["teachers"]["ILYJ#309"]["games"], 2)

    def test_a_broken_replay_is_skipped(self):
        (self.dir / "r").mkdir()
        (self.dir / "r" / "bad.slp").write_bytes(b"not a replay at all")
        (self.dir / "r" / "good.slp").write_bytes(falco_game())
        logs = []
        self.assertEqual(gr.learn(self.dir / "r", self.dir / "gomi", log=logs.append), (2, 1))
        self.assertTrue(any("skipping bad.slp" in m for m in logs), logs)


if __name__ == "__main__":
    unittest.main()
