"""fox_moves.py: Fox's plans and recovery on synthetic states (no game needed)."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import fox_moves as fm  # noqa: E402
from test_mario_moves import fighter, presses  # noqa: E402

FD = 0x20
FOX = fm.CKIND


def run(fox, me, opp, plan, frames):
    s = fm.situation(me, opp, FD)
    return [fox.step(s, plan) for _ in range(frames)]


class FoxTest(unittest.TestCase):
    def test_firefox_aims_at_the_ledge(self):
        for side in (1, -1):
            f = fm.Fox(seed=1)
            me = fighter(x=side * 120, y=-40, air=True, jumps_used=2, vy=-1.0, facing=side, ckind=FOX)
            pads = run(f, me, fighter(), "approach", fm.FIREFOX_CHARGE + 1)
            self.assertEqual((pads[0].button, pads[0].stick_y), (bridge.BUTTON_B, 80), "up-B")
            aim = pads[-1]
            self.assertEqual(fm.sign(aim.stick_x), -side, "toward the stage")
            self.assertGreater(aim.stick_y, 0, "up to the ledge from below")
            self.assertEqual(presses(pads, bridge.BUTTON_B), 1)

    def test_illusion_when_level_and_close(self):
        f = fm.Fox(seed=1)
        me = fighter(x=100, y=5, air=True, jumps_used=2, vy=-0.5, facing=1, ckind=FOX)
        p = run(f, me, fighter(), "approach", 1)[0]
        self.assertEqual((p.button, fm.sign(p.stick_x), p.stick_y), (bridge.BUTTON_B, -1, 0), "side-B home")

    def test_double_jump_first(self):
        f = fm.Fox(seed=1)
        me = fighter(x=100, y=-5, air=True, jumps_used=1, ckind=FOX)
        self.assertEqual(run(f, me, fighter(), "approach", 1)[0].button, bridge.BUTTON_X)

    def test_lasers_from_range(self):
        f = fm.Fox(seed=2)
        pads = run(f, fighter(x=0, ckind=FOX), fighter(x=70), "lasers", 22)
        self.assertEqual(presses(pads, bridge.BUTTON_B), 1)
        self.assertFalse(any(p.stick_y < 0 and p.button & bridge.BUTTON_B for p in pads), "a laser, not a shine")

    def test_shine_up_close(self):
        shines = 0
        for seed in range(10):
            f = fm.Fox(seed=seed)
            for p in run(f, fighter(x=0, ckind=FOX), fighter(x=8, percent=20), "pressure", 30):
                shines += bool(p.button & bridge.BUTTON_B and p.stick_y < 0)
        self.assertGreater(shines, 0)

    def test_plans_press_and_release(self):
        opp = fighter(x=10, percent=120)
        for plan in fm.PLANS:
            for seed in range(8):
                f = fm.Fox(seed=seed)
                held = max_run = 0
                for p in run(f, fighter(x=0, facing=1, ckind=FOX), opp, plan, 200):
                    held = held + 1 if p.button & bridge.BUTTON_A else 0
                    max_run = max(max_run, held)
                self.assertLessEqual(max_run, 1, f"{plan} holds A")


if __name__ == "__main__":
    unittest.main()
