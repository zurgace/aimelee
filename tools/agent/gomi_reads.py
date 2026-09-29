"""Gomihyu reads the human: their habits to punish, their techniques to copy.

Every frame of a match against a human (a HMN slot), Reader watches the
opponent's action states and controller and counts:

- habits: which way they tech, how they get up, what they do from the
  ledge, what they do when she's close. Once an option has been seen a few
  times, the move library covers it: it tech-chases to where they usually
  end up, traps the ledge option they like, grabs a shield-happy player.
- techniques (mario_moves.TECHS): a wavedash, a waveland, an L-cancel, a
  SHFFL, a shield drop, a multishine. She starts without them; after seeing
  one twice she starts doing it too, mid-match, and the more she sees it, the
  more she uses it.

It all lives in <gomi>/rival.json, shared by her characters: it's about the
human, not about her.
"""

import dataclasses
import json
from collections import deque
from pathlib import Path

import bridge
import mario_moves as mm

UNLOCK_AT = 2          # sightings before she copies a technique
READ_AT = 4            # sightings of a habit before she plays around it
READ_SHARE = 0.4       # ...and only when one option is at least this common

TECH_STATES = {0xC7: "in_place", 0xC8: "forward", 0xC9: "backward"}
MISSED = (0xB7, 0xBF)
GETUPS = {0xBA: "stand", 0xC2: "stand", 0xBB: "attack", 0xC3: "attack",
          0xBC: "forward", 0xC4: "forward", 0xBD: "backward", 0xC5: "backward"}
LEDGE_OPTIONS = {0xFE: "stand", 0xFF: "stand", 0x100: "attack", 0x101: "attack", 0x102: "roll", 0x103: "roll",
                 0x104: "jump", 0x105: "jump", 0x106: "jump", 0x107: "jump"}
DEFENSES = {0xB2: "shield", 0xE9: "roll", 0xEA: "roll", 0xEB: "spotdodge", mm.KNEE_BEND: "jump"}
SHINE_START = 0x168    # Fox's and Falco's grounded reflector
SHINERS = (0x02, 0x14)  # Fox, Falco
TRIGGERS = bridge.BUTTON_L | bridge.BUTTON_R | bridge.BUTTON_Z

# What she says when she copies one (her bot posts it as a taunt).
COPY_LINES = {
    "l_cancel": "Cancelling your landings, are we? Cute. My aerials land faster now too.",
    "shffl": "Short hop, fast fall, cancel... I see what you're doing, and now I'm doing it better.",
    "wavedash": "Sliding around like that? Hmph. The Archdemon can wavedash too. Watch.",
    "waveland": "Landing like that... noted. Stolen, actually.",
    "shield_drop": "Dropping through platforms out of shield? Mine now.",
    "multishine": "That shine-shine-shine thing? I'll do it right back in your face.",
}

HABIT_NAMES = {"tech": "when they tech", "getup": "when they miss a tech and get up",
               "ledge": "from the ledge", "defense": "when you're close"}


class Rival:
    """What she has noticed about the human, across matches."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("techs", {})
        self.data.setdefault("habits", {})

    def saw_tech(self, tech):
        """Count one sighting; True when that's the one that unlocks it."""
        n = self.data["techs"].get(tech, 0) + 1
        self.data["techs"][tech] = n
        return n == UNLOCK_AT

    def saw_habit(self, kind, option):
        table = self.data["habits"].setdefault(kind, {})
        table[option] = table.get(option, 0) + 1

    def skills(self, moves):
        """{technique: how often she uses it} for the move library `moves`."""
        out = {}
        for tech, n in self.data["techs"].items():
            if n >= UNLOCK_AT and tech in mm.TECHS and (tech != "multishine" or moves.CKIND in SHINERS):
                out[tech] = min(0.8, 0.3 + 0.05 * (n - UNLOCK_AT))
        return out

    def reads(self):
        """{habit: (their usual option, its share)} for habits seen often enough."""
        out = {}
        for kind, table in self.data["habits"].items():
            total = sum(table.values())
            if total >= READ_AT:
                option, n = max(table.items(), key=lambda kv: kv[1])
                if n / total >= READ_SHARE:
                    out[kind] = (option, n / total)
        return out

    def lines(self):
        """Her prompt's summary of the human."""
        out = []
        for kind, table in self.data["habits"].items():
            total = sum(table.values())
            if total >= READ_AT:
                shares = ", ".join(f"{o} {n / total:.0%}" for o, n in sorted(table.items(), key=lambda kv: -kv[1]))
                out.append(f"{HABIT_NAMES.get(kind, kind)}: {shares} ({total} seen)")
        copied = [t for t, n in self.data["techs"].items() if n >= UNLOCK_AT]
        if copied:
            out.append("techniques you copied from them: " + ", ".join(copied))
        return out

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))


