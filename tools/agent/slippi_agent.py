#!/usr/bin/env python3
"""Play the newer Phillip (vladfi1/slippi-ai) on the agent port of a running game.

    slippi_agent.py --model weights/slippi/medium-v2 [--roster weights/roster.json]
    slippi_agent.py --probe weights/slippi/medium-v2     # characters, delay, step latency

Runs in slippi-ai's own Python environment (play.py sets it up). The game
streams each frame's Slippi replay events over the bridge (protocol v2); they
go through libmelee's own event parser, as a Slippi console's stream does for
the bot on Slippi, then slippi-ai's Parser and Agent, unchanged. The
controller it outputs becomes the next tick's pad.

Per match, from the agent port's character: slippi-ai plays it when the
model covers that character; otherwise, with --roster, the 2017 Phillip
agents (agent.py's roster) do; otherwise the port is released for the match.
"""

import argparse
import os
import sys
import time
import types
from pathlib import Path

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402

# libmelee's Character names -> what the character select shows.
NAMES = {
    "CPTFALCON": "Captain Falcon", "DK": "Donkey Kong", "POPO": "Ice Climbers", "NANA": "Nana",
    "YLINK": "Young Link", "DOC": "Dr. Mario", "GAMEANDWATCH": "Mr. Game & Watch",
    "JIGGLYPUFF": "Jigglypuff", "GANONDORF": "Ganondorf",
}


def display(character):
    return NAMES.get(character.name, character.name.title())


# libmelee Character name -> CKind (the character select's order; libmelee's
# values are the game's internal ids). Nana is part of Ice Climbers.
CKINDS = {
    "CPTFALCON": 0x00, "DK": 0x01, "FOX": 0x02, "GAMEANDWATCH": 0x03, "KIRBY": 0x04,
    "BOWSER": 0x05, "LINK": 0x06, "LUIGI": 0x07, "MARIO": 0x08, "MARTH": 0x09, "MEWTWO": 0x0A,
    "NESS": 0x0B, "PEACH": 0x0C, "PIKACHU": 0x0D, "POPO": 0x0E, "JIGGLYPUFF": 0x0F, "SAMUS": 0x10,
    "YOSHI": 0x11, "ZELDA": 0x12, "SHEIK": 0x13, "FALCO": 0x14, "YLINK": 0x15, "DOC": 0x16,
    "ROY": 0x17, "PICHU": 0x18, "GANONDORF": 0x19,
}


AXIS_RADIUS = 80        # slippi_ai.controller_lib: raw stick values are -80..80
TRIGGER_SPACING = 140   # raw analog L/R is 0..140


# ---------------------------------------------------------------- parsing

def make_console():
    """libmelee's Slippi event parser, fed bytes instead of a Dolphin stream."""
    import melee

    class BridgeConsole(melee.Console):
        def __init__(self):
            super().__init__(is_dolphin=False, path=None)

        def feed(self, payload):
            """One frame's events (or the match header): the GameState it
            completes, else None."""
            if self._temp_gamestate is None:
                self._temp_gamestate = melee.GameState()
                self._events_this_frame = []
            gs = self._temp_gamestate
            if not self._Console__handle_slippstream_events(payload, gs):
                return None
            self._temp_gamestate = None
            self._Console__fixframeindexing(gs)
            self._Console__fixiasa(gs)
            return gs

    return BridgeConsole()


# ---------------------------------------------------------------- output

