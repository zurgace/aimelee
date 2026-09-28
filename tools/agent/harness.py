"""Shared plumbing for the on-machine bridge checks (check_*.py).

GameRun launches build/melee on your disc in an isolated environment (a
throwaway XDG_DATA_HOME so the real memory card is never touched, no loose
files, a bounded MELEE_EXIT_AFTER_FRAMES run) straight into the debug VS
match, with the agent bridge, the Slippi recorder and a MELEE_KEY_FIFO for
driving P1 from the keyboard path. Report collects PASS/FAIL lines.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402

CKIND_FALCON = 0
STKIND_BATTLEFIELD = 0x1F


def default_melee():
    return os.environ.get("MELEE_BIN") or str(ROOT / "build" / "melee")


def default_disc():
    return os.environ.get("MELEE_DISC")


class Report:
    def __init__(self, title):
        self.title = title
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        return bool(ok)

    def info(self, name, detail):
        self.rows.append((name, None, detail))

    def print(self):
        print(f"\n== {self.title}")
        for name, ok, detail in self.rows:
            tag = "INFO" if ok is None else ("PASS" if ok else "FAIL")
            print(f"  [{tag}] {name}" + (f": {detail}" if detail else ""))
        failed = [r for r in self.rows if r[1] is False]
        print(f"== {self.title}: {'FAIL' if failed else 'PASS'} "
              f"({sum(1 for r in self.rows if r[1])} passed, {len(failed)} failed)")
        return not failed


class GameRun:
    """One bounded game run. Use as a context manager."""

    def __init__(self, melee, disc, frames, chars=(CKIND_FALCON, CKIND_FALCON),
                 stage=STKIND_BATTLEFIELD, seed=1, bridge_on=True, key_fifo=True, slp=True,
                 extra_env=None, keep=False, label="run"):
        self.melee = melee
        self.disc = disc
        self.frames = frames
        self.keep = keep
        self.label = label
        self.dir = Path(tempfile.mkdtemp(prefix=f"melee-agent-{label}-"))
        self.socket = str(self.dir / "agent.sock") if bridge_on else None
        self.fifo = str(self.dir / "keys") if key_fifo else None
        self.slp_dir = self.dir / "slp" if slp else None
        self.log_path = self.dir / "stdout.txt"
        env = dict(os.environ)
        for k in list(env):
            if k.startswith("MELEE_AGENT_") or k.startswith("MELEE_DEBUG_VS"):
                del env[k]  # only what this run sets
        env["XDG_DATA_HOME"] = str(self.dir)
        env["XDG_CACHE_HOME"] = str(self.dir)
        env["MELEE_LOG_FILE"] = str(self.dir / "log.txt")
        env["MELEE_FILES_DIR"] = str(self.dir / "no-loose-files")
        env["MELEE_BOOT_SCENE"] = "vs"
        env["MELEE_EXIT_AFTER_FRAMES"] = str(frames)
        env["MELEE_SEED"] = str(seed)
        if chars is not None:
            env["MELEE_DEBUG_VS_CHARS"] = f"{chars[0]},{chars[1]}"
        if stage is not None:
            env["MELEE_DEBUG_VS_STAGE"] = str(stage)
        if self.socket:
            env["MELEE_AGENT_SOCKET"] = self.socket
        if self.fifo:
            os.mkfifo(self.fifo)
            env["MELEE_KEY_FIFO"] = self.fifo
        if self.slp_dir:
            self.slp_dir.mkdir()
            env["MELEE_SLP_DIR"] = str(self.slp_dir)
        env.update(extra_env or {})
        self.env = env
        self.proc = None
        self._fifo_file = None
        self._fifo_ready = threading.Event()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        if not self.keep and not any(exc):
            shutil.rmtree(self.dir, ignore_errors=True)
        else:
            print(f"  (run files kept in {self.dir})")

    def start(self):
        out = open(self.log_path, "w")
        self.proc = subprocess.Popen([self.melee, self.disc], cwd=str(ROOT), env=self.env,
                                     stdout=out, stderr=subprocess.STDOUT)
        if self.fifo:
            # open() blocks until the game's reader opens its end; do it aside.
            def opener():
                try:
                    self._fifo_file = open(self.fifo, "w", buffering=1)
                finally:
                    self._fifo_ready.set()
            threading.Thread(target=opener, daemon=True).start()

    def connect(self, timeout=90.0):
        return bridge.BridgeClient(self.socket, connect_timeout=timeout)

    def keys(self, line):
        """Send one MELEE_KEY_FIFO line to P1, e.g. "Right 500" or "V 60"."""
        if not self._fifo_ready.wait(5.0) or self._fifo_file is None:
            raise RuntimeError("the game never opened MELEE_KEY_FIFO")
        self._fifo_file.write(line.strip() + "\n")

    def wait(self, timeout=120.0):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            return None

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self._fifo_file is not None:
            try:
                self._fifo_file.close()
            except OSError:
                pass

    def log_text(self):
        text = self.log_path.read_text(errors="replace") if self.log_path.exists() else ""
        log = self.dir / "log.txt"
        if log.exists():
            text += "\n" + log.read_text(errors="replace")
        return text

    def slp_file(self):
        if not self.slp_dir:
            return None
        files = sorted(self.slp_dir.glob("*.slp"))
        return files[-1] if files else None


def collect(client, on_state=None, idle_timeout=15.0):
    """Every State until the game closes the connection (or goes quiet)."""
    states = []
    try:
        while True:
            st = client.recv_state(timeout=idle_timeout)
            if st is None:
                break
            states.append(st)
            if on_state is not None:
                on_state(st)
    except ConnectionError:
        pass
    return states


def fight_frames(states):
    """The states that are replay frames: in a fight with the fighters run.
    The .slp recorder numbers them -123, -122, ... in the same order."""
    return [st for st in states if st.in_fight and st.fighters_ran]


def compare_with_slp(report, states, slp_path, ports=(0, 1)):
    """Every replay frame's post-frame state against the recorder's."""
    import slp_read

    frames = slp_read.read_frames(slp_path)
    ours = fight_frames(states)
    mismatches = []
    compared = 0
    for i, st in enumerate(ours):
        frame = -123 + i
        if frame not in frames:
            continue
        for port in ports:
            pre, post = frames[frame].get(port, (None, None))
            f = st.fighters[port]
            if post is None or not f.present:
                continue
            compared += 1
            pairs = [("action", post.action, f.motion_id & 0xFFFF), ("x", post.x, f.cur_x),
                     ("y", post.y, f.cur_y), ("facing", post.facing, f.facing),
                     ("percent", post.percent, f.percent_f), ("shield", post.shield, f.shield),
                     ("action_frame", post.action_frame, f.action_frame),
                     ("hitstun word", post.misc_as, f.mv0_bits),
                     ("airborne", post.airborne, int(f.in_air)), ("hitlag", post.hitlag, f.hitlag),
                     ("self_vx", post.self_air_x, f.self_vx), ("self_vy", post.self_y, f.self_vy),
                     ("kb_vx", post.attack_x, f.kb_vx), ("kb_vy", post.attack_y, f.kb_vy),
                     ("ground_vx", post.self_ground_x, f.ground_vx),
                     ("stocks", post.stocks, f.stocks & 0xFF)]
            if pre is not None and st.flags & bridge.ST_PAD_FRESH and pre.phys_buttons is not None:
                pairs.append(("raw stick x", pre.raw_x, f.input.stick_x))
                pairs.append(("raw stick y", pre.raw_y, f.input.stick_y))
            for name, a, b in pairs:
                if a != b:
                    mismatches.append(f"frame {frame} P{port + 1} {name}: slp {a} vs bridge {b}")
    report.check("slp cross-check: frames compared", compared >= 100,
                 f"{compared} fighter-frames against {slp_path.name}")
    report.check("slp cross-check: every field equal", not mismatches,
                 f"{len(mismatches)} mismatches" + (f", first: {mismatches[0]}" if mismatches else ""))
    return compared, mismatches


def base_args(ap):
    ap.add_argument("--melee", default=default_melee(), help="the game binary (default build/melee)")
    ap.add_argument("--iso", default=default_disc(), help="your disc image (or set MELEE_DISC)")
    ap.add_argument("--keep", action="store_true", help="keep the run directory (logs, .slp)")


def require_disc(args):
    if not args.iso or not Path(args.iso).exists():
        sys.exit("pass --iso <your NTSC-U 1.02 disc> (or set MELEE_DISC)")
    if not Path(args.melee).exists():
        sys.exit(f"{args.melee} not found: build first (cmake -B build -G Ninja && ninja -C build)")


def wait_for(pred, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False
