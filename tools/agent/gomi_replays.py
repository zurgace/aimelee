#!/usr/bin/env python3
"""Gomihyu learns Falco from Slippi replays.

She imitates every Falco player in the replays she's given (the user and
their friends: "me (Uni) as well as (Yomi), and any other falco player"),
the first half of slippi-ai's recipe. For each replay (.slp, Slippi 3.x):

- the Game Start event says the stage and each port's character;
- the metadata says each port's connect code and netplay name;
- the frames (slp_read.py) become bridge.Fighter snapshots, per port, and a
  gomi_reads.Reader watches each Falco port exactly as it watches the human
  live: which techniques they use (wavedash, L-cancel, SHFFL, shield drop,
  multishine) and which option they pick in each situation, with what it
  traded over the next 90 frames.

It all goes in <gomi>/falco/teacher.json: a gomi_reads.Rival record (techs,
options, and the habits Reader counts), plus which files were read and who
taught how much. Files already read are skipped, so a folder can be read
again every time she starts, and only new replays count. Her Falco starts
from it: the techniques they use, and what worked for them, tried first
(gomi_brain).

    python gomi_replays.py ~/Slippi            # a folder (searched recursively)
    python gomi_replays.py 2026-07.zip         # or a zip of replays
"""

import argparse
import dataclasses
import os
import struct
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import gomi_reads  # noqa: E402
import mario_moves as mm  # noqa: E402
import slp_read  # noqa: E402

FALCO = 0x14            # external character id (the Game Start event's, and CKind)
CMD_GAME_START = 0x36
MAX_JUMPS = 2


# ---------------------------------------------------------------- the file

def ubjson(data, i=0):
    """(value, next index) for the UBJSON value at data[i]: enough for Slippi's metadata."""
    t = data[i:i + 1]
    i += 1
    if t == b"{":
        out = {}
        while data[i:i + 1] != b"}":
            key, i = ubjson_string(data, i)
            out[key], i = ubjson(data, i)
        return out, i + 1
    if t == b"[":
        out = []
        while data[i:i + 1] != b"]":
            value, i = ubjson(data, i)
            out.append(value)
        return out, i + 1
    if t == b"S":
        return ubjson_string(data, i)
    sizes = {b"U": ">B", b"i": ">b", b"I": ">h", b"l": ">i", b"L": ">q", b"d": ">f", b"D": ">d"}
    if t in sizes:
        fmt = sizes[t]
        return struct.unpack_from(fmt, data, i)[0], i + struct.calcsize(fmt)
    if t in (b"T", b"F"):
        return t == b"T", i
    if t == b"Z":
        return None, i
    raise ValueError(f"UBJSON type {t!r} at {i - 1}")


def ubjson_string(data, i):
    """A string (the S already read, or a key: its length marker first)."""
    n, i = ubjson(data, i)
    return data[i:i + n].decode("utf-8", "replace"), i + n


def metadata(data):
    """The replay's metadata element ({} when missing or unreadable)."""
    at = data.rfind(b"U\x08metadata{")
    if at < 0:
        return {}
    try:
        return ubjson(data, at + len(b"U\x08metadata"))[0]
    except (ValueError, struct.error, IndexError):
        return {}


@dataclasses.dataclass
class Replay:
    stage: int
    characters: dict        # port -> external character id (present ports only)
    players: dict           # port -> (code, name)
    frames: dict            # frame -> {port: (PreFrame, PostFrame)}


def read_replay(data):
    """A Replay from a .slp file's bytes."""
    stage, characters, frames = 0, {}, {}
    for cmd, p in slp_read.raw_events(data):
        if cmd == CMD_GAME_START and not characters:
            stage = struct.unpack_from(">H", p, 0x12)[0]
            for port in range(4):
                if p[0x65 + 0x24 * port] != 3:           # player type 3: empty
                    characters[port] = p[0x64 + 0x24 * port]
        elif cmd in (slp_read.CMD_PRE_FRAME, slp_read.CMD_POST_FRAME):
            ev = slp_read.parse_pre(p) if cmd == slp_read.CMD_PRE_FRAME else slp_read.parse_post(p)
            if ev.follower:
                continue                                   # Nana: not a player
            slot = frames.setdefault(ev.frame, {}).setdefault(ev.port, [None, None])
            slot[0 if cmd == slp_read.CMD_PRE_FRAME else 1] = ev
    players = {}
    for key, info in (metadata(data).get("players") or {}).items():
        names = info.get("names") or {} if isinstance(info, dict) else {}
        if str(key).isdigit():
            players[int(key)] = (names.get("code") or "", names.get("netplay") or "")
    return Replay(stage, characters, players, frames)


def clamp(v):
    return max(-80, min(80, int(v)))


