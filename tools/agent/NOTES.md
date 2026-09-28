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

- **Timing.** How many frames did Phillip's asynchronous Dolphin pipe actually add at play time? Our default is +1 tick (N → N+1). `--frame-lag` exists to experiment.
- **CSS.** Can P2 be set to HMN and given a character from P1's cursor, and does the coin survive the CPU → N/A → HMN toggle? This needs your machine.

## Known gaps and divergences

- **`hitstun_frames_left`** is the first 4 bytes of `Fighter::mv` read as a float, exactly as Phillip read `fp+0x2340`. It is exact in damage states. In other states it holds whatever the current state stored there, which on LP64 can differ from the GameCube (int endianness, re-laid-out views). Values are near zero in both cases.
- **`character`** (used only by agents with `omit_char=false`) was the CSS door's `sel_icon` byte in Phillip. The door static is not reachable from `src/pc`, so we derive the icon from the CKind. Sheik maps to the Zelda icon, as she did on the CSS.
- **Spawns.** Phillip's Dolphin ran the "Netplay Community Settings" Gecko code, which includes neutral spawns and other rule changes; we run vanilla. Only the starting positions differ.
- **Supported agents.** `delay12/MarthFD` (the predictive model) is a stretch goal. `delay18/*` weights are not in the phillip repo; they are on Google Drive.
- **Out of scope.** Netplay, record/replay and synctest: the bridge refuses to arm under them. Windows builds compile the bridge out in v1.
