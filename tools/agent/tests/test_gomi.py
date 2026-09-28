"""gomi_brain.py against a fake Ollama: her plans drive Mario, the game never
waits for her, failures fall back to her rules, and a match leaves lessons,
a scoreboard and a match record behind."""

import json
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

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
                                    plan_every=0.05, seed=1, **kw)

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
        self.assertEqual(json.loads((Path(self.tmp.name) / "scoreboard.json").read_text())["matches"], 1)

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
        d = Path(self.tmp.name)
        lessons = gomi_brain.read_lessons(d / "lessons.md")
        self.assertEqual(lessons, [f"lesson {i}" for i in range(8)])
        board = json.loads((d / "scoreboard.json").read_text())
        self.assertEqual(board["matches"], 1)
        self.assertEqual(board["by_opponent"]["Fox"]["fireball"]["dealt"], 50)
        record = json.loads((d / "matches.jsonl").read_text().splitlines()[-1])
        self.assertFalse(record["won"])
        self.assertEqual(record["stocks"], [3, 4])
        self.assertTrue(any('after the match: "I let you win' in ln for ln in self.logs))
        # The next match's prompt carries both.
        b2 = self.brain("http://127.0.0.1:9/api/chat")
        b2.opponent = "Fox"
        b2.lessons = gomi_brain.read_lessons(d / "lessons.md")
        prompt = b2.match_prompt()
        self.assertIn("lesson 7", prompt)
        self.assertIn("fireball: ", prompt)

    def test_scoreboard_steers_her_rules(self):
        b = self.brain("http://127.0.0.1:9/api/chat")
        b.opponent = "Fox"
        b.scoreboard.add("Fox", {"fireball": {"frames": 7200, "dealt": 200, "taken": 20, "kos": 2, "deaths": 0},
                                 "approach": {"frames": 7200, "dealt": 20, "taken": 200, "kos": 0, "deaths": 2}},
                         won=True)
        s = mm.situation(fighter(x=0), fighter(x=80, ckind=FOX), 0x20)  # far apart
        picks = [b.fallback((s, 4, 4, "space")) for _ in range(400)]
        self.assertGreater(picks.count("fireball"), 3 * picks.count("approach"))


if __name__ == "__main__":
    unittest.main()
