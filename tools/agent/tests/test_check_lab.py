"""check_lab.py's analysis, on a simulated Lab run: the match frame goes back on a load and on each
playback loop, and a playback that drifts from the recording is caught."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import check_lab  # noqa: E402
import harness  # noqa: E402

LOG = ("lab: Training Lab on: ...\nlab: saved state\nlab: recording P2 (...)\nlab: recorded 140 frames (2.3 s)\n"
       "lab: playing back P2's recording, 140 frames on a loop\nlab: loaded state\n")


def fighter(x, motion=14):
    return SimpleNamespace(cur_x=float(x), cur_y=0.0, motion_id=motion, action_frame=1.0, percent_f=0.0)


def run(drift_at=None):
    """A tiny deterministic match driven the way the Lab drives it: (vs frame, P1 x, P2 x) per tick."""
    states = []
    vs, p1, p2 = 0, -20.0, 20.0
    saved = rec_start = None
    rec = []
    loops = 0

    def tick(p1_vx, p2_vx, drift=0.0):
        nonlocal vs, p1, p2
        vs += 1
        p1 += p1_vx
        p2 += p2_vx + drift
        states.append(SimpleNamespace(in_fight=True, fighters_ran=True, vs_frame=vs,
                                      fighters=[fighter(p1), fighter(p2)]))
    for _ in range(10):
        tick(1, 0)
    saved = (vs, p1, p2)
    tick(0, 0)                            # P1 holds only the save key (the Lab's): a neutral pad
    for _ in range(49):
        tick(0.5, 0)
    rec_start = (vs, p1, p2)
    for i in range(140):                  # recording: P1's controller drives P2, P1 stands still
        rec.append(1.5 if i < 60 else -0.5)
        tick(0, rec[-1])
    for _ in range(60):
        tick(0, 0)
    while loops < 3:                      # playback: back to the recording's start each loop
        vs, p1, p2 = rec_start
        for i, vx in enumerate(rec):
            tick(0, vx, 0.25 if drift_at is not None and loops == 1 and i >= drift_at else 0.0)
        loops += 1
    vs, p1, p2 = rec_start
    for vx in rec[:40]:
        tick(0, vx)
    vs, p1, p2 = saved                    # load state
    for _ in range(30):
        tick(0, 0)
    return states


class AnalyzeTest(unittest.TestCase):
    def analyze(self, states):
        report = harness.Report("lab")
        check_lab.analyze(report, states, LOG)
        return {name: (ok, detail) for name, ok, detail in report.rows}

    def test_a_faithful_lab_passes(self):
        rows = self.analyze(run())
        failed = {k: v for k, v in rows.items() if v[0] is False}
        self.assertEqual(failed, {})
        self.assertEqual(rows["at least two whole playback loops"][1], "3 of 4", "the last one cut by the load")
        self.assertTrue(rows["every playback loop is the recording, field for field"][0])

    def test_a_drifting_playback_fails(self):
        rows = self.analyze(run(drift_at=70))
        ok, detail = rows["every playback loop is the recording, field for field"]
        self.assertFalse(ok)
        self.assertIn("loop 2 frame", detail)

    def test_nothing_happened(self):
        rows = self.analyze([])
        self.assertFalse(rows["loads and loops sent the match frame back"][0])


if __name__ == "__main__":
    unittest.main()
