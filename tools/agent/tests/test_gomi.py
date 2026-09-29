"""gomi_brain.py against a fake Ollama: her plans drive Mario, the game never
waits for her, failures fall back to her rules, and a match leaves lessons,
a scoreboard and a match record behind."""

import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fox_moves  # noqa: E402
import gomi_brain  # noqa: E402
import mario_moves as mm  # noqa: E402
from test_mario_moves import fighter  # noqa: E402

FOX = 0x02


class FakeOllama:
    """/api/tags and /api/chat, answering plans or reflections as told."""

    def __init__(self, plan="fireball", say="Behold my fireballs!", delay=0.0, broken=False, lessons=None):
        self.plan, self.say, self.delay, self.broken = plan, say, delay, broken
        self.lessons = lessons if lessons is not None else ["Fireballs work on Fox."]
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.reply({"models": [{"name": "gemma4:e4b"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                time.sleep(fake.delay)
                if "lessons" in body["format"]["properties"]:
                    content = json.dumps({"lessons": fake.lessons, "line": "I let you win, mortal."})
                elif fake.broken:
                    content = "{not json"
                else:
                    content = json.dumps({"plan": fake.plan, "say": fake.say})
                self.reply({"message": {"role": "assistant", "content": content}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/chat"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def state(frame, me, opp):
    return types.SimpleNamespace(fighters=[opp, me, fighter(), fighter()], stage=0x20, scene_frame=frame)


class Game:
    """Feeds the brain one frame at a time, like the runner does."""

    def __init__(self, brain):
        self.brain = brain
        self.frame = 0
        self.me = fighter(x=-20)
        self.opp = fighter(x=40, ckind=FOX)

    def start(self):
        self.brain.start_match(state(self.frame, self.me, self.opp), 1, 0)

    def tick(self):
        self.frame += 1
        t = time.perf_counter()
        p = self.brain.pad_for(state(self.frame, self.me, self.opp), 1, 0)
        return p, time.perf_counter() - t

    def until(self, cond, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.tick()
            if cond():
                return True
            time.sleep(0.005)
        return False


class GomiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def brain(self, url, **kw):
        return gomi_brain.GomiBrain(log=self.logs.append, data_dir=self.tmp.name, url=url,
                                    plan_every=0.05, seed=1, outbox=Path(self.tmp.name) / "outbox", **kw)

    def test_her_plan_drives_mario_and_she_talks(self):
        fake = FakeOllama(plan="fireball")
        try:
            b = self.brain(fake.url)
            g = Game(b)
            g.start()
            self.assertTrue(g.until(lambda: b.plan == "fireball"), self.logs)
            self.assertTrue(g.until(lambda: any(ln.startswith('Gomi: "Behold') for ln in self.logs)))
            system = fake.requests[0]["messages"][0]["content"]
            self.assertIn("Fox", system)
            self.assertIn("edgeguard", system)
            self.assertIn("Fox:", fake.requests[0]["messages"][1]["content"])
            b.end_match()
            b.close()
        finally:
            fake.close()

    def test_game_never_waits_for_her(self):
        fake = FakeOllama(delay=0.5)
        try:
            b = self.brain(fake.url)
            g = Game(b)
            g.start()
            slowest = max(g.tick()[1] for _ in range(200))
            self.assertLess(slowest, 0.01)
            b.end_match()
            b.close()  # the reflection writes into the temporary folder
        finally:
            fake.close()

    def test_bad_answers_then_rules(self):
        fake = FakeOllama(broken=True)
        try:
            b = self.brain(fake.url)
            g = Game(b)
            g.start()
            self.assertTrue(g.until(lambda: any("stopped answering" in ln for ln in self.logs)), self.logs)
            b.end_match()
            b.close()
        finally:
            fake.close()

    def test_no_ollama_plays_on_rules(self):
        b = self.brain("http://127.0.0.1:9/api/chat")
        g = Game(b)
        g.start()
        self.assertTrue(g.until(lambda: any("isn't answering" in ln for ln in self.logs)))
        self.assertTrue(g.until(lambda: any(ln.startswith("plan:") for ln in self.logs)), self.logs)
        b.end_match()
        b.close()
        self.assertTrue(any("no reflection" in ln for ln in self.logs))
        self.assertEqual(json.loads((Path(self.tmp.name) / "mario" / "scoreboard.json").read_text())["matches"], 1)

    def test_a_match_leaves_lessons_scoreboard_and_record(self):
        fake = FakeOllama(plan="fireball", lessons=[f"lesson {i}" for i in range(12)])
        try:
            b = self.brain(fake.url)
            g = Game(b)
            g.start()
            g.until(lambda: b.plan == "fireball")
            for pct in range(10, 60, 10):  # fireballs land
                g.opp.percent = pct
                g.tick()
            g.me.stocks = 3  # and Mario loses a stock
            g.tick()
            b.end_match()
            b.close()
        finally:
            fake.close()
        d = Path(self.tmp.name) / "mario"
        lessons = gomi_brain.read_lessons(d / "lessons.md")
        self.assertEqual(lessons, [f"lesson {i}" for i in range(8)])
        board = json.loads((d / "scoreboard.json").read_text())
        self.assertEqual(board["matches"], 1)
        self.assertEqual(board["by_opponent"]["Fox"]["fireball"]["dealt"], 50)
        record = json.loads((d / "matches.jsonl").read_text().splitlines()[-1])
        self.assertFalse(record["won"])
        self.assertEqual(record["stocks"], [3, 4])
        self.assertTrue(any('after the match: "I let you win' in ln for ln in self.logs))
        # And a file for her Discord bot, written after the reflection.
        files = sorted((d.parent / "outbox").iterdir())
        self.assertEqual({p.suffix for p in files}, {".json"}, "no temp file left")
        events = [json.loads(p.read_text()) for p in files]
        self.assertEqual([e["kind"] for e in events].count("match"), 1)
        event = next(e for e in events if e["kind"] == "match")
        self.assertEqual((event["opponent_character"], event["stage"], event["won"], event["stocks"]),
                         ("Fox", "Final Destination", False, [3, 4]))
        self.assertEqual(event["line"], "I let you win, mortal.")
        self.assertEqual(event["character"], "Mario")
        self.assertEqual(event["lessons"], [f"lesson {i}" for i in range(8)])
        self.assertEqual(event["record"], {"matches": 1, "wins": 0, "matches_vs": 1, "wins_vs": 0})
        # The next match's prompt carries both.
        b2 = self.brain("http://127.0.0.1:9/api/chat")
        b2.opponent = "Fox"
        b2.lessons = gomi_brain.read_lessons(d / "lessons.md")
        prompt = b2.match_prompt()
        self.assertIn("lesson 7", prompt)
        self.assertIn("fireball: ", prompt)

    def test_she_plays_fox_too(self):
        fake = FakeOllama(plan="lasers")
        try:
            b = self.brain(fake.url)
            g = Game(b)
            g.me = fighter(x=-20, ckind=0x02)
            g.opp = fighter(x=40, ckind=0x00)
            g.start()
            self.assertTrue(g.until(lambda: b.plan == "lasers"), self.logs)
            self.assertIn("Gomihyu plays Fox vs Captain Falcon", self.logs[0])
            system = fake.requests[0]["messages"][0]["content"]
            self.assertIn("lasers", system)
            self.assertIn("playing Fox", system)
            self.assertEqual(fake.requests[0]["format"]["properties"]["plan"]["enum"], list(fox_moves.PLANS))
            b.end_match()
            b.close()
        finally:
            fake.close()
        d = Path(self.tmp.name)
        self.assertTrue((d / "fox" / "lessons.md").exists())
        self.assertFalse((d / "mario").exists(), "Mario's lessons and scoreboard are his own")
        events = [json.loads(p.read_text()) for p in (d / "outbox").iterdir()]
        self.assertEqual({e["character"] for e in events}, {"Fox"})
        self.assertTrue(any(e["kind"] == "match" for e in events))

    def test_old_files_move_to_mario(self):
        d = Path(self.tmp.name)
        (d / "lessons.md").write_text("# Gomi's lessons\n\n- fireballs rule\n")
        (d / "scoreboard.json").write_text('{"matches": 3, "wins": 1}')
        b = self.brain("http://127.0.0.1:9/api/chat")
        self.assertEqual(gomi_brain.read_lessons(d / "mario" / "lessons.md"), ["fireballs rule"])
        self.assertEqual(b.scoreboard.data["matches"], 3)
        self.assertFalse((d / "lessons.md").exists())

    def test_taunts_go_to_discord_too(self):
        fake = FakeOllama(plan="approach", say="Kneel, fox-peasant!")
        try:
            b = self.brain(fake.url)
            g = Game(b)
            g.start()
            self.assertTrue(g.until(lambda: any("Kneel" in ln for ln in self.logs)))
            g.until(lambda: len(fake.requests) > 10, timeout=2)  # more answers, all within 30 s
            b.end_match()
            b.close()
        finally:
            fake.close()
        taunts = [json.loads(p.read_text()) for p in (Path(self.tmp.name) / "outbox").glob("*.json")]
        taunts = [e for e in taunts if e["kind"] == "taunt"]
        self.assertEqual(len(taunts), 1, "at most one every 30 s")
        self.assertEqual((taunts[0]["text"], taunts[0]["opponent_character"]), ("Kneel, fox-peasant!", "Fox"))
        self.assertTrue(any(ln.startswith("sent the match to her Discord bot") for ln in self.logs))

    def test_default_outbox_and_waiting_warning(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/x/data"}, clear=False):
            os.environ.pop("GOMI_OUTBOX", None)
            self.assertEqual(gomi_brain.default_outbox(), Path("/x/data/gomihyu/melee-outbox"))
            os.environ["GOMI_OUTBOX"] = "/y/out"
            self.assertEqual(gomi_brain.default_outbox(), Path("/y/out"))
            del os.environ["GOMI_OUTBOX"]
        b = self.brain("http://127.0.0.1:9/api/chat")
        old = Path(self.tmp.name) / "outbox" / "old.json"
        old.parent.mkdir()
        old.write_text("{}")
        os.utime(old, (time.time() - 600, time.time() - 600))
        g = Game(b)
        g.start()
        b.end_match()
        b.close()
        self.assertTrue(any("1 of her Discord posts are still waiting" in ln for ln in self.logs), self.logs)

    def test_outbox_keeps_the_newest(self):
        b = self.brain("http://127.0.0.1:9/api/chat")
        b.opponent = "Fox"
        record = {"stage": 0x1F, "won": True, "stocks": [2, 0], "percent": [40, 0]}
        for _ in range(gomi_brain.OUTBOX_KEEP + 5):
            b.post(record, "", [])
        posts = list((Path(self.tmp.name) / "outbox").glob("*.json"))
        self.assertEqual(len(posts), gomi_brain.OUTBOX_KEEP)
        self.assertEqual(json.loads(posts[0].read_text())["stage"], "Battlefield")

    def test_scoreboard_steers_her_rules(self):
        b = self.brain("http://127.0.0.1:9/api/chat")
        b.opponent = "Fox"
        b.scoreboard.add("Fox", {"fireball": {"frames": 7200, "dealt": 200, "taken": 20, "kos": 2, "deaths": 0},
                                 "approach": {"frames": 7200, "dealt": 20, "taken": 200, "kos": 0, "deaths": 2}},
                         won=True)
        s = mm.situation(fighter(x=0), fighter(x=80, ckind=FOX), 0x20)  # far apart
        picks = [b.fallback((s, 4, 4, "space")) for _ in range(400)]
        self.assertGreater(picks.count("fireball"), 3 * picks.count("approach"))

    def test_handbook(self):
        import gomi_handbook as hb
        for moves in gomi_brain.CHARACTERS.values():
            self.assertTrue(hb.BASICS[moves.NAME])
            for opp in hb.OPPONENTS:
                self.assertEqual(set(hb.priors(moves.NAME, opp)), set(moves.PLANS), (moves.NAME, opp))
        self.assertGreater(hb.priors("Mario", "Captain Falcon")["edgeguard"],
                           hb.priors("Mario", "Pikachu")["edgeguard"])
        self.assertLess(hb.priors("Fox", "Falco")["lasers"], hb.priors("Fox", "Ganondorf")["lasers"])
        b = self.brain("http://127.0.0.1:9/api/chat")
        g = Game(b)
        g.opp = fighter(x=40, ckind=0x00)
        g.start()
        b.close()
        b.stop_match.set()
        self.assertIn("What every Mario player knows", b.system)
        self.assertIn("Captain Falcon: fast and hits hard", b.system)

    def test_priors_start_her_rules_and_fade(self):
        b = self.brain("http://127.0.0.1:9/api/chat")
        b.opponent = "Captain Falcon"
        b.priors = {"edgeguard": 40.0, "space": -40.0}
        s = mm.situation(fighter(x=0), fighter(x=120, y=-20, air=True, ckind=0x00), 0x20)  # he's offstage
        picks = [b.fallback((s, 4, 4, "space")) for _ in range(300)]
        self.assertGreater(picks.count("edgeguard"), 3 * picks.count("space"), "no numbers yet: the handbook")
        self.assertEqual(b.scoreboard.score("edgeguard", "Captain Falcon", 12.0), 12.0)
        b.scoreboard.add("Captain Falcon", {"edgeguard": {"frames": 36000, "dealt": 0, "taken": 300, "kos": 0,
                                                          "deaths": 3}}, won=False)
        self.assertLess(b.scoreboard.score("edgeguard", "Captain Falcon", 40.0), 0, "ten minutes of numbers win")

    def test_she_copies_a_human_mid_match(self):
        import gomi_reads
        b = self.brain("http://127.0.0.1:9/api/chat")
        g = Game(b)
        g.start()
        self.assertEqual(b.player.skills, {})
        dash = [mm.KNEE_BEND] * 3 + [mm.JUMPING[0], mm.AIRDODGE] + [mm.LANDING_SPECIAL] * 5 + [0x0E]
        for _ in range(2):
            for motion in dash:
                g.opp = fighter(x=40, ckind=FOX, motion=motion, air=motion in (0x19, 0xEC))
                g.tick()
        self.assertIn("wavedash", b.player.skills, "copied mid-match")
        posts = [json.loads(p.read_text()) for p in (Path(self.tmp.name) / "outbox").glob("*.json")]
        self.assertIn(gomi_reads.COPY_LINES["wavedash"], [p.get("text") for p in posts])
        self.assertTrue(any("she copies you" in ln for ln in self.logs))
        b.end_match()
        b.close()
        rival = json.loads((Path(self.tmp.name) / "rival.json").read_text())
        self.assertEqual(rival["techs"], {"wavedash": 2})
        record = json.loads((Path(self.tmp.name) / "mario" / "matches.jsonl").read_text())
        self.assertEqual(record["learned"], ["wavedash"])
        # Her next match, as Fox: she still knows it, and her prompt says so.
        g = Game(b)
        g.me = fighter(x=-20, ckind=FOX)
        g.start()
        b.close()
        b.stop_match.set()
        self.assertIn("wavedash", b.player.skills)
        self.assertIn("techniques you copied from them: wavedash", b.system)

    def test_cpus_teach_her_nothing(self):
        import dataclasses
        b = self.brain("http://127.0.0.1:9/api/chat")
        g = Game(b)
        g.opp = dataclasses.replace(fighter(x=40, ckind=FOX), slot_type=1)
        g.start()
        b.close()
        b.stop_match.set()
        self.assertIsNone(b.reader)


if __name__ == "__main__":
    unittest.main()
