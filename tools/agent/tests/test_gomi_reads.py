"""gomi_reads.py: spotting the human's techniques and habits on synthetic
frames, and the move library punishing what it has read."""

import dataclasses
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import fox_moves  # noqa: E402
import gomi_reads as gr  # noqa: E402
import mario_moves as mm  # noqa: E402
from test_mario_moves import fighter, sit  # noqa: E402

FOX = 0x02
FALL = 0x1D
STAND = 0x0E


def them(motion=STAND, air=False, x=0.0, y=0.0, vy=0.0, facing=1.0, ckind=0x09, pad=None):
    f = fighter(x=x, y=y, air=air, motion=motion, vy=vy, facing=facing, ckind=ckind)
    return dataclasses.replace(f, input=pad or bridge.Pad())


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rival = gr.Rival(Path(self.tmp.name) / "rival.json")
        self.me = fighter(x=40)

    def tearDown(self):
        self.tmp.cleanup()

    def feed(self, reader, frames, dist=40):
        learned = []
        for f in frames:
            learned += reader.watch(f, self.me, dist)
        return learned

    def wavedash(self):
        return [them(), *[them(mm.KNEE_BEND)] * 3, them(mm.JUMPING[0], air=True, y=1),
                them(mm.AIRDODGE, air=True, y=1), *[them(mm.LANDING_SPECIAL)] * 10, them()]

    def test_wavedash_twice_unlocks_it(self):
        r = gr.Reader(self.rival)
        self.assertEqual(self.feed(r, self.wavedash()), [])
        self.assertEqual(self.rival.data["techs"], {"wavedash": 1})
        self.assertEqual(self.feed(r, self.wavedash()), ["wavedash"])
        self.assertEqual(self.feed(r, self.wavedash()), [], "announced once")
        self.assertEqual(r.learned, ["wavedash"])
        self.assertIn("wavedash", self.rival.skills(mm))

    def test_waveland(self):
        r = gr.Reader(self.rival)
        frames = [them(FALL, air=True, y=40)] * 30 + [them(mm.AIRDODGE, air=True, y=3), them(mm.LANDING_SPECIAL)]
        self.feed(r, frames)
        self.assertEqual(self.rival.data["techs"], {"waveland": 1})

    def aerial(self, press_at=None, hold=False, fast_fall=False):
        """Short hop, nair, land: the trigger pressed `press_at` frames before landing."""
        frames = [them(), them(mm.KNEE_BEND), them(mm.KNEE_BEND), them(mm.JUMPING[0], air=True, y=2, vy=2.0)]
        vy = 2.0
        for i in range(20, 0, -1):
            pressed = hold or i == press_at
            pad = bridge.Pad(button=bridge.BUTTON_R if pressed else 0,
                             stick_y=-80 if fast_fall and i == 10 else 0)
            vy = -3.0 if fast_fall and i <= 10 else vy - 0.2
            frames.append(them(0x41, air=True, y=i, vy=vy, pad=pad))
        frames.append(them(0x46))
        return frames

    def test_l_cancel(self):
        for press_at, hold, want in ((3, False, {"l_cancel": 1}), (None, False, {}), (12, False, {}),
                                     (None, True, {})):
            rival = gr.Rival(Path(self.tmp.name) / "x.json")
            self.feed(gr.Reader(rival), self.aerial(press_at, hold))
            self.assertEqual(rival.data["techs"], want, (press_at, hold))

    def test_shffl(self):
        self.feed(gr.Reader(self.rival), self.aerial(press_at=2, fast_fall=True))
        self.assertEqual(self.rival.data["techs"], {"l_cancel": 1, "shffl": 1})

    def test_shield_drop_and_multishine(self):
        r = gr.Reader(self.rival)
        self.feed(r, [them(0xB3, y=27.2)] * 5 + [them(mm.PLATFORM_DROP, air=True, y=26)])
        shine = gr.SHINE_START
        fox = [them(shine, ckind=FOX)] * 4 + [them(mm.KNEE_BEND, ckind=FOX), them(shine, ckind=FOX)]
        self.feed(r, [them(ckind=FOX)] + fox + [them(0x169, ckind=FOX)] + fox[4:])
        self.assertEqual(self.rival.data["techs"], {"shield_drop": 1, "multishine": 2})
        self.assertIn("multishine", self.rival.skills(fox_moves))
        self.assertNotIn("multishine", self.rival.skills(mm), "Mario has no shine")

    def test_habits_and_reads(self):
        r = gr.Reader(self.rival)
        # She's at x=40; they face her (+1) at 0: a forward tech rolls toward her.
        for motion in (0xC8, 0xC8, 0xC8, 0xC9):
            self.feed(r, [them(0x26, air=True, y=5), them(motion)])
        self.feed(r, [them(0xFD, air=True, x=-88), them(0x102, x=-80)] * 2)
        self.assertEqual(self.rival.data["habits"]["tech"], {"toward": 3, "away": 1})
        self.assertEqual(self.rival.reads(), {"tech": ("toward", 0.75)}, "the ledge: only 2 seen")
        self.assertIn("when they tech: toward 75%, away 25% (4 seen)", self.rival.lines())
        self.rival.save()
        self.assertEqual(gr.Rival(self.rival.path).data, self.rival.data)

    def test_rates_grow_with_sightings(self):
        self.rival.data["techs"] = {"wavedash": 2, "l_cancel": 12, "waveland": 1}
        skills = self.rival.skills(mm)
        self.assertEqual(set(skills), {"wavedash", "l_cancel"})
        self.assertLess(skills["wavedash"], skills["l_cancel"])
        self.assertLessEqual(skills["l_cancel"], 0.8)


