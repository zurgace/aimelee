"""Gomihyu notices when the game eats her inputs, and times around it.

Melee ignores most presses made while a character can't act -- in landing
lag, in the middle of an attack, in shield stun -- with almost no buffer. A
press that changes nothing is an eaten input: wasted, and a sign of bad
timing. (User, 2026-09-30: "Can we have gomi observe if her input is being
eaten and then think about how to time it better?")

Timing watches every pad her move library sends. A new press (A, B, X, Z,
a c-stick flick, or a shield press that isn't her L-cancel) opens a check:
if her action state changes within CHECK_FRAMES frames (one frame of bridge
lag, then the game's reaction), or she gets hit, it was taken; if nothing
changes, it was eaten. Counts are kept per action state she pressed in.

A state where most presses get eaten (at least MIN_TRIES, over EATEN_SHARE)
is one she waits out: at a decision point she holds still until her state
changes, then presses on the first frame she can (mario_moves.MAX_WAIT caps
the wait, so a misread can't freeze her). Presses made where she's always
free to act (standing, dashing, falling...) aren't checked: they always work. Known lag states start
that way (SEEDED), and her own counts can clear one she turns out to act
fine in. The counts are kept per character in <gomi>/<character>/timing.json
(gomi_brain), and her match review says how many inputs were eaten and where.
"""

import json
from collections import Counter
from pathlib import Path

import bridge
import mario_moves as mm

CHECK_FRAMES = 4
MIN_TRIES = 5
EATEN_SHARE = 0.5
PRESSES = bridge.BUTTON_A | bridge.BUTTON_B | bridge.BUTTON_X | bridge.BUTTON_Z
# Modes whose presses aren't meant to change her state at once, or (a clip's) were timed by a human.
NOT_CHECKED = ("tech", "mash", "hit", "copy")
LANDING = 0x2A                             # normal landing lag

# States Melee is known to ignore presses in: waited out from the first match.
SEEDED = frozenset({*mm.AERIAL_LANDINGS, mm.LANDING_SPECIAL, LANDING, 0xB5})

NAMES = {LANDING: "landing lag", mm.LANDING_SPECIAL: "a wavedash or waveland landing", 0xB5: "shield stun",
         mm.KNEE_BEND: "jumpsquat", 0xB2: "raising your shield", 0xB4: "dropping your shield",
         0xFC: "grabbing the ledge", 0xFD: "hanging on the ledge", 0xE9: "a roll", 0xEA: "a roll",
         0xEB: "a spot dodge", 0xEC: "an air dodge", 0xD4: "a grab", 0xD6: "a dash grab"}


def state_name(motion):
    if motion in mm.AERIAL_LANDINGS:
        return "an aerial's landing lag"
    if 0x2C <= motion <= 0x40:
        return "a ground attack still playing"
    if motion in mm.AERIALS:
        return "an aerial still playing"
    return NAMES.get(motion, f"action state 0x{motion:X}")


class Timing:
    """Her presses, per action state: how many, how many eaten. One per character."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        data = {}
        if self.path:
            try:
                data = json.loads(self.path.read_text())
            except (OSError, ValueError):
                data = {}
        self.pressed = Counter({int(k): v for k, v in data.get("pressed", {}).items()})
        self.eaten = Counter({int(k): v for k, v in data.get("eaten", {}).items()})
        self.pending = None                  # [state pressed in, frames since]
        self.prev = mm.NEUTRAL
        self.match_pressed = 0
        self.match_eaten = Counter()         # state -> eaten this match

    def new_match(self):
        self.pending = None
        self.prev = mm.NEUTRAL
        self.match_pressed = 0
        self.match_eaten = Counter()

    def blocked(self, motion):
        """She waits this state out: most presses in it get eaten (or it's a known lag state).
        Never a state she can always act in: presses "eaten" there were misread, and waiting it out
        would freeze her."""
        if motion in mm.ACTIONABLE:
            return False
        n = self.pressed[motion]
        if n >= MIN_TRIES:
            return self.eaten[motion] / n > EATEN_SHARE
        return motion in SEEDED

    def observe(self, s, pad, lcancel=False, mode="plan"):
        """One frame: resolve the open check, and open one if this pad is a new press."""
        if self.pending is not None:
            motion, frames = self.pending
            if s.motion != motion or s.hitstun or s.hitlag or s.grabbed:
                self.pressed[motion] += 1                     # taken (or she was hit: can't tell)
                self.pending = None
            elif frames + 1 >= CHECK_FRAMES:
                self.pressed[motion] += 1
                self.eaten[motion] += 1
                self.match_eaten[motion] += 1
                self.pending = None
            else:
                self.pending[1] = frames + 1
        prev, self.prev = self.prev, pad
        if self.pending is not None or mode in NOT_CHECKED or s.hitstun or s.hitlag or s.grabbed:
            return
        if s.motion == mm.TUMBLING or s.motion in mm.DAMAGE or s.motion in mm.ACTIONABLE:
            return                                  # free to act: a press there always works
        new = (pad.button & PRESSES) & ~(prev.button & PRESSES)
        flick = any(abs(a) > 40 >= abs(b) for a, b in ((pad.cstick_x, prev.cstick_x), (pad.cstick_y, prev.cstick_y)))
        shield = bool(pad.button & bridge.BUTTON_R and not prev.button & bridge.BUTTON_R and not lcancel)
        if new or flick or shield:
            self.pending = [s.motion, 0]
            self.match_pressed += 1

    def summary(self):
        """Her review's line about this match's eaten inputs, or None."""
        eaten = sum(self.match_eaten.values())
        if not self.match_pressed or not eaten:
            return None
        where, n = self.match_eaten.most_common(1)[0]
        waits = " -- you wait those out now" if self.blocked(where) else ""
        return (f"{eaten} of your {self.match_pressed} inputs ({eaten / self.match_pressed:.0%}) were eaten, "
                f"most in {state_name(where)} ({n}){waits}.")

    def share(self):
        return sum(self.match_eaten.values()) / self.match_pressed if self.match_pressed else 0.0

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"pressed": {str(k): v for k, v in self.pressed.items()},
                                   "eaten": {str(k): v for k, v in self.eaten.items()}}, indent=1))
        tmp.replace(self.path)
