"""gomi_timing.py and its use in the move library: eaten inputs noticed, laggy states
waited out, moves not waited out once she can act, and the shine as a staple."""

import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import fox_moves as fx  # noqa: E402
import gomi_options  # noqa: E402
import gomi_reads  # noqa: E402
import gomi_timing as gt  # noqa: E402
import mario_moves as mm  # noqa: E402
from test_mario_moves import fighter, sit  # noqa: E402

STAND, FAIR_LANDING, SOME_LAG = 0x0E, 0x47, 0x99


class TimingTest(unittest.TestCase):
    def press(self, t, motion, then=None, pad=None):
        """A press in `motion`; the game shows `then` afterwards (None: nothing changes)."""
        t.observe(sit(motion=motion), pad or mm.pad(mm.A))
        for _ in range(gt.CHECK_FRAMES):
            t.observe(sit(motion=then if then is not None else motion), mm.NEUTRAL)

    def test_eaten_and_taken(self):
        t = gt.Timing()
        self.press(t, SOME_LAG)                        # nothing changed: eaten
        self.press(t, 0x39, then=0x2D)                 # the jab's next hit came out: taken
        self.press(t, STAND)                           # from standing: always works, not checked
        self.assertEqual((t.pressed[SOME_LAG], t.eaten[SOME_LAG]), (1, 1))
        self.assertEqual((t.pressed[0x39], t.eaten[0x39]), (1, 0))
        self.assertEqual(t.match_pressed, 2)

    def test_what_doesnt_count(self):
        t = gt.Timing()
        t.observe(sit(motion=0x41, air=True), mm.pad(mm.R), lcancel=True)       # her L-cancel
        held = mm.pad(mm.R, r=140)
        for _ in range(10):
            t.observe(sit(motion=0xB3), held)                                  # a held shield: one press
        t.observe(sit(motion=mm.TUMBLING, air=True), mm.pad(mm.R))              # a tech press
        t.observe(sit(motion=0xE3), mm.pad(mm.A), mode="mash")                  # mashing out of a grab
        self.assertEqual(t.match_pressed, 0)

    def test_learns_which_states_to_wait_out(self):
        t = gt.Timing()
        self.assertTrue(t.blocked(FAIR_LANDING), "known landing lag: from the start")
        self.assertFalse(t.blocked(SOME_LAG))
        for _ in range(gt.MIN_TRIES):
            self.press(t, SOME_LAG)
        self.assertTrue(t.blocked(SOME_LAG), "most presses there get eaten: wait it out")
        for _ in range(gt.MIN_TRIES):
            self.press(t, FAIR_LANDING, then=STAND)
        self.assertFalse(t.blocked(FAIR_LANDING), "her own counts say it's fine: stop waiting")

    def test_memory_and_review(self):
        path = Path(tempfile.mkdtemp()) / "timing.json"
        t = gt.Timing(path)
        for _ in range(3):
            self.press(t, FAIR_LANDING)
        self.press(t, 0x39, then=0x2D)
        t.save()
        again = gt.Timing(path)
        self.assertEqual((again.pressed[FAIR_LANDING], again.eaten[FAIR_LANDING]), (3, 3))
        self.assertEqual(t.summary(), "3 of your 4 inputs (75%) were eaten, most in an aerial's landing lag (3) "
                                      "-- you wait those out now.")
        self.assertEqual(t.share(), 0.75)


class PlayerTimingTest(unittest.TestCase):
    def test_never_frozen(self):
        m = mm.Mario(seed=1)
        for _ in range(3 * gt.MIN_TRIES):                   # a misread: "eaten" while standing
            m.timing.observe(sit(opp_x=10), mm.pad(mm.A))
            for _ in range(gt.CHECK_FRAMES):
                m.timing.observe(sit(opp_x=10), mm.NEUTRAL)
        self.assertFalse(m.timing.blocked(0x0E), "standing is never waited out")
        m.timing.eaten[SOME_LAG] = m.timing.pressed[SOME_LAG] = 10
        pads = [m.step(sit(opp_x=10, motion=SOME_LAG), "approach") for _ in range(mm.MAX_WAIT + 2)]
        self.assertTrue(any(p.button for p in pads), "stuck in a state that eats presses: she tries anyway")

    def test_waits_out_landing_lag_then_acts(self):
        m = mm.Mario(seed=1)
        lag = sit(opp_x=10, motion=FAIR_LANDING)
        self.assertEqual(m.step(lag, "approach"), mm.NEUTRAL, "presses would be eaten")
        self.assertEqual(m.waited, 1)
        acts = m.step(sit(opp_x=10), "approach")
        self.assertTrue(acts.button or acts.cstick_x or acts.cstick_y or acts.stick_x, "the first frame she can")

    def test_doesnt_stand_there_after_a_move(self):
        m = mm.Mario(seed=1)
        m.chooser = lambda bucket, menu: "dtilt" if "dtilt" in menu else menu[0]
        first = m.step(sit(opp_x=10), "approach")
        self.assertEqual(first.button, mm.A, "down-tilt")
        pads = [m.step(sit(opp_x=10, motion=0x39), "approach") for _ in range(5)]   # the tilt plays out
        self.assertTrue(all(p == mm.NEUTRAL for p in pads))
        again = m.step(sit(opp_x=10), "approach")                                    # standing again
        self.assertEqual(again.button, mm.A, "acts again at once instead of waiting out the rest of the tail")

    def test_waiting_on_purpose_isnt_cut_short(self):
        m = mm.Mario(seed=1)
        m.chooser = lambda bucket, menu: "wait"
        pads = [m.step(sit(opp_x=35), "space") for _ in range(15)]
        self.assertEqual(sum(bool(p.button) for p in pads), 0)
        self.assertEqual(len(m.queue), 0, "the whole wait played out")


