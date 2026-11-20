# AI-Melee: notes for Claude Code

AI-Melee is a fork of [melee-pc](https://github.com/999sian/melee-pc) (Melee's decompiled C, built
natively for PC) where you play against AIs: the newer Phillip (slippi-ai), the 2017 Phillip
agents, or Gomihyu (an LLM, the owner's persona). Plus a launcher, and a Training Mode button that
opens TM-CE in Dolphin. The player guide is [README.md](README.md); decisions, verified facts and
known gaps are in [tools/agent/NOTES.md](tools/agent/NOTES.md).

## Rules (from the owner)

- Never commit game data, ISOs, extracted assets or the owner's replays: the repo is public.
- Don't change game logic under `src/melee` or `src/sysdolphin`. Hooks live in `src/pc` and stay
  inactive unless an env var (e.g. `MELEE_AGENT_SOCKET`) is set. NOTES lists the three approved
  `#ifdef TARGET_PC` exceptions.
- Small commits, each building and passing tests. Work on `main`, or on short-lived branches
  merged into `main`.
- If something is ambiguous or a design choice is expensive to reverse, stop and ask.
- No AI model IDs in repo content (commit trailers aside).
- Don't package Gomihyu further into the Windows installer (her repo is private to the owner).

## Map

- `src/pc/agent_bridge.c`: the game side (state out, one port's pad in, CSS helpers, random legal
  stages). `agent_link.c`: socket transport. `agent_proto.h`: the wire format. `slp.c`: the Slippi
  event stream slippi-ai reads.
- `tools/agent/`: everything else, in Python.
  - **Entry points:** `play.py` (game + AI) and `launcher.py` (Tk window); `tmce.py` (TM-CE in
    Dolphin).
  - **Bridge and its checks:** `bridge.py` (client); `harness.py` and `check_phase*.py` (on-machine
    checks); `dump_state.py`, `inject_script.py`.
  - **2017 Phillip:** `agent.py` runs it; `phillip_obs.py`, `phillip_model.py`, `phillip_agent.py`
    and `roster.py` are the numpy port; `export_weights.py`, `verify_model.py` and `list_agents.py`
    are the TF-side tools.
  - **slippi-ai:** `slippi_agent.py` (also hosts Gomi and her Falco network).
  - **Gomihyu:** `gomi_brain.py` (planner); `mario_moves.py`, `fox_moves.py`, `falco_moves.py`
    (moves); `gomi_options.py`, `gomi_reads.py`, `gomi_review.py`, `gomi_timing.py`,
    `gomi_handbook.py` (learning); `gomi_replays.py`, `gomi_clips.py`, `gomi_train.py` (from
    replays).
  - **Packaging:** `windows/` and `package_windows_agent.sh`. Tests are in `tools/agent/tests/`.
- Runtime files, all git-ignored: `tools/agent/{weights,slippi-env,gomi}/`, `settings.json`,
  `ai-melee.log`.

## Build and test

```sh
cmake -B build -G Ninja && ninja -C build melee unit_tests && (cd build && ctest -L melee -j8)
python3 tools/check_style.py                      # clang-format + portability, C side
cd tools/agent && python3 -m unittest discover -s tests
ruff check --select F,E9 --exclude slippi-env,weights,gomi tools/agent
```

- **Tk window tests** (skipped without them) need xvfb-run and a Python with Tk, e.g.
  `tools/agent/slippi-env/bin/python -m unittest tests.test_launcher` (slippi-env is made by
  `python3 -c "import play; play.ensure_slippi_env()"`).
- **slippi-ai tests with a real model** read `SLIPPI_TEST_MODEL` / `SLIPPI_BASE_MODEL` (medium-v2).
  Windows CI runs them.
- **Claude's container:** there's no disc or GPU, so the game never runs here; the `check_*.py`
  scripts and real play are the owner's (CachyOS + Windows testers). GitHub archive tarballs return
  403 here: if CMake's FetchContent fails, git-clone the pinned dependency and pass
  `-DFETCHCONTENT_SOURCE_DIR_<NAME>=<clone>` (done before for SDL, fmt, imgui, rmlui, tracy,
  xxhash, googletest).

## CI and releases

- `ai-melee-windows-check.yml` ("AI-Melee agent on Windows") runs on pushes to `tools/agent/**`:
  slippi-env without Git, the medium-v2 probe, the live loops, a few training steps, then all the
  agent tests.
- `ai-melee-windows.yml` ("AI-Melee Windows release"): dispatch with `release_tag` (e.g.
  `ai-melee-v0.10`) to build and publish the Windows zip. The release text is
  `tools/agent/RELEASE_NOTES.md`. v0.3 to v0.9 are published.

## Where we left off (2026-10-04)

- **Last work:** the Training Lab rebuilt in `src/pc` was removed (9388e57); Training Mode now
  launches the real TM-CE in Dolphin (`tmce.py` and the launcher button).
- **Open items for the owner:**
  - **TM-CE:** confirm that its release is an `.xdelta` and that `dolphin -b -e` boots the patched
    ISO (plain and Slippi Dolphin). If not, plan again.
  - **Windows release:** none since v0.9. The training progress bar, pause/resume and Training Mode
    ship in the next one.
  - **Gomi's Falco network:** the owner's ~8 h training run. Does it play like them? The move
    libraries' timings still want tuning from real play.
  - **Older:**
    - the delay18 agents (Google Drive) are unverified;
    - report the prewarm boot crash to melee-pc;
    - one flaky test run was never reproduced.
