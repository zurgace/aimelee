"""Mario's move library: a game plan in, a controller pad out, every frame.

Gomihyu (gomi_brain.py) decides every half second or so *what* Mario should
be doing -- approach, zone with fireballs, edgeguard... -- and this module
does it frame by frame from the bridge's state (bridge.Fighter). Some things
never wait for her: recovering when offstage, teching, getting up, mashing
out of grabs, DI in hitstun and ledge options.

Pure Python and deterministic for a given seed, so it can be tested against
synthetic states. Sticks are raw (-80..80, up is positive); a press lasts one
frame and is followed by a release, so nothing is held by accident.
"""

import random
from collections import deque
from dataclasses import dataclass

import bridge

MARIO = 0x08  # CKind
NAME = "Mario"
CKIND = MARIO
ZONE = "fireball"  # the plan that keeps them out at range (her rules use it when they're far)

# The plans Gomi chooses from, with the line her prompt shows for each.
PLANS = {
    "approach": "run in and jab, down-tilt or grab them",
    "fireball": "stay at mid range and throw fireballs",
    "space": "stay just out of their reach and punish them with a dash attack when they whiff",
    "pressure": "up-tilts and up-airs when they're above you, smash them when they're close and hurt",
    "defend": "shield, shield-grab, roll away when your shield is low",
    "edgeguard": "when they're offstage: stand at the ledge and cape or fireball them",
}

# StKind -> x of the stage's edge while standing on it (libmelee's
# EDGE_GROUND_POSITION); the main platform is at y = 0 on all of them.
EDGE = {
    0x1F: 68.4,     # Battlefield
    0x20: 85.566,   # Final Destination
    0x03: 87.75,    # Pokemon Stadium
    0x08: 56.0,     # Yoshi's Story
    0x1C: 77.271,   # Dream Land N64
    0x02: 63.348,   # Fountain of Dreams
}
DEFAULT_EDGE = 60.0

# Action states (motion_id) the library reacts to.
TUMBLING = 0x26
HELPLESS = (0x23, 0x24, 0x25)          # FallSpecial after Up-B
DAMAGE = range(0x4B, 0x5C)
DOWN = range(0xB7, 0xC7)                # missed tech: lying, getting up
SHIELD = range(0xB2, 0xB6)
OWN_GRAB = range(0xD4, 0xDF)            # Mario holding someone
GRABBED = range(0xDF, 0xE9)             # someone holding Mario
LEDGE = (0xFC, 0xFD)                    # CliffCatch, CliffWait


def pad(buttons=0, sx=0, sy=0, cx=0, cy=0, r=0):
    return bridge.Pad(button=buttons, stick_x=int(sx), stick_y=int(sy), cstick_x=int(cx), cstick_y=int(cy),
                      trigger_r=int(r))


NEUTRAL = pad()
A, B, X, Z, R = bridge.BUTTON_A, bridge.BUTTON_B, bridge.BUTTON_X, bridge.BUTTON_Z, bridge.BUTTON_R


def sign(v):
    return 1 if v > 0 else -1 if v < 0 else 0


@dataclass
class Situation:
    """What the library (and Gomi's prompt) needs from one frame."""

    x: float
    y: float
    opp_x: float
    opp_y: float
    edge: float
    air: bool
    opp_air: bool
    facing: int
    motion: int
    opp_motion: int
    hitstun: bool
    hitlag: bool
    jumps_left: int
    shield: float
    vy: float
    percent: int
    opp_percent: int

    @property
    def dx(self):
        return self.opp_x - self.x

    @property
    def dist(self):
        return abs(self.opp_x - self.x)

    @property
    def dy(self):
        return self.opp_y - self.y

    @property
    def toward(self):
        return sign(self.dx) or self.facing

    @property
    def home(self):
        """The direction of the stage's centre."""
        return -sign(self.x) or self.facing

    @property
    def offstage(self):
        return abs(self.x) > self.edge + 2 or self.y < -6

    @property
    def opp_offstage(self):
        return abs(self.opp_x) > self.edge + 2 or self.opp_y < -6

    @property
    def on_ledge(self):
        return self.motion in LEDGE

    @property
    def helpless(self):
        return self.motion in HELPLESS

    @property
    def lying(self):
        return self.motion in DOWN

    @property
    def grabbed(self):
        return self.motion in GRABBED

    @property
    def holding(self):
        return self.motion in OWN_GRAB


def situation(me, opp, stage):
    return Situation(
        x=me.pos_x, y=me.pos_y, opp_x=opp.pos_x, opp_y=opp.pos_y, edge=EDGE.get(stage, DEFAULT_EDGE),
        air=me.in_air, opp_air=opp.in_air, facing=1 if me.facing >= 0 else -1,
        motion=me.motion_id, opp_motion=opp.motion_id,
        hitstun=bool(me.flags & bridge.FT_IN_HITSTUN) or me.motion_id in DAMAGE,
        hitlag=bool(me.flags & bridge.FT_IN_HITLAG),
        jumps_left=max(0, me.max_jumps - me.jumps_used), shield=me.shield, vy=me.self_vy,
        percent=me.percent, opp_percent=opp.percent)


