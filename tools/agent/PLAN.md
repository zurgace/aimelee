# PLAN: Phillip agent bridge for melee-pc (inference only)

## Context

You want to play offline against Phillip (vladfi1/phillip) inside melee-pc, a native PC port of Melee. Phillip used to drive a patched Dolphin: a MemoryWatcher streamed game RAM to it, and named pipes carried its pad commands back. melee-pc has no Dolphin, so we need a bridge:

- the game side, in `src/pc`, is env-gated and exports state and injects one port's pad;
- the Python side, in `tools/agent`, converts the state to Phillip's observation, runs its network and sends pad state back.

This is the output of Phase 0 (read-only investigation), approved on 2026-09-28. Decisions and findings made after that go into [NOTES.md](NOTES.md), which wins wherever the two disagree. Work pauses for on-machine check results after Phase 2 and again after Phase 3.

Decisions you made:
- **Repo:** fork melee-pc into `zurgace/aimelee` with full history, and work on branch `claude/eager-planck-jptjp9`.
- **Test knob:** one test-only knob, `MELEE_DEBUG_VS_CHARS`, is allowed in `src/melee/gm/gmvsmode.c`.
- **Build here:** I seed the blocked CMake dependencies from git clones so every commit compiles and passes unit tests in the container.

Runtime checks need your ISO and GPU, so they run on your CachyOS machine.

Alternatives considered and rejected (recorded in NOTES):
- **Modding doldecomp/melee directly:** it only builds a GameCube executable, so we would be back in Dolphin.
- **Replacing the built-in CPU AI:** that edits game logic and bypasses the controller path Phillip was trained on.
- **Running the network inside the game in C:** one process, but it means rewriting Phillip's logic in C. That is harder to check against the real Phillip, and it rules out Python agents such as slippi-ai later.

You chose the separate Python agent over a socket. The C port stays possible later, using the verified numpy code as its reference.

---

## 1. What Phillip consumes and produces (verified by reading the code and running it under TF 2.13)

### Loop (`phillip/cpu.py`, `phillip/agent.py`)
- **When it acts.** `Agent.act(state, pad)` is called once per new game frame while `menu == Game`, but only after the first 120 frames of the match (`cpu.py:make_action`). Each network step picks an action index. `ActionSet.choose(idx, act_every)` expands it into an `ActionChain` that sends the same controller for `act_every` frames. The network therefore runs every `act_every` frames, and the pad is written every frame.
- **Which player is "self".** By default the agent is pid 1 (port 2) and the opponent is pid 0. With `swap`, the players are swapped in the input, so **`players[1]` is always the agent and `players[0]` the opponent.** Only ports 1 and 2 are tracked.
- **Delay** (`agent.py`, `util.CircularQueue`):
  - It is measured in **network steps**, not frames: `actions = CircularQueue(delay+1)`. Each step pushes the new action and executes the one pushed `delay` steps earlier.
  - The network is fed the D queued-but-unexecuted actions (`delayed_action = actions.as_list()[1:]`, taken before the push), plus `prev_action`, the last executed action.
  - The queue is initialised with action 0.
  - `real_delay` (default 0) picks a newer queue entry to send, to compensate for netplay.
  - Overriding `--delay` on an agent trained with less delay works because `tf_lib.restore` **zero-pads** the mismatched weight rows. Our loader does the same.
- **Sampling** (`ac.py`):
  - The graph outputs `probs = (1-ε)·softmax(logits) + ε/A`.
  - The action is drawn with `numpy.random.choice(p=probs)`.
  - The README play command uses `--epsilon 0`, so that is our default.
- **History and hidden state.** `memory` extra past steps are fed, oldest first, with each step being `[game_embed, onehot(prev_action)]`. The history starts zero-filled. The GRU hidden state (when `core_layers` is set) starts at zero.
- **Banned actions** (`ssbm.SimpleController.banned`): per-character overrides for Peach, Sheik/Zelda, Fox/Falco and Puff that replace the action with a neutral pad. None apply to Falcon. We replicate them.

### Observation (per player, `ssbm.PlayerMemory` via `state_manager.py`, embedded by `embed.PlayerEmbedding`)

Two base addresses appear below:
- **Static block:** `0x80453080 + 0xE90·pid`.
- **Fighter:** the old memwatcher pointer plus 0x60, which lines up exactly with the decomp's `Fighter` offsets.

Every float goes through `(v [+bias]) · scale`, then is clamped to **[-10, 10]**. Non-finite reads are rejected and the previous value is kept (`FloatHandler`).

