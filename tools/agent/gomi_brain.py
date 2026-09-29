"""Gomihyu plays Mario and Fox: she calls the plays, a move library plays them.

Gomihyu (https://github.com/zurgace/gomihyu) is a small language model
(Gemma 4 E4B through Ollama) with an Archdemon persona. She is far too slow
to press buttons 60 times a second, so a planner thread asks her every half
second or so which game plan her character should follow, given the
situation; the game thread only reads her latest answer and never waits for
her. mario_moves.py and fox_moves.py turn a plan into inputs, frame by frame.

After every match she gets a summary -- the result, what each plan dealt and
took, how the stocks were lost -- plus her old lessons, and writes her new
lessons in her own voice for the next match. Every plan's results are also
tallied across matches (per opponent), and her prompt shows that
scoreboard; without Ollama a rule-based chooser picks plans weighted by it,
so she keeps improving on the numbers alone.

Her files (per machine, not in git), in tools/agent/gomi/ or $GOMI_DIR, one
folder per character she plays (mario/, fox/):
  lessons.md        what she has learned, rewritten after each match
  scoreboard.json   per-plan totals, by opponent character
  matches.jsonl     one summary per match

Her Discord bot (gomihyu) posts what she says: a file per match and per
in-match taunt, in the outbox both programs know,
~/.local/share/gomihyu/melee-outbox ($XDG_DATA_HOME; $GOMI_OUTBOX to move
it). The newest 20 files are kept, in case the bot isn't running.

Settings: GOMI_OLLAMA_URL (default http://localhost:11434/api/chat),
GOMI_MODEL (default gemma4:e4b), GOMI_PLAN_EVERY (seconds, default 0.5).
"""

import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import bridge
import fox_moves
import gomi_handbook
import mario_moves
import roster

# CKind -> the move library for each character she plays.
CHARACTERS = {mario_moves.CKIND: mario_moves, fox_moves.CKIND: fox_moves}

HERE = Path(__file__).resolve().parent
DEFAULT_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "gemma4:e4b"
MAX_LESSONS = 8
OUTBOX_KEEP = 20
STAGE_NAMES = {0x1F: "Battlefield", 0x20: "Final Destination", 0x03: "Pokemon Stadium", 0x08: "Yoshi's Story",
               0x1C: "Dream Land N64", 0x02: "Fountain of Dreams"}
SAY_EVERY_S = 30.0
WAITING_WARN_S = 120.0  # outbox files older than this at match start: is her bot running?
FAILURES_BEFORE_RULES = 3



def persona(name):
    return ("You are Gomi (Gomihyu), a self-proclaimed Archdemon destined to rule Hell. You're proud, "
            "dramatic and sure of your own genius, your schemes tend to backfire, and you hate losing -- "
            "but you learn from it, even if you'd never admit you needed to. Right now you're playing "
            f"{name} in Super Smash Bros. Melee against a human, with a controller that does exactly what "
            "you order.")


def plan_schema(moves):
    return {"type": "object",
            "properties": {"plan": {"type": "string", "enum": list(moves.PLANS)}, "say": {"type": "string"}},
            "required": ["plan", "say"]}


REFLECT_SCHEMA = {
    "type": "object",
    "properties": {"lessons": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_LESSONS},
                   "line": {"type": "string"}},
    "required": ["lessons", "line"],
}