def controller_class():
    import melee

    bits = {
        melee.Button.BUTTON_A: bridge.BUTTON_A, melee.Button.BUTTON_B: bridge.BUTTON_B,
        melee.Button.BUTTON_X: bridge.BUTTON_X, melee.Button.BUTTON_Y: bridge.BUTTON_Y,
        melee.Button.BUTTON_Z: bridge.BUTTON_Z, melee.Button.BUTTON_L: bridge.BUTTON_L,
        melee.Button.BUTTON_R: bridge.BUTTON_R, melee.Button.BUTTON_START: bridge.BUTTON_START,
        melee.Button.BUTTON_D_UP: bridge.BUTTON_UP,
    }

    class FakeController:
        """The melee.Controller calls slippi-ai makes (controller_lib.send_controller),
        kept as a bridge Pad instead of written to a Dolphin pipe. slippi-ai's
        outputs are already raw pad values (sticks -80..80 as 0..1, L as
        0..140 over 0..1), so they map straight to the pad's units."""

        def __init__(self, port):
            self.port = port
            self.release_all()

        def release_all(self):
            self.buttons = set()
            self.main = (0.5, 0.5)
            self.c = (0.5, 0.5)
            self.l_shoulder = 0.0
            self.r_shoulder = 0.0

        def press_button(self, button):
            self.buttons.add(button)

        def release_button(self, button):
            self.buttons.discard(button)

        def tilt_analog(self, button, x, y):
            if button == melee.Button.BUTTON_MAIN:
                self.main = (float(x), float(y))
            elif button == melee.Button.BUTTON_C:
                self.c = (float(x), float(y))

        def press_shoulder(self, button, amount):
            if button == melee.Button.BUTTON_L:
                self.l_shoulder = float(amount)
            elif button == melee.Button.BUTTON_R:
                self.r_shoulder = float(amount)

        def flush(self):
            pass

        def pad(self):
            def axis(v):
                return max(-AXIS_RADIUS, min(AXIS_RADIUS, round((v - 0.5) * 2 * AXIS_RADIUS)))

            def trigger(v):
                return max(0, min(TRIGGER_SPACING, round(v * TRIGGER_SPACING)))

            button = 0
            for b in self.buttons:
                button |= bits.get(b, 0)
            return bridge.Pad(button=button, stick_x=axis(self.main[0]), stick_y=axis(self.main[1]),
                              cstick_x=axis(self.c[0]), cstick_y=axis(self.c[1]),
                              trigger_l=trigger(self.l_shoulder), trigger_r=trigger(self.r_shoulder),
                              analog_a=255 if button & bridge.BUTTON_A else 0,
                              analog_b=255 if button & bridge.BUTTON_B else 0)

    return FakeController


# ---------------------------------------------------------------- the model

def pick_name(state):
    """The nametag to play as: slippi-ai's default when the model knows it,
    else the first it was trained with (an RL model overrides it anyway)."""
    from slippi_ai import nametags

    names = list((state.get("name_map") or {}).keys())
    if not names or nametags.DEFAULT_NAME in names:
        return nametags.DEFAULT_NAME
    return names[0]


class SlippiBrain:
    """One slippi-ai model, loaded once and reused across matches."""

    def __init__(self, path, async_inference=False):
        from slippi_ai import eval_lib, saving

        self.eval_lib = eval_lib
        self.path = str(path)
        self.state = saving.load_state_from_disk(self.path)
        self.summary = eval_lib.AgentSummary.from_state(self.state)
        self.async_inference = async_inference
        self.agent = None
        self.controller = None

    @property
    def characters(self):
        return list(self.summary.characters)

    def supports(self, character):
        return character in self.summary.characters

    def prepare(self, port):
        """Build the agent and warm the model up (libmelee port 1-4), once:
        building it compiles the graph, which takes seconds -- too long for a
        match that has started. Returns the seconds it took, or None if it
        was ready already."""
        if self.agent is not None:
            return None
        t0 = time.perf_counter()
        self.controller = controller_class()(port)
        self.agent = self.eval_lib.build_agent(
            opponent_port=1 if port != 1 else 2, port=port, controller=self.controller,
            state=self.state, name=pick_name(self.state), console_delay=0,
            async_inference=self.async_inference)
        if self.async_inference:
            self.agent.start()
        return time.perf_counter() - t0

    def start_match(self, port, opponent_port):
        """libmelee ports (1-4)."""
        self.prepare(port)
        self.controller.port = port
        self.agent._port = port
        self.agent.set_ports(port, opponent_port)
        self.controller.release_all()

    def step(self, gamestate):
        if gamestate.frame != -123 and getattr(self.agent, "_parser", None) is None:
            # Joined mid-match (a restarted agent): no first frame to reset on.
            from slippi_db.parse_libmelee import Parser
            self.agent._parser = Parser(ports=self.agent.players)
        self.agent.step(gamestate)
        return self.controller.pad()

    def stop(self):
        if self.agent is not None and self.async_inference:
            self.agent.stop()


