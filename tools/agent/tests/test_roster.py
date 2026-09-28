"""roster.py: which agent plays which character on which stage, against fake phillip checkouts."""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import roster  # noqa: E402

ALL = [a for opts in roster.AGENTS.values() for _, a, _ in opts]
REPO = [a for a in ALL if not a.startswith("delay18")]  # what the phillip repo ships weights for
FD, BF = 0x20, 0x1F
YOSHIS = 0x08  # StKind of some other stage


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
        fake_phillip(self.root, REPO)
        fake_phillip(self.root, [a for a in ALL if a.startswith("delay18")], snapshot=False)  # as in the repo
        chosen, skipped = roster.choose(self.root)
        self.assertEqual(chosen, {"falcon": {"battlefield": "FalconFalconBF"},
                                  "fox": {"final_destination": "delay0/FoxFD"},
                                  "falco": {"final_destination": "delay0/FalcoFD"},
                                  "marth": {"final_destination": "MarthFD0"},
                                  "peach": {"final_destination": "PeachFD"},
                                  "sheik": {"final_destination": "SheikFD"}})
        self.assertEqual(sorted(a for a, _ in skipped), sorted(a for a in ALL if a.startswith("delay18")))

    def test_reaction_prefers_delayed_agents_or_the_nearest_lower(self):
        fake_phillip(self.root, REPO)
        one, _ = roster.choose(self.root, reaction=1)
        two, _ = roster.choose(self.root, reaction=2)
        fd = "final_destination"
        self.assertEqual((one["marth"][fd], one["peach"][fd], one["sheik"][fd], one["fox"][fd]),
                         ("MarthFD1", "PeachFD1", "SheikFD1", "delay0/FoxFD"))
        self.assertEqual((two["marth"][fd], two["peach"][fd], two["sheik"][fd]),
                         ("MarthFD1", "PeachFD2", "SheikFD2"))

    def test_stage_picks_fd_or_battlefield_agent(self):
        fake_phillip(self.root, ALL)  # with the Google Drive agents in place
        chosen, _ = roster.choose(self.root)
        falco = chosen["falco"]
        self.assertEqual(falco, {"final_destination": "delay0/FalcoFD", "battlefield": "delay18/FalcoBF"})
        self.assertEqual(roster.for_stage(falco, FD), "delay0/FalcoFD")
        self.assertEqual(roster.for_stage(falco, BF), "delay18/FalcoBF")
        self.assertEqual(roster.for_stage(falco, YOSHIS), "delay18/FalcoBF")  # not FD: the BF agent
        # With one agent only, it plays every stage.
        self.assertEqual(roster.for_stage(chosen["falcon"], FD), "FalconFalconBF")
        self.assertEqual(roster.for_stage(chosen["marth"], BF), "MarthFD0")
        self.assertEqual(chosen["puff"], {"final_destination": "delay18/PuffFD"})  # slower only: still used
        self.assertIn("Falco (FD and BF agents)", roster.summary(chosen))

    def test_stand_ins_follow_their_source(self):
        fake_phillip(self.root, ["FalconFalconBF", "PeachFD"])
        chosen, _ = roster.choose(self.root)
        e = roster.entries(chosen)
        ganon, roy = 0x19, 0x17
        self.assertEqual(e[ganon], {"battlefield": ("FalconFalconBF", "falcon", "ganon")})
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
        entry = roster.for_stage(got[0x09], BF)
        self.assertEqual(entry["weights"], str(Path("/w/MarthFD0.npz")))
        roy = roster.for_stage(got[0x17], FD)
        self.assertEqual((roy["stand_in_for"], roy["char"]), ("roy", "marth"))


if __name__ == "__main__":
    unittest.main()
