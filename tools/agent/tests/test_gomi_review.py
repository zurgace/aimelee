"""gomi_review.py: what the match log makes of synthetic frames, and judging a drill."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import gomi_review as gr  # noqa: E402
from test_mario_moves import fighter  # noqa: E402

FAIR, USMASH, DTILT = 0x42, 0x3F, 0x39


class Match:
    """Her (Mario) and them, frame by frame."""

    def __init__(self):
        self.log = gr.MatchLog()
        self.me, self.opp = fighter(x=-20), fighter(x=20, ckind=0x02)
        self.step()

    def step(self, doing="space", her_off=False, their_off=False, n=1):
        for _ in range(n):
            self.log.frame(self.me, self.opp, doing, her_off, their_off)

    def they_hit(self, move, damage, doing="approach", **kw):
        self.opp.motion_id = move
        self.me.percent += damage
        self.step(doing, **kw)
        self.opp.motion_id = 0x0E

    def she_hits(self, move, damage):
        self.me.motion_id = move
        self.opp.percent += damage
        self.step()
        self.me.motion_id = 0x0E


class ReviewTest(unittest.TestCase):
    def test_hits_openings_and_summary(self):
        m = Match()
        m.step("approach", n=60)
        m.they_hit(FAIR, 12, doing="hit")        # opening 1: theirs
        m.step("approach", n=5)
        m.they_hit(FAIR, 8, doing="hit")         # same opening
        m.step(n=60)
        m.she_hits(DTILT, 10)                     # opening 2: hers
        m.step(n=60)
        m.she_hits(DTILT, 10)                     # opening 3: hers
        self.assertEqual(m.log.openings, {"her": [10, 10], "them": [20]})
        metrics = m.log.metrics({"wavedash": 3})
        self.assertEqual(metrics["opening_share"], 0.67)
        self.assertEqual(metrics["damage_per_opening"], 10.0)
        self.assertEqual(metrics["uses:wavedash"], 3)
        lines = m.log.summary()
        self.assertIn("They hit you most with forward-air (20%); the forward-air mostly while you were "
                      "approaching.", lines)
        self.assertIn("Openings: you won 2 of 3, worth 10% each; they won 1, worth 20% each.", lines)
        self.assertEqual(m.log.taken[0][2], "approaching", "what she was doing the frame before the hit")

    def test_how_stocks_are_lost(self):
        m = Match()
        m.step(her_off=True, n=30)                # offstage, nobody touched her: fell
        m.me.stocks = 3
        m.me.percent = 0
        m.step(n=200)
        m.me.percent = 110
        m.step(n=5)
        m.they_hit(USMASH, 16)
        m.me.stocks = 2
        m.step()
        causes = [d["cause"] for d in m.log.deaths]
        self.assertEqual(causes, ["fell short recovering", "killed by their up-smash at 126%"])
        self.assertEqual(m.log.metrics()["offstage_deaths"], 1)

    def test_knocked_off_then_fell_is_an_offstage_death(self):
        m = Match()
        m.they_hit(0x3C, 60, doing="space")      # forward-smash on stage at 60%
        m.step("hit", her_off=True, n=20)         # launched offstage
        m.step("recover", her_off=True, n=110)    # recovering... and short
        m.me.stocks = 3
        m.step("recover", her_off=True)
        self.assertEqual(m.log.deaths, [{"offstage": True,
                                         "cause": "fell short recovering after their forward-smash"}])
        self.assertEqual(m.log.metrics()["offstage_deaths"], 1)

    def test_hit_while_recovering_and_launch_kos(self):
        m = Match()
        m.step("recover", her_off=True, n=10)
        m.they_hit(0x45, 12, doing="recover", her_off=True)   # spiked while recovering
        m.step("hit", her_off=True, n=30)
        m.me.stocks = 3
        m.step("hit", her_off=True)
        m.me.percent = 120
        m.step(n=200)
        m.they_hit(0x3F, 18, doing="space")      # up-smash at 120%: flies off the top in hitstun
        m.step("hit", n=40)
        m.me.stocks = 2
        m.step("hit")
        self.assertEqual([d["cause"] for d in m.log.deaths],
                         ["hit offstage by their down-air", "killed by their up-smash at 138%"])
        self.assertEqual(m.log.metrics()["offstage_deaths"], 1)

    def test_grabs_that_land(self):
        m = Match()
        m.me.motion_id = 0xD4                     # her grab whiffs
        m.step()
        m.me.motion_id = 0x0E
        m.step(n=20)
        m.me.motion_id, m.opp.motion_id = 0xD5, 0xE2   # her grab lands: they're pulled in
        m.step()
        m.me.motion_id, m.opp.motion_id = 0x0E, 0xE3
        m.step()
        self.assertEqual(dict(m.log.grabs), {"her": 1})

    def test_edgeguard_kos(self):
        m = Match()
        m.step(their_off=True, n=10)
        m.she_hits(0x44, 12)                      # hit them offstage
        m.opp.stocks = 3
        m.step(their_off=True)
        self.assertEqual(m.log.metrics()["edgeguard_kos"], 1)

    def test_move_names(self):
        self.assertEqual([gr.move_name(x) for x in (0x2C, 0x35, 0x3C, 0x47, 0xDD, 0x170, 0x0E)],
                         ["jab", "forward-tilt", "forward-smash", "forward-air", "up throw", "a special move",
                          "something else"])

    def test_judge_and_pick(self):
        self.assertEqual(gr.judge("recovery", "g", 3, {"offstage_deaths": 1})["verdict"], "better")
        self.assertEqual(gr.judge("neutral", "g", 0.5, {"opening_share": 0.4})["verdict"], "worse")
        self.assertEqual(gr.judge("tech:wavedash", "g", 2, {})["after"], 0)
        self.assertEqual(gr.pick_drill({"offstage_deaths": 2}), "recovery")
        self.assertEqual(gr.pick_drill({"opening_share": 0.3}), "neutral")
        self.assertEqual(gr.pick_drill({"opening_share": 0.6, "damage_per_opening": 30}, ["wavedash"]),
                         "tech:wavedash")
        self.assertIn("tech:shffl", gr.drill_names({"shffl": 0.5}))


if __name__ == "__main__":
    unittest.main()
