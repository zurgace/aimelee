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
from collections import Counter, deque
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
TECHS_NOW = {0xC7: 18, 0xC8: 30, 0xC9: 30}      # teching: in place, forward, backward -> frame to grab at
GETTING_UP = {0xBA: 22, 0xC2: 22, 0xBC: 26, 0xC4: 26, 0xBD: 26, 0xC5: 26}  # stand / roll up from lying
LYING = (0xB7, 0xB8, 0xBF, 0xC0)                 # missed tech, lying there
LEDGE_ROLLS = (0x102, 0x103)
ROLL = 30                                        # about how far a tech or getup roll goes
ATTACKS = range(0x2C, 0x46)                      # jabs to aerials: they're swinging
IDLE = (0x0E, 0x12, 0x14, 0x15, 0x2A)            # wait, turn, dash, run, landing: free to act
# Free to act again: standing, turning, dashing, running, crouching; falling in the air.
ACTIONABLE = (0x0E, 0x12, 0x14, 0x15, 0x27, 0x28, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x22)
PRESS_GUARD = 4        # frames after a press before its queued tail can be cut short
MAX_WAIT = 30          # waited this long in a state that eats presses: press anyway (gomi_timing re-judges)

# Settings her drills change (gomi_review.DRILLS); these defaults are her everyday game.
KNOBS = {
    "space_dist": 25,        # spacing: back off when they're closer than this
    "recover_early": False,  # double jump and up-B sooner and higher
    "follow_up": 0.25,       # chance to jump after them when a hit sends them above her
    "shield_react": 0.0,     # chance to shield an attack up close
    "ledge_early": False,    # to the ledge whenever they're offstage, whatever the plan
}

# Her options: concrete things to try, at the ranges where they make sense. Each plan offers
# a menu of them, and her chooser (gomi_options.Bandit: trial and error) picks one each time
# she's free to act. Without a chooser, a uniform pick.
RANGES = ("close", "mid", "far")        # < 20, < 45, the rest (units between them)
OPTION_RANGES = {
    "dash_in": ("mid", "far"), "walk_in": ("mid", "far"), "retreat": ("close", "mid"),
    "wait": ("close", "mid", "far"), "shield": ("close", "mid"), "grab": ("close",), "dtilt": ("close",),
    "jab": ("close",), "smash": ("close",), "dash_attack": ("mid",), "sh_aerial": ("close", "mid"),
    "fullhop_aerial": ("close", "mid"), "zone": ("mid", "far"), "shine": ("close",),
    "crouch_shine": ("close", "mid"),
}
MENUS = {
    "approach": ("dash_in", "grab", "dtilt", "jab", "dash_attack", "sh_aerial", "fullhop_aerial", "shine", "wait"),
    "space": ("wait", "retreat", "walk_in", "zone", "fullhop_aerial", "shield", "dash_attack", "shine",
              "crouch_shine"),
    "pressure": ("dash_in", "sh_aerial", "smash", "jab", "grab", "dtilt", "shine", "fullhop_aerial", "crouch_shine"),
    "defend": ("shield", "retreat", "wait", "grab", "shine", "crouch_shine"),
    "zone": ("zone", "retreat", "wait", "dash_in", "shine"),
}
FACE_FIRST = ("grab", "dtilt", "jab", "smash", "zone", "shine")   # only when facing them
BUSY = (set(range(0x2C, 0x41)) | set(AERIAL_LANDINGS) | {LANDING_SPECIAL, 0xE9, 0xEA, 0xEB}
        | set(TECHS_NOW) | set(GETTING_UP) | set(LYING))


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
    opp_facing: int = 1
    opp_frame: float = 0.0

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
        percent=me.percent, opp_percent=opp.percent, platforms=PLATFORMS.get(stage, ()), frame=me.action_frame,
        opp_facing=1 if opp.facing >= 0 else -1, opp_frame=opp.action_frame)


