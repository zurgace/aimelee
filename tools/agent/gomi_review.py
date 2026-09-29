"""Gomihyu's match review, and the drills she practises.

MatchLog watches a match frame by frame and keeps what her lessons need and
her old per-plan totals couldn't say: what hit her and what she was doing
at the time, what she hit them with, how each stock was lost, who won each
neutral exchange (an "opening": the first hit after both have been left
alone for a moment) and how much each opening was worth.

After the match she picks one drill (DRILLS) to practise next match, in her
own words. A drill changes real settings of the move library (Player.knobs),
nudges her plans, and has one number to judge it by; next match's number is
compared with this one's, and her post says whether it paid off.
"""

from collections import Counter, namedtuple

import mario_moves as mm

QUIET = 45          # frames without a hit before the next hit starts a new opening
RECENT = 150        # a hit this recent is what killed you

Seen = namedtuple("Seen", "stocks percent motion_id")   # what's kept of a fighter's previous frame

DOING = {"approach": "approaching", "space": "spacing", "pressure": "pressuring", "defend": "defending",
         "edgeguard": "edgeguarding", "platform": "on the platforms", "fireball": "throwing fireballs",
         "lasers": "lasering", "recover": "recovering", "ledge": "on the ledge", "getup": "getting up",
         "chase": "tech chasing", "tech": "teching", "hit": "already in hitstun", "mash": "grabbed",
         "throw": "throwing", "follow": "following up", "shield": "shielding"}

# The drills. knobs: Player.knobs changes; bias: added to her plan priors (net % per minute);
# metric: MatchLog.metrics() key, and whether higher is better.
DRILLS = {
    "recovery": {
        "does": "recover earlier and higher: double jump sooner, up-B before falling too low",
        "knobs": {"recover_early": True}, "bias": {"edgeguard": -10.0, "space": 5.0},
        "metric": "offstage_deaths", "higher": False, "unit": "stocks lost offstage",
        "goal": "Stop falling to my doom offstage. An Archdemon returns to the stage, always.",
    },
    "neutral": {
        "does": "stay further out and wait for their whiff instead of running in",
        "knobs": {"space_dist": 35, "patience": 0.5}, "bias": {"space": 10.0, "approach": -6.0},
        "metric": "opening_share", "higher": True, "unit": "share of openings won",
        "goal": "Win the neutral game: patience, then strike when they whiff.",
    },
    "punish": {
        "does": "follow up: jump after them with an up-air when a hit sends them above you",
        "knobs": {"follow_up": 0.9}, "bias": {"pressure": 8.0},
        "metric": "damage_per_opening", "higher": True, "unit": "damage per opening",
        "goal": "Make every hit count: chase them into the air and keep the combo going.",
    },
    "defense": {
        "does": "shield their attacks up close, then grab out of shield",
        "knobs": {"shield_react": 0.7}, "bias": {"defend": 10.0},
        "metric": "taken_per_min", "higher": False, "unit": "damage taken per minute",
        "goal": "Take less damage. My shield exists for a reason, apparently.",
    },
    "edgeguard": {
        "does": "go to the ledge as soon as they're knocked offstage, whatever the plan",
        "knobs": {"ledge_early": True}, "bias": {"edgeguard": 12.0},
        "metric": "edgeguard_kos", "higher": True, "unit": "edgeguard KOs",
        "goal": "Nobody comes back from offstage. Guard the ledge like my throne.",
    },
}
TECH_DRILL_RATE = 0.9   # how often she uses the technique she's practising


def drill_names(skills):
    """The drills she can pick: the fixed ones, and practising each technique she has copied."""
    return list(DRILLS) + [f"tech:{t}" for t in skills]


def drill_info(name):
    """DRILLS[name], or the made-up entry for a tech:<technique> drill."""
    if name in DRILLS:
        return DRILLS[name]
    tech = name.split(":", 1)[1]
    return {"does": f"use {mm.TECHS.get(tech, tech)} as often as you can", "knobs": {}, "bias": {},
            "metric": f"uses:{tech}", "higher": True, "unit": f"times you used {tech.replace('_', ' ')}",
            "goal": f"Master {tech.replace('_', ' ')}. I copied it; now I'll own it."}