class ShineTest(unittest.TestCase):
    def test_crouch_then_shine_when_they_come_in(self):
        f = fx.Fox(seed=1)
        f.chooser = lambda bucket, menu: "crouch_shine"
        pads = [f.step(sit(opp_x=25), "defend") for _ in range(5)]
        self.assertTrue(all(p.stick_y == -80 and not p.button for p in pads), "crouching")
        shine = f.step(sit(opp_x=10, motion=0x28), "defend")
        self.assertEqual((shine.button, shine.stick_y), (bridge.BUTTON_B, -80))

    def test_aerial_into_shine(self):
        f = fx.Fox(seed=1)
        f.chooser = lambda bucket, menu: "sh_aerial"
        f.step(sit(opp_x=10), "approach")                                          # jump
        f.step(sit(opp_x=10, air=True, y=2, vy=2.0, motion=mm.JUMPING[0]), "approach")   # the aerial
        f.step(sit(opp_x=10, air=True, y=8, vy=-1.0, motion=0x41), "approach")
        lag = f.step(sit(opp_x=10, motion=0x46), "approach")                       # landed: lag
        self.assertEqual(lag, mm.NEUTRAL, "not into the landing lag")
        shine = f.step(sit(opp_x=10), "approach")
        self.assertEqual((shine.button, shine.stick_y), (bridge.BUTTON_B, -80), "shine as soon as she can")

    def test_shine_in_the_air(self):
        shines = 0
        for seed in range(10):
            f = fx.Fox(seed=seed)
            p = f.step(sit(opp_x=8, opp_y=20, opp_air=True, air=True, y=20, vy=-1.0, motion=0x1D), "pressure")
            shines += p.button == bridge.BUTTON_B and p.stick_y == -80
        self.assertGreater(shines, 1)

    def test_their_crouch_shine_is_read_as_one(self):
        rival = gomi_reads.Rival(Path(tempfile.mkdtemp()) / "r.json")
        r = gomi_reads.Reader(rival)
        me = fighter(x=10)
        for motion in (0x0E, 0x27, 0x28, gomi_reads.SHINE_START):
            r.watch(fighter(motion=motion, ckind=0x14), me, 10)
        for _ in range(gomi_options.WINDOW):
            r.watch(fighter(ckind=0x14), me, 10)
        self.assertIn("crouch_shine", rival.options()["close/grounded/center"])


class PaceTest(unittest.TestCase):
    """A scripted opponent stepping in and out; she stands free to act. With the move tails cut
    short she presses far more often, and as Fox she shines up close."""

    def presses(self, lib, cut_short=True, seconds=60):
        p = lib(seed=4)
        p.chooser = gomi_options.Bandit(Path(tempfile.mkdtemp()) / "o.json", "X", random.Random(4),
                                        priors={"shine": 4.0}).choose
        if not cut_short:
            mm_actionable, mm.ACTIONABLE = mm.ACTIONABLE, ()
        try:
            n = shines = 0
            prev = mm.NEUTRAL
            for f in range(seconds * 60):
                d = 8 + 30 * abs(((f // 90) % 4) - 2) / 2         # 8, 23, 38, 23, 8 ...
                pad = p.step(sit(opp_x=d), "pressure")
                new = (pad.button & ~prev.button) & (mm.A | mm.B | mm.X | mm.Z)
                n += bin(new).count("1")
                shines += bool(new & mm.B and pad.stick_y < -40)
                prev = pad
        finally:
            if not cut_short:
                mm.ACTIONABLE = mm_actionable
        return n, shines

    def test_busier_and_shining(self):
        before, _ = self.presses(fx.Fox, cut_short=False)
        after, shines = self.presses(fx.Fox)
        self.assertGreater(after, before * 1.2, (before, after))     # measured: 216 -> 272 a minute
        self.assertGreater(shines, 10, "shines a minute, up close half the time")


if __name__ == "__main__":
    unittest.main()
