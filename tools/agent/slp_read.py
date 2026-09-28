"""Minimal reader for the .slp files src/pc/slp.c writes (Slippi replay 3.18).

Only what the bridge checks compare against: the pre-frame and post-frame
events, per frame and port. Offsets follow src/pc/slp_format.c (event byte 0
is the command; payload offset = event offset - 1). Big-endian throughout.
"""

import struct
from dataclasses import dataclass

CMD_EVENT_PAYLOADS = 0x35
CMD_PRE_FRAME = 0x37
CMD_POST_FRAME = 0x38


@dataclass
class PreFrame:
    frame: int
    port: int
    follower: int
    action: int
    x: float
    y: float
    facing: float
    buttons: int
    phys_buttons: int
    phys_l: float
    phys_r: float
    raw_x: int
    raw_y: int
    raw_cx: int
    raw_cy: int
    percent: float


@dataclass
class PostFrame:
    frame: int
    port: int
    follower: int
    character: int
    action: int
    x: float
    y: float
    facing: float
    percent: float
    shield: float
    stocks: int
    action_frame: float
    misc_as: int
    airborne: int
    jumps: int
    hurtbox: int
    self_air_x: float
    self_y: float
    attack_x: float
    attack_y: float
    self_ground_x: float
    hitlag: float


def _s8(b):
    return b - 256 if b > 127 else b


def parse_pre(p):
    frame, port, follower, _seed, action = struct.unpack_from(">iBBIH", p, 0)
    x, y, facing = struct.unpack_from(">fff", p, 0xC)
    buttons, phys_buttons = struct.unpack_from(">IH", p, 0x2C)
    phys_l, phys_r = struct.unpack_from(">ff", p, 0x32)
    percent = struct.unpack_from(">f", p, 0x3B)[0]
    return PreFrame(frame, port, follower, action, x, y, facing, buttons, phys_buttons, phys_l,
                    phys_r, _s8(p[0x3A]), _s8(p[0x3F]), _s8(p[0x40]), _s8(p[0x41]), percent)


def parse_post(p):
    frame, port, follower, character, action = struct.unpack_from(">iBBBH", p, 0)
    x, y, facing, percent, shield = struct.unpack_from(">fffff", p, 0x9)
    stocks = p[0x20]
    action_frame = struct.unpack_from(">f", p, 0x21)[0]
    misc_as = struct.unpack_from(">I", p, 0x2A)[0]
    airborne = p[0x2E]
    jumps = p[0x31]
    hurtbox = p[0x33]
    self_air_x, self_y, attack_x, attack_y, self_ground_x, hitlag = struct.unpack_from(">ffffff", p, 0x34)
    return PostFrame(frame, port, follower, character, action, x, y, facing, percent, shield, stocks,
                     action_frame, misc_as, airborne, jumps, hurtbox, self_air_x, self_y, attack_x,
                     attack_y, self_ground_x, hitlag)


def raw_events(data):
    """Yield (command, payload bytes) from the raw element of a .slp file."""
    key = b"raw[$U#l"
    at = data.find(key)
    if at < 0:
        raise ValueError("no raw element")
    length = struct.unpack_from(">I", data, at + len(key))[0]
    raw = data[at + len(key) + 4:]
    if length:
        raw = raw[:length]
    if not raw or raw[0] != CMD_EVENT_PAYLOADS:
        raise ValueError("raw element does not start with Event Payloads")
    n = raw[1]
    sizes = {CMD_EVENT_PAYLOADS: n}
    for i in range(2, 1 + n, 3):
        sizes[raw[i]] = struct.unpack_from(">H", raw, i + 1)[0]
    i = 0
    while i < len(raw):
        cmd = raw[i]
        size = sizes.get(cmd)
        if size is None or i + 1 + size > len(raw):
            break  # an unknown command or a cut-off tail: stop
        yield cmd, raw[i + 1:i + 1 + size]
        i += 1 + size


def read_frames(path):
    """{frame: {port: (PreFrame or None, PostFrame or None)}} for the leader fighters."""
    with open(path, "rb") as f:
        data = f.read()
    frames = {}
    for cmd, p in raw_events(data):
        if cmd == CMD_PRE_FRAME:
            ev = parse_pre(p)
        elif cmd == CMD_POST_FRAME:
            ev = parse_post(p)
        else:
            continue
        if ev.follower:
            continue
        slot = frames.setdefault(ev.frame, {}).setdefault(ev.port, [None, None])
        slot[0 if cmd == CMD_PRE_FRAME else 1] = ev
    return frames


def frame_events(path):
    """The pre/post frame events as raw bytes in file order, for byte-level diffs."""
    with open(path, "rb") as f:
        data = f.read()
    return [(cmd, p) for cmd, p in raw_events(data) if cmd in (CMD_PRE_FRAME, CMD_POST_FRAME)]
