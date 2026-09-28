"""Phillip's view of the game, rebuilt from the bridge's State.

Everything here mirrors vladfi1/phillip (MIT) as of its last commit,
without importing it or TensorFlow:

- PlayerObs is ssbm.PlayerMemory: the fields Phillip's memory watcher read,
  with the same sources (see NOTES.md / PLAN.md for the mapping).
- The action sets are ssbm.actionTypes; a SimpleController is a button plus
  a main stick position in [0, 1]^2.
- dolphin_pad() turns a controller into the PADStatus the game would have
  seen through Dolphin's pipe device, exactly as Phillip's Dolphin did it.
"""

import itertools
import math
import struct
from dataclasses import dataclass, field

import bridge

# ---------------------------------------------------------------- observation

# Phillip's state.Character order is the CSS icon order (mncharsel.c's icon
# table), and the byte it read was the CSS door's sel_icon. From CKind:
CSS_ICON_BY_CKIND = {
    0x00: 7,   # Captain Falcon
    0x01: 6,   # Donkey Kong
    0x02: 10,  # Fox
    0x03: 22,  # Mr. Game & Watch
    0x04: 13,  # Kirby
    0x05: 3,   # Bowser
    0x06: 16,  # Link
    0x07: 2,   # Luigi
    0x08: 1,   # Mario
    0x09: 23,  # Marth
    0x0A: 21,  # Mewtwo
    0x0B: 11,  # Ness
    0x0C: 4,   # Peach
    0x0D: 19,  # Pikachu
    0x0E: 12,  # Ice Climbers
    0x0F: 20,  # Jigglypuff
    0x10: 14,  # Samus
    0x11: 5,   # Yoshi
    0x12: 15,  # Zelda
    0x13: 15,  # Sheik: picked on the Zelda icon
    0x14: 9,   # Falco
    0x15: 17,  # Young Link
    0x16: 0,   # Dr. Mario
    0x17: 24,  # Roy
    0x18: 18,  # Pichu
    0x19: 8,   # Ganondorf
}

# Phillip's character names (menu_manager.characters / params "char") -> CKind.
CKIND_BY_PHILLIP_NAME = {
    "fox": 0x02, "falco": 0x14, "falcon": 0x00, "roy": 0x17, "marth": 0x09, "zelda": 0x12,
    "sheik": 0x13, "mewtwo": 0x0A, "luigi": 0x07, "puff": 0x0F, "kirby": 0x04, "peach": 0x0C,
    "ganon": 0x19, "samus": 0x10, "bowser": 0x05, "yoshi": 0x11, "dk": 0x01,
}
# Phillip's stage names (movie.stages) -> StKind.
STKIND_BY_PHILLIP_NAME = {"battlefield": 0x1F, "final_destination": 0x20}


def _f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


@dataclass
class PlayerObs:
    """ssbm.PlayerMemory, the fields Phillip embeds (in its embedding order)."""

    percent: int = 0
    facing: float = 0.0
    x: float = 0.0
    y: float = 0.0
    action_state: int = 0
    action_frame: float = 0.0
    character: int = 0
    invulnerable: bool = False
    hitlag_frames_left: float = 0.0
    hitstun_frames_left: float = 0.0
    jumps_used: int = 0
    charging_smash: bool = False
    shield_size: float = 0.0
    in_air: bool = False
    speed_air_x_self: float = 0.0
    speed_ground_x_self: float = 0.0
    speed_y_self: float = 0.0
    speed_x_attack: float = 0.0
    speed_y_attack: float = 0.0


FLOAT_FIELDS = ("facing", "x", "y", "action_frame", "hitlag_frames_left", "hitstun_frames_left",
                "shield_size", "speed_air_x_self", "speed_ground_x_self", "speed_y_self",
                "speed_x_attack", "speed_y_attack")


