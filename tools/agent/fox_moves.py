"""Fox's move library, for Gomihyu's game plans: mario_moves.py with Fox's moves.

Everything that doesn't depend on the character -- reading the situation,
teching, DI, getups, ledge options, grab mashing, never running off the
stage -- comes from mario_moves.Mario. Fox has his own plans (lasers instead
of fireballs, the shine up close) and his own recovery: Illusion (side-B)
when he's level with the ledge and not far out, else Firefox (up-B), aimed at
the ledge for its whole charge.
"""

import math

import mario_moves as base
from mario_moves import NEUTRAL, A, B, R, X, Z, pad, sign, situation  # noqa: F401  (situation: same reading)

FOX = 0x02  # CKind
NAME = "Fox"
CKIND = FOX
ZONE = "lasers"

PLANS = {
    "approach": "dash in and grab, down-tilt or shine them",
    "lasers": "stay at range and pepper them with short-hop lasers",
    "space": "stay just out of their reach and punish them with a dash attack when they whiff",
    "pressure": "up-airs and up-smash when they're above you or close, shine them up close",
    "defend": "shield, then shine or grab out of it; roll away when your shield is low",
    "edgeguard": "when they're offstage: stand at the ledge and shine or laser them",
    "platform": "get on a platform above them and drop onto them with down-airs (not on Final Destination)",
}

FIREFOX_CHARGE = 45  # frames Fox holds the stick while Firefox charges (it launches at the end)


def shine(then=()):
    """Down-B, jump-cancelled a few frames in."""
    return [pad(B, sy=-80), NEUTRAL, NEUTRAL, pad(X), NEUTRAL, NEUTRAL, *then]


def multishine(n):
    """n shines, each jump-cancelled on its 4th frame and the jumpsquat shined out of."""
    return [pad(B, sy=-80), NEUTRAL, NEUTRAL, pad(X)] * n + [NEUTRAL] * 6


class Fox(base.Mario):
    ZONE = ZONE
    SIDE_B_REACH = 55      # side-B (Illusion) when level with the ledge and at most this far out
    UP_B_FROM = 25         # up-B (Firefox) once this far out, or below the stage

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
        if self.upb_used or s.vy > 0:
            return pad(sx=home * 80)
        if -12 < s.y < 20 and beyond < (self.SIDE_B_REACH - 15 if early else self.SIDE_B_REACH):
            self.upb_used = True
            self.run(*[NEUTRAL] * 20)
            return pad(B, sx=home * 80)                  # Illusion, straight at the stage
        if s.y < (10 if early else 0) or beyond > (self.UP_B_FROM - 10 if early else self.UP_B_FROM):
            self.upb_used = True
            ledge_x = sign(s.x) * s.edge
            dx, dy = ledge_x - s.x, 6 - s.y
            n = math.hypot(dx, dy) or 1.0
            aim = pad(sx=80 * dx / n, sy=80 * dy / n)
            self.run(*[aim] * FIREFOX_CHARGE)
            return pad(B, sy=80)                         # Firefox, aimed at the ledge
        return pad(sx=home * 80)

    # ---- his options (mario_moves has the menus and the rest) --------------

    OPTIONS = frozenset(base.OPTION_RANGES)       # everything, the shine included

    def aerial_for(self, s):
        """Up-air above them, down-air when they're below, else neutral-air."""
        if s.dy > 12:
            return pad(cy=80)
        return pad(cy=-80) if self.rng.random() < 0.4 else pad(A)

    def opt_zone(self, s):
        if self.cooldown:
            self.run(*[NEUTRAL] * 4)
            return NEUTRAL
        self.cooldown = 22
        if self.rng.random() < 0.3:
            return self.hit(pad(B), 15)                  # laser from the ground
        self.run(NEUTRAL, NEUTRAL, pad(B), NEUTRAL, NEUTRAL, *[pad(sy=-80)] * 6, *[NEUTRAL] * 6)
        return pad(X)                                    # short hop, laser, fast fall

    def opt_smash(self, s):
        return self.hit(pad(cy=80), 30)                  # up-smash: his kill move

    def opt_shine(self, s):
        if self.knows("multishine"):
            self.used["multishine"] += 1
            shines = multishine(self.rng.randint(2, 4))
            self.run(*shines[1:])
            return shines[0]
        if self.rng.random() < 0.5:
            self.run(*shine([pad(cy=80), *[NEUTRAL] * 16])[1:])
            return pad(B, sy=-80)                        # shine, jump out of it into an up-air
        self.run(*shine()[1:], *[NEUTRAL] * 4)
        return pad(B, sy=-80)                            # shine

    def opt_shield(self, s):
        if s.shield < 25:
            return self.opt_retreat(s)
        shield = [pad(R, r=140)] * 10
        pick = self.rng.random()
        if s.dist < 18 and pick < 0.4:
            self.run(*shield[1:], pad(R | A, r=140), *[NEUTRAL] * 25)                   # shield grab
        elif s.dist < 18 and pick < 0.7:
            self.run(*shield[1:], pad(X), NEUTRAL, pad(B, sy=-80), *[NEUTRAL] * 12)    # shine out of shield
        else:
            self.run(*shield[1:], *[NEUTRAL] * 3)
        return shield[0]

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
        self.cooldown = 25
        if s.dist < 25 and s.opp_y > -25:
            return self.hit(pad(B, sy=-80), 20)          # shine them away from the ledge
        return self.hit(pad(B), 15)                      # laser them

    def air(self, s, plan):
        if s.dist < 18 and s.dy < -6 and not self.cooldown:
            self.cooldown = 20
            return self.hit(pad(cy=-80), 12)             # down-air onto them
        return super().air(s, plan)


Player = Fox
