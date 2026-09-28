"""roster.py: which agent plays which character, against fake phillip checkouts."""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import roster  # noqa: E402

ALL = [a for opts in roster.AGENTS.values() for _, a in opts]


def fake_phillip(root, agents, snapshot=True):
    for a in agents:
        d = Path(root) / "agents" / a
        d.mkdir(parents=True, exist_ok=True)
        (d / "params").write_text("{}")
        if snapshot:
            (d / "snapshot").write_bytes(b"\0")


class RosterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_are_the_delay0_agents(self):
        fake_phillip(self.root, [a for a in ALL if not a.startswith("delay18")])
        fake_phillip(self.root, ["delay18/PuffFD"], snapshot=False)  # as in the repo
        chosen, skipped = roster.choose(self.root)
        self.assertEqual(chosen, {"falcon": "FalconFalconBF", "fox": "delay0/FoxFD",
                                  "falco": "delay0/FalcoFD", "marth": "MarthFD0", "peach": "PeachFD",
                                  "sheik": "SheikFD"})
        self.assertEqual([a for a, _ in skipped], ["delay18/PuffFD"])

    def test_reaction_prefers_delayed_agents_or_the_nearest_lower(self):
        fake_phillip(self.root, ALL)
        one, _ = roster.choose(self.root, reaction=1)
        two, _ = roster.choose(self.root, reaction=2)
        self.assertEqual((one["marth"], one["peach"], one["sheik"], one["fox"]),
                         ("MarthFD1", "PeachFD1", "SheikFD1", "delay0/FoxFD"))
        self.assertEqual((two["marth"], two["peach"], two["sheik"], two["falcon"]),
                         ("MarthFD1", "PeachFD2", "SheikFD2", "FalconFalconBF"))
        # Puff's only agent is delayed 4 steps: it plays at any setting.
        self.assertEqual(one["puff"], "delay18/PuffFD")

    def test_stand_ins_follow_their_source(self):
        fake_phillip(self.root, ["FalconFalconBF", "PeachFD"])
        chosen, _ = roster.choose(self.root)
        e = roster.entries(chosen)
        ganon, roy = 0x19, 0x17
        self.assertEqual(e[ganon], ("FalconFalconBF", "falcon", "ganon"))
        self.assertNotIn(roy, e)  # no Marth agent, so no Roy either
        self.assertEqual(roster.summary(chosen), "Captain Falcon, Peach (+ Ganondorf as stand-ins)")

    def test_excluded_agents_are_never_picked(self):
        fake_phillip(self.root, ["FoxFD1", "delay12/MarthFD"])
        chosen, _ = roster.choose(self.root, reaction=2)
        self.assertEqual(chosen, {})

    def test_file_roundtrip(self):
        fake_phillip(self.root, ["MarthFD0"])
        chosen, _ = roster.choose(self.root)
        path = Path(self.root) / "roster.json"
        roster.write(path, chosen, lambda a: Path("/w") / (a.replace("/", "_") + ".npz"))
        got = roster.read(path)
        self.assertEqual(got[0x09]["weights"], str(Path("/w/MarthFD0.npz")))
        self.assertEqual(got[0x17]["stand_in_for"], "roy")
        self.assertEqual(got[0x17]["char"], "marth")


if __name__ == "__main__":
    unittest.main()
