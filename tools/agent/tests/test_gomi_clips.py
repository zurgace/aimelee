"""gomi_clips.py: her teachers' clips cut from replays, and her Falco playing them."""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import falco_moves  # noqa: E402
import gomi_clips as gc  # noqa: E402
import gomi_replays as gr  # noqa: E402
import mario_moves as mm  # noqa: E402
from test_gomi_replays import FALCO, MARTH, build_slp, falco_game, still  # noqa: E402
from test_mario_moves import sit  # noqa: E402

STAND, DASH, DAIR, DAIR_LANDING, HIT = 0x0E, 0x14, 0x45, 0x4A, 0x4B
UNI, SOUL = ("ILYJ#309", "Uni"), ("MRDO#248", "SoulvII")


def game(falco_frames, falco_x=0.0, other_x=30.0, players=None):
    """A Falco (port 1) doing `falco_frames` (dicts) next to a standing Marth (port 0)."""
    frames = [{1: dict(dict(x=falco_x), **f), 0: dict(x=other_x)} for f in falco_frames]
    return build_slp(0x20, {0: MARTH, 1: FALCO}, players or {1: UNI, 0: SOUL}, frames)


def clips_of(data):
    store = gc.Clips(Path(tempfile.mkdtemp()) / "clips.json")
    gr.learn_replay(None, data, clips=store)
    return store, [c for clips in store.data["keys"].values() for c in clips]


def frames_of(clip):
    return sum(n for _, n in clip["p"])


