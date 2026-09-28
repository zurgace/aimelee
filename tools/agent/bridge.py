"""Client side of the melee-pc agent bridge (src/pc/agent_proto.h).

The game listens on MELEE_AGENT_SOCKET; this connects, reads the HELLO, then
yields one State after every simulation tick and sends Input back for the
agent's port. Standard library only.

    with BridgeClient("/tmp/melee-agent.sock") as bridge:
        for state in bridge.states():
            bridge.send_input(state.tick + 1, Pad.neutral())
"""

import os
import socket
import struct
import time
from dataclasses import dataclass, field, fields

PROTO_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)  # v2 adds MSG_SLP_EVENTS; the other layouts are unchanged
MAGIC = 0x4247414D

MSG_HELLO = 1
MSG_STATE = 2
MSG_INPUT = 3
MSG_SLP_EVENTS = 4  # raw Slippi replay events of a frame (v2), before that tick's STATE

SYNC_LOCKSTEP = 0
SYNC_ASYNC = 1

MAX_PORTS = 4

# PADStatus.button bits (extern/aurora/include/dolphin/pad.h)
BUTTON_LEFT = 0x0001
BUTTON_RIGHT = 0x0002
BUTTON_DOWN = 0x0004
BUTTON_UP = 0x0008
BUTTON_Z = 0x0010
BUTTON_R = 0x0020
BUTTON_L = 0x0040
BUTTON_A = 0x0100
BUTTON_B = 0x0200
BUTTON_X = 0x0400
BUTTON_Y = 0x0800
BUTTON_START = 0x1000

# AgentFighter.flags
FT_ASLEEP = 1 << 0
FT_IN_AIR = 1 << 1
FT_IN_HITSTUN = 1 << 2
FT_IN_HITLAG = 1 << 3

# AgentState.flags
ST_IN_FIGHT = 1 << 0
ST_FIGHTERS_RAN = 1 << 1
ST_MATCH_START = 1 << 2
ST_AGENT_INPUT = 1 << 3
ST_INPUT_LATE = 1 << 4
ST_PAD_FRESH = 1 << 5

# AgentInput.flags
IN_RELEASE = 1 << 0

HEADER = struct.Struct("<IHH")
PAD = struct.Struct("<HbbbbBBBBbB")
HELLO = struct.Struct("<HBBHHI32s")
FIGHTER = struct.Struct("<BBBBbBBBhHI8fI6fiiI" + PAD.format[1:])
STATE_HEAD = struct.Struct("<IIIIBBBBHBBII")
INPUT_HEAD = struct.Struct("<IBBH")
STATE_SIZE = STATE_HEAD.size + MAX_PORTS * FIGHTER.size

assert HEADER.size == 8 and PAD.size == 12 and HELLO.size == 44
assert FIGHTER.size == 100 and STATE_SIZE == 432 and INPUT_HEAD.size + PAD.size == 20


@dataclass
class Pad:
    """A raw PADStatus: sticks centred at 0 (the game clamps to radius 80)."""

    button: int = 0
    stick_x: int = 0
    stick_y: int = 0
    cstick_x: int = 0
    cstick_y: int = 0
    trigger_l: int = 0
    trigger_r: int = 0
    analog_a: int = 0
    analog_b: int = 0
    err: int = 0

    @classmethod
    def neutral(cls):
        return cls()

    def pack(self):
        return PAD.pack(self.button, self.stick_x, self.stick_y, self.cstick_x, self.cstick_y,
                        self.trigger_l, self.trigger_r, self.analog_a, self.analog_b, self.err, 0)

    @classmethod
    def unpack(cls, values):
        return cls(*values[:10])