class Ollama:
    """The /api/chat call gomihyu's bot makes, answering in JSON."""

    def __init__(self, url, model):
        self.url = url
        self.model = model

    def chat(self, system, user, schema, num_predict, temperature, timeout):
        body = {"model": self.model, "stream": False, "think": False, "format": schema, "keep_alive": "30m",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": 4096}}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            reply = json.loads(r.read())
        return json.loads(reply["message"]["content"])

    def check(self, timeout=3.0):
        """None when the model is there to answer, else why not."""
        tags = self.url.rsplit("/api/", 1)[0] + "/api/tags"
        try:
            with urllib.request.urlopen(tags, timeout=timeout) as r:
                models = {m.get("name") for m in json.loads(r.read()).get("models", [])}
        except (OSError, ValueError) as e:
            return f"Ollama isn't answering at {tags} ({e})"
        if self.model not in models and f"{self.model}:latest" not in models:
            return f"Ollama has no {self.model}: run  ollama pull {self.model}"
        return None


# ---------------------------------------------------------------- the numbers

def new_tally():
    return {"frames": 0, "dealt": 0, "taken": 0, "kos": 0, "deaths": 0}


class Scoreboard:
    """Per-plan totals across matches, by opponent and overall."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("matches", 0)
        self.data.setdefault("wins", 0)
        self.data.setdefault("by_opponent", {})
        self.data.setdefault("all", {})
        self.data.setdefault("records", {})  # opponent -> matches and wins

    def add(self, opponent, plans, won):
        self.data["matches"] += 1
        self.data["wins"] += bool(won)
        rec = self.data["records"].setdefault(opponent, {"matches": 0, "wins": 0})
        rec["matches"] += 1
        rec["wins"] += bool(won)
        for table in (self.data["all"], self.data["by_opponent"].setdefault(opponent, {})):
            for plan, t in plans.items():
                row = table.setdefault(plan, new_tally())
                for k, v in t.items():
                    row[k] += v

    def table(self, opponent):
        return self.data["by_opponent"].get(opponent) or self.data["all"]

    def score(self, plan, opponent, prior=0.0):
        """Net percent per minute in this plan, KOs worth 40; shrunk toward the
        handbook's prior until the plan has had a few minutes."""
        t = self.table(opponent).get(plan)
        if not t:
            return prior
        net = t["dealt"] - t["taken"] + 40 * (t["kos"] - t["deaths"])
        minutes = t["frames"] / 3600
        return (net + prior * 0.5) / (minutes + 0.5)

    def lines(self, opponent):
        table = self.table(opponent)
        out = []
        for plan, t in table.items():
            if t["frames"] > 0:
                out.append(f"{plan}: {t['frames'] / 3600:.1f} min, dealt {t['dealt']}%, took {t['taken']}%, "
                           f"KOs {t['kos']}, lost {t['deaths']} stocks -> {self.score(plan, opponent):+.0f}/min")
        return out

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))


def read_lessons(path):
    try:
        return [ln[2:].strip() for ln in Path(path).read_text().splitlines() if ln.startswith("- ")]
    except OSError:
        return []


def write_lessons(path, lessons):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Gomi's lessons\n\n" + "".join(f"- {ln}\n" for ln in lessons))


def where(x, y, air, edge):
    if abs(x) > edge + 2 or y < -6:
        side = "left" if x < 0 else "right"
        return f"offstage {side}" + (" below the ledge" if y < -6 else "")
    third = "left side" if x < -edge / 3 else "right side" if x > edge / 3 else "centre"
    return f"{'in the air' if air else 'on the ground'}, {third}"


def default_outbox():
    """Where her Discord bot looks for what she says (gomihyu's MELEE_OUTBOX default)."""
    if os.environ.get("GOMI_OUTBOX"):
        return Path(os.environ["GOMI_OUTBOX"]).expanduser()
    data = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data) / "gomihyu" / "melee-outbox"


# ---------------------------------------------------------------- Gomi

