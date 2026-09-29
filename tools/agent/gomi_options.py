"""Gomihyu learns by trying things: which option works in which situation.

Her move library (mario_moves.Player) offers a menu of options each time
she's free to act -- grab, down-tilt, a short-hop aerial, a fireball, backing
off, shielding, just waiting... -- and Bandit picks one. Every try is scored
by what happens in the next 90 frames: damage dealt minus damage taken, 40 a
stock (the same reward phillip and slippi-ai's reinforcement learning use).
The scores are kept per situation (range, what they're doing, where she
stands), per opponent character.

She picks by Thompson sampling: each option's score is drawn from what she
knows of it, wide when she hardly knows it and narrow once tried often.
Untried options are tried first, so she experiments; after that she leans
on what works, and still now and then tries the rest.

Starting guesses (`priors`, in the same units) come from her drill and her
reads of the human; gomi_reads adds what has worked for the human (their
head start, worth up to HEAD_START of her own tries).

The file: <gomi>/<character>/options.json, {"by_opponent": {opponent:
{bucket: {option: [tries, total, total of squares]}}}, "all": {...}}.
"""

import json
import math
from collections import Counter
from pathlib import Path

WINDOW = 90          # frames a try is scored over
SPREAD = 25.0        # how far one try's score strays (percent traded)
STOCK = 40.0         # a stock, in percent
HEAD_START = 3.0     # the human's results count as up to this many of her own tries
MIN_FRAMES = 30      # a try cut short by the match ending counts only past this

THEM = {"grounded": "they're on the ground", "air": "they're in the air", "shield": "they're shielding",
        "busy": "they're stuck in a move or roll"}
NAMES = {"dash_in": "dash in", "walk_in": "walk in", "retreat": "back off", "wait": "wait for it",
         "shield": "shield", "grab": "grab", "dtilt": "down-tilt", "jab": "jab", "smash": "smash attack",
         "dash_attack": "dash attack", "sh_aerial": "short-hop aerial", "fullhop_aerial": "full-hop aerial",
         "zone": "projectile", "shine": "shine"}


def key(bucket):
    return "/".join(bucket)


def describe(bucket_key):
    rng, them, spot = bucket_key.split("/")
    return f"{rng}, {THEM.get(them, them)}" + (", you by the ledge" if spot == "edge" else "")


def mean_of(row):
    return row[1] / row[0] if row and row[0] else 0.0


class Bandit:
    """Her options' scores for one match against `opponent`, and the choosing."""

    def __init__(self, path, opponent, rng, priors=None, head_start=None):
        self.path = Path(path)
        self.opponent = opponent
        self.rng = rng
        self.priors = dict(priors or {})          # option -> starting guess
        self.head_start = head_start or {}        # bucket key -> option -> [n, total, squares] (the human's)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("by_opponent", {})
        self.data.setdefault("all", {})
        self.mine = self.data["by_opponent"].setdefault(opponent, {})
        self.trials = []                          # open tries: [bucket key, option, frames, score]
        self.tried = Counter()                    # option -> tries this match
        self.started = Counter()                  # (bucket key, option) -> tries this match

    def guess(self, k, option):
        """(mean, how many tries it's worth) before her own tries against this opponent."""
        m, weight = self.priors.get(option, 0.0), 1.0
        allrow = self.data["all"].get(k, {}).get(option)
        if allrow and allrow[0]:                  # other opponents: a hint, not the answer
            w = min(allrow[0], 3)
            m, weight = (m * weight + mean_of(allrow) * w) / (weight + w), weight + w
        human = self.head_start.get(k, {}).get(option)
        if human and human[0]:
            w = min(human[0], HEAD_START)
            m, weight = (m * weight + mean_of(human) * w) / (weight + w), weight + w
        return m, weight

    def value(self, k, option):
        """Her best estimate of an option's score here, and how many tries it rests on."""
        row = self.mine.get(k, {}).get(option) or [0, 0.0, 0.0]
        m0, w0 = self.guess(k, option)
        n = row[0] + w0
        return (row[1] + m0 * w0) / n, n

    def untried(self, k, option):
        own = self.mine.get(k, {}).get(option)
        human = self.head_start.get(k, {}).get(option)
        return not (own and own[0]) and not (human and human[0]) and not self.started[(k, option)]

    def choose(self, bucket, menu):
        """Pick an option from `menu` for `bucket` (range, them, spot), and start scoring it."""
        k = key(bucket)
        fresh = [o for o in menu if self.untried(k, o)]
        if fresh:
            pick = self.rng.choice(fresh)         # experiment: never tried here yet
        else:
            draws = []
            for o in menu:
                m, n = self.value(k, o)
                draws.append((self.rng.gauss(m, SPREAD / math.sqrt(n)), o))
            pick = max(draws)[1]
        self.trials.append([k, pick, 0, 0.0])
        self.tried[pick] += 1
        self.started[(k, pick)] += 1
        return pick

    def frame(self, dealt, taken, kos=0, deaths=0):
        """One game frame's trade, credited to every try still being scored."""
        score = dealt - taken + STOCK * (kos - deaths)
        still = []
        for trial in self.trials:
            trial[2] += 1
            trial[3] += score
            if trial[2] >= WINDOW:
                self.record(trial)
            else:
                still.append(trial)
        self.trials = still

    def record(self, trial):
        k, option, _, score = trial
        for table in (self.mine, self.data["all"]):
            row = table.setdefault(k, {}).setdefault(option, [0, 0.0, 0.0])
            row[0] += 1
            row[1] += score
            row[2] += score * score

    def finish(self):
        """The match is over: tries far enough along count, the rest are dropped."""
        for trial in self.trials:
            if trial[2] >= MIN_FRAMES:
                self.record(trial)
        self.trials = []

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))

    def lines(self, top=4):
        """Her prompts' summary: in the situations she's been in most, what works and what doesn't."""
        busiest = sorted(self.mine.items(), key=lambda kv: -sum(r[0] for r in kv[1].values()))[:top]
        out = []
        for k, rows in busiest:
            tried = [(mean_of(r), o, r[0]) for o, r in rows.items() if r[0] >= 2]
            if len(tried) < 2:
                continue
            tried.sort(reverse=True)
            best, worst = tried[0], tried[-1]
            out.append(f"{describe(k)}: {NAMES.get(best[1], best[1])} {best[0]:+.0f} a try ({best[2]} tries); "
                       f"{NAMES.get(worst[1], worst[1])} {worst[0]:+.0f} ({worst[2]} tries)")
        return out

    def variety(self):
        """This match: how many different options she tried, and the most used."""
        return len(self.tried), self.tried.most_common(3)
