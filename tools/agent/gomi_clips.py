"""Clips of her teachers' play: how the Falco players in her replays actually move.

gomi_replays teaches her which techniques to use and which options pay; this
teaches her *how they play*. From each replay it cuts the Falco player's
controller inputs into short clips, one each time they're free to act on the
ground: a dash-dance step, a wavedash, a short-hop laser or down-air, a shine
out of a crouch... each until they can act again (MIN_CLIP to MAX_CLIP frames).
Each clip is filed under the situation it started in (how far, how high, what
the opponent was doing, by the ledge or not, facing them or not) with what it
traded over the next 90 frames, like gomi_options scores her own tries.

When she's free to act in a situation like it, her move library can play one
of them ("copy"): the teacher's own inputs, frame for frame, turned to face
her opponent the way the teacher faced theirs. Her bandit scores copying like
any other option.

Mostly the user: the replays are their own games, so the player in the most
files is the owner, and their clips count most (the others' OTHERS as much).

The file: <gomi>/<character>/clips.json, {"files": {key: n}, "players": {code:
{"name", "files"}}, "keys": {situation: [clip]}, "seen": {situation: n}}; a
clip is {"t": code, "o": traded, "k": kind, "p": [[[buttons, sx, sy, cx, cy,
trigger], frames], ...]}.
"""

import json
import math
import random
from pathlib import Path

import bridge
import gomi_options
import mario_moves as mm

MIN_CLIP = 12          # frames: shorter only when cut (hit, grabbed, offstage)
MAX_CLIP = 45
MIN_CUT = 6            # a cut clip shorter than this isn't kept
PER_KEY = 150          # clips kept per situation (a random sample of all seen)
MIN_POOL = 5           # a situation with fewer clips borrows from the coarser ones
OTHERS = 0.25          # the other players' clips, next to the owner's
MASK = (bridge.BUTTON_A | bridge.BUTTON_B | bridge.BUTTON_X | bridge.BUTTON_Y | bridge.BUTTON_Z
        | bridge.BUTTON_R | bridge.BUTTON_L)     # no Start, no d-pad
VERSION = 1

DIST = ((12, "touch"), (25, "close"), (45, "mid"), (80, "far"))
KINDS = ("shine", "laser", "grab", "attack", "shield", "move")
PLAN_KINDS = {"approach": ("attack", "shine", "grab"), "pressure": ("attack", "shine", "grab"),
              "zone": ("laser",), "defend": ("shield", "shine"), "space": ("move", "laser")}
LASERS = range(0x155, 0x15B)      # Falco's and Fox's blaster, ground and air
SHINES = range(0x168, 0x16E)      # their reflector, ground and air
GRABS = range(0xD4, 0xD7)
NAMES = {"shine": "shines", "laser": "lasers", "grab": "grabs", "attack": "attacks", "shield": "shields",
         "move": "movement"}


def key_of(s):
    """The situation a clip is filed under, and her own is looked up by: an mm.Situation."""
    rng = next((name for limit, name in DIST if s.dist < limit), "away")
    if s.opp_offstage:
        them = "offstage"
    elif s.opp_motion in mm.DAMAGE or s.opp_motion == mm.TUMBLING:
        them = "hit"
    elif s.opp_motion in mm.SHIELDING:
        them = "shield"
    elif s.opp_motion in mm.BUSY:
        them = "busy"
    elif s.opp_air:
        them = "air"
    else:
        them = "grounded"
    dy = "above" if s.dy > 15 else "below" if s.dy < -15 else "level"
    spot = "edge" if mm.sign(s.x) == -s.toward and abs(s.x) > s.edge - 25 else "center"
    facing = "facing" if s.facing == s.toward else "away"
    return "/".join((rng, them, dy, spot, facing))


def coarser(k):
    """The key, then the same without height, ledge or facing, then the range alone."""
    parts = k.split("/")
    return [k, "/".join(parts[:2]), parts[0]]


def free(s):
    """On the stage, on the ground, and free to act: where a clip may start."""
    return (not s.air and s.motion in mm.ACTIONABLE and not s.offstage and not s.hitstun
            and not s.grabbed)


def encode(pads):
    """Run-length: [[pad, frames], ...]."""
    out = []
    for p in pads:
        if out and out[-1][0] == p:
            out[-1][1] += 1
        else:
            out.append([p, 1])
    return out


def decode(runs):
    return [list(p) for p, n in runs for _ in range(n)]


def as_pad(p, d):
    """A stored pad (facing the opponent) as her controller, the opponent in direction d."""
    buttons, sx, sy, cx, cy, trigger = p
    return bridge.Pad(button=buttons, stick_x=sx * d, stick_y=sy, cstick_x=cx * d, cstick_y=cy,
                         trigger_r=trigger if not buttons & bridge.BUTTON_L else 0,
                      trigger_l=trigger if buttons & bridge.BUTTON_L else 0)


def kind_of(actions):
    seen = set(actions)
    for kind, states in (("shine", SHINES), ("laser", LASERS), ("grab", GRABS), ("attack", mm.ATTACKS),
                         ("shield", mm.SHIELDING)):
        if any(a in states for a in seen):
            return kind
    return "move"


