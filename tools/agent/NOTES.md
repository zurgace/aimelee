# Agent bridge: running notes

A running record of decisions, open questions and known gaps for the Phillip agent bridge. The
plan it started from is [PLAN.md](PLAN.md); where the two disagree, this file is current.

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-09-28 | This repo is a fork of 999sian/melee-pc (full history); work goes on `claude/eager-planck-jptjp9`. | You can add it as a remote in `./melee-pc`, and rebasing onto upstream stays a plain git operation. |
| 2026-09-28 | The agent runs as a separate Python process over an AF_UNIX `SOCK_STREAM` socket, with the game as server. | The Python agent can be checked frame by frame against the real Phillip code, and later agents (e.g. slippi-ai) can use the same socket. |
| 2026-09-28 | Rejected: modding doldecomp/melee directly. | It only builds a GameCube executable, so we would be back in Dolphin. |
| 2026-09-28 | Rejected: replacing Melee's CPU AI. | That edits game logic and bypasses the controller path Phillip was trained on. |
| 2026-09-28 | Rejected, for now: running Phillip inside the game in C. | A C port is harder to check against Phillip and closes the door on Python agents. The verified numpy code can serve as its reference later. |
| 2026-09-28 | The live agent uses a numpy forward pass. TF 2.13 (Python 3.11 via `uv`) is used only to export weights and verify against Phillip. | Keeps TF bit-rot and GPU drivers out of the game loop. |
| 2026-09-28 | Lockstep with a short timeout is the default sync mode. The observation is the end of tick N; the action applies from tick N+1. | This is the lowest possible latency, the same as Phillip's lockstep training, and the agent has about a frame of slack anyway. |
| 2026-09-28 | Hooks piggyback on existing per-tick `src/pc` calls (`pc_net_sync`, `pc_slp_tick_end`), so nothing under `src/melee` or `src/sysdolphin` changes game logic. | Ground rule. |
| 2026-09-28 | The only `src/melee` edit is the test-only `MELEE_DEBUG_VS_CHARS` knob, inside the existing `#ifdef TARGET_PC` block of `gmvsmode.c`. | You approved it. It gives reproducible Falcon-vs-Falcon-on-Battlefield checks without the CSS. |
| 2026-09-28 | The container build seeds CMake FetchContent dependencies from git clones. | `github.com/<owner>/<repo>/archive/...` tarball URLs return 403 here, while release assets and git work. |

## Open questions

- **Flaky test run.** One `unittest discover` run in the container ended with `FAILED (errors=1)`. 21 later runs (15 sequential, 6 in parallel) all passed, and the error text was not captured. If it shows up for you, please keep the output.

- **Timing.** How many frames did Phillip's asynchronous Dolphin pipe actually add at play time? Our default is +1 tick (N → N+1). `--frame-lag` exists to experiment.
- **CSS.** Can P2 be set to HMN and given a character from P1's cursor, and does the coin survive the CPU → N/A → HMN toggle? This needs your machine.

## Phillip port: verification results (container, 2026-09-28)

`verify_model.py` against Phillip's own code under TF 2.13 (CPU, Python 3.11), 2400 synthetic frames per agent:

| Agent | Network: max \|numpy − TF\| (300 random histories) | Pipeline: Phillip's `Agent.act` vs ours, same TF policy and seed | With the numpy policy |
|---|---|---|---|
| FalconFalconBF | 7.9e-5 | 2400/2400 frames identical | 2400/2400 |
| delay0/FoxFD | 4.2e-5 | 2400/2400 | 2400/2400 |
| delay0/FalcoFD | 2.1e-4 | 2400/2400 | 2400/2400 |
| FoxFD1 | 3.1e-5 | 2400/2400 | 2400/2400 |
| SheikFD2 | 1.0e-4 | 2400/2400 | 2400/2400 |
| PeachFD1 | 1.0e-4 | 2400/2400 | 2400/2400 |
| MarthFD0 | 2.4e-4 | 2400/2400 | 2400/2400 |
| MarthFD1 | 7.9e-5 | 2400/2400 | 2400/2400 |
| PeachFD | 3.3e-5 | 2400/2400 | 2400/2400 |
| PeachFD2 | 1.8e-4 | 2400/2400 | 2400/2400 |
| SheikFD | 9.3e-5 | 2400/2400 | 2400/2400 |
| SheikFD1 | 8.3e-5 | 2400/2400 | 2400/2400 |

