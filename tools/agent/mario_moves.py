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
from dataclasses import dataclass, replace

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
    "platform": "get on a platform above them and drop onto them with aerials (not on Final Destination)",
}

# Techniques she only uses once she has seen the human do them (gomi_reads.py
# spots them); the library knows how, and Player.skills says how often.
TECHS = {
    "l_cancel": "L-cancelling aerials (a shield press just before landing halves the landing lag)",
    "shffl": "short hop, aerial, fast fall, L-cancel (SHFFL)",
    "wavedash": "wavedashing (jump, air dodge diagonally into the ground, slide)",
    "waveland": "wavelanding (air dodge into the ground or a platform while falling)",
    "shield_drop": "shield dropping (from shield, straight through a platform)",
    "multishine": "multishining (shine, jump out of it, shine again: Fox only)",
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

# StKind -> (height, left x, right x) of each platform (libmelee's stages.py);
# Fountain of Dreams' side platforms move, so only its top one is here.
PLATFORMS = {
    0x1F: ((27.2, -57.6, -20.0), (27.2, 20.0, 57.6), (54.4, -18.8, 18.8)),
    0x03: ((25.0, -55.0, -25.0), (25.0, 25.0, 55.0)),
    0x08: ((23.45, -59.5, -28.0), (23.45, 28.0, 59.5), (42.0, -15.75, 15.75)),
    0x1C: ((30.14, -61.39, -31.73), (30.24, 31.70, 63.07), (51.43, -19.02, 19.02)),
    0x02: ((42.75, -14.25, 14.25),),
}

# Action states (motion_id) the library reacts to.
TUMBLING = 0x26
HELPLESS = (0x23, 0x24, 0x25)          # FallSpecial after Up-B
DAMAGE = range(0x4B, 0x5C)
DOWN = range(0xB7, 0xC7)                # missed tech: lying, getting up
SHIELD = range(0xB2, 0xB6)
OWN_GRAB = range(0xD4, 0xDF)            # Mario holding someone
GRABBED = range(0xDF, 0xE9)             # someone holding Mario
LEDGE = (0xFC, 0xFD)                    # CliffCatch, CliffWait
KNEE_BEND = 0x18                        # jumpsquat
JUMPING = (0x19, 0x1A)                  # first jump, forward and back
LANDING_SPECIAL = 0x2B                  # wavedash / waveland landing
AERIALS = range(0x41, 0x46)             # nair, fair, bair, uair, dair
AERIAL_LANDINGS = range(0x46, 0x4B)
SHIELDING = (0xB2, 0xB3, 0xB5)          # GuardOn, Guard, GuardSetOff
AIRDODGE = 0xEC
PLATFORM_DROP = 0xF4


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
    platforms: tuple = ()
    frame: float = 0.0

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

    def surface(self, x, y):
        """Height of what's under (x, y): a platform, the stage (0), or None over the void."""
        under = [h for h, left, right in self.platforms if left <= x <= right and h <= y + 1]
        if under:
            return max(under)
        return 0.0 if abs(x) <= self.edge else None

    @property
    def height(self):
        """How far above the ground or platform under her; None over the void."""
        ground = self.surface(self.x, self.y)
        return None if ground is None else self.y - ground

    @property
    def on_platform(self):
        return not self.air and self.y > 5

    def platform_near(self, x):
        """The side platform (low enough for a full hop) nearest x, or None."""
        low = [p for p in self.platforms if p[0] < 35]
        return min(low, key=lambda p: abs((p[1] + p[2]) / 2 - x)) if low else None


def situation(me, opp, stage):
    return Situation(
        x=me.pos_x, y=me.pos_y, opp_x=opp.pos_x, opp_y=opp.pos_y, edge=EDGE.get(stage, DEFAULT_EDGE),
        air=me.in_air, opp_air=opp.in_air, facing=1 if me.facing >= 0 else -1,
        motion=me.motion_id, opp_motion=opp.motion_id,
        hitstun=bool(me.flags & bridge.FT_IN_HITSTUN) or me.motion_id in DAMAGE,
        hitlag=bool(me.flags & bridge.FT_IN_HITLAG),
        jumps_left=max(0, me.max_jumps - me.jumps_used), shield=me.shield, vy=me.self_vy,
        percent=me.percent, opp_percent=opp.percent, platforms=PLATFORMS.get(stage, ()), frame=me.action_frame)


class Mario:
    """Turns the current plan into one pad per game frame."""

    ZONE = ZONE

    def __init__(self, seed=None, skills=None):
        self.rng = random.Random(seed)
        self.queue = deque()
        self.combo = None       # a technique in progress: a generator sent each frame's Situation
        self.skills = dict(skills or {})  # TECHS name -> how often she uses it (0..1)
        self.reset()

    def reset(self):
        self.queue.clear()
        self.combo = None
        self.mode = "plan"      # what produced the queued inputs (logged, and used for stats)
        self.teched = False     # one tech press per tumble: a second one locks teching out
        self.upb_used = False   # one Up-B per trip offstage
        self.cooldown = 0
        self.l_cancel = None    # this aerial: None (not decided), True (press before landing), False
        self.waveland_tried = False

    def run(self, *pads):
        self.queue.extend(pads)

    def drop(self):
        """Whatever was queued is stale now."""
        self.queue.clear()
        self.combo = None

    def knows(self, tech):
        """She has seen it and feels like using it this time."""
        rate = self.skills.get(tech, 0.0)
        return rate > 0 and self.rng.random() < rate

    def start(self, combo):
        """Run a technique frame by frame: its first pad now, the rest as it reacts."""
        self.combo = combo
        return next(combo)

    def step(self, s, plan):
        """The pad for this frame. `s` is a Situation, `plan` one of PLANS."""
        p = self.choose(s, plan)
        if self.lcancel_now(s):
            p = replace(p, button=p.button | R, trigger_r=140)
        return p

    def lcancel_now(self, s):
        """Press the shield just before an aerial lands (once per aerial)."""
        if s.motion not in AERIALS or not s.air:
            self.l_cancel = None
            return False
        if self.l_cancel is None:
            self.l_cancel = self.knows("l_cancel") or self.knows("shffl")
        if self.l_cancel and s.vy < 0 and s.height is not None and s.height < 10:
            self.l_cancel = False
            return True
        return False

    def choose(self, s, plan):
        if self.cooldown:
            self.cooldown -= 1
        if not s.air:
            self.upb_used = False
            self.waveland_tried = False
        if s.motion != TUMBLING and s.motion not in DAMAGE:
            self.teched = False
        if self.can_tech(s):
            self.teched = True
            self.drop()
            self.mode = "tech"
            return pad(R, sx=s.home * 80, r=140)  # tech roll toward the centre
        if s.hitlag or s.hitstun:
            # Survival DI: up and toward the stage. Queued moves are stale now.
            self.drop()
            self.mode = "hit"
            return pad(sx=s.home * 55, sy=45)
        if s.grabbed:
            self.drop()
            self.mode = "mash"
            flip = self.rng.random() < 0.5
            return pad(A if flip else B, sx=80 if flip else -80)
        if self.combo is not None:
            try:
                return self.combo.send(s)
            except StopIteration:
                self.combo = None
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

    # ---- techniques (combos: generators sent each frame's Situation) --------

    def wavedash(self, direction):
        """Jump, and on the first airborne frame air dodge down and `direction`."""
        s = yield pad(X)
        for _ in range(8):
            if s.air or s.motion in JUMPING:
                break
            s = yield NEUTRAL
        else:
            return
        s = yield pad(R, sx=direction * 72, sy=-35, r=140)
        for _ in range(20):
            if not s.air and s.motion != LANDING_SPECIAL:
                return
            s = yield NEUTRAL

    def shffl(self, aerial, drift=0):
        """Short hop, `aerial` (a c-stick or A pad) at once, fast fall at the top;
        step() adds the L-cancel."""
        s = yield pad(X)
        for _ in range(8):
            if s.air:
                break
            s = yield NEUTRAL
        else:
            return
        s = yield aerial
        fell = False
        for _ in range(50):
            if not s.air:
                return
            if not fell and s.vy < 0:
                fell = True
                s = yield pad(sy=-80)                    # fast fall: a fresh tap down
            else:
                s = yield pad(sx=drift)

    def shield_drop(self):
        """On a platform: shield, then the stick down just far enough to drop through
        (y -0.6875: past the pass threshold, short of a spot dodge)."""
        s = yield pad(R, r=140)
        for _ in range(8):
            if s.motion in SHIELDING and s.motion != 0xB2:
                break
            s = yield pad(R, r=140)
        s = yield pad(R, sy=-55, r=140)
        for _ in range(6):
            if s.air:
                return
            s = yield NEUTRAL

    def platform_hop(self, target):
        """Full hop onto the platform `target` (height, left, right), drifting to its
        middle; double jump if short; waveland onto it if she knows how."""
        height, left, right = target
        mid = (left + right) / 2
        s = yield pad(X)
        airborne = False
        waveland = self.knows("waveland")
        for i in range(90):
            airborne = airborne or s.air
            if airborne and not s.air:
                return
            drift = max(-80, min(80, (mid - s.x) * 6))
            if i < 7:
                s = yield pad(X, sx=drift)               # hold X: a full hop
            elif s.vy < 0 and s.y < height - 2 and s.jumps_left > 0 and abs(mid - s.x) < 25:
                s = yield pad(X, sx=drift)               # short: double jump
                s = yield pad(sx=drift)
            elif waveland and s.vy < 0 and 0 < s.y - height < 6 and left < s.x < right:
                waveland = False
                s = yield pad(R, sx=sign(drift) * 60, sy=-50, r=140)
            else:
                s = yield pad(sx=drift)

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
        if s.facing == s.toward and self.knows("wavedash"):
            return self.start(self.wavedash(-s.toward))   # wavedash back, still facing them
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
            if s.dist < 70 and self.knows("wavedash"):
                return self.start(self.wavedash(s.toward))
            return self.dash(s.toward, 5)
        turn = self.turn(s)
        if turn:
            return turn
        if self.knows("shffl"):
            return self.start(self.shffl(pad(cx=s.facing * 80)))   # SHFFL'd forward-air
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

    def plan_platform(self, s):
        """Above them on a platform, dropping onto them; up there from the stage."""
        if not s.platforms:
            return self.plan_space(s)
        if s.on_platform:
            if s.dist > 45 or (s.dy < -8 and s.dist < 25):
                if self.knows("shield_drop"):
                    return self.start(self.shield_drop())
                self.run(NEUTRAL, NEUTRAL)
                return pad(sy=-80)                       # tap down: drop through
            if abs(s.dy) <= 8:
                return self.plan_approach(s)             # they're up here too
            if s.dy > 8:
                return self.plan_pressure(s)
            return NEUTRAL                               # above them: wait for them to come close
        target = s.platform_near(s.opp_x)
        if target is None:
            return self.plan_space(s)
        mid = (target[1] + target[2]) / 2
        if abs(mid - s.x) > 12:
            return self.dash(sign(mid - s.x), 3)
        return self.start(self.platform_hop(target))

    def air(self, s, plan):
        """Airborne over the stage: drift, and swing when they're close."""
        if (not self.waveland_tried and s.vy < 0 and s.motion not in AERIALS and s.motion != AIRDODGE
                and s.height is not None and 0 < s.height < 6 and plan in (self.ZONE, "space", "platform")):
            self.waveland_tried = True
            if self.knows("waveland"):
                direction = -s.toward if s.dist < 30 else s.toward
                return self.hit(pad(R, sx=direction * 60, sy=-50, r=140), 10)
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