| Field (order = embedding order) | Phillip source | Type → embed | Scale (default / FalconFalconBF) |
|---|---|---|---|
| percent | static +0x60 **s16** (integer HUD percent) | uint → float | 0.01 |
| facing | Fighter+0x2C | float | 1 |
| x, y | static +0x10 / +0x14 (`player_poses.byIndex[0]`, i.e. the "nametag" pos) | float | `xy_scale` 0.1 / 0.1 |
| action_state | Fighter+0x10 | one-hot 383 (ids ≥383 → all-zero), optionally a linear/NL FC down to `action_space` | — |
| action_frame | Fighter+0x894 | float | 0.02 |
| character | CSS door `sel_icon` byte (0x803F0E08+0x24·pid, bits 8–15) = **CSS icon index** | one-hot 32, or nothing if `omit_char` | — |
| invulnerable | Fighter+0x198C int ≠0 | bool | 1 |
| hitlag_frames_left | Fighter+0x195C | float | `frame_scale` 0.1 / 1 |
| hitstun_frames_left | Fighter+0x2340 = **first 4 bytes of the `mv` union, read as float in every state** | float | `frame_scale` |
| jumps_used | Fighter+0x1968 u8 | float | 1 |
| charging_smash | Fighter+0x2114 int & 2 | bool | 1 |
| shield_size | Fighter+0x1998 | float | `shield_scale` 0.01 / 1 (60 → clamped to 10) |
| in_air | Fighter+0xE0 int ≠0 | bool | 1 |
| speed_air_x_self, speed_ground_x_self, speed_y_self, speed_x_attack, speed_y_attack | Fighter +0x80, +0xEC, +0x84, +0x8C, +0x90 | float | `speed_scale` 0.5 / 1 |

- **Game embedding:** `concat(P[0], P[1])`, optionally through a shared FC of size `player_space`.
- **Not embedded:** `frame`, `menu`, `stage`, `stock`, `z` and the controller. We still export them for gating and matchup checks.

### Actions (`ssbm.actionTypes`, `pad.py`, `dolphin.py`)
- **`old` (FalconFalconBF), 30 actions:** buttons {NONE,A,B,Z,Y,L} × sticks {(.5,.5),(.5,1),(.5,0),(0,.5),(1,.5)}. The C-stick is always neutral.
- **Other action sets:**
  - `custom` has 35 actions: A/B × cardinal, A × tilts ±0.1, {NONE,L} × 9 diagonals, Z, Y, and a "repeat" action that sends nothing.
  - `diagonal` has 54 actions.
- **How Dolphin turned a command into a pad value:**
  1. Pipe text `SET MAIN x y` with values rounded to 2 decimals.
  2. `hi = max(0, v-.5)·2`, `lo = max(0, .5-v)·2`.
  3. AnalogStick: deadzone 0, radius 1, **no circular clamp**, per-axis clamp to [-1,1].
  4. `u8 = (u8)(0x80 + v·0x7F)`, so the game sees `s8 = u8 − 128` (range −127…127).
- **Buttons:** A, B, X, Y, Z, L, R and START are digital. The pipe config maps only digital L/R; Dolphin's MixedTriggers then reports analog = full, so we inject `trigger = 255` with the button bit set.

### Model (checkpoint shapes confirmed)
- **FalconFalconBF**:
  - Structure: action-state FC 383→32 (linear); player FC 49→64 (linear); game 128; plus prev-action one-hot 30, giving an input of **158 → 128 → 128 → 30** with leaky_relu.
  - No GRU; `delay` 0; `memory` 0; `act_every` 2; `char` falcon; `stage` battlefield.
- **delay0/FoxFD**, **delay0/FalcoFD**:
  - Structure: `memory` 1, `custom` action set (35), no FCs, character one-hot, elu. Input 1798 → 128 → 128 → 35.
  - `act_every` is 2 for FoxFD and 4 for FalcoFD.
- **Older agents** (FoxFD0/1, MarthFD0/1, PeachFD*, SheikFD*): leaky_softplus defaults, `action_space` 64, `delay` 1–2, `memory` 1–3.
- **delay12/MarthFD** uses the predictive `Model` (`predict: true`), so it is a stretch goal.
- **delay18/\*** have no weights in the repo (Google Drive only).
- All weights are V1 checkpoints (LevelDB) or V2. TF 2.13 (Python 3.11) reads them, and Phillip's own `Actor` builds and runs under TF 2.13 CPU in about **3.2 ms/step**, measured here.