@dataclass
class Fighter:
    present: bool
    slot_type: int
    ckind: int
    fkind: int
    stocks: int
    flags: int
    jumps_used: int
    max_jumps: int
    percent: int
    motion_id: int
    percent_f: float
    facing: float
    pos_x: float
    pos_y: float
    cur_x: float
    cur_y: float
    action_frame: float
    hitlag: float
    mv0_bits: int
    shield: float
    self_vx: float
    self_vy: float
    kb_vx: float
    kb_vy: float
    ground_vx: float
    body_state: int
    body_state_move: int
    smash_state: int
    input: Pad = field(default_factory=Pad)

    @property
    def mv0_float(self):
        """fp+2340 as a float: hitstun frames left while in a damage state."""
        return struct.unpack("<f", struct.pack("<I", self.mv0_bits))[0]

    @property
    def in_air(self):
        return bool(self.flags & FT_IN_AIR)

    @property
    def asleep(self):
        return bool(self.flags & FT_ASLEEP)

    @classmethod
    def unpack(cls, buf, offset):
        v = FIGHTER.unpack_from(buf, offset)
        head = list(v[:9]) + list(v[10:29])  # skip the reserved u16 after percent
        head[0] = bool(head[0])
        return cls(*head, input=Pad.unpack(v[29:]))


@dataclass
class State:
    tick: int
    match_tick: int
    scene_frame: int
    vs_frame: int
    scene_kind: int
    game_mode: int
    flags: int
    match_result: int
    stage: int
    agent_port: int
    late_inputs: int
    dropped_states: int
    fighters: list

    @property
    def in_fight(self):
        return bool(self.flags & ST_IN_FIGHT)

    @property
    def fighters_ran(self):
        return bool(self.flags & ST_FIGHTERS_RAN)

    @property
    def match_start(self):
        return bool(self.flags & ST_MATCH_START)

    @classmethod
    def unpack(cls, buf):
        v = STATE_HEAD.unpack_from(buf, 0)
        head = list(v[:10]) + list(v[11:])  # skip the reserved byte
        fighters = [Fighter.unpack(buf, STATE_HEAD.size + i * FIGHTER.size) for i in range(MAX_PORTS)]
        return cls(*head, fighters=fighters)

    def to_dict(self):
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "fighters"}
        d["fighters"] = []
        for ft in self.fighters:
            fd = {f.name: getattr(ft, f.name) for f in fields(ft) if f.name != "input"}
            fd["input"] = {f.name: getattr(ft.input, f.name) for f in fields(ft.input)}
            fd["mv0_float"] = ft.mv0_float
            d["fighters"].append(fd)
        return d


@dataclass
class Hello:
    proto_version: int
    agent_port: int
    sync_mode: int
    timeout_us: int
    state_size: int
    build: str


class BridgeClient:
    """One connection to the game. Blocking reads with optional timeouts."""

    def __init__(self, path, connect_timeout=30.0, collect_slp=False):
        self.path = path
        self.sock = None
        self.hello = None
        self.last_payload = b""  # raw bytes of the last State, for recordings
        # With collect_slp, the Slippi event messages received since the last
        # take_slp(); otherwise they are dropped as they arrive.
        self.collect_slp = collect_slp
        self._slp = []
        self._buf = bytearray()
        self._connect(connect_timeout)

    def _connect(self, timeout):
        deadline = time.monotonic() + timeout
        last_err = None
        family, target = parse_address(self.path)
        while True:
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.connect(target)
                if family == socket.AF_INET:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock = sock
                break
            except OSError as e:
                sock.close()
                last_err = e
                if time.monotonic() >= deadline:
                    raise ConnectionError(f"cannot connect to {self.path}: {last_err}") from e
                time.sleep(0.1)
        msg_type, payload = self._read_msg(timeout=max(1.0, deadline - time.monotonic()))
        if msg_type != MSG_HELLO:
            raise ConnectionError(f"expected HELLO, got message type {msg_type}")
        v = HELLO.unpack(payload)
        self.hello = Hello(v[0], v[1], v[2], v[3], v[5], v[6].split(b"\0", 1)[0].decode(errors="replace"))
        if self.hello.proto_version not in SUPPORTED_VERSIONS or self.hello.state_size != STATE_SIZE:
            raise ConnectionError(
                f"bridge protocol mismatch: game speaks v{self.hello.proto_version} "
                f"(state {self.hello.state_size} bytes), this tool v{PROTO_VERSION} ({STATE_SIZE})")

    def _read_msg(self, timeout=None):
        """(type, payload), or (None, None) on timeout. Raises ConnectionError on EOF."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if len(self._buf) >= HEADER.size:
                magic, msg_type, size = HEADER.unpack_from(self._buf, 0)
                if magic != MAGIC:
                    raise ConnectionError("bridge stream out of sync (bad magic)")
                if len(self._buf) >= HEADER.size + size:
                    payload = bytes(self._buf[HEADER.size:HEADER.size + size])
                    del self._buf[:HEADER.size + size]
                    return msg_type, payload
            if deadline is None:
                self.sock.settimeout(None)
            else:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, None
                self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None, None
            if not chunk:
                raise ConnectionError("the game closed the bridge connection")
            self._buf += chunk

    def recv_state(self, timeout=None):
        """The next State, or None when `timeout` seconds pass without one."""
        while True:
            msg_type, payload = self._read_msg(timeout)
            if msg_type is None:
                return None
            if msg_type == MSG_STATE:
                self.last_payload = payload
                return State.unpack(payload)
            if msg_type == MSG_SLP_EVENTS and self.collect_slp:
                self._slp.append(payload)

    def take_slp(self):
        """The Slippi event messages received since the last call, in order:
        one frame each (the match header is one too). libmelee ends a parse at
        a frame's bookend, so feed them one at a time."""
        out = self._slp
        self._slp = []
        return out

    def states(self, timeout=None):
        while True:
            st = self.recv_state(timeout)
            if st is None:
                return
            yield st

    def send_input(self, target_tick, pad, release=False, port=None):
        port = self.hello.agent_port if port is None else port
        payload = INPUT_HEAD.pack(target_tick & 0xFFFFFFFF, port, IN_RELEASE if release else 0, 0) + pad.pack()
        self.sock.sendall(HEADER.pack(MAGIC, MSG_INPUT, len(payload)) + payload)

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