class Mario:
    """Turns the current plan into one pad per game frame."""

    ZONE = ZONE

    def __init__(self, seed=None, skills=None):
        self.rng = random.Random(seed)
        self.queue = deque()
        self.combo = None       # a technique in progress: a generator sent each frame's Situation
        self.skills = dict(skills or {})  # TECHS name -> how often she uses it (0..1)
        self.reads = {}   # gomi_reads: habit -> (the human's usual option, its share)
        self.knobs = dict(KNOBS)
        self.chooser = None   # (bucket, menu) -> option; gomi_brain sets gomi_options.Bandit.choose
        import gomi_timing    # here, not at the top: gomi_timing reads this module's constants
        self.timing = gomi_timing.Timing()   # eaten inputs; gomi_brain gives it her saved counts
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
        self.reacted = False    # one follow-up or shield per time they're launched or swing
        self.used = Counter()   # techniques done this match (her drills count them)
        self.since_press = 99   # frames since her last button press
        self.waited = 0         # frames she held still to let a laggy state pass (gomi_timing)
        self.wait_motion, self.wait_frames = None, 0   # how long she has waited in this state
        if hasattr(self, "timing"):
            self.timing.new_match()

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

    def read(self, habit):
        """The human's usual option for `habit` (gomi_reads), or None."""
        got = self.reads.get(habit)
        return got[0] if got else None

    def start(self, combo):
        """Run a technique frame by frame: its first pad now, the rest as it reacts."""
        self.combo = combo
        if combo.__name__ in TECHS:
            self.used[combo.__name__] += 1
        return next(combo)

    def step(self, s, plan):
        """The pad for this frame. `s` is a Situation, `plan` one of PLANS."""
        p = self.choose(s, plan)
        lcancel = self.lcancel_now(s)
        if lcancel:
            p = replace(p, button=p.button | R, trigger_r=140)
        self.timing.observe(s, p, lcancel=lcancel, mode=self.mode)
        self.since_press = 0 if p.button & ~R or p.cstick_x or p.cstick_y else self.since_press + 1
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
            self.used["l_cancel"] += 1
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
        if (self.queue and s.motion in ACTIONABLE and self.since_press >= PRESS_GUARD
                and all(q is NEUTRAL for q in self.queue)):
            self.queue.clear()          # the move is over and she can act: don't stand there waiting it out
        if s.motion != self.wait_motion:
            self.wait_motion, self.wait_frames = s.motion, 0
        if (not self.queue and self.timing.blocked(s.motion) and not (s.air and s.offstage)
                and self.wait_frames < MAX_WAIT):
            self.waited += 1
            self.wait_frames += 1
            return NEUTRAL              # a state that eats presses: wait it out, press when it ends
        if not self.queue or (s.motion in IDLE and all(q == NEUTRAL for q in self.queue)):
            react = self.react(s)
            if react is not None:
                return react
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

    def react(self, s):
        """Free on the ground: follow a launched opponent up, or shield their swing (knobs)."""
        launched = (s.opp_motion in DAMAGE or s.opp_motion == TUMBLING) and s.opp_air
        swinging = s.opp_motion in ATTACKS and not s.opp_air
        if not launched and not swinging:
            self.reacted = False
            return None
        if s.air or self.reacted or s.lying or s.holding or s.on_ledge:
            return None
        if launched and 10 < s.dy < 45 and s.dist < 30:
            self.reacted = True
            if self.rng.random() < self.knobs["follow_up"]:
                self.queue.clear()
                self.mode = "follow"
                return self.start(self.follow_up())
        elif swinging and s.dist < 22:
            self.reacted = True
            if self.rng.random() < self.knobs["shield_react"]:
                self.queue.clear()
                self.mode = "shield"
                self.run(*[pad(R, r=140)] * 9, pad(R | A, r=140), *[NEUTRAL] * 20)   # hold it, grab out
                return pad(R, r=140)
        return None

    def follow_up(self):
        """Jump after them and up-air (or forward-air) when close."""
        s = yield pad(X)
        for i in range(45):
            if i > 4 and not s.air:
                return
            if s.air and s.dist < 16 and abs(s.dy) < 18:
                s = yield pad(cy=80) if s.dy > 4 else pad(cx=s.facing * 80)
                for _ in range(40):
                    if not s.air:
                        return
                    s = yield pad(sx=s.toward * 50)
                return
            s = yield pad(X if i < 4 else 0, sx=s.toward * 70)     # hold X: a full hop, drift under them

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
        early = self.knobs["recover_early"]
        if s.jumps_left > 0 and s.y < (30 if early else 15):
            self.run(*[pad(sx=home * 80)] * 8)
            return pad(X, sx=home * 80)
        if not self.upb_used and ((s.y < 0 or beyond > 20) if early else (s.y < -12 or beyond > 35)) and s.vy <= 0:
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
        return (yield from self.short_hop(aerial, drift, fast_fall=True))

    def short_hop(self, aerial, drift=0, fast_fall=False):
        """Short hop, `aerial` at once, drift down (fast falling at the top if asked)."""
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
                return s                                 # landed: the caller may follow up
            if fast_fall and not fell and s.vy < 0:
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
        if plan != "defend":
            chase = self.tech_chase(s)
            if chase is not None:
                return chase
        if self.knobs["ledge_early"] and s.opp_offstage and plan != "defend":
            plan = "edgeguard"
        if plan == "edgeguard" and not s.opp_offstage:
            plan = "space"
        return getattr(self, "plan_" + plan, self.plan_space)(s)

    # ---- punishing what the human does (reads from gomi_reads) --------------

    def chase_target(self, s):
        """Where they'll end up (x) and the frame of it to grab at; None when there's nothing to chase."""
        away_from_her = -(sign(s.x - s.opp_x) or s.facing)

        def rolling(direction, grab_at):
            left = ROLL * max(0.0, 1 - s.opp_frame / (grab_at + 8))   # of the roll still to go
            return s.opp_x + direction * left, grab_at

        if s.opp_motion in TECHS_NOW:
            rolls = {0xC7: 0, 0xC8: s.opp_facing, 0xC9: -s.opp_facing}[s.opp_motion]
            return rolling(rolls, TECHS_NOW[s.opp_motion])
        if s.opp_motion in GETTING_UP:
            rolls = {0xBA: 0, 0xC2: 0, 0xBC: s.opp_facing, 0xC4: s.opp_facing}.get(s.opp_motion, -s.opp_facing)
            return rolling(rolls, GETTING_UP[s.opp_motion])
        if s.opp_motion in LEDGE_ROLLS:
            return sign(s.opp_x) * (s.edge - 38), 32
        if s.opp_motion in LYING:
            guess = self.read("getup")
            rolls = {"toward": -away_from_her, "away": away_from_her}.get(guess, 0)
            return s.opp_x + rolls * ROLL, None
        falling = s.opp_motion == TUMBLING or s.opp_motion in DAMAGE
        if falling and s.opp_air and s.opp_y < 15 and not s.opp_offstage and self.read("tech"):
            rolls = {"toward": -away_from_her, "away": away_from_her}.get(self.read("tech"), 0)
            return s.opp_x + rolls * ROLL, None       # about to land: be where they usually tech
        return None

    def tech_chase(self, s):
        """Be where they'll end up, and grab them as they get there."""
        if s.dist > 70:
            return None
        got = self.chase_target(s)
        if got is None:
            return None
        target, grab_at = got
        target = max(-s.edge + 5, min(s.edge - 5, target))
        if abs(target - s.x) > 10:
            self.mode = "chase"
            return self.dash(sign(target - s.x), 2)
        turn = self.turn(s)
        if turn:
            return turn
        self.mode = "chase"
        if s.opp_motion in LYING and s.dist < 14:
            return self.hit(pad(A, sy=-45), 14)        # down-tilt them where they lie
        if grab_at is not None and s.opp_frame >= grab_at and s.dist < 14:
            return self.hit(pad(Z), 25)                # grab as the roll or getup ends
        return NEUTRAL

    def ledge_trap(self, s):
        """They're on the ledge: wait where their usual option ends. None: nothing to do."""
        if s.opp_motion not in LEDGE:
            return None
        guess = self.read("ledge")
        spot = {"stand": 28, "attack": 28, "roll": 45, "jump": 18}.get(guess)
        if spot is None:
            return None                               # drops (or nothing known): the usual edgeguard
        side = sign(s.opp_x) or s.facing
        target = side * (s.edge - spot)
        if abs(target - s.x) > 6:
            return self.dash(sign(target - s.x), 2)
        if s.facing != side:
            self.run(NEUTRAL)
            return pad(sx=side * 35)
        return NEUTRAL

    def jumped_at(self, s):
        """They jumped right over her: an up-smash."""
        if s.opp_air and 8 < s.dy < 35 and s.dist < 14 and not self.cooldown and self.read("ledge") == "jump":
            self.cooldown = 30
            return self.hit(pad(cy=80), 30)
        return None

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

    # ---- options: her menu, and each option's inputs ----------------------

    OPTIONS = frozenset(OPTION_RANGES) - {"shine", "crouch_shine"}   # what he can do (Fox adds the shine)

    def bucket(self, s):
        """(range, their state, her spot): the situation her options are scored in."""
        rng = "close" if s.dist < 20 else "mid" if s.dist < 45 else "far"
        if s.opp_motion in SHIELDING:
            them = "shield"
        elif s.opp_motion in BUSY:
            them = "busy"
        elif s.opp_air:
            them = "air"
        else:
            them = "grounded"
        spot = "edge" if sign(s.x) == -s.toward and abs(s.x) > s.edge - 25 else "center"
        return rng, them, spot

    def menu(self, s, plan, bucket):
        """The options `plan` offers here."""
        rng, them, _ = bucket
        near = self.knobs["space_dist"]
        out = []
        for name in MENUS[plan]:
            if name not in self.OPTIONS or rng not in OPTION_RANGES[name]:
                continue
            if plan == "space" and (name == "dash_attack" and them != "busy"
                                    or name == "walk_in" and s.dist < near + 20
                                    or name == "retreat" and s.dist >= near):
                continue
            if plan == "zone" and name == "dash_in" and s.dist < 90:
                continue
            out.append(name)
        return out

    def pick_option(self, s, plan):
        """Free to act in `plan`: face them if an option needs it, then try an option."""
        bucket = self.bucket(s)
        menu = self.menu(s, plan, bucket)
        if not menu:
            return self.dash(s.toward, 4) if s.dist >= 45 else NEUTRAL
        if bucket[0] == "close":
            turn = self.turn(s)
            if turn:
                return turn
        name = self.chooser(bucket, menu) if self.chooser else self.rng.choice(menu)
        self.option = name
        return getattr(self, "opt_" + name)(s)

    def plan_approach(self, s):
        return self.pick_option(s, "approach")

    def plan_space(self, s):
        return self.pick_option(s, "space")

    def plan_pressure(self, s):
        return self.pick_option(s, "pressure")

    def plan_defend(self, s):
        return self.pick_option(s, "defend" if s.dist <= 30 else "space")

    def plan_zone(self, s):
        return self.pick_option(s, "zone")

    plan_fireball = plan_zone
    plan_lasers = plan_zone

    def opt_dash_in(self, s):
        if 25 < s.dist < 70 and self.knows("wavedash"):
            return self.start(self.wavedash(s.toward))
        return self.dash(s.toward, 6)

    def opt_walk_in(self, s):
        self.run(*[pad(sx=s.toward * 50)] * 9)
        return pad(sx=s.toward * 50)

    def opt_retreat(self, s):
        return self.retreat(s, 6)

    def opt_wait(self, s):
        self.run(*[pad() for _ in range(14)])            # stand there: bait a whiff (not cut short: not NEUTRAL)
        return pad()

    def opt_shield(self, s):
        if s.shield < 25:
            return self.opt_retreat(s)
        shield = [pad(R, r=140)] * 12
        if s.dist < 18 and self.rng.random() < 0.5:
            self.run(*shield[1:], pad(R | A, r=140), *[NEUTRAL] * 25)   # shield, grab out of it
        else:
            self.run(*shield[1:], *[NEUTRAL] * 3)
        return shield[0]

    def opt_grab(self, s):
        if s.dist > 12:
            self.run(pad(sx=s.toward * 80), pad(Z, sx=s.toward * 80), *[NEUTRAL] * 30)
            return pad(sx=s.toward * 80)                 # dash in, grab
        return self.hit(pad(Z), 25)

    def opt_dtilt(self, s):
        return self.hit(pad(A, sy=-45), 12)

    def opt_jab(self, s):
        self.run(NEUTRAL, pad(A), NEUTRAL, pad(A))
        return self.hit(pad(A), 10)

    def opt_smash(self, s):
        if s.opp_air or s.dy > 8:
            return self.hit(pad(cy=80), 30)              # up-smash
        return self.hit(pad(cx=s.toward * 80), 35)       # forward smash

    def opt_dash_attack(self, s):
        self.run(*[pad(sx=s.toward * 80)] * 5, pad(A, sx=s.toward * 80), *[NEUTRAL] * 25)
        return pad(sx=s.toward * 80)

    def aerial_for(self, s):
        """The aerial to throw at them: up-air above, else forward (or back) air toward them."""
        return pad(cy=80) if s.dy > 12 else pad(cx=s.toward * 80)

    def opt_sh_aerial(self, s):
        hop = self.shffl if self.knows("shffl") else self.short_hop
        return self.start(hop(self.aerial_for(s), drift=s.toward * 40))

    def opt_fullhop_aerial(self, s):
        return self.start(self.hop_aerial(s.toward))

    def opt_zone(self, s):
        if self.cooldown:
            self.run(*[NEUTRAL] * 4)
            return NEUTRAL
        self.cooldown = 35
        if self.rng.random() < 0.5:
            return self.hit(pad(B), 30)                  # fireball from the ground
        self.run(NEUTRAL, NEUTRAL, NEUTRAL, pad(B), *[NEUTRAL] * 28)
        return pad(X)                                    # short hop (X released in jumpsquat), fireball

    def hop_aerial(self, direction):
        """Full hop toward them, the aerial when they're in reach (or at the top), drift down."""
        s = yield pad(X, sx=direction * 60)
        swung = False
        for i in range(70):
            if i > 4 and not s.air:
                return
            if not swung and s.air and (s.dist < 16 and abs(s.dy) < 18 or s.vy < 0.3 and i > 8):
                swung = True
                s = yield self.aerial_for(s)
                continue
            s = yield pad(X if i < 4 else 0, sx=s.toward * 60)

    def plan_edgeguard(self, s):
        trap = self.ledge_trap(s) or self.jumped_at(s)
        if trap is not None:
            return trap
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
                self.used["waveland"] += 1
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
