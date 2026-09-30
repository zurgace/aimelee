#!/usr/bin/env python3
"""Play Phillip on the agent port of a running game (numpy only, no TF).

    MELEE_AGENT_SOCKET=/tmp/melee-agent.sock build/melee $MELEE_DISC     # terminal 1
    python3 tools/agent/agent.py --weights tools/agent/weights/FalconFalconBF.npz

(tools/agent/play.py starts both.) Export the weights once first with
export_weights.py. In a match the agent replies to every tick's state with
the next tick's pad, the way Phillip's Dolphin loop did:

- the first 120 frames of a match are left alone (cpu.py waits for the game
  to load), then Agent.act runs on every new frame -- a frame whose scene
  counter moved on, as Phillip's memory watcher saw it;
- act_every / delay / memory / sampling / banned actions: phillip_agent.py;
- the controller becomes the pad Dolphin's pipe device would have produced
  (phillip_obs.dolphin_pad), applied from the next tick. --frame-lag K holds
  it K more ticks, to emulate slower pipes.

Outside a match the port is released (it reads as a neutral, plugged-in pad,
so the character select can seat it). If the game goes away the agent waits
for it to come back unless --once.

With --roster (a file play.py writes from roster.py) the agent is chosen per
match from the agent port's character and the stage: Phillip's agent for that
character (its Final Destination agent on FD, its Battlefield one elsewhere,
or whichever it has), a clone's as a stand-in (Ganondorf, Roy), or none --
then the port is released for the whole match.
"""

import os

# Phillip's matrices are tiny: BLAS threads only add latency jitter
# (measured: p99 per network step 420 -> 311 us, max 3.4 -> 1.8 ms).
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse  # noqa: E402
import collections  # noqa: E402
import json  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import names  # noqa: E402

WARMUP_FRAMES = 120  # cpu.py: "if self.game_frame <= 120: return"


class Stats:
    def __init__(self):
        self.step_us = []   # policy + loop time per network step
        self.reply_us = []  # state received -> input sent, every tick
        self.gaps_ms = []   # wall time between consecutive states in a fight
        self.steps = 0
        self.frames = 0
        self.late_start = None
        self.late_end = None

    @staticmethod
    def pct(xs, q):
        if not xs:
            return 0.0
        s = sorted(xs)
        return s[min(len(s) - 1, int(q * len(s)))]

    def summary(self):
        return {
            "network_steps": self.steps,
            "frames_acted": self.frames,
            "step_us_p50": self.pct(self.step_us, 0.5),
            "step_us_p99": self.pct(self.step_us, 0.99),
            "step_us_max": max(self.step_us) if self.step_us else 0.0,
            "reply_us_p50": self.pct(self.reply_us, 0.5),
            "reply_us_p99": self.pct(self.reply_us, 0.99),
            "tick_gap_ms_p50": self.pct(self.gaps_ms, 0.5),
            "tick_gap_ms_p99": self.pct(self.gaps_ms, 0.99),
            "tick_gaps_over_25ms": sum(1 for g in self.gaps_ms if g > 25.0),
            "late_inputs": (self.late_end - self.late_start) if self.late_start is not None else 0,
        }


