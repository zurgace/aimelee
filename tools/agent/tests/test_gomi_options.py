"""gomi_options.py and the move library's options: situations, menus, trying
everything, learning what pays, and the scoring window."""

import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import fox_moves as fm  # noqa: E402
import gomi_options as go  # noqa: E402
import mario_moves as mm  # noqa: E402
from test_mario_moves import sit  # noqa: E402


class BanditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "options.json"

    def tearDown(self):
        self.tmp.cleanup()

    def bandit(self, seed=1, **kw):
        return go.Bandit(self.path, "Fox", random.Random(seed), **kw)

    def play(self, b, bucket, menu, pays, decisions):
        """`decisions` tries, each scored by `pays` over its whole window."""
        picks = []
        for _ in range(decisions):
            o = b.choose(bucket, menu)
            picks.append(o)
            for _ in range(go.WINDOW):
                b.frame(pays.get(o, 0.0) / go.WINDOW, 0)
        return picks

    def test_tries_everything_first(self):
        menu = ["grab", "dtilt", "jab", "smash", "shield", "retreat"]
        b = self.bandit()
        picks = [b.choose(("close", "grounded", "center"), menu) for _ in range(len(menu))]
        self.assertEqual(sorted(picks), sorted(menu), "every option once before any repeat")

    def test_learns_what_pays(self):
        menu = ["grab", "dtilt", "jab", "dash_attack", "shield"]
        b = self.bandit()
        picks = self.play(b, ("close", "grounded", "center"), menu, {"grab": 15.0, "dash_attack": -12.0}, 200)
        late = picks[-100:]
        self.assertGreater(late.count("grab"), 60, "leans on what works")
        self.assertLess(late.count("dash_attack"), 5, "drops what gets punished")
        self.assertGreater(len(set(late)), 1, "and still tries the rest now and then")

    def test_scoring_window(self):
        b = self.bandit()
        b.choose(("mid", "air", "center"), ["zone"])
        for _ in range(30):
            b.frame(0, 0)
        b.choose(("mid", "air", "center"), ["wait"])     # overlaps the first
        b.frame(10, 0)                                    # both get it
        b.frame(0, 0, kos=1)                              # a stock: 40
        for _ in range(go.WINDOW):
            b.frame(0, 4)
        rows = b.data["by_opponent"]["Fox"]["mid/air/center"]
        self.assertEqual(rows["zone"][:2], [1, 10 + 40 - 4 * (go.WINDOW - 32)])
        self.assertEqual(rows["wait"][:2], [1, 10 + 40 - 4 * (go.WINDOW - 2)])
        b.choose(("far", "grounded", "center"), ["dash_in"])
        for _ in range(go.MIN_FRAMES - 1):
            b.frame(0, 0)
        b.finish()
        self.assertNotIn("far/grounded/center", b.data["by_opponent"]["Fox"], "cut too short to count")

    def test_memory_and_other_opponents(self):
        b = self.bandit()
        self.play(b, ("close", "grounded", "center"), ["grab", "jab"], {"grab": 20.0}, 20)
        b.save()
        again = self.bandit()
        self.assertEqual(again.data, b.data)
        marth = go.Bandit(self.path, "Marth", random.Random(2))
        self.assertGreater(marth.value("close/grounded/center", "grab")[0],
                           marth.value("close/grounded/center", "jab")[0], "a hint from other opponents")
        self.assertEqual(marth.mine, {}, "but Marth's own table starts empty")

    def test_priors_steer_the_first_choices(self):
        b = self.bandit(priors={"grab": 30.0})
        menu = ["grab", "jab"]
        for o in menu:                                   # both tried once, at nothing either way
            b.started[("close/shield/center", o)] = 1
        picks = [b.choose(("close", "shield", "center"), menu) for _ in range(40)]
        self.assertGreater(picks.count("grab"), 30)

    def test_lines(self):
        b = self.bandit()
        self.play(b, ("close", "grounded", "center"), ["grab", "dash_attack"], {"grab": 12.0, "dash_attack": -8.0}, 12)
        line = b.lines()[0]
        self.assertTrue(line.startswith("close, they're on the ground: grab +12 a try"), line)
        self.assertIn("dash attack -8", line)


class MenuTest(unittest.TestCase):
    def test_buckets(self):
        m = mm.Mario(seed=1)
        self.assertEqual(m.bucket(sit(opp_x=10)), ("close", "grounded", "center"))
        self.assertEqual(m.bucket(sit(opp_x=30, opp_air=True, opp_y=20)), ("mid", "air", "center"))
        self.assertEqual(m.bucket(sit(opp_x=60, opp_motion=0xB3)), ("far", "shield", "center"))
        self.assertEqual(m.bucket(sit(opp_x=15, opp_motion=0x47)), ("close", "busy", "center"))
        self.assertEqual(m.bucket(sit(x=-70, opp_x=-55)), ("close", "grounded", "edge"))

    def test_menus_fit_plan_and_range(self):
        m = mm.Mario(seed=1)
        close = sit(opp_x=10)
        self.assertNotIn("zone", m.menu(close, "approach", m.bucket(close)))
        self.assertNotIn("shine", m.menu(close, "pressure", m.bucket(close)), "Mario has no shine")
        f = fm.Fox(seed=1)
        self.assertIn("shine", f.menu(close, "pressure", f.bucket(close)))
        mid = sit(opp_x=35)
        self.assertNotIn("dash_attack", m.menu(mid, "space", m.bucket(mid)), "spacing: only on a whiff")
        busy = sit(opp_x=35, opp_motion=0x3C)
        self.assertIn("dash_attack", m.menu(busy, "space", m.bucket(busy)))
        for plan in mm.MENUS:
            for d in (10, 35, 80):
                s = sit(opp_x=d)
                self.assertTrue(set(m.menu(s, plan, m.bucket(s))) <= set(mm.MENUS[plan]))

    def test_every_option_plays_cleanly(self):
        for lib in (mm.Mario, fm.Fox, __import__("falco_moves").Falco):
            for option in lib.OPTIONS:
                for d in (8, 30, 70):
                    p = lib(seed=3, skills={"shffl": 1.0, "wavedash": 1.0, "multishine": 1.0})
                    p.chooser = lambda bucket, menu, option=option: option if option in menu else menu[0]
                    held = run = 0
                    for _ in range(150):
                        pad = p.step(sit(opp_x=d, air=False), "pressure" if option in mm.MENUS["pressure"] else
                                     "space")
                        held = held + 1 if pad.button & bridge.BUTTON_A else 0
                        run = max(run, held)
                    self.assertLessEqual(run, 1, f"{lib.__name__} {option} holds A")

    def test_space_no_longer_spams_dash_attack(self):
        for seed in range(10):
            m = mm.Mario(seed=seed)
            b = go.Bandit(Path(tempfile.gettempdir()) / f"gomi-opts-{seed}.json", "Fox", random.Random(seed))
            m.chooser = b.choose
            dash_attacks = 0
            for _ in range(600):
                p = m.step(sit(opp_x=35), "space")
                b.frame(0, 0)
                dash_attacks += bool(p.button & bridge.BUTTON_A and abs(p.stick_x) == 80)
            self.assertEqual(dash_attacks, 0, "no whiff to punish, no dash attack")


if __name__ == "__main__":
    unittest.main()