class GomiBrain:
    def __init__(self, log=None, data_dir=None, url=None, model=None, plan_every=None, seed=None, llm=None,
                 outbox=None):
        self.log = log or (lambda msg: print(f"gomi: {msg}", flush=True))
        self.dir = Path(data_dir or os.environ.get("GOMI_DIR") or HERE / "gomi")
        self.outbox = Path(outbox) if outbox else default_outbox()
        self.llm = llm or Ollama(url or os.environ.get("GOMI_OLLAMA_URL") or DEFAULT_URL,
                                 model or os.environ.get("GOMI_MODEL") or DEFAULT_MODEL)
        self.plan_every = float(plan_every or os.environ.get("GOMI_PLAN_EVERY") or 0.5)
        self.rng = random.Random(seed)
        self.seed = seed
        self.migrate()
        self.use(mario_moves)
        self.priors = {}
        self.lock = threading.Lock()
        self.planner = None
        self.reflecting = None
        self.stop_match = threading.Event()
        self.active = False
        self.llm_down = None   # why Ollama can't play, once known
        self.said_at = 0.0

    def migrate(self):
        """Her files from when she only played Mario go in mario/."""
        old = [self.dir / n for n in ("lessons.md", "scoreboard.json", "matches.jsonl")]
        if any(p.exists() for p in old) and not (self.dir / "mario").exists():
            (self.dir / "mario").mkdir(parents=True)
            for p in old:
                if p.exists():
                    p.replace(self.dir / "mario" / p.name)

    def use(self, moves):
        """Play this character (a move library), with its own lessons and scoreboard."""
        self.moves = moves
        self.player = moves.Player(self.seed)
        self.char_dir = self.dir / moves.NAME.lower()
        self.scoreboard = Scoreboard(self.char_dir / "scoreboard.json")

    # ---- the game thread -------------------------------------------------

    def start_match(self, st, port, opp_port):
        opp = st.fighters[opp_port]
        self.use(CHARACTERS.get(st.fighters[port].ckind, mario_moves))
        self.port, self.opp_port = port, opp_port
        self.opponent = roster.display(opp.ckind)
        self.stage = st.stage
        self.player.reset()
        self.plan = "space"
        self.plan_since = time.monotonic()
        self.tally = {p: new_tally() for p in self.moves.PLANS}
        self.recover_deaths = 0
        self.last = None
        self.last_frame = None
        self.pad = bridge.Pad.neutral()
        self.snapshot = None
        self.lessons = read_lessons(self.char_dir / "lessons.md")
        self.priors = gomi_handbook.priors(self.moves.NAME, self.opponent)
        self.system = self.match_prompt()
        self.active = True
        self.stop_match = threading.Event()
        self.planner = threading.Thread(target=self.plan_loop, args=(self.stop_match,), daemon=True)
        self.planner.start()
        self.log(f"Gomihyu plays {self.moves.NAME} vs {self.opponent} ({self.llm.model}; "
                 f"{len(self.lessons)} lessons, {self.scoreboard.data['matches']} matches played)")
        self.warn_if_waiting()

    def warn_if_waiting(self):
        """Posts nobody took: her Discord bot isn't running, or looks elsewhere."""
        now = time.time()
        waiting = [p for p in self.outbox.glob("*.json") if now - p.stat().st_mtime > WAITING_WARN_S]
        if waiting:
            self.log(f"{len(waiting)} of her Discord posts are still waiting: is her gomihyu bot running? "
                     f"(it posts from {self.outbox})")

    def pad_for(self, st, port, opp_port):
        me, opp = st.fighters[port], st.fighters[opp_port]
        s = self.moves.situation(me, opp, st.stage)
        new_frame = self.last_frame is None or st.scene_frame > self.last_frame
        if not new_frame:
            return self.pad
        self.last_frame = st.scene_frame
        with self.lock:
            plan = self.plan
        self.count(plan, me, opp)
        self.pad = self.player.step(s, plan)
        self.snapshot = (s, me.stocks, opp.stocks, plan)
        return self.pad

    def count(self, plan, me, opp):
        t = self.tally[plan]
        t["frames"] += 1
        if self.last is not None:
            my_stocks, my_pct, their_stocks, their_pct = self.last
            if me.stocks < my_stocks:
                t["deaths"] += 1
                if self.player.mode == "recover":
                    self.recover_deaths += 1
            elif me.percent > my_pct:
                t["taken"] += me.percent - my_pct
            if opp.stocks < their_stocks:
                t["kos"] += 1
            elif opp.percent > their_pct:
                t["dealt"] += opp.percent - their_pct
        self.last = (me.stocks, me.percent, opp.stocks, opp.percent)

    def end_match(self):
        """The match is over: tally it, then reflect on it in the background."""
        if not self.active:
            return
        self.active = False
        self.stop_match.set()
        if self.last is None:
            return
        my_stocks, my_pct, their_stocks, their_pct = self.last
        won = my_stocks > their_stocks or (my_stocks == their_stocks and my_pct < their_pct)
        plans = {p: t for p, t in self.tally.items() if t["frames"]}
        self.scoreboard.add(self.opponent, plans, won)
        self.scoreboard.save()
        record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "opponent": self.opponent, "stage": self.stage,
                  "won": won, "stocks": [my_stocks, their_stocks], "percent": [my_pct, their_pct],
                  "plans": plans, "recovery_deaths": self.recover_deaths}
        self.char_dir.mkdir(parents=True, exist_ok=True)
        with open(self.char_dir / "matches.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        ranked = sorted(plans, key=lambda p: self.scoreboard.score(p, self.opponent))
        best = f"; best plan so far {ranked[-1]}, worst {ranked[0]}" if len(ranked) > 1 else ""
        self.log(f"match over: {'won' if won else 'lost'} {my_stocks}-{their_stocks} stocks vs "
                 f"{self.opponent}{best}")
        if ranked:
            record["best_plan"], record["worst_plan"] = ranked[-1], ranked[0]
        self.reflecting = threading.Thread(target=self.after_match, args=(record,), daemon=True)
        self.reflecting.start()

    def after_match(self, record):
        line, lessons = self.reflect(record)
        self.post(record, line, lessons)

    def post(self, record, line, lessons):
        """The match, for her Discord bot to post about."""
        board = self.scoreboard.data
        vs = board["records"].get(self.opponent, {})
        event = {"version": 1, "kind": "match", "time": time.time(), "character": self.moves.NAME,
                 "opponent_character": self.opponent,
                 "stage": STAGE_NAMES.get(record["stage"], f"stage {record['stage']}"),
                 "won": record["won"], "stocks": record["stocks"], "percent": record["percent"],
                 "line": line, "lessons": lessons,
                 "best_plan": record.get("best_plan"), "worst_plan": record.get("worst_plan"),
                 "record": {"matches": board["matches"], "wins": board["wins"],
                            "matches_vs": vs.get("matches", 0), "wins_vs": vs.get("wins", 0)}}
        if self.send(event):
            self.log(f"sent the match to her Discord bot ({self.outbox})")

    def taunt(self, text):
        """Trash talk mid-match: her bot posts it as it is."""
        self.send({"version": 1, "kind": "taunt", "time": time.time(), "character": self.moves.NAME,
                   "opponent_character": self.opponent, "text": text})

    def send(self, event):
        """One JSON file in the outbox, written whole (temp file, then rename);
        past OUTBOX_KEEP the oldest go, in case the bot isn't running."""
        try:
            self.outbox.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000000:09d}.json"
            tmp = self.outbox / (name + ".tmp")
            tmp.write_text(json.dumps(event, indent=1))
            tmp.replace(self.outbox / name)
            for old in sorted(self.outbox.glob("*.json"))[:-OUTBOX_KEEP]:
                old.unlink(missing_ok=True)
        except OSError as e:
            self.log(f"couldn't leave that for her Discord bot in {self.outbox} ({e})")
            return False
        return True

    def close(self, timeout=120.0):
        """Let the reflection on the last match finish."""
        self.stop_match.set()
        if self.reflecting is not None:
            self.reflecting.join(timeout)

    # ---- the planner thread ---------------------------------------------

    def match_prompt(self):
        plans = "\n".join(f"- {p}: {d}" for p, d in self.moves.PLANS.items())
        lessons = "\n".join(f"- {ln}" for ln in self.lessons) or "- (none yet: this is your first match)"
        board = "\n".join(f"- {ln}" for ln in self.scoreboard.lines(self.opponent)) or "- (no numbers yet)"
        return (f"{persona(self.moves.NAME)}\n\nYou're facing {self.opponent}. Twice a second you're told "
                f"the situation and choose {self.moves.NAME}'s game plan:\n{plans}\n"
                "Recovering, teching and getting up happen by themselves.\n\n"
                f"What every {self.moves.NAME} player knows:\n"
                f"{gomi_handbook.lines(self.moves.NAME, self.opponent)}\n\n"
                f"Your lessons from earlier matches:\n{lessons}\n\n"
                f"What each plan has done against {self.opponent} so far:\n{board}\n\n"
                'Answer in JSON: {"plan": one of the plans, "say": a short in-character taunt, or "" '
                "most of the time}.")

    def situation_text(self, snap):
        s, my_stocks, their_stocks, plan = snap
        t = self.tally[plan]
        secs = time.monotonic() - self.plan_since
        return (f"You: {self.moves.NAME}, {s.percent}%, {my_stocks} stocks, {where(s.x, s.y, s.air, s.edge)}. "
                f"{self.opponent}: {s.opp_percent}%, {their_stocks} stocks, "
                f"{where(s.opp_x, s.opp_y, s.opp_air, s.edge)}, {s.dist:.0f} units "
                f"{'to your right' if s.dx > 0 else 'to your left'}"
                f"{', above you' if s.dy > 10 else ', below you' if s.dy < -10 else ''}. "
                f"Your plan: {plan} for {secs:.0f}s; with it this match you dealt {t['dealt']}% "
                f"and took {t['taken']}%.")

    def fallback(self, snap):
        """Her rules when Ollama can't answer: by the situation, weighted by the scoreboard."""
        s = snap[0]
        if s.opp_offstage and not s.offstage:
            options = ["edgeguard", "space"]
        elif s.dist > 60:
            options = [self.moves.ZONE, "approach", "space"]
        else:
            options = ["approach", "pressure", "defend", "space", self.moves.ZONE]
            if s.platforms:
                options.append("platform")
        if self.rng.random() < 0.15:
            return self.rng.choice(options)
        scores = [self.scoreboard.score(p, self.opponent, self.priors.get(p, 0.0)) for p in options]
        weights = [math.exp(max(-5.0, min(5.0, v / 15))) for v in scores]
        return self.rng.choices(options, weights)[0]

    def set_plan(self, plan):
        with self.lock:
            if plan != self.plan:
                self.plan = plan
                self.plan_since = time.monotonic()
                changed = True
            else:
                changed = False
        if changed:
            self.log(f"plan: {plan}")

    def plan_loop(self, stop):
        if self.llm_down is None or self.llm_down:
            self.llm_down = self.llm.check()
            if self.llm_down:
                self.log(f"{self.llm_down}; {self.moves.NAME} plays on Gomi's rules this match")
        failures = 0
        while not stop.is_set():
            t0 = time.monotonic()
            snap = self.snapshot
            if snap is not None:
                if self.llm_down:
                    self.set_plan(self.fallback(snap))
                else:
                    try:
                        ans = self.llm.chat(self.system, self.situation_text(snap), plan_schema(self.moves),
                                            num_predict=80, temperature=0.8, timeout=5.0)
                        failures = 0
                        if stop.is_set():
                            break
                        if ans.get("plan") in self.moves.PLANS:
                            self.set_plan(ans["plan"])
                        say = str(ans.get("say") or "").strip()
                        if say and time.monotonic() - self.said_at > SAY_EVERY_S:
                            self.said_at = time.monotonic()
                            self.log(f'Gomi: "{say[:160]}"')
                            self.taunt(say[:300])
                    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as e:
                        failures += 1
                        if failures >= FAILURES_BEFORE_RULES:
                            self.llm_down = f"Ollama stopped answering ({e})"
                            self.log(f"{self.llm_down}; {self.moves.NAME} plays on Gomi's rules for the rest "
                                     "of the match")
            stop.wait(max(0.02, self.plan_every - (time.monotonic() - t0)))

    # ---- after the match -------------------------------------------------

    def reflect(self, record):
        """Her new lessons from the match, and her line about it: (line, lessons)."""
        lessons = read_lessons(self.char_dir / "lessons.md")
        rows = "\n".join(f"- {p}: {t['frames'] / 60:.0f}s, dealt {t['dealt']}%, took {t['taken']}%, "
                         f"KOs {t['kos']}, lost {t['deaths']} stocks" for p, t in record["plans"].items())
        board = "\n".join(f"- {ln}" for ln in self.scoreboard.lines(self.opponent)) or "- (none)"
        old = "\n".join(f"- {ln}" for ln in lessons) or "- (none yet)"
        user = (f"The match against {self.opponent} is over. You {'WON' if record['won'] else 'LOST'}: "
                f"your stocks {record['stocks'][0]}, theirs {record['stocks'][1]}; "
                f"percent {record['percent'][0]}% vs {record['percent'][1]}%. "
                f"Stocks lost while recovering: {record['recovery_deaths']}.\n"
                f"Your plans this match:\n{rows or '- (none)'}\n\n"
                f"All your matches against {self.opponent} (per plan):\n{board}\n\n"
                f"Your lessons so far:\n{old}\n\n"
                f"Rewrite your lessons: at most {MAX_LESSONS}, each one short and concrete about which plan to "
                "use when (keep the old ones that still hold, drop what the numbers disprove), in your own "
                'voice. Answer in JSON: {"lessons": [...], "line": one dramatic in-character line about '
                "this match}.")
        system = f"{persona(self.moves.NAME)}\n\nYou just finished a match and are thinking it over."
        if self.llm_down:
            self.llm_down = self.llm.check()
        if self.llm_down:
            self.log("no reflection this time (Ollama isn't answering); the scoreboard still counts the match")
            return "", lessons
        try:
            ans = self.llm.chat(system, user, REFLECT_SCHEMA, num_predict=500, temperature=0.7, timeout=120.0)
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as e:
            self.log(f"couldn't reflect on the match ({e}); her lessons stay as they were")
            return "", lessons
        new = [str(ln).strip() for ln in ans.get("lessons", []) if str(ln).strip()][:MAX_LESSONS]
        if new:
            write_lessons(self.char_dir / "lessons.md", new)
        line = str(ans.get("line") or "").strip()
        if line:
            self.log(f'after the match: "{line[:200]}"')
        self.log(f"{len(new)} lessons in {self.char_dir / 'lessons.md'}")
        return line, new or lessons


def plays(ckind):
    return ckind in CHARACTERS