class Runner:
    def __init__(self, args):
        import numpy as np  # noqa: F401 - fail early with a clear message if missing
        import phillip_agent
        import phillip_model
        import phillip_obs as po

        self.po = po
        self.args = args
        self.phillip_agent = phillip_agent
        self.phillip_model = phillip_model
        self.models = {}  # weights path -> PhillipModel
        self.roster = None
        self.stand_in = None
        if args.roster is not None or args.weights is None:
            # A roster; or none at all, when Gomihyu alone plays (--gomi without agents).
            import roster
            self.roster_mod = roster
            self.roster = roster.read(args.roster) if args.roster is not None else {}
            self.agent = None
            self.name = "roster"
        else:
            self.use(args.weights, args.char)
        self.stats = Stats()
        self.gomi = None
        self.gomi_match = False
        if getattr(args, "gomi", False):
            import gomi_brain
            self.gomi_mod = gomi_brain
            self.gomi = gomi_brain.GomiBrain(seed=args.seed)
        self.rec = None
        if args.record:
            self.rec = open(args.record, "wb")
            bridge.write_record_header(self.rec)
            self.rec.flush()
        if self.roster is None:
            self.describe()
        elif self.roster:
            chars = sorted({self.roster_mod.display(ck) for ck in self.roster})
            self.log(f"roster: {', '.join(chars)}")
        else:
            self.log("Gomihyu alone plays: " + ", ".join(m.NAME for m in self.gomi_mod.CHARACTERS.values()))

    def use(self, weights, char=None):
        """Make the agent at `weights` the one that plays."""
        weights = str(weights)
        if weights not in self.models:
            self.models[weights] = self.phillip_model.PhillipModel(weights)
        self.model = self.models[weights]
        meta = self.model.meta
        params = meta.get("params", {})
        self.char = char or params.get("char")
        self.expect_stage = params.get("stage", "final_destination")
        self.name = meta.get("name", Path(weights).stem)
        self.agent = self.phillip_agent.PhillipAgent(self.model, self.char, epsilon=self.args.epsilon,
                                                     real_delay=0, seed=self.args.seed)

    def describe(self):
        self.log(f"{self.name}: {self.char} on {self.expect_stage}, {self.model.action_type} actions, "
                 f"act_every {self.model.act_every}, delay {self.model.delay} steps "
                 f"({self.model.delay * self.model.act_every} frames), memory {self.model.memory}, "
                 f"epsilon {self.args.epsilon}")

    def plays(self, ckind):
        """Roster mode: an agent (or Gomihyu) plays this character."""
        return self.gomi_plays(ckind) or (self.roster is not None and ckind in self.roster)

    def gomi_plays(self, ckind):
        return self.gomi is not None and self.gomi_mod.plays(ckind)

    def pick(self, ckind, stkind):
        """Roster mode: choose this match's agent from the port's character
        and the stage. False when no agent plays it."""
        if self.gomi_plays(ckind):
            self.gomi_match = True
            return True
        rd = self.roster_mod
        by_stage = self.roster.get(ckind)
        entry = rd.for_stage(by_stage, stkind) if by_stage else None
        if entry is None:
            if not self.roster and self.gomi is not None:
                names = [m.NAME for m in self.gomi_mod.CHARACTERS.values()]
                gomi = ", ".join(names[:-1]) + " or " + names[-1] if len(names) > 1 else names[0]
                self.log(f"Gomihyu doesn't play {rd.display(ckind)}; the port stands still this match. "
                         f"Pick {gomi} for her, or set it to CPU on the character select")
                return False
            covered = sorted({rd.display(ck) for ck in self.roster})
            self.log(f"no Phillip agent plays {rd.display(ckind)}; the port stands still this match. "
                     f"Pick {', '.join(covered)}, or set it to CPU on the character select")
            return False
        self.use(entry["weights"], entry["char"])
        self.name = entry.get("agent", self.name)
        self.stand_in = None
        if entry.get("stand_in_for"):
            # The network only ever saw its own character: it sees that one.
            self.stand_in = self.po.CSS_ICON_BY_CKIND[self.po.CKIND_BY_PHILLIP_NAME[entry["char"]]]
            source = rd.display(self.po.CKIND_BY_PHILLIP_NAME[entry["char"]])
            self.log(f"{rd.display(ckind)}: no Phillip agent, {source}'s ({self.name}) stands in")
        else:
            self.log(f"{rd.display(ckind)}: {self.name}")
        return True

    def log(self, msg):
        if not self.args.quiet:
            print(f"agent: {msg}", flush=True)

    # ---- per match ------------------------------------------------------

    def match_start(self, st, port):
        if self.roster is not None:
            self.agent = None  # chosen once the port's fighter is in the state
        else:
            self.agent.reset()
        self.idle = False
        self.gomi_match = False
        self.frames_in_match = 0
        self.last_scene_frame = None
        self.prev_obs = {}
        self.pad = bridge.Pad.neutral()
        self.lag = collections.deque([bridge.Pad.neutral()] * self.args.frame_lag)
        self.opp_port = None
        self.warned = False
        self.stats.late_start = st.late_inputs
        self.log(f"match start on {names.STAGE_KIND.get(st.stage, st.stage)}, agent on P{port + 1}")

    def check_matchup(self, st, port):
        po = self.po
        me = st.fighters[port]
        opp = st.fighters[self.opp_port]
        want_ck = po.CKIND_BY_PHILLIP_NAME.get(self.char)
        want_st = po.STKIND_BY_PHILLIP_NAME.get(self.expect_stage)
        problems = []
        if self.roster is None and want_ck is not None and me.ckind != want_ck:
            problems.append(f"it plays {self.char} but P{port + 1} is "
                            f"{names.CHARACTER_KIND.get(me.ckind, me.ckind)}")
        if want_st is not None and st.stage != want_st:
            problems.append(f"it was trained on {self.expect_stage}, this is "
                            f"{names.STAGE_KIND.get(st.stage, st.stage)}")
        if self.name.startswith("FalconFalcon") and opp.ckind != 0:
            problems.append("it was trained against Captain Falcon, the opponent is "
                            f"{names.CHARACTER_KIND.get(opp.ckind, opp.ckind)}")
        others = [i for i, f in enumerate(st.fighters) if f.present and i not in (port, self.opp_port)]
        if others:
            problems.append(f"it only sees two players; ignoring P{', P'.join(str(i + 1) for i in others)}")
        for p in problems:
            print(f"agent: WARNING: {self.name} {p}; expect odd play", flush=True)

    def pad_for(self, st, port):
        """The pad for the next tick, running Agent.act on a new frame; None
        when no agent plays this match (roster mode) and the port is let go."""
        me = st.fighters[port]
        if self.idle:
            return None
        if self.agent is None:
            if not me.present:
                return self.pad
            if not self.pick(me.ckind, st.stage):
                self.idle = True
                return None
        if self.opp_port is None:
            self.opp_port = next((i for i, f in enumerate(st.fighters) if f.present and i != port), None)
            if self.opp_port is not None and me.present:
                if self.gomi_match:
                    self.gomi.start_match(st, port, self.opp_port)
                else:
                    self.check_matchup(st, port)
        if self.gomi_match:
            if self.opp_port is not None and me.present:
                self.pad = self.gomi.pad_for(st, port, self.opp_port)
            return self.pad
        new_frame = self.last_scene_frame is None or st.scene_frame > self.last_scene_frame
        self.last_scene_frame = st.scene_frame
        if not new_frame or self.opp_port is None or not me.present:
            return self.pad
        self.frames_in_match += 1
        if self.frames_in_match % 300 == 0:
            self.checkpoint()  # a killed agent still leaves its numbers behind
        if self.frames_in_match <= WARMUP_FRAMES:
            return self.pad
        obs = []
        for p in (self.opp_port, port):
            o = self.po.player_obs(st.fighters[p], self.prev_obs.get(p))
            if p == port and self.stand_in is not None:
                o.character = self.stand_in
            self.prev_obs[p] = o
            obs.append(o)
        steps = self.agent.steps
        t0 = time.perf_counter()
        c = self.agent.act(obs)
        if self.agent.steps != steps:
            self.stats.step_us.append((time.perf_counter() - t0) * 1e6)
            self.stats.steps += 1
        self.stats.frames += 1
        if c is not self.po.REPEAT:
            self.pad = self.po.dolphin_pad(c)
        return self.pad

    def watch_for(self, st, port, model_info):
        """slippi_agent: a network trained on the human's replays plays her character this match
        (`model_info`: its model.json); Gomihyu watches it, frame by frame."""
        me = st.fighters[port]
        if self.opp_port is None:
            self.opp_port = next((i for i, f in enumerate(st.fighters) if f.present and i != port), None)
            if self.opp_port is not None and me.present:
                self.gomi.start_match(st, port, self.opp_port, watching=model_info)
                self.gomi_match = True
        if self.gomi_match and me.present:
            self.gomi.watch(st, port, self.opp_port)

    # ---- connection -----------------------------------------------------

    def serve(self, client):
        port = client.hello.agent_port
        self.log(f"connected to melee-pc {client.hello.build}; driving P{port + 1} "
                 f"({'async' if client.hello.sync_mode else 'lockstep'}, "
                 f"timeout {client.hello.timeout_us / 1000:.1f} ms)")
        in_fight = False
        released = False
        last_t = None
        progress_at = 0
        while True:
            st = client.recv_state(timeout=None)
            t_recv = time.perf_counter()
            if self.rec is not None:
                self.rec.write(client.last_payload)
            if not st.in_fight:
                if in_fight:
                    self.match_over(st)
                in_fight = False
                last_t = None
                if not released:
                    client.send_input(st.tick + 1, bridge.Pad.neutral(), release=True)
                    released = True
                continue
            if not in_fight or st.match_start:
                self.match_start(st, port)
                in_fight = True
            pad = self.pad_for(st, port)
            if pad is None:
                if not released:
                    client.send_input(st.tick + 1, bridge.Pad.neutral(), release=True)
                    released = True
                continue
            released = False
            if self.args.frame_lag:
                self.lag.append(pad)
                pad = self.lag.popleft()
            client.send_input(st.tick + 1, pad)
            now = time.perf_counter()
            self.stats.reply_us.append((now - t_recv) * 1e6)
            if last_t is not None and self.frames_in_match > WARMUP_FRAMES:
                self.stats.gaps_ms.append((t_recv - last_t) * 1e3)
            last_t = t_recv
            self.stats.late_end = st.late_inputs
            if self.args.progress and self.frames_in_match >= progress_at:
                progress_at = self.frames_in_match + self.args.progress
                me = st.fighters[port]
                print(f"progress frame {self.frames_in_match} tick {st.tick} late {st.late_inputs} "
                      f"P{port + 1} {me.percent}% as 0x{me.motion_id:X}", flush=True)

    def match_over(self, st):
        if self.gomi_match:
            self.gomi.end_match()
            return
        s = self.stats.summary()
        self.log(f"match over: {s['network_steps']} network steps, step p50 {s['step_us_p50']:.0f} us "
                 f"p99 {s['step_us_p99']:.0f} us, reply p99 {s['reply_us_p99']:.0f} us, "
                 f"{s['late_inputs']} late inputs, {s['tick_gaps_over_25ms']} tick gaps over 25 ms")

    def checkpoint(self):
        self.write_stats()
        if self.rec is not None:
            self.rec.flush()

    def write_stats(self):
        if self.args.stats_json:
            with open(self.args.stats_json, "w") as f:
                json.dump(self.stats.summary(), f, indent=1)

    def close(self):
        if self.gomi is not None:
            self.gomi.close()
        self.write_stats()
        if self.rec is not None:
            self.rec.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--weights", type=Path, help="an .npz from export_weights.py: one agent")
    which.add_argument("--roster", type=Path,
                       help="a roster file from play.py: the agent follows the port's character")
    ap.add_argument("--socket", default=bridge.default_socket_path())
    ap.add_argument("--epsilon", type=float, default=0.0,
                    help="random-action rate (Phillip's README plays with 0; training used ~0.02)")
    ap.add_argument("--seed", type=int, help="sampling seed, for reproducible runs")
    ap.add_argument("--frame-lag", type=int, default=0, help="hold each pad K extra ticks")
    ap.add_argument("--char", help="override the agent's character name (banned-action rules)")
    ap.add_argument("--record", type=Path, help="write every state received (dump_state format)")
    ap.add_argument("--stats-json", type=Path, help="write latency/late-input stats here on exit")
    ap.add_argument("--progress", type=int, default=0, help="print a progress line every N frames")
    ap.add_argument("--once", action="store_true", help="exit when the game closes the connection")
    ap.add_argument("--exit-with-game", action="store_true",
                    help="like --once, but keep retrying until the game first connects (play.py uses it: "
                         "Gomihyu then ends her session before the AI is stopped)")
    ap.add_argument("--connect-timeout", type=float, default=120.0)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--gomi", action="store_true",
                    help="roster mode: Gomihyu (gomi_brain.py, through Ollama) plays Mario")
    args = ap.parse_args()
    if args.weights is None and args.roster is None and not args.gomi:
        ap.error("one of --weights, --roster or --gomi is required")

    runner = Runner(args)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            try:
                client = bridge.BridgeClient(args.socket, connect_timeout=args.connect_timeout)
            except ConnectionError as e:
                print(f"agent: {e}", file=sys.stderr)
                if args.once:
                    sys.exit(1)
                continue
            try:
                runner.serve(client)
            except ConnectionError as e:
                runner.log(f"game connection closed ({e})")
            finally:
                client.close()
            runner.write_stats()
            if args.once or args.exit_with_game:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        runner.close()


if __name__ == "__main__":
    main()