Every agent the roster can pick is in this table.

- **Network gaps are float32 noise.** TF's float32 result is as far from a float64 evaluation as numpy's is (the verifier prints both), and the gap only shows on extreme random inputs; the median is ~1e-39. Tolerance is 5e-4 on probabilities. A layer mix-up or broken file shows up at 1e-2 or more; the loader replays 16 reference cases from the export on every start.
- **Pipeline** covers the delay queue, action chains, memory, banned rules and numpy's `choice`: the recorded pad-command text matches Phillip's `Pad.send_controller` output exactly.

## Latency (container, 2.1 GHz Xeon vCPU, numpy 2.5, single-threaded BLAS)

Per network step: observation → Agent.act → pad. Chain-only frames cost ~6 µs.

| Agent | p50 | p99 | max |
|---|---|---|---|
| FalconFalconBF | 141 µs | 311 µs | 1.8 ms |
| delay0/FoxFD | 292 µs | 701 µs | 3.1 ms |
| SheikFD2 | 527 µs | 1.0 ms | 3.7 ms |

The budget per tick is 16.7 ms, and the agent gets about one frame of slack anyway: the state goes out right after the tick's logic, and the game only waits for the input after rendering and pacing. With the default BLAS thread count, FalconFalconBF's p99 was 420 µs and its max 3.4 ms, so `agent.py` pins BLAS to one thread. `check_phase3.py` measures the real numbers on your machine, including tick-to-tick wall time.

## Agents

`list_agents.py --check` (TF oracle) lists the matchup each agent expects:

| Agent | Char | Stage | act_every | Delay (steps, frames) | Memory | Actions | Trained vs | Restore |
|---|---|---|---|---|---|---|---|---|
| FalconFalconBF | falcon | battlefield | 2 | 0 | 0 | old (30) | Falcon (by name) | ok |
| delay0/FoxFD | fox | final_destination | 2 | 0 | 1 | custom (35) | delay0 population | ok |
| delay0/FalcoFD | falco | final_destination | 4 | 0 | 1 | custom (35) | delay0 population | ok |
| FoxFD0, MarthFD0, PeachFD, SheikFD | as named | final_destination | 3 | 0 | 1 | diagonal (54) | delay0 | ok |
| MarthFD1, PeachFD1, SheikFD1 | as named | final_destination | 3 | 1 (3f) | 2 | diagonal (54) | delay1 | delay pad (harmless) |
| PeachFD2, SheikFD2 | as named | final_destination | 3 | 2 (6f) | 3 | diagonal (54) | delay2 | delay pad (harmless) |
| FoxFD1 | fox | final_destination | 3 | 1 (3f) | 2 | diagonal (54) | delay1 | **MISMATCH** |
| delay12/MarthFD | marth | final_destination | 3 | 4 (12f) | 1 | custom | delay12 | predictive model: not supported |
| delay18/* | falco/fox/puff | mixed | 3 | 4-6 (12-18f) | 0 | custom | self | weights not in the repo |

- **FoxFD1's params do not describe its checkpoint.** It was trained with a 64-wide action-state FC (`weight [383,64]` is in the checkpoint), but its params omit `action_space`. Phillip builds the net without the FC, leaves those weights unused and zero-pads the first actor layer from 840 to 2808 rows. So Phillip itself runs FoxFD1 with misaligned weights, and so do we. Fixing it would mean overriding its params (`action_space: 64`), which is a deviation from Phillip.
- **delay12/MarthFD** (predictive model) does not build under Phillip's own code at HEAD (a shape error in its `Model.predict` loop), so there is no oracle to check a port against.

## Roster: the agent follows P2's character

`play.py` (without `--agent`) writes `weights/roster.json` from `roster.py`, and `agent.py --roster` picks the agent at each match start from the agent port's CKind and the stage. The pick happens on the first tick the fighter is in the state, inside the 120-frame warm-up.

**Stage rule.** On Final Destination a character gets its FD agent; on any other stage it gets its Battlefield agent. A character with only one of the two uses it everywhere. Only Falcon (FalconFalconBF) and Falco (delay18/FalcoBF) have Battlefield agents. Falco's is in the Google Drive zip, not the repo, so until it's added Falco plays FalcoFD everywhere. `delay18/FalcoFD`'s params say battlefield, but the roster goes by its name and treats it as the FD agent.

| P2's character | FD agent (default) | BF agent | `--reaction 1` | `--reaction 2` |
|---|---|---|---|---|
| Captain Falcon | (BF one) | FalconFalconBF | same | same |
| Fox | delay0/FoxFD | (FD one) | same (FoxFD1 excluded) | same |
| Falco | delay0/FalcoFD | delay18/FalcoBF if present | same | same |
| Marth | MarthFD0 | (FD one) | MarthFD1 | MarthFD1 |
| Peach | PeachFD | (FD one) | PeachFD1 | PeachFD2 |
| Sheik | SheikFD | (FD one) | SheikFD1 | SheikFD2 |
| Jigglypuff | delay18/PuffFD, only with its weights from Google Drive | (FD one) | same | same |
| Ganondorf | Falcon's agent as a stand-in | | | |
| Roy | Marth's agent as a stand-in | | | |
| anyone else | none: the port is released for the match | | | |

The delay18 agents (6 steps, 18 frames; Puff 4) are only picked when they are the only agent for that stage, since `--reaction` tops out at 2. They're unverified here: their weights aren't in the repo, so `verify_model.py` hasn't run on them. Run it after adding them.

- **Stand-ins.** The agent keeps its own character for the banned-action rules. The agent port's `character` observation is set to the agent's own character, since the network was only trained on it; FalconFalconBF ignores the character input anyway.
- **No agent.** The agent logs which characters are covered and releases the port for the match. P2 stands still, or a controller on P2 plays it.
- **Export.** The first `play.py` run exports every roster agent in one TensorFlow process (`export_weights.py --agent A --agent B ...`, about 40 s in the container once TF is installed).
- **Tests.** `tests/test_roster.py` covers the choices; `tests/test_agent_loop.py` plays Marth, Roy and Mario matches against a scripted game.

## The newer Phillip (slippi-ai)

**Why.** The bot on Slippi is `vladfi1/slippi-ai`: behaviour cloning on human Slippi replays, then self-play RL. The 2017 `vladfi1/phillip` agents are pure RL from 2017, mostly on Final Destination, and much weaker. The bridge reads every field from the offsets Phillip's memory watcher used, so the gap is the agents, not the bridge.

**How it sees the game.**
- With the bridge on, `slp.c` serializes every VS match, file or not. Each frame's events go out as `AGENT_MSG_SLP_EVENTS` just before that tick's STATE (protocol v2).
- Offline, with the stream on, a frame is emitted at the end of its own tick rather than when the next begins; the bytes are the same.
- The match header (Event Payloads + Game Start) is kept and re-sent to a client that connects mid-match.
- `slippi_agent.py` feeds each message to libmelee's own event parser, a `melee.Console` given bytes instead of a Dolphin stream. It then runs slippi-ai's `Parser` and `eval_lib.Agent` unchanged, which is the same path the live bot takes from a Slippi console.
- slippi-ai's controller output snaps to raw pad values (sticks ±80, L 0-140). `FakeController` maps them straight to the pad.
- The agent steps once per game frame. A paused tick sends no frame, so the pad holds.
- `console_delay` is 0: frame N's state yields the pad for tick N+1, as libmelee with Dolphin does.

**Environment.** `slippi-requirements.txt` pins slippi-ai to commit `275c072`, plus the exact packages tested: tf-nightly 2.21.0.dev20260203, libmelee 0.47.3, wandb (imported when a TF model's config is upgraded). Python 3.12, CPU.

**Verified in the container.** The model itself can't be downloaded here (Dropbox is blocked by the container's proxy).
- libmelee parses a synthetic stream written by melee-pc's own serializer (`tests/data/slp_stream.bin`, from `tests/gen_slp_stream.c`): stage, characters, positions, percent, facing, jumps, shield and controller sticks.
- slippi-ai's `Parser` accepts the result.
- The live loop against a fake game: a random 3x512 model (slippi-ai's `create_model.py`) plays P2 as Falco, one reply per tick.
- The classic fallback runs in the same process.
- Step time of that random model on a 4-vCPU Xeon:

  | Mode | p50 | p99 |
  |---|---|---|
  | sync | 7.4 ms | 10.4 ms |
  | async, paced at 60 Hz | 0.2 ms | 8.9 ms |

  So play uses `--async-inference`.

**medium-v2 on a real machine** (Ryzen 7 2700X, CPU, first `play.py` run):
- An RL (self-play) model, 21 frames of delay.
- 12 characters: Captain Falcon, Falco, Fox, Ice Climbers, Jigglypuff, Luigi, Marth, Peach, Pikachu, Samus, Sheik, Yoshi. Being self-play, it was trained against those same 12.
- Load 12.2 s. Async step paced at 60 Hz: p50 0.19 ms, p99 10.4 ms, max 12.1 ms.

**Unverified:** a real match with the game's own stream (the first match logs `slippi: <Character>: slippi-ai plays P2`).

## Checks to run on your machine

The container can build and unit-test everything but cannot run the game (no disc, no GPU). Each check boots its own isolated run (a throwaway XDG_DATA_HOME, so your memory card is never touched) and prints PASS/FAIL per assertion. Paste the output back. The commands are fish syntax, as in the README; they also work in bash. They expect `MELEE_DISC` to be set (`set -Ux MELEE_DISC ~/path/to/GALE01.iso`, README step 4); otherwise add `--iso /path/to/GALE01.iso` to each.

```fish
cmake -B build -G Ninja && ninja -C build
git clone https://github.com/vladfi1/phillip ../phillip
sudo pacman -S python-numpy uv

# once: export the agent (TensorFlow 2.13 in a throwaway Python 3.11 via uv)
uv run --python 3.11 --with 'tensorflow-cpu==2.13.*' --with attrs \
    tools/agent/export_weights.py --phillip ../phillip --agent FalconFalconBF

python3 tools/agent/check_phase1.py   # state export, cross-checked against the .slp recorder
python3 tools/agent/check_phase2.py   # injection on P2 while P1 plays, hits, disconnect
python3 tools/agent/check_phase3.py   # Phillip live: no late inputs, latency, kill -9, reconnect
python3 tools/agent/check_vanilla.py --upstream ../melee-pc-upstream/build/melee

# optional: check the numpy port against Phillip on a real match's observations
python3 tools/agent/play.py --record /tmp/match.bin   # records what the agent saw
uv run --python 3.11 --with 'tensorflow-cpu==2.13.*' --with attrs \
    tools/agent/verify_model.py --phillip ../phillip --record /tmp/match.bin --agent-port 2
```

By hand:

- **Play.** `python3 tools/agent/play.py` (or `ai-melee`; add `--quick` to skip the menus). This is the definition-of-done run: does Phillip play smoothly, and do the log's `agent: match over` / `agent: fight over` lines report 0 late inputs?
- **Watch the state.** `python3 tools/agent/dump_state.py` prints it live, with the game started as `MELEE_AGENT_SOCKET=/tmp/melee-agent.sock build/melee $MELEE_DISC`.
- **Drive P2 without an AI.** `python3 tools/agent/inject_script.py` walks, hops, jabs and shields P2 while you play P1.
- **CSS.** P2 should read as plugged in. Try P1's cursor on P2's door toggle: CPU → pick Falcon → toggle to HMN. Does the character stay selected?

## Known gaps and divergences

- **Full pad queue.** If the game falls five or more pad samples behind (a long hitch), the raw pad queue (qtype 0) merges the newest sample into the head the bridge just wrote, so the agent port can see one tick of its physical/neutral pad. Netplay pins qtype 2 for this; the bridge leaves the queue alone so it changes nothing for the human port.
- **Blocking catch-up.** A lockstep wait blocks the game thread, and the pad alarm catches up afterwards (one extra tick next frame). The 4 ms timeout and degraded mode (after 30 consecutive misses) bound it; a numpy agent normally answers within the frame's render slack, so it never waits.
- **Non-GCC platforms.** On Clang-built targets (Android, Apple, Windows ARM64), `co_attrs.max_jumps` would read byte-swapped from the `melee` target, the same latent issue slp.c has. Linux, the target here, builds everything with GCC.
- **`hitstun_frames_left`** is the first 4 bytes of `Fighter::mv` read as a float, exactly as Phillip read `fp+0x2340`. It is exact in damage states. In other states it holds whatever the current state stored there, which on LP64 can differ from the GameCube (int endianness, re-laid-out views). Values are near zero in both cases.
- **`character`** (used only by agents with `omit_char=false`) was the CSS door's `sel_icon` byte in Phillip. The door static is not reachable from `src/pc`, so we derive the icon from the CKind. Sheik maps to the Zelda icon, as she did on the CSS.
- **Spawns.** Phillip's Dolphin ran the "Netplay Community Settings" Gecko code, which includes neutral spawns and other rule changes; we run vanilla. Only the starting positions differ.
- **Boot crash with prewarm (upstream).** With melee-pc's background prewarm on, the game's own boot-time preload of `LbRb.dat` (1045 bytes) was once read as all zeros. The game stopped with `HSD_ArchiveParse: byte-order mismatch! Please check data format 0 415` (lbarchive.c:28).
  - It happened on a SHA-1-verified GALE01 1.02 disc. Plain `./melee` on the same disc booted fine because the prewarm started later relative to the game's loads.
  - `MELEE_PREWARM=0` avoids it, and `play.py` sets it unless `MELEE_PREWARM` is already set.
  - Root cause not pinned down: every read goes through aurora's single DVD worker (per-file nod handles), so the collision is more likely in how the preload's completion interacts with the prewarm's synchronous reads. It's upstream melee-pc's, to be reported there.
- **Results screen.** It waits for every plugged-in human port to press Start, and counts an unplugged port as ready (gmresultplayer.c). A second Start un-readies a port.
  - On `GS_RESULTS` the bridge forces the agent port to read unplugged (`PAD_ERR_NO_CONTROLLER`) in every queued pad sample, so one Start from the player moves on.
  - It forces this whatever is attached. aurora reports a port as connected when an SDL gamepad holds that player slot, when keyboard bindings exist for it, or when an adapter slot publishes a virtual pad there. The first fix only skipped the neutral fill-in, so it did nothing on a machine where P2 was already claimed that way.
  - It logs `agent: results screen: P2 reads as unplugged ...` once per screen, naming what the port had.
- **Supported agents.** `delay12/MarthFD` (the predictive model) is a stretch goal. `delay18/*` weights are not in the phillip repo; they are on Google Drive.
- **Out of scope.** Netplay, record/replay and synctest: the bridge refuses to arm under them.

## Windows

- **Transport.** Windows' Python has no AF_UNIX, so the bridge also listens on loopback TCP: `MELEE_AGENT_SOCKET=tcp:127.0.0.1:<port>` or `tcp:<port>`, on every platform. It binds 127.0.0.1 only and refuses any other host, because whoever connects drives a port. `play.py` uses TCP on Windows, or with `--tcp`, and picks a free port. Framing, stale inputs, degraded mode and disconnects are unchanged. `TCP_NODELAY` is on at both ends. The address port uses `SO_EXCLUSIVEADDRUSE` on Windows and `SO_REUSEADDR` elsewhere.
- **Tests.**
  - `tools/test_agent_link.c` runs its whole suite twice, over AF_UNIX and over TCP, and checks that non-loopback and malformed addresses are refused.
  - `tests/test_bridge_proto.py` runs the fake game over both transports.
  - `agent_link.c`, `agent_bridge.c` and `fake_game.c` cross-compile with mingw-w64 (with the game's flags for the first two). Under Wine 9, the Windows `fake_game.exe` passes the TCP protocol tests against the Linux `bridge.py`. It also refuses a path address with a pointer to the TCP form.
- **Package.** `.github/workflows/ai-melee-windows.yml` builds upstream's Windows zip (`tools/package_windows.sh`) and adds the agent (`package_windows_agent.sh`): `ai-melee/` with the Python tools, `Play AI-Melee.bat` and `README-AI-Melee.txt`. An `ai-melee-v*` tag publishes it as a Release. The full game couldn't be cross-built in the development container: sqlite.org and the freetype mirror are blocked there, so CI makes the first full build.
- **Unverified.** Nobody has played it on real Windows yet, including the tkinter disc picker, uv's TensorFlow export on Windows (`tensorflow-cpu` 2.13.1 has a cp311 win_amd64 wheel) and Ctrl-C handling in the console.