class ExtractTest(unittest.TestCase):
    def test_dash_dance_comes_in_short_steps(self):
        dance = [dict(action=DASH, raw_x=80 if (i // 6) % 2 else -80) for i in range(40)]
        _, clips = clips_of(game(dance))
        self.assertEqual([frames_of(c) for c in clips], [12, 12, 12], "and no scrap at the end")
        self.assertEqual({c["k"] for c in clips}, {"move"})

    def test_a_short_hop_dair_runs_until_she_can_act(self):
        hop = ([dict(action=STAND), dict(action=mm.KNEE_BEND, buttons=bridge.BUTTON_X)] + [dict(action=mm.KNEE_BEND)] * 2
               + [dict(action=mm.JUMPING[0], air=True, y=5.0)] * 5
               + [dict(action=DAIR, air=True, y=10.0, raw_x=0)] * 15
               + [dict(action=DAIR_LANDING)] * 5 + [dict(action=STAND)] * 12)
        _, clips = clips_of(game(hop))
        self.assertEqual(frames_of(clips[0]), 29, "the jump, the dair, its landing and the first free press")
        self.assertEqual(clips[0]["k"], "attack")
        self.assertEqual(clips[0]["p"][0][0][0], bridge.BUTTON_X)

    def test_cut_short_by_a_hit(self):
        frames = [dict(action=STAND)] * 3 + [dict(action=HIT)] * 20 + [dict(action=STAND)] * 14
        _, clips = clips_of(game(frames))
        self.assertEqual([frames_of(c) for c in clips], [12], "the few frames before the hit are dropped")

    def test_inputs_stored_toward_the_opponent(self):
        # On the right, the opponent to the left: holding left is "toward them".
        store, clips = clips_of(game([dict(action=DASH, raw_x=-80)] * 14, falco_x=40.0, other_x=0.0))
        self.assertEqual(clips[0]["p"][0][0][1], 80)
        # She plays it from the left: toward them is right.
        f = falco_moves.Falco(seed=1)
        f.clips, f.copy_share = store, 1.0
        s = sit(x=-40.0, opp_x=0.0, facing=1)
        self.assertEqual(gc.key_of(s).split("/")[:2], list(store.data["keys"])[0].split("/")[:2])

    def test_no_start_button(self):
        _, clips = clips_of(game([dict(action=STAND, buttons=bridge.BUTTON_START | bridge.BUTTON_A)] * 14))
        self.assertEqual(clips[0]["p"][0][0][0], bridge.BUTTON_A)

    def test_what_a_clip_traded(self):
        frames = [{1: dict(), 0: dict(x=10.0, percent=0)} for _ in range(20)]
        frames += [{1: dict(), 0: dict(x=10.0, percent=12)} for _ in range(20)]
        store = gc.Clips(Path(tempfile.mkdtemp()) / "c.json")
        gr.learn_replay(None, build_slp(0x20, {0: MARTH, 1: FALCO}, {1: UNI, 0: SOUL}, frames), clips=store)
        first = next(iter(store.data["keys"].values()))[0]
        self.assertEqual(first["o"], 12.0)


class StoreTest(unittest.TestCase):
    def clip(self, o=0.0, kind="move", sx=80):
        return {"o": o, "k": kind, "p": gc.encode([[0, sx, 0, 0, 0, 0]] * 12)}

    def test_run_length(self):
        pads = [[0, 80, 0, 0, 0, 0]] * 5 + [[bridge.BUTTON_B, 0, -80, 0, 0, 0]] + [[0, 0, 0, 0, 0, 0]] * 3
        self.assertEqual(len(gc.encode(pads)), 3)
        self.assertEqual(gc.decode(json.loads(json.dumps(gc.encode(pads)))), pads)

    def test_kept_per_situation(self):
        store = gc.Clips(Path(tempfile.mkdtemp()) / "c.json")
        for _ in range(gc.PER_KEY + 50):
            store.add("mid/grounded/level/center/facing", self.clip(), "A#1")
        self.assertEqual(len(store.data["keys"]["mid/grounded/level/center/facing"]), gc.PER_KEY)
        self.assertEqual(store.data["seen"]["mid/grounded/level/center/facing"], gc.PER_KEY + 50)

    def test_mostly_the_owner_and_what_paid(self):
        store = gc.Clips(Path(tempfile.mkdtemp()) / "c.json")
        for _ in range(5):
            store.saw_player("ILYJ#309", "Uni")
        store.saw_player("YOMI#762", "Yomiki")
        k = "mid/grounded/level/center/facing"
        store.add(k, self.clip(sx=10), "ILYJ#309")
        store.add(k, self.clip(sx=20), "YOMI#762")
        store.add(k, self.clip(sx=30, o=60.0), "ILYJ#309")
        store.add(k, self.clip(sx=40, o=-60.0), "ILYJ#309")
        store.add(k, self.clip(sx=50, kind="laser"), "ILYJ#309")
        self.assertEqual(store.owner(), "ILYJ#309")
        s = sit(x=0.0, opp_x=30.0)
        rng = random.Random(1)
        picks = [store.pick(s, "zone", rng)[0][1] for _ in range(3000)]
        n = {v: picks.count(v) for v in (10, 20, 30, 40, 50)}
        self.assertGreater(n[10], 2.5 * n[20], "the owner's clips count more")
        self.assertGreater(n[30], 5 * n[40], "and the ones that paid")
        self.assertGreater(n[50], 1.5 * n[10], "and the plan's kind (zone: lasers)")
        self.assertIn("Uni (4 clips) and others (1)", store.lines()[0])

    def test_borrows_from_coarser_situations(self):
        store = gc.Clips(Path(tempfile.mkdtemp()) / "c.json")
        for i in range(gc.MIN_POOL):
            store.add(f"mid/grounded/{'above' if i % 2 else 'level'}/center/facing", self.clip(), "A#1")
        self.assertTrue(store.has(sit(x=0.0, opp_x=30.0, facing=-1)), "range and their state match")
        self.assertTrue(store.has(sit(x=0.0, opp_x=30.0, opp_air=True)), "the range alone, at worst")
        self.assertFalse(store.has(sit(x=0.0, opp_x=10.0)), "never another range")


class LearnTest(unittest.TestCase):
    def test_clips_for_replays_read_before(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "r").mkdir()
        (tmp / "r" / "g.slp").write_bytes(falco_game())
        gr.learn(tmp / "r", tmp / "gomi", log=lambda m: None)
        (tmp / "gomi" / "falco" / "clips.json").unlink()          # from before clips existed
        self.assertEqual(gr.learn(tmp / "r", tmp / "gomi", log=lambda m: None), (0, 0), "nothing new")
        teacher = gr.Teacher(tmp / "gomi" / "falco" / "teacher.json")
        self.assertEqual(teacher.data["teachers"]["ILYJ#309"]["games"], 1, "not counted twice")
        clips = gc.Clips(tmp / "gomi" / "falco" / "clips.json")
        self.assertTrue(clips)
        self.assertEqual(clips.owner(), "ILYJ#309")
        self.assertEqual(gr.learn(tmp / "r", tmp / "gomi", log=lambda m: None), (0, 0))

    def test_every_player_counts_toward_the_owner(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "r").mkdir()
        (tmp / "r" / "fox.slp").write_bytes(build_slp(0x1F, {0: MARTH, 1: 0x02}, {1: UNI, 0: ("BIGM#987", "Big Mac")},
                                                      still(20, {0: -20.0, 1: 20.0})))
        (tmp / "r" / "g.slp").write_bytes(game([dict(action=STAND)] * 14, players={1: SOUL, 0: UNI}))
        gr.learn(tmp / "r", tmp / "gomi", log=lambda m: None)
        clips = gc.Clips(tmp / "gomi" / "falco" / "clips.json")
        self.assertEqual(clips.owner(), "ILYJ#309", "Uni is in both files, as Fox and as Marth")
        self.assertEqual(clips.counts(), {"MRDO#248": 1})


if __name__ == "__main__":
    unittest.main()