DEFAULT_TCP = "tcp:127.0.0.1:47101"


def default_socket_path():
    """MELEE_AGENT_SOCKET if set; else a Unix socket, or loopback TCP on Windows
    (whose Python has no AF_UNIX)."""
    if os.environ.get("MELEE_AGENT_SOCKET"):
        return os.environ["MELEE_AGENT_SOCKET"]
    return DEFAULT_TCP if os.name == "nt" else "/tmp/melee-agent.sock"


def parse_address(addr):
    """(socket family, connect target) for a bridge address, the same forms
    the game accepts: 'tcp:<port>', 'tcp:<host>:<port>' or a Unix socket path."""
    if addr.startswith("tcp:"):
        rest = addr[4:]
        host, _, port = rest.rpartition(":")
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError(f"bad bridge address {addr!r}: want tcp:<port> or tcp:127.0.0.1:<port>")
        return socket.AF_INET, (host or "127.0.0.1", int(port))
    if not hasattr(socket, "AF_UNIX"):
        raise ValueError(f"{addr!r}: this Python has no Unix sockets; use tcp:127.0.0.1:<port>")
    return socket.AF_UNIX, addr


def free_tcp_address():
    """A loopback TCP bridge address with a port nothing is listening on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return f"tcp:127.0.0.1:{s.getsockname()[1]}"


# Recording format written by dump_state.py --record: a 16-byte file header
# (magic, version, state size, reserved) then raw STATE payloads back to back.
RECORD_MAGIC = b"MAGR"
RECORD_HEADER = struct.Struct("<4sIII")


def write_record_header(f):
    f.write(RECORD_HEADER.pack(RECORD_MAGIC, PROTO_VERSION, STATE_SIZE, 0))


def read_record(path):
    """Yield the States of a recording."""
    with open(path, "rb") as f:
        head = f.read(RECORD_HEADER.size)
        if len(head) < RECORD_HEADER.size:
            return  # empty: the recorder was killed before writing anything
        magic, version, size, _ = RECORD_HEADER.unpack(head)
        if magic != RECORD_MAGIC or size != STATE_SIZE:
            raise ValueError(f"{path}: not a v{PROTO_VERSION} bridge recording")
        while True:
            chunk = f.read(size)
            if len(chunk) < size:
                return
            yield State.unpack(chunk)