# ---------------------------------------------------------------- the loop

class Runner:
    def __init__(self, args):
        self.args = args
        self.brain = SlippiBrain(args.model, async_inference=args.async_inference)
        self.classic = None
        if args.roster is not None:
            import agent as classic_agent

            ns = types.SimpleNamespace(roster=args.roster, weights=None, char=None, epsilon=args.epsilon,
                                       seed=args.seed, frame_lag=0, record=None, stats_json=None,
                                       progress=0, quiet=args.quiet)
            self.classic = classic_agent.Runner(ns)
        import melee
        self.melee = melee
        names = ", ".join(sorted(display(c) for c in self.brain.characters))
        self.log(f"slippi-ai model {Path(args.model).name}: {len(self.brain.characters)} characters "
                 f"({names}), {self.brain.summary.delay} frames of delay")

    def log(self, msg):
        if not self.args.quiet:
            print(f"slippi: {msg}", flush=True)

    def serve(self, client):
        port = client.hello.agent_port
        if self.args.brain != "classic":
            took = self.brain.prepare(port + 1)
            if took is not None:
                self.log(f"model ready (warm-up {took:.1f} s)")
        if client.hello.proto_version < 2:
            self.log("this game build does not stream Slippi events (bridge v1): "
                     "only the classic agents can play")
        console = make_console()
        in_fight = False
        released = False
        mode = None      # "slippi", "classic" or "idle" for the current match
        pad = bridge.Pad.neutral()
        latest = None
        while True:
            st = client.recv_state(timeout=None)
            new_frames = []
            for payload in client.take_slp():
                gs = console.feed(payload)
                if gs is not None:
                    new_frames.append(gs)
            if new_frames:
                latest = new_frames[-1]
            if not st.in_fight:
                in_fight = False
                mode = None
                if not released:
                    client.send_input(st.tick + 1, bridge.Pad.neutral(), release=True)
                    released = True
                continue
            if not in_fight or st.match_start:
                in_fight = True
                mode = None
                pad = bridge.Pad.neutral()
            if mode is None:
                mode = self.choose(st, port, latest)
                if mode is None:
                    continue  # the port's fighter is not in the state yet
            if mode == "slippi":
                for gs in new_frames:  # one step per game frame; paused ticks hold the pad
                    pad = self.brain.step(gs)
            elif mode == "classic":
                pad = self.classic.pad_for(st, port)
            else:
                pad = None
            if pad is None:
                if not released:
                    client.send_input(st.tick + 1, bridge.Pad.neutral(), release=True)
                    released = True
                continue
            released = False
            client.send_input(st.tick + 1, pad)

    def choose(self, st, port, latest):
        """This match's player, once the port's fighter is known."""
        me = st.fighters[port]
        if not me.present:
            return None
        if latest is not None and (port + 1) in latest.players:
            ports = sorted(latest.players)
            character = latest.players[port + 1].character
            others = [p for p in ports if p != port + 1]
            if self.args.brain != "classic" and self.brain.supports(character) and len(others) == 1:
                self.brain.start_match(port + 1, others[0])
                self.log(f"{display(character)}: slippi-ai plays P{port + 1}")
                return "slippi"
            if self.brain.supports(character) and len(others) != 1:
                self.log(f"slippi-ai plays one-on-one only ({len(ports)} players here)")
        elif self.args.brain != "classic":
            self.log("no Slippi events from the game this match: slippi-ai cannot see it")
        if (self.classic is not None and self.args.brain != "slippi"
                and me.ckind in self.classic.roster):
            self.classic.match_start(st, port)
            return "classic"
        self.log(self.uncovered_message(me.ckind, port))
        return "idle"

    def uncovered_message(self, ckind, port):
        import roster

        parts = [f"no AI plays {roster.display(ckind)}; P{port + 1} stands still this match."]
        if self.args.brain != "classic":
            parts.append("slippi-ai plays: " + ", ".join(sorted(display(c) for c in self.brain.characters)) + ";")
        if self.classic is not None and self.args.brain != "slippi":
            parts.append("the 2017 agents play: "
                         + ", ".join(sorted({roster.display(ck) for ck in self.classic.roster})) + ";")
        parts.append(f"or set P{port + 1} to CPU on the character select")
        return " ".join(parts)

    def close(self):
        self.brain.stop()


