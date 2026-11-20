# AI-Melee developer notes

Decisions, verified facts and known gaps. [CLAUDE.md](../../CLAUDE.md) has the rules, the file map and
what's next; the player guide is the [README](../../README.md).

## Decisions

| Decision | Why |
|---|---|
| Fork of 999sian/melee-pc with full history; all work on `main` (the default), or on short-lived branches merged into it. | Upstream syncs stay plain git. |
| The AI is a separate Python process; the game is a socket server (AF_UNIX, or loopback TCP, the only form on Windows). | The agent can be checked frame by frame against the real Phillip, and any Python agent (slippi-ai, Gomihyu) uses the same socket. |
| Hooks ride existing per-tick `src/pc` calls (`pc_net_sync`'s offline path, `pc_slp_tick_end`); nothing under `src/melee`/`src/sysdolphin` changes game logic. | Ground rule. |
| Three approved `#ifdef TARGET_PC` edits in `src/melee`: the test-only `MELEE_DEBUG_VS_CHARS` knob (`gmvsmode.c`); in `mncharsel.c`, the agent's door opening as HMN with a random character from `MELEE_AGENT_CSS_CHARS`, and any cursor moving the agent's HMN token (`pc_agent_css_door`). | The doors and tokens are `static` there, out of `src/pc`'s reach. Unchanged without the bridge. |
| 2017 Phillip runs as a numpy forward pass; TF 2.13 (Python 3.11 via `uv`) only exports and verifies weights. | Keeps TF out of the game loop. |
| Lockstep with a 4 ms timeout by default: the state is the end of tick N, the pad applies from tick N+1. | Lowest latency, as Phillip trained; degrades to async after 30 misses. |
| slippi-ai (the newer Phillip) reads the game through the Slippi event stream `slp.c` already writes, parsed by libmelee as from a console. | The same path the live Slippi bot uses. |
| Rejected: modding doldecomp/melee (GameCube-only), replacing Melee's CPU AI (edits game logic), running Phillip in C (harder to verify). | |
| Training Mode is the real TM-CE in Dolphin (`tmce.py`). A rebuilt "Training Lab" in `src/pc` was built and then removed (commit 9388e57) at the user's request. | TM-CE is PowerPC code patched into the disc; melee-pc can't run it. Neither TM-CE (license unstated) nor Dolphin is shipped. |
| Gomi is not packaged into the Windows zip any further (her GitHub repo is private to the owner). | User's instruction. |

## 2017 Phillip port

- **Verified** (`verify_model.py` vs Phillip's own code under TF 2.13, 2400 frames per agent): all 12 roster agents match. Network max \|numpy−TF\| ≤ 2.4e-4 (float32 noise; tolerance 5e-4), and the full pipeline (delay queue, action chains, memory, banned actions, sampling) is 2400/2400 identical. The loader replays 16 reference cases on every start.
- **Latency** per network step (2.1 GHz Xeon, 1 BLAS thread): FalconFalconBF p99 0.3 ms, FoxFD 0.7 ms, SheikFD2 1.0 ms, out of a 16.7 ms tick.
- **Field mapping:** each field's GameCube offset is commented in `src/pc/agent_bridge.c` `capture_fighter`; Phillip's semantics are in `phillip_obs.py` and `phillip_model.py`. The original Phase 0 analysis is `tools/agent/PLAN.md` in git history (`git show 9892749:tools/agent/PLAN.md`).
- **Agents** (`list_agents.py --check`): FalconFalconBF (Battlefield); delay0/FoxFD and FalcoFD; FoxFD0, MarthFD0, PeachFD, SheikFD; MarthFD1, PeachFD1, SheikFD1 (3 frames late); PeachFD2, SheikFD2 (6 frames late). FoxFD1's params don't describe its checkpoint (Phillip itself runs it misaligned), so the roster skips it. delay12/MarthFD doesn't build under Phillip at HEAD. delay18/* are only in Phillip's Google Drive zip and are unverified.
- **Roster** (`roster.py`, `agent.py --roster`): picked per match from P2's character and the stage. FD agent on Final Destination, Battlefield agent elsewhere, the only one if there's one. Ganondorf and Roy borrow Falcon's and Marth's. `--reaction 1/2` prefers the late-reacting agents. Anyone else: the port is released and stands still.

## The newer Phillip (slippi-ai)

- `slippi-requirements.txt` pins slippi-ai to commit 275c072 as a GitHub archive zip (no Git needed, which matters on Windows), with tf-nightly 2.21.0.dev20260203 and libmelee 0.47.3. Python 3.12, CPU, in `slippi-env/` (made by `play.ensure_slippi_env`).
- Default model medium-v2 (downloaded by `play.ensure_slippi_model` from this repo's `models` release, pinned by sha256: slippi-ai deleted it from Dropbox by 2026-11-20; load it with `slippi_ai.saving.load_state_from_disk`). It's an RL model, 21 frames of delay, 12 characters. On a Ryzen 7 2700X: load 12 s, async step p99 ~10 ms. Play uses `--async-inference`.
- Each frame's Slippi events go to the agent just before that tick's state (protocol v2); the match header is re-sent to a client that connects mid-match. A paused tick sends no frame.
- Windows CI (`ai-melee-windows-check.yml`) installs slippi-env without Git, probes medium-v2 (p99 ~9-12 ms), runs the live loop tests and a few training steps.

## Gomihyu (`--gomi`)

- **Shape:** an LLM (Gemma 4 E4B via Ollama, `GOMI_MODEL`, `GOMI_OLLAMA_URL`) picks a plan about twice a second (`gomi_brain.py`). Move libraries (`mario_moves.py`; `fox_moves.py` subclasses it, and `falco_moves.py` subclasses Fox) turn the plan into one pad per frame. The game thread only reads her latest plan, so a slow Ollama never costs a late input. With `--gomi`, no roster, slippi-ai or Phillip checkout is needed.
- **Learning:** per-character memory in `tools/agent/gomi/<char>/` (lessons, scoreboard, matches, drill, options, timing) and `rival.json`. The option bandit is `gomi_options.py`; reads of the human are `gomi_reads.py`; the post-match review and drills are `gomi_review.py`; input timing is `gomi_timing.py`; the starting knowledge is `gomi_handbook.py`. Discord: lines go to `~/.local/share/gomihyu/melee-outbox` for her bot (zurgace/gomihyu).
- **Replays:** `gomi_replays.py` (teacher stats from every Falco in a replay folder) and `gomi_clips.py` (input clips). These didn't make her Falco play like the user, hence the network below.
- **Her Falco network:** `gomi_train.py`, run in slippi-env:
  - parses the replays and picks the owner (the netplay code in the most files) and their Falco games;
  - holds one game in five out by hash;
  - fine-tunes from medium-v2 (LR 3e-5, batch 16 × unroll 64, `LearnerConfig` defaults);
  - evaluates on fixed held-out batches every 100 steps, with patience 5;
  - writes `gomi/falco/model` and `model.json`.

  Pause and resume: the launcher writes `gomi/falco/train/pause` (or sends SIGTERM); a checkpoint (`train/latest.pkl` with the optimizer, plus `progress.json`) is saved on pause and every 10 min, and the next run picks up there. Once trained, `slippi_agent.py --gomi-falco` plays it and Gomi only watches (no drills or lessons written).

## Training Mode (TM-CE)

`tmce.py` finds Dolphin (the saved one, `dolphin-emu`, the flatpak, Program Files, Slippi Launcher's), applies TM-CE's `.xdelta` to the user's disc with `xdelta3 -d -f -s` (a wrong disc fails xdelta's checksum), remembers both in `settings.json` (`tmce`), and runs `dolphin -b -e <iso>` until it closes. The launcher's **Training Mode** button runs it through the same process plumbing as Play.

## Checks to run on a real machine

The container has no disc and no GPU, so these are the user's. Each boots an isolated run and prints PASS/FAIL. They need `MELEE_DISC` or `--iso`.

```sh
python3 tools/agent/check_phase1.py   # state export vs the .slp recorder
python3 tools/agent/check_phase2.py   # injection on P2 while P1 plays, hits, disconnect
python3 tools/agent/check_phase3.py   # Phillip live: late inputs, latency, kill -9, reconnect
python3 tools/agent/check_vanilla.py --upstream ../melee-pc-upstream/build/melee
```

By hand: `play.py` (or the launcher) with `--quick`; `dump_state.py` (live state); `inject_script.py` (drives P2 with no AI).

## Known gaps

- **Prewarm boot crash (upstream):** melee-pc's background prewarm once made `LbRb.dat` read as zeros ("HSD_ArchiveParse: byte-order mismatch"). `play.py` sets `MELEE_PREWARM=0`. It should be reported to melee-pc.
- **Results screen:** the bridge forces the agent port to read unplugged on `GS_RESULTS` so one Start moves on.
- **Full pad queue:** after a long hitch (5+ samples behind), the agent port can see one tick of a neutral pad.
- **Clang targets** (Android, Apple, Windows ARM64): `max_jumps` would read byte-swapped (as in slp.c). Linux and Windows x86_64 build with GCC.
- **Approximations:** `hitstun_frames_left` reads `Fighter::mv` as Phillip did; `character` is derived from the CKind; spawns are vanilla (Phillip's Dolphin used neutral spawns).
- **Never under** netplay, record/replay or synctest: the bridge refuses to arm.
- **One flaky `unittest discover` run** (errors=1, text not captured), never reproduced in 21 later runs.

## Windows

- `ai-melee-windows.yml` (workflow_dispatch with `release_tag`, or an `ai-melee-v*` tag) builds upstream's Windows zip and adds `ai-melee/` (`package_windows_agent.sh`): the Python tools, `AI-Melee.exe` (`windows/ai_melee_exe.c`, opens the launcher with `pyw.exe`, the windowless Python), `Play AI-Melee.bat` and `README-AI-Melee.txt`. Releases v0.3 to v0.10 are published.
- Quit on Windows: `taskkill` without `/F` first (a graceful close), then forced after a grace period (`launcher.stop_command`, `quit_grace_s`).

## play.py extras and environment variables

`play.py --help` lists every option: `--brain`, `--reaction`, `--agent`, `--port`, `--delay`, `--epsilon`, `--sync`, `--timeout-ms`, `--frame-lag`, `--quick`, `--record`, `--tcp`, `--gomi`, `--box-controller`, `--p2-pick`, `--random-stages`.

| Variable (read by the game) | Meaning |
|---|---|
| `MELEE_AGENT_SOCKET=<path>` or `tcp:127.0.0.1:<port>` | Turns the bridge on (off when unset). |
| `MELEE_AGENT_PORT=<1-4>` | The port the agent drives (default 2). |
| `MELEE_AGENT_SYNC=lockstep\|async`, `MELEE_AGENT_TIMEOUT_MS` | Lockstep wait per tick (default 4 ms). |
| `MELEE_AGENT_RANDOM_STAGES=legal` | Random stage picks only the six tournament stages. |
| `MELEE_AGENT_CSS_CHARS=<ckinds>` | The agent's door opens as HMN on a random one of these. |
| `MELEE_BOX_CONTROLLER=1` | Read sticks as Dolphin does (box controllers). |
| `MELEE_PREWARM=0`, `MELEE_LOG_FILE`, `MELEE_DEBUG_VS_CHARS` | No prewarm; the log file; the debug VS boot (tests). |