def player_obs(f, prev=None):
    """PlayerObs from a bridge Fighter.

    Phillip's float handler rejected non-finite reads and kept the previous
    value (state_manager.FloatHandler); `prev` is that previous PlayerObs.
    """
    o = PlayerObs(
        percent=max(f.percent, 0) & 0xFFFF,  # StaticPlayer+0x60, read as an unsigned short
        facing=f.facing,
        x=f.pos_x,
        y=f.pos_y,
        action_state=f.motion_id,
        action_frame=f.action_frame,
        character=CSS_ICON_BY_CKIND.get(f.ckind, 0),
        invulnerable=f.body_state != 0,
        hitlag_frames_left=f.hitlag,
        hitstun_frames_left=f.mv0_float,
        jumps_used=f.jumps_used,
        charging_smash=bool(f.smash_state & 2),
        shield_size=f.shield,
        in_air=f.in_air,
        speed_air_x_self=f.self_vx,
        speed_ground_x_self=f.ground_vx,
        speed_y_self=f.self_vy,
        speed_x_attack=f.kb_vx,
        speed_y_attack=f.kb_vy,
    )
    for name in FLOAT_FIELDS:
        if not math.isfinite(getattr(o, name)):
            setattr(o, name, getattr(prev, name) if prev is not None else 0.0)
    return o


# ------------------------------------------------------------------- actions

NEUTRAL_STICK = (0.5, 0.5)
BUTTONS = ("NONE", "A", "B", "Z", "Y", "L")  # ssbm.SimpleButton


@dataclass(frozen=True)
class SimpleController:
    """ssbm.SimpleController: one button (or NONE) and the main stick in [0, 1]^2."""

    button: str = "NONE"
    stick: tuple = NEUTRAL_STICK
    duration: int = None

    def banned(self, player, char):
        """ssbm.SimpleController.banned, verbatim."""
        if char == "peach" and self.button == "B" and self.stick == NEUTRAL_STICK:
            return True
        if char in ("sheik", "zelda") and self.button == "B" and self.stick[1] < 0.5:
            return True
        if char in ("fox", "falco") and self.button == "B":
            if self.stick[0] > 0.5 and player.x > 0:
                return True
            if self.stick[0] < 0.5 and player.x < 0:
                return True
            if self.stick[1] < 0.5:
                if abs(player.x) > 100 or player.y < -5:
                    return True
        if char == "puff":
            if (player.jumps_used >= 6 and self.button == "B" and self.stick[0] != 0.5
                    and player.y < -5):
                return True
        return False


NEUTRAL = SimpleController()


class Repeat:
    """ssbm.RepeatController: sends nothing, the pad keeps its last state."""

    duration = None

    def __repr__(self):
        return "Repeat()"


REPEAT = Repeat()


def _controllers(pairs):
    return [SimpleController(b, tuple(float(v) for v in s)) for b, s in pairs]


def _polar(theta, r=1.0):
    r /= 2.0
    return (0.5 + r * math.cos(theta), 0.5 + r * math.sin(theta))


_axis = [0.0, 0.5, 1.0]  # np.linspace(0, 1, 3)
DIAGONAL_STICKS = list(itertools.product(_axis, repeat=2))
OLD_STICKS = [(0.5, 0.5), (0.5, 1), (0.5, 0), (0, 0.5), (1, 0.5)]
CARDINAL_STICKS = [(0, 0.5), (1, 0.5), (0.5, 0), (0.5, 1), (0.5, 0.5)]
TILT_STICKS = [(0.4, 0.5), (0.6, 0.5), (0.5, 0.4), (0.5, 0.6)]

_custom = _controllers(itertools.chain(
    itertools.product(["A", "B"], CARDINAL_STICKS),
    itertools.product(["A"], TILT_STICKS),
    itertools.product(["NONE", "L"], DIAGONAL_STICKS),
    itertools.product(["Z", "Y"], [NEUTRAL_STICK]),
)) + [REPEAT]

_short_hop_chain = [SimpleController("Y", NEUTRAL_STICK, 2), NEUTRAL]
_jc_chain = [SimpleController("Y", NEUTRAL_STICK, 1), SimpleController("Z")]
_sh2_chain = [SimpleController("NONE", NEUTRAL_STICK, 2), SimpleController("Y")]
_fox_wd_chain_left = [SimpleController("Y", NEUTRAL_STICK, 3),
                      SimpleController("L", _polar(-7 / 8 * math.pi))]
_wd_left = SimpleController("L", _polar(-7 / 8 * math.pi))
_wd_right = SimpleController("L", _polar(-1 / 8 * math.pi))

ACTION_TYPES = {
    "old": _controllers(itertools.product(BUTTONS, OLD_STICKS)),
    "cardinal": _controllers(itertools.product(BUTTONS, CARDINAL_STICKS)),
    "diagonal": _controllers(itertools.product(BUTTONS, DIAGONAL_STICKS)),
    "custom": _custom,
    "short_hop_test": [NEUTRAL] * 10 + [_short_hop_chain],
    "custom_sh_jc": _custom + [_short_hop_chain, _jc_chain],
    "fox_wd_test": [NEUTRAL] * 10 + [_fox_wd_chain_left],
    "custom_sh2_wd": _custom + [_sh2_chain, _wd_left, _wd_right],
}