def toward(fighter, forward, me_x):
    """'toward' or 'away' (from her) for a roll that goes forward (or backward) from where it faces."""
    facing = 1 if fighter.facing >= 0 else -1
    moves = facing if forward else -facing
    return "toward" if moves * (me_x - fighter.pos_x) > 0 else "away"


class Reader:
    """Watches the human through one match, frame by frame."""

    def __init__(self, rival):
        self.rival = rival
        self.t = 0
        self.prev = None           # their previous Fighter
        self.inputs = deque(maxlen=9)
        self.kneebend_at = -99
        self.dodge_at = -99
        self.takeoff_at = -99
        self.fast_fell = False
        self.shine_at = -99
        self.learned = []          # techniques unlocked this match

    def watch(self, opp, me, dist):
        """One frame; returns the techniques this frame unlocked."""
        self.t += 1
        self.inputs.append(opp.input)
        prev, self.prev = self.prev, opp
        if prev is None:
            prev = dataclasses.replace(opp, motion_id=-1)  # their first frame: everything is new
        seen = []
        was, now = prev.motion_id, opp.motion_id
        if now == mm.KNEE_BEND and was != mm.KNEE_BEND:
            self.kneebend_at = self.t
        if now == mm.AIRDODGE and was != mm.AIRDODGE:
            self.dodge_at = self.t
        if opp.in_air and not prev.in_air:
            self.takeoff_at = self.t
            self.fast_fell = False
        if opp.in_air and prev.self_vy <= 0.3 and opp.self_vy < prev.self_vy - 0.8 and opp.input.stick_y < -40:
            self.fast_fell = True

        if now == mm.LANDING_SPECIAL and was == mm.AIRDODGE and self.t - self.dodge_at <= 8:
            seen.append("wavedash" if self.t - self.kneebend_at <= 14 else "waveland")
        if now in mm.AERIAL_LANDINGS and was in mm.AERIALS and self.l_cancelled():
            seen.append("l_cancel")
            if self.fast_fell and self.t - self.takeoff_at <= 40:
                seen.append("shffl")
        if now == mm.PLATFORM_DROP and was in mm.SHIELDING:
            seen.append("shield_drop")
        if now == SHINE_START and was != SHINE_START and opp.ckind in SHINERS:
            if was == mm.KNEE_BEND and self.t - self.shine_at <= 12:
                seen.append("multishine")
            self.shine_at = self.t

        self.habits(prev, opp, me, dist)
        learned = [tech for tech in seen if self.rival.saw_tech(tech)]
        self.learned += learned
        return learned

    def l_cancelled(self):
        """A fresh trigger press (L, R, Z or an analog press) in the last 7 frames."""
        def down(p):
            return bool(p.button & TRIGGERS) or max(p.trigger_l, p.trigger_r) > 40
        frames = list(self.inputs)
        return any(down(frames[i]) and not down(frames[i - 1]) for i in range(max(1, len(frames) - 7), len(frames)))

    def habits(self, prev, opp, me, dist):
        was, now = prev.motion_id, opp.motion_id
        if now == was:
            return
        if now in TECH_STATES:
            kind = TECH_STATES[now]
            self.rival.saw_habit("tech", kind if kind == "in_place" else toward(opp, kind == "forward", me.pos_x))
        elif now in MISSED:
            self.rival.saw_habit("tech", "missed")
        elif now in GETUPS and was not in GETUPS:
            kind = GETUPS[now]
            if kind in ("forward", "backward"):
                kind = toward(opp, kind == "forward", me.pos_x)
            self.rival.saw_habit("getup", kind)
        elif was in mm.LEDGE and now not in mm.LEDGE:
            option = LEDGE_OPTIONS.get(now) or ("drop" if opp.in_air and now not in mm.DAMAGE else None)
            if option:
                self.rival.saw_habit("ledge", option)
        elif now in DEFENSES and dist < 25 and not opp.in_air:
            self.rival.saw_habit("defense", DEFENSES[now])