# ---------------------------------------------------------------- probe

def probe(path, steps, async_inference):
    """Load a model, print what it plays, and time its steps on this machine."""
    import numpy as np
    from slippi_ai import eval_lib, saving, utils
    from slippi_ai.types import Game, reify_tuple_type

    t0 = time.perf_counter()
    state = saving.load_state_from_disk(str(path))
    summary = eval_lib.AgentSummary.from_state(state)
    agent = eval_lib.build_delayed_agent(state, console_delay=0, batch_size=1,
                                         name=pick_name(state), async_inference=async_inference)
    load_s = time.perf_counter() - t0
    game = utils.map_nt(lambda t: np.zeros([1], dtype=t), reify_tuple_type(Game))
    reset = np.array([True])
    times = []
    frame = 1 / 60
    if async_inference:
        agent.start()
    try:
        next_t = time.perf_counter()
        for i in range(steps):
            if async_inference:
                # Paced like the game: the worker has a frame to keep up.
                next_t += frame
                time.sleep(max(0.0, next_t - time.perf_counter()))
            t = time.perf_counter()
            agent.step(game, reset)
            times.append((time.perf_counter() - t) * 1e3)
            reset = np.array([False])
    finally:
        if async_inference:
            agent.stop()
    warm = sorted(times[min(30, len(times) - 1):])  # the first steps build and warm up the graph
    pct = lambda q: warm[min(len(warm) - 1, int(q * len(warm)))]  # noqa: E731
    chars = ", ".join(sorted(display(c) for c in summary.characters))
    print(f"model: {path}")
    print(f"type: {summary.type.name.lower()}, delay {summary.delay} frames, "
          f"{len(summary.characters)} characters: {chars}")
    ckinds = sorted({CKINDS[c.name] for c in summary.characters if c.name in CKINDS})
    print(f"ckinds: {','.join(str(c) for c in ckinds)}")
    print(f"load: {load_s:.1f} s; {'async' if async_inference else 'sync'} step over {steps} steps: "
          f"p50 {pct(0.5):.2f} ms, p99 {pct(0.99):.2f} ms, max {warm[-1]:.2f} ms")
    return pct(0.99)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", type=Path, help="a slippi-ai model file (e.g. medium-v2)")
    ap.add_argument("--probe", type=Path, help="print a model's characters and step latency, then exit")
    ap.add_argument("--probe-steps", type=int, default=1000)
    ap.add_argument("--roster", type=Path, help="the classic agents' roster, for other characters")
    ap.add_argument("--brain", choices=["auto", "slippi", "classic"], default="auto")
    ap.add_argument("--async-inference", action="store_true",
                    help="run the model on a worker thread (for CPUs slower than a tick per step)")
    ap.add_argument("--socket", default=bridge.default_socket_path())
    ap.add_argument("--epsilon", type=float, default=0.0, help="classic agents' random-action rate")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--once", action="store_true", help="exit when the game closes the connection")
    ap.add_argument("--connect-timeout", type=float, default=120.0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.probe is not None:
        probe(args.probe, args.probe_steps, args.async_inference)
        return
    if args.model is None:
        ap.error("--model is required (or --probe)")
    runner = Runner(args)
    try:
        while True:
            try:
                client = bridge.BridgeClient(args.socket, connect_timeout=args.connect_timeout,
                                             collect_slp=True)
            except ConnectionError as e:
                print(f"slippi: {e}", file=sys.stderr)
                if args.once:
                    sys.exit(1)
                continue
            try:
                runner.serve(client)
            except ConnectionError as e:
                runner.log(f"game connection closed ({e})")
            finally:
                client.close()
            if args.once:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        runner.close()


if __name__ == "__main__":
    main()