def move_name(motion):
    """What an attacker's action state was, as a player would say it."""
    if 0x2C <= motion <= 0x31:
        return "jab"
    names = {0x32: "dash attack", 0x38: "up-tilt", 0x39: "down-tilt", 0x3F: "up-smash", 0x40: "down-smash",
             0x41: "neutral-air", 0x42: "forward-air", 0x43: "back-air", 0x44: "up-air", 0x45: "down-air",
             0xD9: "pummel", 0xDB: "forward throw", 0xDC: "back throw", 0xDD: "up throw", 0xDE: "down throw",
             0xBB: "getup attack", 0xC3: "getup attack", 0x100: "ledge attack", 0x101: "ledge attack"}
    if motion in names:
        return names[motion]
    if 0x33 <= motion <= 0x37:
        return "forward-tilt"
    if 0x3A <= motion <= 0x3E:
        return "forward-smash"
    if 0x46 <= motion <= 0x4A:
        return names[motion - 5]   # hit as the aerial landed
    if motion >= 0x155:
        return "a special move"
    return "something else"


class MatchLog:
    """One match, from her side: port `me` against `opp`."""

    def __init__(self):
        self.frames = 0
        self.taken = []           # (damage, their move, what she was doing)
        self.dealt = []           # (damage, her move)
        self.deaths = []          # {"offstage": bool, "cause": str}
        self.kos = []             # {"offstage": bool}
        self.openings = {"her": [], "them": []}   # damage of each opening each side won
        self.opening = None       # (owner, index) of the opening in progress
        self.quiet = QUIET
        self.last = None          # previous frame: (Seen me, Seen opp, doing, her_offstage, their_offstage)
        self.hit_by = None        # (frame, move, offstage) of the last hit she took
        self.hit_them = None      # (frame, offstage) of the last hit they took
        self.offstage_at = -999   # last frame she was offstage
        self.their_offstage_at = -999
        self.grabs = Counter()    # "her" / "them": grabs landed

    def frame(self, me, opp, doing, her_offstage, their_offstage):
        """One game frame, after her pad for it was chosen; `doing` is her plan or mode now."""
        self.frames += 1
        t = self.frames
        if her_offstage:
            self.offstage_at = t
        if their_offstage:
            self.their_offstage_at = t
        now = (Seen(me.stocks, me.percent, me.motion_id), Seen(opp.stocks, opp.percent, opp.motion_id),
               doing, her_offstage, their_offstage)
        if self.last is None:
            self.last = now
            return
        pme, popp, pdoing, p_her_off, p_their_off = self.last
        self.last = now
        self.quiet += 1

        # A grab lands when the one grabbed goes into a grabbed state (a whiff never does).
        if me.motion_id in mm.GRABBED and pme.motion_id not in mm.GRABBED:
            self.grabs["them"] += 1
        if opp.motion_id in mm.GRABBED and popp.motion_id not in mm.GRABBED:
            self.grabs["her"] += 1

        if me.stocks < pme.stocks:
            self.died(pme, pdoing, p_her_off)
        elif me.percent > pme.percent:
            move = move_name(opp.motion_id)
            self.taken.append((me.percent - pme.percent, move, DOING.get(pdoing, pdoing)))
            self.hit_by = (t, move, p_her_off)
            self.scored("them", me.percent - pme.percent)
        if opp.stocks < popp.stocks:
            recent = self.hit_them is not None and t - self.hit_them[0] <= RECENT
            offstage = self.hit_them[1] if recent else t - self.their_offstage_at <= RECENT
            self.kos.append({"offstage": bool(offstage)})
        elif opp.percent > popp.percent:
            self.dealt.append((opp.percent - popp.percent, move_name(me.motion_id)))
            self.hit_them = (t, p_their_off)
            self.scored("her", opp.percent - popp.percent)

    def died(self, before, doing, offstage):
        """A stock lost. `doing` and `offstage`: her last frame before it. Hit while offstage:
        edgeguarded. Still flying from a hit on stage (hitstun): that hit killed her. Anything
        else offstage: her recovery fell short."""
        t = self.frames
        recent = self.hit_by is not None and t - self.hit_by[0] <= RECENT
        if recent and self.hit_by[2]:
            self.deaths.append({"offstage": True, "cause": f"hit offstage by their {self.hit_by[1]}"})
        elif recent and doing == "hit":
            self.deaths.append({"offstage": False,
                                "cause": f"killed by their {self.hit_by[1]} at {before.percent}%"})
        elif offstage or doing == "recover" or t - self.offstage_at <= RECENT:
            after = f" after their {self.hit_by[1]}" if recent else ""
            self.deaths.append({"offstage": True, "cause": f"fell short recovering{after}"})
        elif recent:
            self.deaths.append({"offstage": False,
                                "cause": f"killed by their {self.hit_by[1]} at {before.percent}%"})
        else:
            self.deaths.append({"offstage": False, "cause": f"lost at {before.percent}%"})

    def scored(self, side, damage):
        """A hit by `side`: part of its opening, or the start of a new one."""
        if self.opening is None or self.opening[0] != side or self.quiet >= QUIET:
            self.openings[side].append(0)
            self.opening = (side, len(self.openings[side]) - 1)
        self.openings[side][self.opening[1]] += damage
        self.quiet = 0

    def metrics(self, used=None):
        """The numbers drills are judged by. `used`: Player.used (techniques she did)."""
        mine, theirs = self.openings["her"], self.openings["them"]
        out = {
            "offstage_deaths": sum(d["offstage"] for d in self.deaths),
            "opening_share": round(len(mine) / max(1, len(mine) + len(theirs)), 2),
            "damage_per_opening": round(sum(mine) / max(1, len(mine)), 1),
            "taken_per_min": round(sum(d for d, _, _ in self.taken) / max(1 / 60, self.frames / 3600), 1),
            "edgeguard_kos": sum(k["offstage"] for k in self.kos),
        }
        for tech, n in (used or {}).items():
            out[f"uses:{tech}"] = n
        return out

    def summary(self):
        """A few short lines about the match, for her prompts."""
        lines = []
        if self.taken:
            by_move = Counter()
            doing = {}
            for dmg, move, what in self.taken:
                by_move[move] += dmg
                doing.setdefault(move, Counter())[what] += dmg
            top = by_move.most_common(2)
            first = top[0][0]
            lines.append("They hit you most with " + " and ".join(f"{m} ({d}%)" for m, d in top)
                         + f"; the {first} mostly while you were {doing[first].most_common(1)[0][0]}.")
        if self.dealt:
            by_move = Counter()
            for dmg, move in self.dealt:
                by_move[move] += dmg
            lines.append("You hit them most with "
                         + " and ".join(f"{m} ({d}%)" for m, d in by_move.most_common(2)) + ".")
        mine, theirs = self.openings["her"], self.openings["them"]
        if mine or theirs:
            lines.append(f"Openings: you won {len(mine)} of {len(mine) + len(theirs)}, worth "
                         f"{sum(mine) / max(1, len(mine)):.0f}% each; they won {len(theirs)}, worth "
                         f"{sum(theirs) / max(1, len(theirs)):.0f}% each.")
        if self.deaths:
            lines.append("Stocks you lost: " + "; ".join(d["cause"] for d in self.deaths) + ".")
        if self.kos:
            lines.append(f"Stocks you took: {len(self.kos)}, {sum(k['offstage'] for k in self.kos)} of them "
                         "offstage.")
        if self.grabs:
            lines.append(f"Grabs: you grabbed them {self.grabs['her']} times, they grabbed you "
                         f"{self.grabs['them']} times.")
        return lines


def judge(drill, goal, before, metrics):
    """How the drill went this match: a dict for the record, her prompt and her post."""
    info = drill_info(drill)
    after = metrics.get(info["metric"], 0)
    if after == before:
        verdict = "no change"
    else:
        verdict = "better" if (after > before) == info["higher"] else "worse"
    return {"drill": drill, "goal": goal, "unit": info["unit"], "before": before, "after": after,
            "verdict": verdict,
            "text": f'"{goal}" ({info["unit"]}: {before} -> {after}, {verdict})'}


def pick_drill(metrics, skills=()):
    """Her rules' choice when Ollama can't reflect: whatever looks worst."""
    if metrics.get("offstage_deaths", 0) >= 2:
        return "recovery"
    if metrics.get("opening_share", 1) < 0.4:
        return "neutral"
    if metrics.get("taken_per_min", 0) > 60:
        return "defense"
    if metrics.get("damage_per_opening", 99) < 15:
        return "punish"
    for tech in skills:
        return f"tech:{tech}"
    return "edgeguard"