class Mario:
    """Turns the current plan into one pad per game frame."""

    ZONE = ZONE

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.queue = deque()
        self.mode = "plan"      # what produced the queued inputs (logged, and used for stats)
        self.teched = False     # one tech press per tumble: a second one locks teching out
        self.upb_used = False   # one Up-B per trip offstage
        self.cooldown = 0

    def reset(self):
        self.queue.clear()
        self.mode = "plan"
        self.teched = False
        self.upb_used = False
        self.cooldown = 0

    def run(self, *pads):
        self.queue.extend(pads)

    def step(self, s, plan):
        """The pad for this frame. `s` is a Situation, `plan` one of PLANS."""
        if self.cooldown:
            self.cooldown -= 1
        if not s.air:
            self.upb_used = False
        if s.motion != TUMBLING and s.motion not in DAMAGE:
            self.teched = False
        if self.can_tech(s):
            self.teched = True
            self.queue.clear()
            self.mode = "tech"
            return pad(R, sx=s.home * 80, r=140)  # tech roll toward the centre
        if s.hitlag or s.hitstun:
            # Survival DI: up and toward the stage. Queued moves are stale now.
            self.queue.clear()
            self.mode = "hit"
            return pad(sx=s.home * 55, sy=45)
        if s.grabbed:
            self.queue.clear()
            self.mode = "mash"
            flip = self.rng.random() < 0.5
            return pad(A if flip else B, sx=80 if flip else -80)
        if self.queue:
            return self.queue.popleft()
        if s.on_ledge:
            return self.ledge(s)
        if s.lying:
            return self.getup(s)
        if s.air and s.offstage:
            return self.recover(s)
        if s.holding:
            return self.throw(s)
        self.mode = "plan"
        return self.do_plan(s, plan)

    # ---- things that never wait for Gomi ----------------------------------

    def can_tech(self, s):
        """Knocked down and about to hit the stage: one tech press (a second
        within the window would lock teching out)."""
        knocked = s.motion == TUMBLING or s.motion in DAMAGE
        return knocked and s.air and not self.teched and not s.offstage and s.vy < 0 and s.y < 8

    def recover(self, s):
        self.mode = "recover"
        home = s.home
        if s.helpless:
            return pad(sx=home * 80)
        beyond = abs(s.x) - s.edge
        if s.jumps_left > 0 and s.y < 15:
            self.run(*[pad(sx=home * 80)] * 8)
            return pad(X, sx=home * 80)
        if not self.upb_used and (s.y < -12 or beyond > 35) and s.vy <= 0:
            self.upb_used = True
            self.run(*[pad(sx=home * 60, sy=80)] * 3)
            return pad(B, sx=home * 30, sy=80)
        return pad(sx=home * 80)

    def ledge(self, s):
        self.mode = "ledge"
        home = s.home
        roll = self.rng.random()
        if roll < 0.35:
            self.run(*[NEUTRAL] * 20)
            return pad(sx=home * 80)       # stand up
        if roll < 0.6:
            self.run(*[pad(sx=home * 80)] * 20)
            return pad(X)                  # ledge jump, drift onto the stage
        if roll < 0.8:
            self.run(*[NEUTRAL] * 30)
            return pad(R, r=140)           # ledge roll
        self.run(*[NEUTRAL] * 30)
        return pad(A)                      # getup attack

    def getup(self, s):
        self.mode = "getup"
        roll = self.rng.random()
        self.run(*[NEUTRAL] * 20)
        if roll < 0.4:
            return pad(sx=s.home * 80)     # roll toward the centre
        if roll < 0.7:
            return pad(A)                  # getup attack
        return pad(sy=80)                  # stand up in place

    def throw(self, s):
        self.mode = "throw"
        self.run(*[NEUTRAL] * 3)
        if abs(s.x) > s.edge * 0.5 and sign(s.x) == s.facing:
            return pad(sx=s.facing * 80)   # forward throw off the stage
        if abs(s.x) > s.edge * 0.5:
            return pad(sx=-s.facing * 80)  # back throw off the stage
        return pad(sy=80) if s.opp_percent < 60 else pad(sx=s.facing * 80)  # up-throw for follow-ups

    # ---- the plans -----------------------------------------------------------

    def do_plan(self, s, plan):
        if s.air:
            return self.air(s, plan)
        if plan == "edgeguard" and not s.opp_offstage:
            plan = "space"
        return getattr(self, "plan_" + plan, self.plan_space)(s)

    def turn(self, s):
        """None when facing the opponent; else this frame's turn-around input."""
        if s.facing == s.toward:
            return None
        self.run(NEUTRAL)
        return pad(sx=s.toward * 35)  # a tilt turns without dashing

    def cornered(self, s):
        """Backing off from the opponent would run Mario off the stage."""
        return sign(s.x) == -s.toward and abs(s.x) > s.edge - 18

    def escape(self, s):
        """Cornered at the ledge: jump over them or shield."""
        if self.rng.random() < 0.5:
            self.run(*[pad(sx=s.toward * 80)] * 6, pad(A, sx=s.toward * 80), *[pad(sx=s.toward * 80)] * 12)
            return pad(X, sx=s.toward * 80)              # full hop over them, nair on the way
        self.run(*[pad(R, r=140)] * 12, *[NEUTRAL] * 3)
        return pad(R, r=140)

    def retreat(self, s, frames):
        if self.cornered(s):
            return self.escape(s)
        return self.dash(-s.toward, frames)

    def dash(self, direction, frames=6):
        self.run(*[pad(sx=direction * 80)] * (frames - 1))
        return pad(sx=direction * 80)

    def hit(self, first, recovery=12):
        """A move: its one input frame, then hands off the stick while it plays out."""
        self.run(*[NEUTRAL] * recovery)
        return first

    def plan_approach(self, s):
        if s.dist > 22:
            return self.dash(s.toward, 5)
        turn = self.turn(s)
        if turn:
            return turn
        pick = self.rng.random()
        if pick < 0.35:
            return self.hit(pad(Z), 25)                  # grab
        if pick < 0.65:
            return self.hit(pad(A, sy=-45), 12)          # down-tilt
        self.run(NEUTRAL, pad(A), NEUTRAL, pad(A))
        return self.hit(pad(A), 10)                      # jab, jab, jab

    def plan_fireball(self, s):
        if s.dist < 30:
            return self.retreat(s, 8)
        if s.dist > 90:
            return self.dash(s.toward, 5)
        turn = self.turn(s)
        if turn:
            return turn
        if self.cooldown:
            return NEUTRAL
        self.cooldown = 35
        if self.rng.random() < 0.5:
            return self.hit(pad(B), 30)                  # fireball from the ground
        self.run(NEUTRAL, NEUTRAL, NEUTRAL, pad(B), *[NEUTRAL] * 28)
        return pad(X)                                    # short hop (X released in jumpsquat), fireball

    def plan_space(self, s):
        if s.dist < 25:
            return self.retreat(s, 5)
        if s.dist > 45:
            self.run(*[pad(sx=s.toward * 50)] * 5)
            return pad(sx=s.toward * 50)                 # walk in
        if self.rng.random() < 0.12:
            self.run(*[pad(sx=s.toward * 80)] * 6, pad(A, sx=s.toward * 80))
            return self.hit(pad(sx=s.toward * 80), 25)   # dash attack
        return NEUTRAL

    def plan_pressure(self, s):
        if s.dist > 20:
            return self.dash(s.toward, 4)
        turn = self.turn(s)
        if turn:
            return turn
        if s.dy > 8:
            return self.hit(pad(A, sy=50), 18)           # up-tilt
        if s.opp_percent > 90 and self.rng.random() < 0.6:
            return self.hit(pad(cx=s.toward * 80), 35)   # f-smash
        if self.rng.random() < 0.5:
            return self.hit(pad(cy=80), 30)              # up-smash
        self.run(NEUTRAL, NEUTRAL, NEUTRAL, pad(cy=80), *[NEUTRAL] * 20)
        return pad(X)                                    # short hop up-air

    def plan_defend(self, s):
        if s.dist > 30:
            return self.plan_space(s)
        if s.shield < 25:
            if self.cornered(s):
                return self.escape(s)
            self.run(*[NEUTRAL] * 25)
            return pad(R, sx=-s.toward * 80, r=140)      # roll away
        shield = [pad(R, r=140)] * 10
        if self.rng.random() < 0.5:
            self.run(*shield[1:], pad(R | A, r=140), *[NEUTRAL] * 25)   # shield grab
        else:
            self.run(*shield[1:], *[NEUTRAL] * 3)
        return shield[0]

    def plan_edgeguard(self, s):
        side = sign(s.opp_x) or s.facing
        target = side * (s.edge - 6)
        if abs(s.x - target) > 8:
            return self.dash(sign(target - s.x), 4)
        if s.facing != side:
            self.run(NEUTRAL)
            return pad(sx=side * 35)
        if self.cooldown:
            return NEUTRAL
        self.cooldown = 30
        if s.dist < 30 and s.opp_y > -30:
            return self.hit(pad(B, sx=side * 80), 30)    # cape
        return self.hit(pad(B), 30)                      # fireball off the ledge

    def air(self, s, plan):
        """Airborne over the stage: drift, and swing when they're close."""
        close = s.dist < 18 and abs(s.dy) < 20
        if close and not self.cooldown:
            self.cooldown = 20
            if s.dy > 6:
                return self.hit(pad(cy=80), 12)          # up-air
            if s.toward != s.facing:
                return self.hit(pad(cx=-s.facing * 80), 12)  # back-air
            return self.hit(pad(A), 12)                  # neutral-air
        away = plan in (self.ZONE, "space", "defend")
        direction = -s.toward if away and s.dist < 30 else s.toward
        fast_fall = s.vy < 0 and s.y > 5 and self.rng.random() < 0.05
        return pad(sx=direction * 60, sy=-80 if fast_fall else 0)


Player = Mario
