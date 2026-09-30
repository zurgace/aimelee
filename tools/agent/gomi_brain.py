"""Gomihyu plays Mario, Fox and Falco: she calls the plays, a move library plays them.

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

What to do each time she's free to act -- grab, a short-hop aerial, a
fireball, backing off, waiting... -- is chosen by trial and error
(gomi_options.py): within her plan, she tries options and keeps score of what
each one trades in each situation.

Her Falco also learns from Slippi replays (gomi_replays.py): every Falco
player in them teaches her -- their techniques, and what worked for them in
each situation, which she tries first. $GOMI_REPLAYS names the folder (or
.zip); new replays in it are read each time she starts.

She also reads the human (gomi_reads.py): their habits, which the move
library then punishes, and their techniques, which she copies once she has
seen them twice -- mid-match, announcing it in Discord.

Her files (per machine, not in git), in tools/agent/gomi/ or $GOMI_DIR, one
folder per character she plays (mario/, fox/):
  lessons.md        what she has learned, rewritten after each match
  scoreboard.json   per-plan totals, by opponent character
  matches.jsonl     one summary per match, with its review (gomi_review.py)
  drill.json        what she practises next match, in her words, and its baseline
  options.json      what each option has traded, per situation and opponent
  teacher.json      (falco/) what the replays taught her
  clips.json        (falco/) her teachers' inputs, in clips, by situation (gomi_clips.py)
and rival.json, what she has noticed about the human (for all her characters).

Her Discord bot (gomihyu) posts what she says: a file per match and per
in-match taunt, in the outbox both programs know,
~/.local/share/gomihyu/melee-outbox ($XDG_DATA_HOME; $GOMI_OUTBOX to move
it). It holds them and posts once per session: when the game closes (a
"session_end" file, from end_session) or when the player tells her "GG".
The newest 200 files are kept, in case the bot isn't running.

Settings: GOMI_OLLAMA_URL (default http://localhost:11434/api/chat),
GOMI_MODEL (default gemma4:e4b), GOMI_PLAN_EVERY (seconds, default 0.5),
GOMI_NUM_CTX (default 8192: keep it equal to her bot's OLLAMA_NUM_CTX, or
Ollama reloads the model each time the two take turns).
"""

import json
import math
import os
import random
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import bridge
import falco_moves
import fox_moves
import gomi_clips
import gomi_handbook
import gomi_options
import gomi_reads
import gomi_replays
import gomi_review
import gomi_timing
import mario_moves
import roster

# CKind -> the move library for each character she plays.
CHARACTERS = {mario_moves.CKIND: mario_moves, fox_moves.CKIND: fox_moves, falco_moves.CKIND: falco_moves}

HERE = Path(__file__).resolve().parent
DEFAULT_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_NUM_CTX = 8192  # gomihyu's bot's OLLAMA_NUM_CTX default: the same model, the same context
MAX_LESSONS = 8
OUTBOX_KEEP = 200       # her bot holds a session's matches until it's over: room for a long one
STAGE_NAMES = {0x1F: "Battlefield", 0x20: "Final Destination", 0x03: "Pokemon Stadium", 0x08: "Yoshi's Story",
               0x1C: "Dream Land N64", 0x02: "Fountain of Dreams"}
SAY_EVERY_S = 30.0
WAITING_WARN_S = 120.0  # a taunt or session end older than this at match start: is her bot running?
HELD_WARN_S = 40 * 60   # a match: her bot holds those for the session, but not this long
FAILURES_BEFORE_RULES = 3
COPY_SHARE = 0.6        # of her choices in neutral, a teacher's clip (when replays taught her the character)



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


def reflect_schema(drills):
    return {"type": "object",
            "properties": {"lessons": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_LESSONS},
                           "line": {"type": "string"}, "review": {"type": "string"},
                           "drill": {"type": "string", "enum": drills}, "goal": {"type": "string"}},
            "required": ["lessons", "line", "review", "drill", "goal"]}