def extract(frames):
    """The clips in one player's game. `frames`: a list of (situation, pad, stocks, percent, their
    stocks, their percent), the pad pressed with that situation on screen and the rest after it.
    Returns [(key, clip)], the clips without their teacher's code."""
    n = len(frames)
    score = [0.0] * n                    # what frame i traded
    for i in range(1, n):
        _, _, st, pc, ost, opc = frames[i]
        _, _, st0, pc0, ost0, opc0 = frames[i - 1]
        died, ko = st < st0, ost < ost0
        taken = pc - pc0 if not died and pc > pc0 else 0
        dealt = opc - opc0 if not ko and opc > opc0 else 0
        score[i] = dealt - taken + gomi_options.STOCK * (int(ko) - int(died))
    ahead = [0.0] * (n + 1)              # prefix sums
    for i in range(n):
        ahead[i + 1] = ahead[i] + score[i]
    out = []
    i = 0
    while i < n:
        s = frames[i][0]
        if not free(s):
            i += 1
            continue
        d = s.toward
        end, cut = i + 1, False
        while end < n and end - i < MAX_CLIP:
            t = frames[end][0]
            if t.hitstun or t.grabbed or t.motion == mm.TUMBLING or t.offstage:
                cut = True
                break
            if end - i >= MIN_CLIP and free(t):
                break
            end += 1
        if end - i >= (MIN_CUT if cut else MIN_CLIP):       # (not a scrap from the game's end)
            pads = []
            for j in range(i, end):
                b, sx, sy, cx, cy, trig = frames[j][1]
                pads.append([b & MASK, sx * d, sy, cx * d, cy, trig])
            actions = [frames[j][0].motion for j in range(i, end)]
            traded = ahead[min(n, i + gomi_options.WINDOW)] - ahead[i]
            out.append((key_of(s), {"o": round(traded, 1), "k": kind_of(actions), "p": encode(pads)}))
        i = max(end, i + 1)
    return out


class Clips:
    """Her teachers' clips for one character."""

    def __init__(self, path, seed=0):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        if self.data.get("version") != VERSION:
            self.data = {"version": VERSION}
        for k in ("files", "players", "keys", "seen"):
            self.data.setdefault(k, {})
        self.rng = random.Random(seed)
        self.index = None
        self._owner = None

    def __bool__(self):
        return bool(self.data["keys"])

    def saw_player(self, code, name):
        """A player in a replay read (any character): the one in the most is the owner."""
        p = self.data["players"].setdefault(code, {"name": name, "files": 0})
        p["name"] = name or p["name"]
        p["files"] += 1
        self._owner = None

    def add(self, k, clip, code):
        """Keep the clip, or (PER_KEY already kept here) keep a random sample of all seen."""
        clip = dict(clip, t=code)
        kept = self.data["keys"].setdefault(k, [])
        seen = self.data["seen"][k] = self.data["seen"].get(k, 0) + 1
        if len(kept) < PER_KEY:
            kept.append(clip)
        else:
            at = self.rng.randrange(seen)
            if at < PER_KEY:
                kept[at] = clip
        self.index = self._owner = None

    def owner(self):
        """Whose replays these are: the player in the most of them (a tie: the one with more clips)."""
        if self._owner is None:
            players, counts = self.data["players"], self.counts()
            self._owner = max(players, key=lambda c: (players[c]["files"], counts.get(c, 0), c)) if players else ""
        return self._owner or None

    def name(self, code):
        return (self.data["players"].get(code) or {}).get("name") or code

    def pool(self, s):
        """The clips for her situation: the closest match with at least MIN_POOL of them."""
        if self.index is None:
            self.index = {}
            for k, clips in self.data["keys"].items():
                for level in coarser(k):
                    self.index.setdefault(level, []).extend(clips)
        for level in coarser(key_of(s)):
            clips = self.index.get(level, ())
            if len(clips) >= MIN_POOL:
                return clips
        return ()

    def has(self, s):
        return bool(self.pool(s))

    def weight(self, clip, plan, owner):
        w = 1.0 if clip.get("t") == owner else OTHERS
        w *= min(3.0, max(0.3, math.exp(clip["o"] / 30)))
        return w * (2.0 if clip["k"] in PLAN_KINDS.get(plan, ()) else 1.0)

    def pick(self, s, plan, rng):
        """A clip for her situation (weighted: the owner's, the ones that paid, the plan's kind),
        as a list of stored pads; None when there's nothing like it."""
        clips = self.pool(s)
        if not clips:
            return None
        owner = self.owner()
        chosen = rng.choices(clips, weights=[self.weight(c, plan, owner) for c in clips])[0]
        return decode(chosen["p"])

    def counts(self):
        """code -> clips kept, most first."""
        out = {}
        for clips in self.data["keys"].values():
            for c in clips:
                out[c.get("t")] = out.get(c.get("t"), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def lines(self):
        """Her prompt's line about them."""
        counts = self.counts()
        if not counts:
            return []
        owner = self.owner()
        mine = counts.get(owner, 0)
        rest = sum(counts.values()) - mine
        who = f"{self.name(owner)} ({mine} clip{'s' if mine != 1 else ''})" + (f" and others ({rest})" if rest else "")
        return [f"in neutral you often play moves copied straight from {who}: their movement, lasers and "
                f"aerials, frame for frame"]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, separators=(",", ":")))
        tmp.replace(self.path)