def action_chain(action_type, index, act_every):
    """ssbm.ActionChain: the controller to send on each of the next act_every frames."""
    entry = ACTION_TYPES[action_type][index]
    actions = entry if isinstance(entry, list) else [entry]
    out = []
    for a in actions:
        if a.duration:
            out += [a] * a.duration
        else:
            out += [a] * (act_every - len(out))
    if len(out) != act_every:
        raise ValueError(f"action {index} of {action_type} does not fill {act_every} frames")
    return out


# ------------------------------------------------ controller -> game pad

# GCPadStatus constants Dolphin uses (InputCommon/GCPadStatus.h).
STICK_CENTER = 0x80
STICK_RADIUS = 0x7F


def _pipe_axis(v):
    """Dolphin's pipe device: 'SET MAIN x y' is sent as '%.2f' and split into
    the + and - half-axes (Pipes.cpp SetAxis), which the AnalogStick then
    recombines. Returns the recombined axis in [-1, 1]."""
    v = float(f"{_f32(v):.2f}")  # pad.py writes the c_float stick with '{:.2f}'
    v = min(max(v, 0.0), 1.0)
    hi = max(0.0, v - 0.5) * 2.0
    lo = max(0.0, 0.5 - v) * 2.0
    return hi - lo


def _analog_stick(x, y):
    """ControllerEmu AnalogStick::GetState with Phillip's pipe config (dead
    zone 0, radius 100%, no modifier): no circular clamp, each axis clamped."""
    ang = math.atan2(y, x)
    dist = math.sqrt(x * x + y * y)
    return (max(-1.0, min(1.0, math.cos(ang) * dist)), max(-1.0, min(1.0, math.sin(ang) * dist)))


def _stick_byte(v):
    """GCPad::GetInput: u8 = static_cast<u8>(center + v * radius), then the
    SDK subtracts the origin: the game sees an s8."""
    u8 = int(STICK_CENTER + v * STICK_RADIUS)  # C cast truncates toward zero
    return u8 - STICK_CENTER


def stick_to_pad(sx, sy):
    x, y = _analog_stick(_pipe_axis(sx), _pipe_axis(sy))
    return _stick_byte(x), _stick_byte(y)


_BUTTON_BITS = {"A": bridge.BUTTON_A, "B": bridge.BUTTON_B, "Z": bridge.BUTTON_Z,
                "Y": bridge.BUTTON_Y, "L": bridge.BUTTON_L}


def dolphin_pad(controller):
    """The PADStatus Phillip's Dolphin produced for a SimpleController.

    Pad.send_controller presses the one button and releases the others,
    tilts the main stick and leaves the C-stick at 0.5. The pipe config maps
    only digital L/R; Dolphin's MixedTriggers reports a full analog value
    for a pressed digital trigger, so L comes with trigger 255 (the game
    clamps it to 140). Analog A/B follow the buttons, as on a real pad.
    """
    sx, sy = stick_to_pad(*controller.stick)
    button = _BUTTON_BITS.get(controller.button, 0)
    return bridge.Pad(button=button, stick_x=sx, stick_y=sy, cstick_x=0, cstick_y=0,
                      trigger_l=255 if button & bridge.BUTTON_L else 0, trigger_r=0,
                      analog_a=255 if button & bridge.BUTTON_A else 0,
                      analog_b=255 if button & bridge.BUTTON_B else 0)


def pipe_commands(controller):
    """The exact text Phillip's pad.Pad.send_controller wrote for a controller
    (buttons in pad.Button order, then both sticks), for comparing streams."""
    lines = []
    for name in ("A", "B", "X", "Y", "Z", "START", "L", "R"):
        # RealControllerState has no button_START field set by SimpleController,
        # but hasattr() is true (reset() sets it False): START is released too.
        pressed = controller.button == name
        lines.append(("PRESS " if pressed else "RELEASE ") + name)
    lines.append("SET MAIN {:.2f} {:.2f}".format(*(_f32(v) for v in controller.stick)))
    lines.append("SET C {:.2f} {:.2f}".format(0.5, 0.5))
    return lines