class Ollama:
    """The /api/chat call gomihyu's bot makes, answering in JSON."""

    def __init__(self, url, model, num_ctx=None):
        self.url = url
        self.model = model
        self.num_ctx = int(num_ctx or os.environ.get("GOMI_NUM_CTX") or DEFAULT_NUM_CTX)

    def chat(self, system, user, schema, num_predict, temperature, timeout):
        body = {"model": self.model, "stream": False, "think": False, "format": schema, "keep_alive": "30m",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": self.num_ctx}}
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
        self.rival = gomi_reads.Rival(self.dir / "rival.json")
        self.reader = None
        self.drill = None
        self.review = None
        self.options = None
        self.use(mario_moves)
        self.priors = {}
        self.lock = threading.Lock()
        self.planner = None
        self.reflecting = None
        self.stop_match = threading.Event()
        self.active = False
        self.llm_down = None   # why Ollama can't play, once known
        self.said_at = 0.0
        self.session_matches = 0   # matches ended since the last session_end
        self.session_lock = threading.Lock()
        self.learning = None       # the thread reading new replays
        replays = os.environ.get("GOMI_REPLAYS", "").strip()
        if replays:
            self.learning = threading.Thread(target=self.learn_replays, args=(replays,), daemon=True)
            self.learning.start()

    def learn_replays(self, source):
        """New replays in `source`: what the Falco players in them do (gomi_replays)."""
        try:
            gomi_replays.learn(source, self.dir, log=self.log)
        except Exception as e:  # a background thread: say so, don't vanish
            self.log(f"couldn't read the replays in {source} ({type(e).__name__}: {e})")

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
        self.player.reads = self.rival.reads()
        self.char_dir = self.dir / moves.NAME.lower()
        self.scoreboard = Scoreboard(self.char_dir / "scoreboard.json")
        self.teacher = gomi_replays.Teacher(self.char_dir / "teacher.json")   # {} unless replays taught her
        self.clips = gomi_clips.Clips(self.char_dir / "clips.json", self.seed)  # and their clips
        if self.clips:
            self.player.clips, self.player.copy_share = self.clips, COPY_SHARE
        self.player.timing = gomi_timing.Timing(self.char_dir / "timing.json")  # her eaten inputs, by state
        self.drill = self.load_drill()
        self.apply_skills()

    def load_drill(self):
        """What she chose to practise after her last match as this character, or None."""
        try:
            drill = json.loads((self.char_dir / "drill.json").read_text())
        except (OSError, ValueError):
            return None
        names = gomi_review.drill_names({**self.rival.skills(self.moves), **self.teacher.skills(self.moves)})
        return drill if isinstance(drill, dict) and drill.get("drill") in names else None

    def apply_skills(self):
        """The techniques she has copied; the one she's practising, nearly always."""
        skills = self.rival.skills(self.moves)
        for tech, rate in self.teacher.skills(self.moves).items():   # what her teachers use
            skills[tech] = max(rate, skills.get(tech, 0.0))
        if self.drill and self.drill["drill"].startswith("tech:"):
            tech = self.drill["drill"].split(":", 1)[1]
            if tech in skills:
                skills[tech] = gomi_review.TECH_DRILL_RATE
        self.player.skills = skills

    # ---- the game thread -------------------------------------------------

    def start_match(self, st, port, opp_port):
        opp = st.fighters[opp_port]
        self.use(CHARACTERS.get(st.fighters[port].ckind, mario_moves))
        self.port, self.opp_port = port, opp_port
        self.opponent = roster.display(opp.ckind)
        self.stage = st.stage
        self.player.reset()
        self.reader = gomi_reads.Reader(self.rival) if opp.slot_type == 0 else None  # humans only
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
        self.review = gomi_review.MatchLog()
        option_priors = self.option_priors()
        if self.drill:
            info = gomi_review.drill_info(self.drill["drill"])
            self.player.knobs.update(info["knobs"])
            for plan, bias in info["bias"].items():
                self.priors[plan] = self.priors.get(plan, 0.0) + bias
            for option, bias in info["options"].items():
                option_priors[option] = option_priors.get(option, 0.0) + bias
            self.log(f'practising this match: "{self.drill["goal"]}" ({self.drill["drill"]}: {info["does"]})')
        self.options = gomi_options.Bandit(self.char_dir / "options.json", self.opponent,
                                           random.Random(self.seed), option_priors,
                                           head_start=self.rival.options(),  # what works for the human
                                           teacher=self.teacher.options())   # and for the players in replays
        self.player.chooser = self.options.choose
        self.system = self.match_prompt()
        self.active = True
        self.stop_match = threading.Event()
        self.planner = threading.Thread(target=self.plan_loop, args=(self.stop_match,), daemon=True)
        self.planner.start()
        self.log(f"Gomihyu plays {self.moves.NAME} vs {self.opponent} ({self.llm.model}; "
                 f"{len(self.lessons)} lessons, {self.scoreboard.data['matches']} matches played)")
        self.warn_if_waiting()

    def copy_line(self):
        """The review's line on the clips she played from her teachers, or None."""
        n, mean = self.options.this_match("copy")
        if not self.player.clips or not n:
            return None
        who = self.clips.name(self.clips.owner())
        times = "Once" if n == 1 else f"{n} times"
        return (f"{times} you played a move copied straight from {who}'s replays (or another teacher's); "
                f"those traded {mean:+.0f}% a try")

    def option_priors(self):
        """Starting guesses for her options from what she has read of the human."""
        priors = {}
        if self.moves.CKIND in gomi_reads.SHINERS:
            priors["shine"] = 4.0         # frame 1, their bread and butter
        habit = self.player.read("defense")
        if habit == "shield":
            priors["grab"] = 4.0          # they shield when she comes in: grabs beat shields
        elif habit == "jump":
            priors["smash"] = 3.0         # they jump when she comes in: up-smash them
        return priors

    def warn_if_waiting(self):
        """Posts nobody took: her Discord bot isn't running, or looks elsewhere. It takes taunts and
        session ends at once; matches it holds until the session is over, so only old ones count."""
        now = time.time()
        waiting = []
        for p in self.outbox.glob("*.json"):
            age = now - p.stat().st_mtime
            if age > HELD_WARN_S:
                waiting.append(p)
            elif age > WAITING_WARN_S:
                try:
                    kind = json.loads(p.read_text()).get("kind")
                except (OSError, ValueError, AttributeError):
                    kind = None
                if kind != "match":
                    waiting.append(p)
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
        if self.reader is not None:
            self.read_them(opp, me, s.dist, s.edge)
        with self.lock:
            plan = self.plan
        self.count(plan, me, opp)
        self.pad = self.player.step(s, plan)
        mode = self.player.mode
        self.review.frame(me, opp, plan if mode == "plan" else mode, s.offstage, s.opp_offstage)
        self.snapshot = (s, me.stocks, opp.stocks, plan)
        return self.pad

    def read_them(self, opp, me, dist, edge):
        """Watch the human this frame; copy what she has now seen enough of."""
        learned = self.reader.watch(opp, me, dist, edge)
        if self.reader.t % 60 == 0:
            self.player.reads = self.rival.reads()
        for tech in learned:
            self.apply_skills()
            if tech not in self.player.skills:
                continue  # multishine, when she's Mario
            self.log(f"she copies you: {mario_moves.TECHS[tech]}")
            self.said_at = time.monotonic()
            self.taunt(gomi_reads.COPY_LINES[tech])

    def count(self, plan, me, opp):
        t = self.tally[plan]
        t["frames"] += 1
        if self.last is not None:
            my_stocks, my_pct, their_stocks, their_pct = self.last
            died = me.stocks < my_stocks
            ko = opp.stocks < their_stocks
            taken = me.percent - my_pct if not died and me.percent > my_pct else 0
            dealt = opp.percent - their_pct if not ko and opp.percent > their_pct else 0
            t["deaths"] += died
            t["kos"] += ko
            t["taken"] += taken
            t["dealt"] += dealt
            if died and self.player.mode == "recover":
                self.recover_deaths += 1
            if self.options is not None:
                self.options.frame(dealt, taken, int(ko), int(died))
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
        self.rival.save()
        learned = self.reader.learned if self.reader is not None else []
        self.options.finish()
        self.options.save()
        variety, favourites = self.options.variety()
        timing = self.player.timing
        timing.save()
        eaten = timing.summary()
        if eaten:
            self.log(f"inputs eaten: {eaten}")
        copied = self.copy_line()
        metrics = self.review.metrics(self.player.used)
        metrics["eaten_share"] = round(timing.share(), 2)
        record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "opponent": self.opponent, "stage": self.stage,
                  "won": won, "stocks": [my_stocks, their_stocks], "percent": [my_pct, their_pct],
                  "plans": plans, "recovery_deaths": self.recover_deaths, "learned": learned,
                  "review": self.review.summary() + [ln for ln in (eaten, copied) if ln], "metrics": metrics,
                  "options": {"different": variety, "most": favourites}}
        if self.drill:
            result = gomi_review.judge(self.drill["drill"], self.drill["goal"], self.drill.get("baseline", 0), metrics)
            record["drill_result"] = result
            self.log(f"her practice: {result['text']}")
        ranked = sorted(plans, key=lambda p: self.scoreboard.score(p, self.opponent))
        best = f"; best plan so far {ranked[-1]}, worst {ranked[0]}" if len(ranked) > 1 else ""
        self.log(f"match over: {'won' if won else 'lost'} {my_stocks}-{their_stocks} stocks vs "
                 f"{self.opponent}{best}")
        if ranked:
            record["best_plan"], record["worst_plan"] = ranked[-1], ranked[0]
        # What reflecting needs, frozen here on the game thread: the next match may start meanwhile
        # (as the other character), and its Reader writes to the rival while she's still thinking.
        ctx = types.SimpleNamespace(moves=self.moves, char_dir=self.char_dir, opponent=self.opponent,
                                    scoreboard=self.scoreboard, skills=dict(self.rival.skills(self.moves)),
                                    noticed=self.rival.lines(), options=self.options.lines())
        self.session_matches += 1
        self.reflecting = threading.Thread(target=self.after_match, args=(record, ctx), daemon=True)
        self.reflecting.start()

    def after_match(self, record, ctx):
        try:
            self.think_it_over(record, ctx)
        except Exception as e:  # a background thread: say so, don't vanish
            self.log(f"couldn't finish thinking about the match ({type(e).__name__}: {e})")

    def think_it_over(self, record, ctx):
        thoughts = self.reflect(record, ctx)
        record.update(review_in_her_words=thoughts["review"], next_drill=thoughts["drill"], goal=thoughts["goal"])
        ctx.char_dir.mkdir(parents=True, exist_ok=True)
        with open(ctx.char_dir / "matches.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        self.save_drill(ctx, thoughts, record["metrics"])
        self.post(record, thoughts, ctx)

    def save_drill(self, ctx, thoughts, metrics):
        """Next match's practice, with this match's number to beat."""
        info = gomi_review.drill_info(thoughts["drill"])
        drill = {"drill": thoughts["drill"], "goal": thoughts["goal"], "baseline": metrics.get(info["metric"], 0),
                 "set": time.strftime("%Y-%m-%d %H:%M:%S")}
        (ctx.char_dir / "drill.json").write_text(json.dumps(drill, indent=1))
        self.log(f'next match she works on: "{thoughts["goal"]}" ({thoughts["drill"]}: {info["does"]})')

    def post(self, record, thoughts, ctx=None):
        """The match, for her Discord bot to post about."""
        ctx = ctx or self
        board = ctx.scoreboard.data
        vs = board["records"].get(ctx.opponent, {})
        event = {"version": 1, "kind": "match", "time": time.time(), "character": ctx.moves.NAME,
                 "opponent_character": ctx.opponent,
                 "stage": STAGE_NAMES.get(record["stage"], f"stage {record['stage']}"),
                 "won": record["won"], "stocks": record["stocks"], "percent": record["percent"],
                 "line": thoughts.get("line", ""), "lessons": thoughts.get("lessons", []),
                 "review": thoughts.get("review", ""), "notes": record.get("review", [])[:4],
                 "goal": thoughts.get("goal", ""), "drill": thoughts.get("drill"),
                 "drill_result": record.get("drill_result"),
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
        """Let the reflection on the last match finish, and end the session."""
        self.end_session(timeout)

    def end_session(self, timeout=120.0):
        """The game is closing: once her last match is thought over (and its file written), tell her
        bot the session is over, so it posts about the whole session now. Once per session, and only
        if a match ended in it."""
        self.stop_match.set()
        if self.reflecting is not None:
            self.reflecting.join(timeout)
        with self.session_lock:
            if not self.session_matches:
                return
            n, self.session_matches = self.session_matches, 0
        if self.send({"version": 1, "kind": "session_end", "time": time.time(), "matches": n}):
            self.log(f"session over: {n} match{'es' if n != 1 else ''}; her Discord bot posts about it now")

    # ---- the planner thread ---------------------------------------------

    def match_prompt(self):
        plans = "\n".join(f"- {p}: {d}" for p, d in self.moves.PLANS.items())
        lessons = "\n".join(f"- {ln}" for ln in self.lessons) or "- (none yet: this is your first match)"
        board = "\n".join(f"- {ln}" for ln in self.scoreboard.lines(self.opponent)) or "- (no numbers yet)"
        rival = "\n".join(f"- {ln}" for ln in self.rival.lines()) or "- (nothing yet)"
        taught = "".join(f"- {ln}\n" for ln in self.teacher.lines() + self.clips.lines())
        taught = f"What the replays taught you:\n{taught}\n" if taught else ""
        options = "\n".join(f"- {ln}" for ln in self.options.lines()) if self.options else ""
        options = options or "- (nothing yet: you'll try everything)"
        practice = ""
        if self.drill:
            does = gomi_review.drill_info(self.drill["drill"])["does"]
            practice = (f'This match you\'re practising: "{self.drill["goal"]}". Your moves already {does}; '
                        "lean on the plans that help with it.\n\n")
        return (f"{persona(self.moves.NAME)}\n\nYou're facing {self.opponent}. Twice a second you're told "
                f"the situation and choose {self.moves.NAME}'s game plan:\n{plans}\n"
                "Recovering, teching and getting up happen by themselves.\n\n"
                f"What every {self.moves.NAME} player knows:\n"
                f"{gomi_handbook.lines(self.moves.NAME, self.opponent)}\n\n"
                f"{practice}{taught}Your lessons from earlier matches:\n{lessons}\n\n"
                f"What each plan has done against {self.opponent} so far:\n{board}\n\n"
                "What you've noticed about this human (your moves already punish their habits):\n"
                f"{rival}\n\n"
                "Within your plan, your moves try options and learn what each one trades (per try, % dealt "
                f"minus % taken):\n{options}\n\n"
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

    def reflect(self, record, ctx):
        """Her new lessons, her review, and what she'll practise next: a dict of
        line, lessons, review, drill, goal."""
        lessons = read_lessons(ctx.char_dir / "lessons.md")
        drills = gomi_review.drill_names(ctx.skills)
        fallback = gomi_review.pick_drill(record["metrics"], ctx.skills)
        thoughts = {"line": "", "lessons": lessons, "review": "", "drill": fallback,
                    "goal": gomi_review.drill_info(fallback)["goal"]}
        rows = "\n".join(f"- {p}: {t['frames'] / 60:.0f}s, dealt {t['dealt']}%, took {t['taken']}%, "
                         f"KOs {t['kos']}, lost {t['deaths']} stocks" for p, t in record["plans"].items())
        board = "\n".join(f"- {ln}" for ln in ctx.scoreboard.lines(ctx.opponent)) or "- (none)"
        old = "\n".join(f"- {ln}" for ln in lessons) or "- (none yet)"
        review = "\n".join(f"- {ln}" for ln in record["review"]) or "- (nothing happened)"
        variety = record.get("options", {})
        if variety.get("different"):
            most = ", ".join(f"{gomi_options.NAMES.get(o, o)} {n}x" for o, n in variety.get("most", []))
            review += f"\n- You tried {variety['different']} different options; most: {most}."
        tried = "\n".join(f"- {ln}" for ln in ctx.options) or "- (not enough tries yet)"
        copied = f"You copied from them this match: {', '.join(record['learned'])}.\n" if record.get("learned") else ""
        noticed = "; ".join(ctx.noticed) or "nothing yet"
        practice = (f"This match you were practising {record['drill_result']['text']}.\n"
                    if record.get("drill_result") else "")
        menu = "\n".join(f"- {d}: {gomi_review.drill_info(d)['does']}" for d in drills)
        user = (f"The match against {ctx.opponent} is over. You {'WON' if record['won'] else 'LOST'}: "
                f"your stocks {record['stocks'][0]}, theirs {record['stocks'][1]}; "
                f"percent {record['percent'][0]}% vs {record['percent'][1]}%.\n{practice}{copied}"
                f"What happened:\n{review}\n"
                f"What you've noticed about them: {noticed}.\n\n"
                f"What your options trade (per try):\n{tried}\n\n"
                f"Your plans this match:\n{rows or '- (none)'}\n\n"
                f"All your matches against {ctx.opponent} (per plan):\n{board}\n\n"
                f"Your lessons so far:\n{old}\n\n"
                f"Drills you can practise next match:\n{menu}\n\n"
                f"Rewrite your lessons: at most {MAX_LESSONS}, each one short and concrete about which plan to "
                "use when (keep the old ones that still hold, drop what the numbers disprove), in your own "
                "voice. Then pick the drill that fixes your biggest weakness this match (keep the same one if "
                'it didn\'t pay off yet). Answer in JSON: {"lessons": [...], "line": one dramatic in-character '
                'line about this match, "review": one or two sentences on what went well and what went badly, '
                'naming the moves and situations, "drill": one of the drills, "goal": what you will work on '
                "next match, one sentence in your own words}.")
        system = f"{persona(ctx.moves.NAME)}\n\nYou just finished a match and are thinking it over."
        if self.llm_down:
            self.llm_down = self.llm.check()
        if self.llm_down:
            self.log("no reflection this time (Ollama isn't answering); her rules pick what to practise")
            return thoughts
        try:
            ans = self.llm.chat(system, user, reflect_schema(drills), num_predict=800, temperature=0.7,
                                timeout=120.0)
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as e:
            self.log(f"couldn't reflect on the match ({e}); her lessons stay as they were")
            return thoughts
        new = [str(ln).strip() for ln in ans.get("lessons", []) if str(ln).strip()][:MAX_LESSONS]
        if new:
            write_lessons(ctx.char_dir / "lessons.md", new)
            thoughts["lessons"] = new
        thoughts["line"] = str(ans.get("line") or "").strip()
        thoughts["review"] = str(ans.get("review") or "").strip()
        if ans.get("drill") in drills:
            thoughts["drill"] = ans["drill"]
            thoughts["goal"] = gomi_review.drill_info(ans["drill"])["goal"]
        if str(ans.get("goal") or "").strip():
            thoughts["goal"] = str(ans["goal"]).strip()[:200]
        if thoughts["line"]:
            self.log(f'after the match: "{thoughts["line"][:200]}"')
        if thoughts["review"]:
            self.log(f'her review: "{thoughts["review"][:300]}"')
        self.log(f"{len(new)} lessons in {ctx.char_dir / 'lessons.md'}")
        return thoughts


def plays(ckind):
    return ckind in CHARACTERS