### Runtime choice: option (b), with (a) as a dev-only oracle
- **Live agent:** a numpy forward pass (numpy only, any Python). It needs about 0.1 ms/step and has no TF or CUDA in the loop.
- **Oracle:** a pinned TF 2.13 environment (`uv run --python 3.11 --with tensorflow-cpu==2.13.*`) used only to:
  1. export weights by building Phillip's real `Actor` and dumping post-`restore()` variables (so padding semantics are identical) to `.npz` + params JSON;
  2. check numpy against the original.

  Keeping TF out of the loop avoids bit-rot and GPU driver issues. The oracle is a one-time step per agent.

---

## 2. melee-pc findings

- **Porting notes** (`docs/porting-notes.md`): native 8-byte pointers mean GameCube offsets are invalid, so we read **by field name only**.
  - Bitfields are LSB-first, so we use named bitfields.
  - `co_attrs` is a big-endian `DISC_STRUCT`, but named access under GCC swaps correctly.
  - `mv` union views differ on LP64.
  - `ASSERT_SIZE(Fighter)` is inactive on PC.
- **The game is single-threaded.** Several **ticks can run per rendered frame** (pad-queue catch-up), so we hook per tick.
- **Tick hooks.** Both already exist in `src/pc` and are called from `src/melee/gm/gmscene.c`, so **zero game-logic changes** are needed:
  - **(b) Pre-tick, before pads are consumed:** the offline branch of `pc_net_sync()` (`src/pc/net.c:3274`, called at `gmscene.c:433` right before `gm_RunSimTick`, whose first act is popping the pad queue).
  - **(a) Post-tick snapshot:** the top of `pc_slp_tick_end(proc_mask)` (`src/pc/slp.c:610`, called right after `HSD_GObj_RunProcs`). The `proc_mask` bit `HSD_GOBJ_PLINK_FIGHTER` tells us whether fighters actually ran (pause and the GAME! freeze don't).
- **Injection method:** overwrite `HSD_PadLibData.queue->stat[qread*4 + port]` with `err = PAD_ERR_NONE`, which is exactly how netplay and replay inject (`net.c:2519–2684`).
  - `PADSetVirtualStatus` is unsuitable: it is only a max-merge, and `gcadapter.c` clears ports 1–3 every frame.
  - The human's port is untouched: the keyboard is port 1, and gamepads use the SDL player index.
- **Pads:** `PADStatus` values are `s8` centred at 0. The game clamps the stick to radius 80 and normalises by /80; triggers clamp to 140. Phillip's full tilt (127) therefore saturates to 1.0, as it did on the GameCube.
- **Pacing:** a fixed 60 Hz wall clock (`src/pc/vi.c`). Between the post-tick of N and the pre-tick of N+1 comes render plus the pacing sleep, so the agent has about a frame to answer before the game ever waits.
- **CSS gotcha** (`mncharsel.c:3118`): a door can only be toggled to **HMN** if that port's pad reports `err == 0`. When the bridge is enabled, it makes the agent port look like a connected neutral pad outside matches, unless a real device drives that port. That lets you set P2 to HMN and pick Falcon from P1's cursor. Whether the coin persists across toggles is to be confirmed on your machine.
- **Env var convention:** `MELEE_*`, read once and cached. `PC_SOURCES` is an explicit list in `CMakeLists.txt:187`. For includes, copy `slp.c`'s `compat.h` and `-Wscalar-storage-order` pragma pattern.
- **Existing test harness:** `MELEE_BOOT_SCENE=vs` (debug VS), `MELEE_DEBUG_VS_STAGE`, `MELEE_DEBUG_VS_STOCKS`, `MELEE_EXIT_AFTER_FRAMES`, `MELEE_SEED`, `MELEE_KEY_FIFO` (P1 key injection), `MELEE_SLP_DIR` (Slippi recorder, an independent serializer we can cross-check against) and `tools/smoke_test.py`.

### Field mapping (Phillip field → melee-pc source, read on the game thread in the post-tick hook)

Ports are enumerated with `slp.c`'s `fighter_gobj()` guard: `Player_GetPlayerSlotType`, `Player_GetEntity`, classifier check, and "asleep" `x221F_b3`. We read only while in a fight, because entity pointers dangle between scenes.

| Phillip field | melee-pc source | Status |
|---|---|---|
| percent | `Player_GetDamage(slot)` (s16 `staminas`, the truncated float) | ✅ exact mirror. We also export `fp->dmg.x1830_percent` |
| x, y | `StaticPlayer.player_poses.byIndex[0]` (copied from `cur_pos` by `Fighter_8006DA4C`, the last fighter proc at priority 0x16) | ✅ Stale while the fighter is asleep, exactly as in Phillip. We also export `cur_pos` |
| facing | `fp->facing_dir` | ✅ |
| action_state | `fp->motion_id` | ✅ Values match `ftCo_MS_*` (Wait 0x0E, …) |
| action_frame | `fp->cur_anim_frame` | ✅ |
| character | `sel_icon`, derived from CKind through a CSS-icon table (Phillip's `state.Character` order; Sheik → Zelda icon 15) | ⚠️ Derived, since the CSS static isn't reachable. Only used by agents with `omit_char=false` |
| invulnerable | `fp->x198C != 0` | ✅ Phillip ignored `x1988` (Slippi does not) |
| hitlag_frames_left | `fp->dmg.x195c_hitlag_frames` | ✅ |
| hitstun_frames_left | first 4 bytes of `fp->mv`, memcpy'd as float (same as `slp.c misc_as`) | ⚠️ Exact in damage states. Elsewhere it's "whatever `mv` holds", which may differ from the GameCube when a state's first `mv` member is an int (endianness) or a view was re-laid out for LP64. Values are near zero either way |
| jumps_used | `fp->x1968_jumpsUsed` | ✅ |
| charging_smash | `(fp->smash_attrs.state & 2) != 0` | ✅ True for Charging and Release, as in Phillip |
| shield_size | `fp->shield_health` | ✅ |
| in_air | `fp->ground_or_air == GA_Air` | ✅ |
| speeds | `self_vel.x`, `gr_vel`, `self_vel.y`, `x8c_kb_vel.x/.y` | ✅ |
| stock (not embedded) | `Player_GetStocks` | ✅ |
| stage (warning only) | `gm_GetStKind()` (Battlefield 0x1F, FD 0x20) | ✅ |
| menu / frame (gating) | `gm_804D6720->scene_kind ∈ {GS_VS, GS_SUDDEN_DEATH}`, bridge tick counter, `gm_801A4BB8()`, `VsSceneState.frame_count` | ✅ Replaces the 0x80479D30/0x80479D60 reads |

**Timing difference.** Dolphin sampled RAM at the XFB copy and the pipe input landed at the next SI poll. We define observation = end of tick N and action applied from tick N+1, which is the minimum latency, the same as lockstep-zmq training. `--frame-lag K` adds K extra ticks if play looks off.

---

## 3. Architecture

```
build/melee (game thread)                                tools/agent (Python, numpy)
 pc_net_sync() offline ─► pc_agent_pre_tick()  ◄── INPUT(target_tick, port, PADStatus) ──┐
     write queue head for agent port                                                     │
 gm_RunSimTick → HSD_GObj_RunProcs                                                       │
 pc_slp_tick_end() ─────► pc_agent_post_tick() ── STATE(tick, scene, 4×fighter) ──► agent.py
                                                   AF_UNIX SOCK_STREAM, $MELEE_AGENT_SOCKET
```

- **IPC: Unix domain socket (`SOCK_STREAM`), game as server, one client** (a new client replaces the old one).
  - Messages are fixed-size, little-endian, packed structs `{u32 magic, u16 type, u16 size, payload}`, with `_Static_assert` sizes and a protocol version in `HELLO`. Python parses them with `struct` or a numpy dtype.
  - Why not shared memory: each message is under 1 KB at 60 Hz, a round trip is about 20 µs, and it gives free disconnect detection (EOF/EPIPE). Shared memory would add futex signalling and crash-recovery work for no measurable gain.
  - Portability: `SOCK_STREAM` rather than `SEQPACKET` so macOS and Windows AF_UNIX stay possible. v1 is Linux-first and compiled out elsewhere with a log line.
- **Sync: lockstep with timeout by default** (`MELEE_AGENT_SYNC=lockstep|async`).
  - The pre-tick waits up to `MELEE_AGENT_TIMEOUT_MS` (default 4 ms) for the input whose `target_tick == current`.
  - Stale inputs are dropped; on a miss the last input is held and a counter logged.
  - After 30 consecutive misses it drops to non-blocking until the agent catches up.
  - On disconnect the port goes neutral and connected, and the server keeps listening.
  - The game never blocks without a bound: `MSG_NOSIGNAL` on sends, non-blocking accept and send, and a full send buffer drops the frame.
- **Env vars:**
  - `MELEE_AGENT_SOCKET=<path>` enables the bridge; with it unset, each hook costs one cached branch.
  - `MELEE_AGENT_PORT` (1–4, default 2), `MELEE_AGENT_SYNC`, `MELEE_AGENT_TIMEOUT_MS`.
  - The bridge refuses to arm under netplay, record/replay or synctest.
- **Agent activation:** the agent drives the port only while in a fight and the client has sent input. Its 120-tick warm-up after match start mirrors Phillip. Inputs are per tick: the Python side sends one `INPUT` for every `STATE`, repeating chain entries so the network runs every `act_every` ticks.

## 4. Files

**Game side:**

| File | Change |
|---|---|
| `src/pc/agent_proto.h` | New: wire structs |
| `src/pc/agent_bridge.{c,h}` | New: env parsing, server, snapshot, injection, stats |
| `src/pc/net.c` | +1 line in the offline branch of `pc_net_sync`: `pc_agent_pre_tick()` |
| `src/pc/slp.c` | +1 line at the top of `pc_slp_tick_end`: `pc_agent_post_tick(proc_mask)` |
| `CMakeLists.txt` | Add the source to `PC_SOURCES`, plus a unit test `tools/test_agent_bridge.c` (server, timeout, disconnect, stale-drop; labelled `melee`) |
| `src/melee/gm/gmvsmode.c` | Test-only `MELEE_DEBUG_VS_CHARS=<ckind1>,<ckind2>` inside the existing `#ifdef TARGET_PC` block |
| `README.md` | Env var rows and an "Agent bridge" section |

**Python side** (`tools/agent/`; the live agent needs only numpy):

| File | Purpose |
|---|---|
| `bridge.py` | Client and protocol |
| `dump_state.py` | Pretty printer, plus `--record` and `--check` |
| `inject_script.py` | Phase 2 scripted walk, jump, attack and shield |
| `phillip_obs.py` | Embedding inputs, CSS-icon table, action sets, the Dolphin pad mapping, banned rules |
| `phillip_model.py` | numpy MLP/GRU with nonlinearities leaky_relu, leaky_softplus, elu, relu, tanh, sigmoid |
| `export_weights.py`, `verify_model.py` | Oracle environment |
| `list_agents.py` | Prints the matchup and parameter table for every agent |
| `agent.py` | Agent loop |
| `play.py` | One-command launcher |
| `check_phase{1,2,3}.py`, `check_vanilla.py` | Runtime checks |
| `tests/` | unittest, no pytest dependency |
| `PLAN.md`, `NOTES.md` | Plan and running notes |

Exported weights go to the gitignored `tools/agent/weights/`. Nothing from the ISO is ever read by these tools or committed.

## 5. Commits (each one builds and passes unit tests in the container before it is pushed)

0. **Repo bootstrap:**
   - Fetch 999sian/melee-pc master and scan the history for disc files (`*.iso|gcm|ciso|rvz|dat|usd|dol`), expecting none.
   - Create `claude/eager-planck-jptjp9` from it.
   - Commit `PLAN.md` and `NOTES.md`, then run `git push -u origin claude/eager-planck-jptjp9`.
   - Set up the container build in scratch: `FETCHCONTENT_SOURCE_DIR_*` pointing at git clones.
1. Bridge skeleton: proto, server and no-op hooks, plus the unit test.
2. **Phase 1:** the `STATE` snapshot, `bridge.py`, `dump_state.py`.
3. `MELEE_DEBUG_VS_CHARS`, `check_phase1.py`, `check_vanilla.py`.
4. **Phase 2:** injection, menu connected-neutral, lockstep, timeout, disconnect; `inject_script.py`, `check_phase2.py`. **→ pause for your results**
5. **Phase 3a:** `phillip_obs`, `phillip_model`, export and verify, `list_agents`, tests.
6. **Phase 3b:** `agent.py` (delay queue, `act_every` chain, ε, banned rules, matchup warnings, reset on match start, latency stats), `check_phase3.py`. **→ pause for your results**
7. **Phase 4:** `play.py`, README section, performance numbers in NOTES.

## 6. Verification

**In the container, for every commit:** `ninja -C build melee unit_tests && ctest --test-dir build -L melee`, `python3 tools/check_style.py`, and `python3 -m unittest discover tools/agent/tests`.

**`verify_model.py` (oracle vs numpy):**
- Synthetic random states covering edge ranges: max |Δprob| ≤ 1e-5.
- **Full pipeline check:** drive Phillip's real `phillip.agent.Agent` (reload 0, fake `Pad` capturing PRESS/RELEASE/SET strings) and our `agent.py` on the same observation sequence with the same seeded numpy RNG. The pad command streams must be identical frame by frame. This covers delay, chain, history and banned rules.
- Run on FalconFalconBF, delay0/FoxFD and delay0/FalcoFD here, and on your recorded observations when available.

**On your machine** (each script launches `build/melee <iso>` itself with `MELEE_BOOT_SCENE=vs`, `MELEE_DEBUG_VS_CHARS=0,0` (Falcon), `MELEE_DEBUG_VS_STAGE=31` (Battlefield), `MELEE_EXIT_AFTER_FRAMES`, `MELEE_SEED` and `MELEE_SLP_DIR`, then prints PASS or FAIL per assertion):

- **`check_vanilla.py`:** with the bridge env unset, the `.slp` frame payloads from this branch match an upstream build (built in `git worktree ../melee-pc-upstream`). It checks run-to-run determinism first, and runs `tools/smoke_test.py`.
- **`check_phase1.py`:**
  - `tick` strictly +1.
  - Both ports are Falcon (CKind 0, `Ft_Kind_Captain` 2) and the stage is 0x1F.
  - Action ids progress Entry (0x142–0x144) → Wait (0x0E).
  - Positions, percent, action and facing agree **frame by frame with the `.slp` recorder**, which is an independent serializer.
  - Spawn coordinates are recorded in NOTES as the regression baseline; later runs must match exactly.
  - P1 driven by `MELEE_KEY_FIFO` shows Walk (0x0F–0x11), KneeBend (0x18) → Jump, and x/y changing on the correct port.
- **`check_phase2.py`:**
  - P2 is injected: walk right, then left, then a short hop, then jab toward P1, then shield (L).
  - Expected: the matching action states, P1's percent rising together with Damage states (0x4B–0x5B) and hitlag > 0, and the `.slp` pre-frame raw pad for P2 equal to what we injected.
  - Throughout, P1 (`MELEE_KEY_FIFO`) keeps walking and jumping, which shows your port still works. You also confirm it by hand with keyboard or gamepad.
- **`check_phase3.py`:**
  - FalconFalconBF on P2 for 3600 frames: 0 timeouts, varied P2 action states, agent step latency p50 and p99 printed.
  - Then `kill -9` the agent at frame 1800: the game continues, exits 0, and logs the disconnect; P2 goes neutral (Wait).
  - A restarted agent reconnects.
- **Definition of done (by hand):**
  - `python3 tools/agent/play.py --iso <ISO>`.
  - You pick Falcon for P1 and HMN Falcon for P2 via the CSS (P2 appears connected), and Battlefield.
  - The agent plays in real time with no stalls, and the log shows 0 late frames.

**Performance budget per 16.7 ms tick:** snapshot plus send is about 10 µs; the agent has about one frame of slack before the pre-tick. The numpy step is about 0.1–0.3 ms including Python overhead, against the 3.2 ms TF oracle. Measured numbers go into NOTES.

## 7. Risks and known gaps (seeding NOTES.md)

- **Timing:** Dolphin's asynchronous pipe likely added 1–2 frames compared with our tick N → N+1. `--frame-lag` is the knob. The "correct" value is unknowable from the code.
- **Hitstun outside damage states** (`mv` reinterpretation): values are near zero either way, but not bit-identical.
- **Spawns:** Phillip ran Dolphin's "Netplay Community Settings" Gecko code, whose effects (e.g. neutral spawns) we don't replicate. Only the starting positions differ.
- **CSS:** setting P2 to HMN and picking its character without a second controller needs to be confirmed on your machine. The fallback is a physical pad on P2 during the CSS, which the bridge leaves alone.
- **A blocked game thread** (a slow agent) can cause pad-alarm catch-up, meaning an extra tick later. Lockstep's short timeout and the degraded mode bound this.
- **Out of scope:** netplay, record and synctest (the bridge refuses them). Windows AF_UNIX is compiled out in v1.
- **Other agents:** the predictive `delay12/MarthFD` is a stretch goal; the `delay18/*` weights are not in the repo. Agents are matchup-specific: `list_agents.py` prints each agent's expected character, stage, `act_every` and delay, and `agent.py` warns at match start when the live characters or stage differ.
- **Sheik:** she is picked on the Zelda icon while holding A (Phillip did the same), so the character one-hot uses the Zelda icon.