def fighter(pre, post, ckind):
    """A replay frame's pre and post events as the bridge.Fighter the Reader watches."""
    pad = bridge.Pad(button=pre.phys_buttons, stick_x=clamp(pre.raw_x), stick_y=clamp(pre.raw_y),
                     cstick_x=clamp(pre.raw_cx), cstick_y=clamp(pre.raw_cy),
                     trigger_l=int(pre.phys_l * 255), trigger_r=int(pre.phys_r * 255))
    return bridge.Fighter(
        present=True, slot_type=0, ckind=ckind, fkind=0, stocks=post.stocks,
        flags=bridge.FT_IN_AIR if post.airborne else 0, jumps_used=max(0, MAX_JUMPS - post.jumps),
        max_jumps=MAX_JUMPS, percent=int(post.percent), motion_id=post.action, percent_f=post.percent,
        facing=post.facing, pos_x=post.x, pos_y=post.y, cur_x=post.x, cur_y=post.y,
        action_frame=post.action_frame, hitlag=0.0, mv0_bits=0, shield=post.shield,
        self_vx=post.self_air_x, self_vy=post.self_y, kb_vx=post.attack_x, kb_vy=post.attack_y,
        ground_vx=post.self_ground_x, body_state=0, body_state_move=0, smash_state=0, input=pad)


# ---------------------------------------------------------------- learning

class Teacher(gomi_reads.Rival):
    """What the Falco players in the replays showed her: a Rival record (techs, options), plus the
    files read and who taught how much."""

    def __init__(self, path):
        super().__init__(path)
        self.data.setdefault("files", {})
        self.data.setdefault("teachers", {})    # code -> {"name", "games", "minutes"}

    def lines(self):
        """Her prompt's summary of her teachers."""
        who = sorted(self.data["teachers"].items(), key=lambda kv: -kv[1]["games"])
        if not who:
            return []
        out = ["you learned Falco from replays of " + ", ".join(
            f"{t['name'] or code} ({t['games']} games)" for code, t in who[:5])]
        used = [t for t, n in sorted(self.data["techs"].items(), key=lambda kv: -kv[1]) if n >= gomi_reads.UNLOCK_AT]
        if used:
            out.append("techniques they use: " + ", ".join(used))
        works = self.what_works(view="teacher")
        if works:
            out.append("what works for them: " + "; ".join(works))
        return out


def learn_replay(teacher, data, log=print, name="replay"):
    """Learn from one replay's bytes: every Falco port teaches. Returns how many ports taught."""
    replay = read_replay(data)
    falcos = [port for port, ch in replay.characters.items() if ch == FALCO]
    if len(replay.characters) != 2 or not falcos:
        return 0                                   # singles with a Falco in it only
    edge = mm.EDGE.get(replay.stage, mm.DEFAULT_EDGE)
    for port in falcos:
        other = next(p for p in replay.characters if p != port)
        reader = gomi_reads.Reader(teacher)
        frames = 0
        for f in sorted(replay.frames):
            ports = replay.frames[f]
            mine, theirs = ports.get(port), ports.get(other)
            if not mine or not theirs or None in mine or None in theirs:
                continue
            falco = fighter(*mine, FALCO)
            opp = fighter(*theirs, replay.characters[other])
            reader.watch(falco, opp, abs(falco.pos_x - opp.pos_x), edge)
            frames += 1
        code, player = replay.players.get(port, ("", ""))
        code = code or player or f"port {port + 1}"
        t = teacher.data["teachers"].setdefault(code, {"name": player, "games": 0, "minutes": 0.0})
        t["name"] = player or t["name"]
        t["games"] += 1
        t["minutes"] = round(t["minutes"] + frames / 3600, 1)
    return len(falcos)


def replay_files(source):
    """(key, reader) for each .slp in a folder (recursively) or a .zip; key: path and size."""
    source = Path(source).expanduser()
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as z:
            for info in sorted(z.infolist(), key=lambda i: i.filename):
                if info.filename.lower().endswith(".slp"):
                    yield f"{source.name}:{info.filename}:{info.file_size}", lambda i=info: z.read(i)
        return
    for path in sorted(source.rglob("*.slp")):
        yield f"{path.relative_to(source)}:{path.stat().st_size}", path.read_bytes


def learn(source, gomi_dir, log=print):
    """Learn from the replays in `source` not read before; returns (new files, ports that taught)."""
    teacher = Teacher(Path(gomi_dir) / "falco" / "teacher.json")
    files = taught = 0
    started = time.monotonic()
    try:
        for key, read in replay_files(source):
            if key in teacher.data["files"]:
                continue
            try:
                n = learn_replay(teacher, read(), log, key)
            except (ValueError, struct.error, IndexError, OSError) as e:
                log(f"skipping {key.rsplit(':', 1)[0]} ({e})")
                n = 0
            teacher.data["files"][key] = n
            files += 1
            taught += n
            if files % 20 == 0:
                teacher.save()                     # a big folder: keep what's learned so far
    except (OSError, zipfile.BadZipFile) as e:
        log(f"can't read replays from {source} ({e})")
    if files:
        teacher.save()
        log(f"read {files} new replay{'s' if files != 1 else ''} in {time.monotonic() - started:.0f}s; "
            f"{taught} Falco game{'s' if taught != 1 else ''} to learn from")
    return files, taught


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a folder of .slp replays (searched recursively) or a .zip of them")
    ap.add_argument("--gomi-dir", default=os.environ.get("GOMI_DIR") or str(HERE / "gomi"))
    args = ap.parse_args()
    learn(args.source, args.gomi_dir, log=lambda m: print(f"gomi: {m}", flush=True))
    teacher = Teacher(Path(args.gomi_dir) / "falco" / "teacher.json")
    for line in teacher.lines():
        print(f"  {line}")
    games = Counter({code: t["games"] for code, t in teacher.data["teachers"].items()})
    if not games:
        print("  (no Falco games found: singles replays with a Falco in them teach her)")


if __name__ == "__main__":
    main()
