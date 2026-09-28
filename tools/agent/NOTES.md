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

- **Network gaps are float32 noise.** TF's float32 result is as far from a float64 evaluation as numpy's is (the verifier prints both), and the gap only shows on extreme random inputs; the median is ~1e-39. Tolerance is 5e-4 on probabilities. A layer mix-up or broken file shows up at 1e-2 or more; the loader replays 16 reference cases from the export on every start.
- **Pipeline** covers the delay queue, action chains, memory, banned rules and numpy's `choice`: the recorded pad-command text matches Phillip's `Pad.send_controller` output exactly.

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

## Checks to run on your machine

The container can build and unit-test everything but cannot run the game (no disc, no GPU). Each check boots its own isolated run (a throwaway XDG_DATA_HOME, so your memory card is never touched) and prints PASS/FAIL per assertion. Paste the output back.

```sh
cmake -B build -G Ninja && ninja -C build
python3 tools/agent/check_phase1.py --iso <disc>   # state export, cross-checked against the .slp recorder
python3 tools/agent/check_phase2.py --iso <disc>   # injection on P2 while P1 plays, hits, disconnect
python3 tools/agent/check_vanilla.py --iso <disc> --upstream ../melee-pc-upstream/build/melee
```

By hand, with the bridge on (`MELEE_AGENT_SOCKET=/tmp/melee-agent.sock build/melee <disc>`):

- `python3 tools/agent/dump_state.py` prints the live state.
- `python3 tools/agent/inject_script.py` walks, hops, jabs and shields P2 while you play P1.
- **CSS:** P2 should read as plugged in. Try P1's cursor on P2's door toggle: CPU → pick Falcon → toggle to HMN. Does the character stay selected?

## Known gaps and divergences

- **Full pad queue.** If the game falls five or more pad samples behind (a long hitch), the raw pad queue (qtype 0) merges the newest sample into the head the bridge just wrote, so the agent port can see one tick of its physical/neutral pad. Netplay pins qtype 2 for this; the bridge leaves the queue alone so it changes nothing for the human port.
- **Blocking catch-up.** A lockstep wait blocks the game thread, and the pad alarm catches up afterwards (one extra tick next frame). The 4 ms timeout and degraded mode (after 30 consecutive misses) bound it; a numpy agent normally answers within the frame's render slack, so it never waits.
- **Non-GCC platforms.** On Clang-built targets (Android, Apple, Windows ARM64), `co_attrs.max_jumps` would read byte-swapped from the `melee` target, the same latent issue slp.c has. Linux, the target here, builds everything with GCC.
- **`hitstun_frames_left`** is the first 4 bytes of `Fighter::mv` read as a float, exactly as Phillip read `fp+0x2340`. It is exact in damage states. In other states it holds whatever the current state stored there, which on LP64 can differ from the GameCube (int endianness, re-laid-out views). Values are near zero in both cases.
- **`character`** (used only by agents with `omit_char=false`) was the CSS door's `sel_icon` byte in Phillip. The door static is not reachable from `src/pc`, so we derive the icon from the CKind. Sheik maps to the Zelda icon, as she did on the CSS.
- **Spawns.** Phillip's Dolphin ran the "Netplay Community Settings" Gecko code, which includes neutral spawns and other rule changes; we run vanilla. Only the starting positions differ.
- **Supported agents.** `delay12/MarthFD` (the predictive model) is a stretch goal. `delay18/*` weights are not in the phillip repo; they are on Google Drive.
- **Out of scope.** Netplay, record/replay and synctest: the bridge refuses to arm under them. Windows builds compile the bridge out in v1.
