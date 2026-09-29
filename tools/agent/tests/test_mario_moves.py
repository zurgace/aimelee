"""mario_moves.py: the move library on synthetic states (no game needed)."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import bridge  # noqa: E402
import mario_moves as mm  # noqa: E402

FD = 0x20


def fighter(x=0.0, y=0.0, facing=1.0, air=False, motion=0x0E, jumps_used=0, percent=0, shield=60.0,
            vy=0.0, hitstun=False, ckind=mm.MARIO):
    flags = (bridge.FT_IN_AIR if air else 0) | (bridge.FT_IN_HITSTUN if hitstun else 0)
    return bridge.Fighter(present=True, slot_type=0, ckind=ckind, fkind=0, stocks=4, flags=flags,
                          jumps_used=jumps_used, max_jumps=2, percent=percent, motion_id=motion,
                          percent_f=float(percent), facing=facing, pos_x=x, pos_y=y, cur_x=x, cur_y=y,
                          action_frame=0.0, hitlag=0.0, mv0_bits=0, shield=shield, self_vx=0.0, self_vy=vy,
                          kb_vx=0.0, kb_vy=0.0, ground_vx=0.0, body_state=0, body_state_move=0, smash_state=0)


def run(mario, me, opp, plan, frames):
    s = mm.situation(me, opp, FD)
    return [mario.step(s, plan) for _ in range(frames)]


def presses(pads, button):
    """How many separate presses of `button` (a held button counts once)."""
    n, held = 0, False
    for p in pads:
        down = bool(p.button & button)
        n += down and not held
        held = down
    return n


class MovesTest(unittest.TestCase):
    def test_recovery_jumps_then_up_b_toward_the_stage(self):
        for side in (1, -1):
            m = mm.Mario(seed=1)
            me = fighter(x=side * 110, y=-5, air=True, jumps_used=1, facing=side)
            first = m.step(mm.situation(me, fighter(), FD), "approach")
            self.assertEqual(first.button, bridge.BUTTON_X, "double jump first")
            self.assertEqual(mm.sign(first.stick_x), -side, "toward the stage")
            m.queue.clear()
            me = fighter(x=side * 110, y=-30, air=True, jumps_used=2, vy=-1.0, facing=side)
            upb = m.step(mm.situation(me, fighter(), FD), "approach")
            self.assertTrue(upb.button & bridge.BUTTON_B)
            self.assertGreater(upb.stick_y, 60)
            self.assertEqual(mm.sign(upb.stick_x), -side)
            m.queue.clear()
            again = m.step(mm.situation(me, fighter(), FD), "approach")
            self.assertFalse(again.button & bridge.BUTTON_B, "one Up-B per trip offstage")

    def test_hitstun_is_di_only(self):
        m = mm.Mario(seed=1)
        m.run(mm.pad(mm.A))  # a stale queued move
        p = m.step(mm.situation(fighter(x=40, y=30, air=True, hitstun=True), fighter(), FD), "approach")
        self.assertEqual(p.button, 0)
        self.assertEqual(mm.sign(p.stick_x), -1, "DI toward the centre")
        self.assertFalse(m.queue)

    def test_tech_once_per_knockdown(self):
        m = mm.Mario(seed=1)
        me = fighter(x=10, y=4, air=True, motion=mm.TUMBLING, vy=-2.0)
        pads = run(m, me, fighter(), "approach", 5)
        self.assertEqual(presses(pads, bridge.BUTTON_R), 1)

    def test_plans_press_and_release(self):
        opp = fighter(x=10, percent=120)
        for plan in mm.PLANS:
            for seed in range(8):
                m = mm.Mario(seed=seed)
                pads = run(m, fighter(x=0, facing=1), opp, plan, 200)
                for p in pads:
                    self.assertFalse(p.button & bridge.BUTTON_START, plan)
                held = max_run = 0
                for p in pads:
                    held = held + 1 if p.button & bridge.BUTTON_A else 0
                    max_run = max(max_run, held)
                self.assertLessEqual(max_run, 1, f"{plan} holds A")

    def test_turns_before_attacking_behind(self):
        m = mm.Mario(seed=0)
        pads = run(m, fighter(x=0, facing=1), fighter(x=-10), "approach", 1)
        self.assertEqual(pads[0].button, 0)
        self.assertEqual(mm.sign(pads[0].stick_x), -1)
        self.assertLess(abs(pads[0].stick_x), 50, "a tilt, not a dash")

    def test_fireball_backs_off_when_close_and_throws_from_range(self):
        m = mm.Mario(seed=3)
        close = run(m, fighter(x=0), fighter(x=15), "fireball", 1)[0]
        self.assertEqual(mm.sign(close.stick_x), -1)
        m = mm.Mario(seed=3)
        pads = run(m, fighter(x=0), fighter(x=60), "fireball", 34)  # one cooldown
        self.assertEqual(presses(pads, bridge.BUTTON_B), 1)

    def test_edgeguard_goes_to_their_side(self):
        m = mm.Mario(seed=0)
        p = run(m, fighter(x=0), fighter(x=120, y=-20, air=True), "edgeguard", 1)[0]
        self.assertEqual(mm.sign(p.stick_x), 1)
        # Not offstage: edgeguard means spacing, and backing off never runs off the edge.
        for seed in range(6):
            m = mm.Mario(seed=seed)
            for p in run(m, fighter(x=80), fighter(x=60), "edgeguard", 20):
                self.assertFalse(p.stick_x > 0 and not p.button, "runs toward the ledge")

    def test_ledge_and_grabbed(self):
        m = mm.Mario(seed=0)
        p = run(m, fighter(x=-87, y=-10, motion=0xFD, air=True), fighter(), "space", 1)[0]
        self.assertTrue(p.button or p.stick_x > 0)
        m = mm.Mario(seed=0)
        pads = run(m, fighter(motion=0xE3), fighter(x=5), "space", 6)
        self.assertEqual(len({p.stick_x for p in pads} - {0}), 2, "mashes both ways")


BF = 0x1F


def sit(**kw):
    """A Situation with everything else neutral: Mario standing at 0, Fox-free FD."""
    base = dict(x=0.0, y=0.0, opp_x=40.0, opp_y=0.0, edge=85.566, air=False, opp_air=False, facing=1,
                motion=0x0E, opp_motion=0x0E, hitstun=False, hitlag=False, jumps_left=2, shield=60.0, vy=0.0,
                percent=0, opp_percent=0)
    base.update(kw)
    return mm.Situation(**base)


class TechTest(unittest.TestCase):
    """Techniques are off until she has seen them; then they come out right."""

    def test_nothing_without_skills(self):
        for seed in range(10):
            m = mm.Mario(seed=seed)
            for plan in mm.PLANS:
                for p in run(m, fighter(x=0), fighter(x=50), plan, 120):
                    self.assertFalse(p.button & bridge.BUTTON_R and p.stick_y < 0, f"{plan}: an air dodge")

    def test_wavedash(self):
        m = mm.Mario(seed=1, skills={"wavedash": 1.0})
        first = m.step(sit(opp_x=50), "approach")
        self.assertEqual(first.button, bridge.BUTTON_X, "jump")
        self.assertEqual(m.step(sit(opp_x=50, motion=mm.KNEE_BEND), "approach"), mm.NEUTRAL)
        dodge = m.step(sit(opp_x=50, motion=mm.JUMPING[0], air=True, y=1.0), "approach")
        self.assertTrue(dodge.button & bridge.BUTTON_R)
        self.assertGreater(dodge.stick_x, 50, "toward them, low angle")
        self.assertLess(dodge.stick_y, 0, "into the ground")
        self.assertEqual(m.step(sit(motion=mm.LANDING_SPECIAL), "approach"), mm.NEUTRAL, "slide")
        m.step(sit(opp_x=10), "defend")
        self.assertIsNone(m.combo, "over once she's standing")

    def test_wavedash_back_when_spacing(self):
        m = mm.Mario(seed=1, skills={"wavedash": 1.0})
        m.step(sit(opp_x=15), "space")
        m.step(sit(opp_x=15, motion=mm.KNEE_BEND), "space")
        dodge = m.step(sit(opp_x=15, motion=mm.JUMPING[1], air=True, y=1.0), "space")
        self.assertLess(dodge.stick_x, 0, "away from them")

    def test_l_cancel_once_before_landing(self):
        for skills, want in (({}, 0), ({"l_cancel": 1.0}, 1)):
            m = mm.Mario(seed=1, skills=skills)
            pads = [m.step(sit(air=True, motion=0x41, y=y, vy=-2.5, opp_x=60), "space") for y in (30, 20, 9, 6, 3)]
            self.assertEqual(presses(pads, bridge.BUTTON_R), want, skills)
            self.assertFalse(pads[1].button & bridge.BUTTON_R, "not while still high")

    def test_l_cancel_on_platforms_too(self):
        m = mm.Mario(seed=1, skills={"l_cancel": 1.0})
        s = sit(air=True, motion=0x41, x=-40, y=33, vy=-2.5, platforms=mm.PLATFORMS[BF], opp_x=60)
        self.assertTrue(m.step(s, "space").button & bridge.BUTTON_R, "6 above the side platform")

    def test_shffl(self):
        m = mm.Mario(seed=1, skills={"shffl": 1.0})
        self.assertEqual(m.step(sit(opp_x=10), "approach").button, bridge.BUTTON_X)
        self.assertEqual(m.step(sit(opp_x=10, motion=mm.KNEE_BEND), "approach"), mm.NEUTRAL, "short hop")
        aerial = m.step(sit(opp_x=10, air=True, y=2, vy=2.0, motion=mm.JUMPING[0]), "approach")
        self.assertGreater(aerial.cstick_x, 0, "forward-air")
        m.step(sit(opp_x=10, air=True, y=12, vy=0.5, motion=0x42), "approach")
        ff = m.step(sit(opp_x=10, air=True, y=12, vy=-0.2, motion=0x42), "approach")
        self.assertEqual(ff.stick_y, -80, "fast fall at the top")
        low = m.step(sit(opp_x=10, air=True, y=5, vy=-3.0, motion=0x42), "approach")
        self.assertTrue(low.button & bridge.BUTTON_R, "and the L-cancel")

    def test_platform_plan(self):
        m = mm.Mario(seed=1)
        self.assertEqual(run(m, fighter(x=0), fighter(x=40), "platform", 1)[0].stick_x, 0,
                         "no platforms on FD: spacing")
        m = mm.Mario(seed=1)
        s = sit(opp_x=40, platforms=mm.PLATFORMS[BF], edge=68.4)
        self.assertGreater(m.step(s, "platform").stick_x, 0, "to the platform on their side")
        m = mm.Mario(seed=1)
        s = sit(x=38, opp_x=40, platforms=mm.PLATFORMS[BF], edge=68.4)
        self.assertEqual(m.step(s, "platform").button, bridge.BUTTON_X, "under it: jump")

    def test_drop_onto_them(self):
        on = dict(x=-38, y=27.2, opp_x=-40, opp_y=0, platforms=mm.PLATFORMS[BF], edge=68.4)
        m = mm.Mario(seed=1)
        self.assertEqual(m.step(sit(**on), "platform").stick_y, -80, "tap down through it")
        m = mm.Mario(seed=1, skills={"shield_drop": 1.0})
        pads = [m.step(sit(**on), "platform")]
        pads.append(m.step(sit(**on, motion=0xB2), "platform"))
        pads.append(m.step(sit(**on, motion=0xB3), "platform"))
        self.assertTrue(all(p.button & bridge.BUTTON_R for p in pads))
        self.assertEqual(pads[-1].stick_y, -55, "the shield drop notch")
        self.assertEqual(pads[1].stick_y, 0, "still shielding")

    def test_waveland(self):
        m = mm.Mario(seed=1, skills={"waveland": 1.0})
        p = m.step(sit(air=True, y=4, vy=-2.0, motion=0x1D, opp_x=60), "space")
        self.assertTrue(p.button & bridge.BUTTON_R)
        self.assertLess(p.stick_y, 0)
        m = mm.Mario(seed=1)
        p = m.step(sit(air=True, y=4, vy=-2.0, motion=0x1D, opp_x=60), "space")
        self.assertFalse(p.button & bridge.BUTTON_R)


if __name__ == "__main__":
    unittest.main()
