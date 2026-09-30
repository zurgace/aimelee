"""falco_moves.py: Falco's recovery, pillar and edgeguard on synthetic states."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import falco_moves as fm  # noqa: E402
from test_mario_moves import fighter, sit  # noqa: E402

FD = 0x20


def run(p, me, opp, plan, frames):
    s = fm.situation(me, opp, FD)
    return [p.step(s, plan) for _ in range(frames)]


class FalcoTest(unittest.TestCase):
    def test_phantasm_only_from_close(self):
        near = fighter(x=120, y=5, air=True, jumps_used=2, vy=-0.5, facing=1, ckind=fm.CKIND)   # 34 out
        p = run(fm.Falco(seed=1), near, fighter(), "approach", 1)[0]
        self.assertEqual((p.button, fm.sign(p.stick_x), p.stick_y), (bridge.BUTTON_B, -1, 0), "side-B home")
        far = fighter(x=135, y=5, air=True, jumps_used=2, vy=-0.5, facing=1, ckind=fm.CKIND)    # 49 out
        p = run(fm.Falco(seed=1), far, fighter(), "approach", 1)[0]
        self.assertEqual((p.button, p.stick_y), (bridge.BUTTON_B, 80), "too far for Phantasm: Fire Bird")
        fox = __import__("fox_moves").Fox(seed=1)
        p = run(fox, far, fighter(), "approach", 1)[0]
        self.assertEqual(p.stick_y, 0, "Fox's Illusion still reaches from there")

    def test_pillar(self):
        f = fm.Falco(seed=2)
        f.chooser = lambda bucket, menu: "shine"
        pads = run(f, fighter(x=0, ckind=fm.CKIND), fighter(x=8, percent=30), "pressure", 8)
        self.assertEqual((pads[0].button, pads[0].stick_y), (bridge.BUTTON_B, -80), "shine")
        self.assertEqual(pads[3].button, bridge.BUTTON_X, "jump out of it")
        self.assertIn(pads[6].cstick_y, (80, -80), "into an up-air or down-air")

    def test_spikes_them_off_the_ledge(self):
        for seed in range(6):
            f = fm.Falco(seed=seed)
            s = sit(x=79, opp_x=95, opp_y=-15, opp_air=True, edge=85.566)
            pads = [f.step(s, "edgeguard") for _ in range(6)]
            if any(p.cstick_y == -80 for p in pads):
                self.assertGreater(pads[0].stick_x, 0, "runs off toward them")
                return
        self.fail("never went for the spike")

    def test_down_air_below_them(self):
        f = fm.Falco(seed=1)
        self.assertEqual(f.aerial_for(sit(opp_y=-10)).cstick_y, -80)
        self.assertEqual(f.aerial_for(sit(opp_y=20)).cstick_y, 80)


if __name__ == "__main__":
    unittest.main()
