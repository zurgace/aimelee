"""gomi_train.py: whose replays, which games teach, the held-out split, the step budget and the base
network. The training itself needs slippi-ai's environment (checked end to end on real replays; see
the commit that added it)."""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import gomi_train as gt  # noqa: E402

MARTH = 18                  # internal ids, as slippi-ai's parsed rows have them


def player(code, character, name=None, damage=150.0):
    return {"character": character, "type": 0, "damage_taken": damage,
            "netplay": {"code": code, "name": name or code.split("#")[0].title()}}


def row(md5, *players, **kw):
    r = {"valid": True, "is_training": True, "data_ok": True, "slp_md5": md5, "lastFrame": 7200,
         "players": list(players)}
    r.update(kw)
    return r


UNI = "ILYJ#309"


class OwnerTest(unittest.TestCase):
    def test_the_player_in_the_most_replays(self):
        rows = {"a": row("a", player(UNI, gt.FALCO, "Uni"), player("MRDO#248", MARTH)),
                "b": row("b", player(UNI, 2, "Uni"), player("YOMI#762", gt.FALCO)),
                "c": row("c", player("YOMI#762", gt.FALCO), player(UNI, 9))}
        self.assertEqual(gt.owner_of(rows), UNI)
        self.assertEqual(gt.name_of(rows, UNI), "Uni")
        self.assertIsNone(gt.owner_of({"x": row("x", {"character": 1}, {"character": 2})}), "no codes: whose?")

    def test_fullwidth_hash_in_codes(self):
        self.assertEqual(gt.code_of({"netplay": {"code": "ILYJ＃309"}}), UNI)


class GamesTest(unittest.TestCase):
    def test_only_their_falco_games_that_count(self):
        ok = row("a", player(UNI, gt.FALCO), player("MRDO#248", MARTH))
        self.assertIsNone(gt.falco_game(ok, UNI))
        self.assertEqual(gt.falco_game(row("b", player(UNI, 2), player("X#1", gt.FALCO)), UNI), "not their Falco")
        self.assertEqual(gt.falco_game(dict(ok, valid=False), UNI), "unreadable")
        self.assertEqual(gt.falco_game(dict(ok, is_training=False, not_training_reason="not 1v1"), UNI),
                         "not 1v1")
        self.assertEqual(gt.falco_game(dict(ok, data_ok=False), UNI), "failed slippi-ai's data check")
        quiet = row("c", player(UNI, gt.FALCO, damage=20.0), player("X#1", MARTH, damage=30.0))
        self.assertEqual(gt.falco_game(quiet, UNI), "hardly anything happened")

    def test_held_out_games_stay_held_out(self):
        rows = [row(f"{i:032x}") for i in range(200)]
        train, test = gt.split(rows)
        self.assertEqual(len(train) + len(test), 200)
        self.assertTrue(25 < len(test) < 60, len(test))
        more_train, more_test = gt.split(rows + [row(f"{i:032x}") for i in range(200, 300)])
        self.assertTrue({r["slp_md5"] for r in test} <= {r["slp_md5"] for r in more_test},
                        "adding games never moves a held-out one into training")

    def test_one_or_two_games(self):
        one = [row("a")]
        self.assertEqual(gt.split(one), (one, one))
        train, test = gt.split([row("a"), row("b")])
        self.assertEqual((len(train), len(test)), (1, 1))

    def test_a_few_passes(self):
        self.assertEqual(gt.steps_for([]), gt.MIN_STEPS)
        games = [row(str(i), lastFrame=10000) for i in range(11)]           # the July zip's size
        self.assertEqual(gt.steps_for(games), 323)


class BaseTest(unittest.TestCase):
    def test_which_network_to_start_from(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertEqual(gt.find_base(tmp, explicit=None), next(
            (HERE.parent / "weights" / "slippi" / n for n in gt.BASE_NAMES
             if (HERE.parent / "weights" / "slippi" / n).is_file()), None))
        mine = tmp / "falco" / "base-model"
        mine.parent.mkdir(parents=True)
        mine.write_bytes(b"x")
        self.assertEqual(gt.find_base(tmp), mine, "one she was given")
        given = tmp / "given"
        given.write_bytes(b"x")
        self.assertEqual(gt.find_base(tmp, str(given)), given, "--base first")

    def test_jsonable(self):
        class Scalar:
            def __init__(self, v):
                self.v = v

            def item(self):
                return self.v
        self.assertEqual(gt.jsonable({"a": (Scalar(1.5), [Scalar(2)]), 3: "x"}), {"a": [1.5, [2]], "3": "x"})


@unittest.skipUnless(os.environ.get("SLIPPI_BASE_MODEL") and importlib.util.find_spec("slippi_ai"),
                     "needs slippi-ai's environment and SLIPPI_BASE_MODEL (a slippi-ai network, e.g. medium-v2)")
class TrainTest(unittest.TestCase):
    """The whole of it on slippi-ai's own sample replay (Sheik vs Mewtwo, both "Diamond Player"),
    standing in for Falco: parse, train a few steps from the base network, save, and the saved model
    loads and steps in slippi_agent as the newer Phillip's models do."""

    def test_train_save_and_play(self):
        from slippi_ai import paths as slippi_paths

        import slippi_agent

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        with zipfile.ZipFile(slippi_paths.TOY_DATASET / "Raw" / "single_game.zip") as z:
            z.extractall(tmp / "replays")
        small = dict(FALCO=7, FALCO_NAME="sheik", MIN_STEPS=6, EPOCHS=0, EVAL_EVERY=3, EVAL_BATCHES=2)
        with mock.patch.multiple(gt, **small):
            p = gt.paths(tmp / "gomi")
            rows = gt.parse_new(tmp / "replays", p)
            self.assertEqual(len(rows), 1)
            owner = gt.owner_of(rows)
            self.assertEqual(owner, "Diamond Player")
            gt.train(p, rows, owner, os.environ["SLIPPI_BASE_MODEL"])
        self.assertTrue(p["model"].is_file(), "a step closer to the game it trains on: saved")
        import json
        info = json.loads(p["info"].read_text())
        self.assertLess(info["loss"], info["base_loss"])
        self.assertEqual(info["games"], 1)
        self.assertLess(slippi_agent.probe(p["model"], 60, False), 1000.0)    # loads, plays Sheik, steps


if __name__ == "__main__":
    unittest.main()