class ImitationTest(unittest.TestCase):
    """What they do when free to act, and what it trades for them: her head start."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rival = gr.Rival(Path(self.tmp.name) / "rival.json")
        self.reader = gr.Reader(self.rival)

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, opp, me=None, dist=10):
        self.reader.watch(opp, me or fighter(x=10), dist)

    def test_their_grab_and_what_it_traded(self):
        self.frame(them())
        self.frame(them(0xD4))                           # they grab, close, she's standing
        me = fighter(x=10)
        me.percent = 10
        for _ in range(20):
            self.frame(them(0xD5), fighter(x=10))
        for _ in range(gr.gomi_options.WINDOW):
            self.frame(them(), me)                        # she took 10 from it
        self.assertEqual(self.rival.options()["close/grounded/center"]["grab"], [1, 10, 100])

    def test_hops_rolls_and_shines(self):
        hop = [them(), them(mm.KNEE_BEND), them(mm.JUMPING[0], air=True, y=5)]
        self.feed(hop + [them(0x41, air=True, y=12)])                     # short hop nair
        self.feed(hop + [them(FALL, air=True, y=30)] * 3 + [them(0x42, air=True, y=34)])   # full hop fair
        self.feed([them(), them(0xEA, x=0.0, facing=1.0)])                # rolls back, away from her at 10
        self.feed([them(), them(0xE9, x=0.0, facing=1.0)])                # rolls toward her: not a retreat
        self.feed([them(ckind=FOX), them(gr.SHINE_START, ckind=FOX)])
        for _ in range(gr.gomi_options.WINDOW):
            self.frame(them())
        got = {o for rows in self.rival.options().values() for o in rows}
        self.assertEqual(got, {"sh_aerial", "fullhop_aerial", "retreat", "shine"})

    def feed(self, frames):
        for f in frames:
            self.frame(f)

    def test_what_works_for_them_goes_first(self):
        self.rival.data["options"] = {"close/grounded/center": {"dtilt": [4, 60.0, 900.0], "jab": [3, -9.0, 30.0]}}
        b = gr.gomi_options.Bandit(Path(self.tmp.name) / "o.json", "Marth", random.Random(1),
                                   head_start=self.rival.options())
        menu = ["grab", "dtilt", "jab", "smash"]
        self.assertEqual(b.choose(("close", "grounded", "center"), menu), "dtilt", "their best, first")
        self.assertNotIn("jab", [b.choose(("close", "grounded", "center"), menu) for _ in range(2)],
                         "then the untried ones: what failed them waits")
        self.assertIn("what works for them: close, you're on the ground: down-tilt (+15 a try, 4 times)",
                      self.rival.lines())


class PunishTest(unittest.TestCase):
    def test_tech_chase_to_where_the_roll_ends(self):
        m = mm.Mario(seed=1)
        # They tech forward (facing +1) from x=0; she's at -20: the roll ends near +30.
        p = m.step(sit(x=-20, opp_x=0, opp_motion=0xC8, opp_facing=1), "space")
        self.assertGreater(p.stick_x, 0)
        m = mm.Mario(seed=1)
        early = m.step(sit(x=40, opp_x=20, facing=-1, opp_motion=0xC8, opp_facing=1, opp_frame=10), "space")
        self.assertEqual(early, mm.NEUTRAL, "wait for the roll to end")
        grab = m.step(sit(x=26, opp_x=24, facing=-1, opp_motion=0xC8, opp_facing=1, opp_frame=31), "space")
        self.assertEqual(grab.button, bridge.BUTTON_Z)

    def test_prepositions_for_their_usual_tech(self):
        tumbling = dict(x=0, opp_x=10, opp_y=8, opp_air=True, opp_motion=mm.TUMBLING)
        m = mm.Mario(seed=1)
        self.assertNotEqual(m.step(sit(**tumbling), "space").stick_x, 80, "no read: no guess")
        m = mm.Mario(seed=1)
        m.reads = {"tech": ("away", 0.7)}
        self.assertEqual(m.step(sit(**tumbling), "space").stick_x, 80, "they tech away: past them")

    def test_ledge_trap(self):
        hanging = dict(x=40, opp_x=-88, opp_y=-10, opp_air=True, opp_motion=0xFD, facing=-1)
        m = mm.Mario(seed=1)
        m.reads = {"ledge": ("roll", 0.6)}
        self.assertLess(m.step(sit(**hanging), "edgeguard").stick_x, 0, "to where their roll ends")
        m = mm.Mario(seed=1)
        m.reads = {"ledge": ("roll", 0.6)}
        self.assertEqual(m.step(sit(**dict(hanging, x=-40.5)), "edgeguard"), mm.NEUTRAL, "waiting there")


if __name__ == "__main__":
    unittest.main()
