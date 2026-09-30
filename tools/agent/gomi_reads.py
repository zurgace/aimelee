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
- options: what they do when they're free to act -- grab, down-tilt, jab,
  dash attack, smash, short-hop or full-hop aerial, shield, roll away, shine
  -- in the same situations her own options are scored in (gomi_options),
  and what each traded for them over the next 90 frames. Her own choosing
  starts from that: what works for them, she tries first (imitation, the
  first half of slippi-ai's recipe).

It all lives in <gomi>/rival.json, shared by her characters: it's about the
human, not about her.
"""

import dataclasses
import json
from collections import deque
from pathlib import Path

import bridge
import gomi_options
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

# Their action state as it starts -> the option of hers it matches.
OPTION_STARTS = {0xD4: "grab", 0xD6: "grab", 0x39: "dtilt", 0x2C: "jab", 0x32: "dash_attack", 0xB2: "shield",
                 **{m: "smash" for m in range(0x3A, 0x41)}}
ROLLS = (0xE9, 0xEA)
BLASTER_STARTS = (0x155, 0x158)  # Fox's and Falco's laser, on the ground and in the air: zoning
SH_HEIGHT = 22         # an aerial started below this after a jump: a short hop

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
        works = self.what_works()
        if works:
            out.append("what works for them: " + "; ".join(works))
        return out

    def saw_option(self, bucket_key, option, score):
        row = self.data.setdefault("options", {}).setdefault(bucket_key, {}).setdefault(option, [0, 0.0, 0.0])
        row[0] += 1
        row[1] += score
        row[2] += score * score

    def options(self):
        """{bucket key: {option: [tries, total, squares]}}: their results, her head start."""
        return self.data.setdefault("options", {})

    def what_works(self, top=3, view="rival"):
        """Their best options: told to her about the human she plays ("you're on the ground"), or,
        view="teacher", about a player she imitates ("their opponent's on the ground")."""
        best = []
        for k, rows in self.options().items():
            for o, r in rows.items():
                if r[0] >= 3 and r[1] > 0:
                    best.append((r[1] / r[0], k, o, r[0]))
        best.sort(reverse=True)
        where = their_situation if view == "rival" else teacher_situation
        return [f"{where(k)}: {gomi_options.NAMES.get(o, o)} ({m:+.0f} a try, {n} times)"
                for m, k, o, n in best[:top]]

    def save(self):
        """Whole or not at all (write, then rename): another thread may be reading it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.path)


YOU = {"grounded": "you're on the ground", "air": "you're in the air", "shield": "you're shielding",
       "busy": "you're stuck in a move or roll"}


def their_situation(k):
    """A bucket key seen from the human's side, told to her: 'close, you're on the ground'."""
    rng, her, spot = k.split("/")
    return f"{rng}, {YOU.get(her, her)}" + (", them by the ledge" if spot == "edge" else "")


OPPONENT = {"grounded": "their opponent on the ground", "air": "their opponent in the air",
            "shield": "their opponent shielding", "busy": "their opponent stuck in a move or roll"}


def teacher_situation(k):
    """A bucket key seen from a player she imitates: 'close, their opponent on the ground'."""
    rng, opp, spot = k.split("/")
    return f"{rng}, {OPPONENT.get(opp, opp)}" + (", by the ledge" if spot == "edge" else "")


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
        self.trials = []           # their open options: [bucket key, option, frames, score]
        self.swung = False         # an aerial already counted this airtime
        self.prev_me = None        # her previous Fighter (their damage dealt is hers taken)

    def watch(self, opp, me, dist, edge=70.0):
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
        self.options(prev, opp, me, dist, edge)
        learned = [tech for tech in seen if self.rival.saw_tech(tech)]
        self.learned += learned
        return learned

    def options(self, prev, opp, me, dist, edge):
        """Score their open options this frame; start one if they just began something."""
        if self.prev_me is not None:
            pme = self.prev_me
            died, ko = opp.stocks < prev.stocks, me.stocks < pme.stocks
            dealt = me.percent - pme.percent if not ko and me.percent > pme.percent else 0
            taken = opp.percent - prev.percent if not died and opp.percent > prev.percent else 0
            score = dealt - taken + gomi_options.STOCK * (int(ko) - int(died))
            still = []
            for trial in self.trials:
                trial[2] += 1
                trial[3] += score
                if trial[2] >= gomi_options.WINDOW:
                    self.rival.saw_option(trial[0], trial[1], trial[3])
                else:
                    still.append(trial)
            self.trials = still
        self.prev_me = me
        if not opp.in_air:
            self.swung = False
        was, now = prev.motion_id, opp.motion_id
        if now == was:
            return
        option = OPTION_STARTS.get(now) if not opp.in_air else None
        if now in ROLLS and not opp.in_air:
            option = "retreat" if toward(opp, now == 0xE9, me.pos_x) == "away" else None
        if now == SHINE_START and opp.ckind in SHINERS:
            option = "shine"
        if now in BLASTER_STARTS and opp.ckind in SHINERS:
            option = "zone"
        if now in mm.AERIALS and opp.in_air and not self.swung and self.t - self.takeoff_at <= 20:
            self.swung = True
            option = "sh_aerial" if opp.pos_y < SH_HEIGHT else "fullhop_aerial"
        if option is None:
            return
        rng = "close" if dist < 20 else "mid" if dist < 45 else "far"
        if me.motion_id in mm.SHIELDING:
            her = "shield"
        elif me.motion_id in mm.BUSY:
            her = "busy"
        elif me.in_air:
            her = "air"
        else:
            her = "grounded"
        toward_her = 1 if me.pos_x > opp.pos_x else -1
        spot = "edge" if (1 if opp.pos_x > 0 else -1) == -toward_her and abs(opp.pos_x) > edge - 25 else "center"
        self.trials.append([gomi_options.key((rng, her, spot)), option, 0, 0.0])

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
