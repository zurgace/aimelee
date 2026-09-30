"""Falco's move library, for Gomihyu's game plans: fox_moves.py with Falco's moves.

Falco is Fox's clone with a different game: his lasers come out of a short
hop and stop approaches, his shine pops them up for a follow-up (shine, jump,
down-air or up-air: a "pillar"), and his down-air spikes. His recovery is
shorter: Phantasm (side-B) only from close in at ledge height, else Fire Bird
(up-B) aimed at the ledge, started sooner. The distances are guesses to check
in game, like Fox's.

What she knows of Falco before playing him comes from Slippi replays of the
Falco players she imitates (gomi_replays.py).
"""

import fox_moves
from mario_moves import NEUTRAL, A, B, X, pad, sign, situation  # noqa: F401  (situation: same reading)

FALCO = 0x14  # CKind
NAME = "Falco"
CKIND = FALCO
ZONE = "lasers"

PLANS = {
    "approach": "dash in and grab, down-tilt, shine or short-hop down-air them",
    "lasers": "short-hop lasers at range: they stop approaches and set up your pressure",
    "space": "stay just out of their reach and punish them when they whiff",
    "pressure": "pillar them: shine, then a down-air or up-air, and again; up-smash when they're above you",
    "defend": "shield, then shine or grab out of it; roll away when your shield is low",
    "edgeguard": "when they're offstage: laser them, or down-air spike them off the ledge",
    "platform": "get on a platform above them and drop onto them with down-airs (not on Final Destination)",
}


class Falco(fox_moves.Fox):
    ZONE = ZONE
    SIDE_B_REACH = 40      # Phantasm is shorter than Illusion
    UP_B_FROM = 18         # and Fire Bird doesn't go as far: start it sooner

    def aerial_for(self, s):
        """Down-air (his spike) when they're below, up-air above, else neutral-air."""
        if s.dy > 12:
            return pad(cy=80)
        if s.dy < -4 or self.rng.random() < 0.5:
            return pad(cy=-80)
        return pad(A)

    def opt_shine(self, s):
        """A pillar: shine pops them up, jump out of it, down-air or up-air them."""
        if self.knows("multishine") and self.rng.random() < 0.3:
            return super().opt_shine(s)
        follow = pad(cy=80) if self.rng.random() < 0.4 else pad(cy=-80)
        self.run(*fox_moves.shine([follow, *[NEUTRAL] * 18])[1:])
        return pad(B, sy=-80)

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
        if s.dist < 30 and -40 < s.opp_y < 5:
            # Run off the ledge with a down-air: his spike. The rest of the recovery handles getting back.
            self.run(*[pad(sx=side * 80)] * 3, pad(cy=-80), *[NEUTRAL] * 10)
            return pad(sx=side * 80)
        return self.hit(pad(B), 15)                      # laser them


Player = Falco
