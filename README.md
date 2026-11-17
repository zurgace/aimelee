# AI-Melee

Play Super Smash Bros. Melee on your PC against **Phillip**, the Melee AI. The game runs natively, without Dolphin. You play P1 with a keyboard or controller, and Phillip plays P2.

- **The newer Phillip** ([slippi-ai](https://github.com/vladfi1/slippi-ai)) plays by default. It's the bot you meet on Slippi, trained on human replays and then by self-play. It reacts about 21 frames late, like a person online. It plays Captain Falcon, Falco, Fox, Ice Climbers, Jigglypuff, Luigi, Marth, Peach, Pikachu, Samus, Sheik and Yoshi.
- **The 2017 Phillip** ([phillip](https://github.com/vladfi1/phillip)) covers what the newer one doesn't: Ganondorf and Roy (with Falcon's and Marth's agents), and anyone else it has an agent for.
- **Your own disc.** You need a Super Smash Bros. Melee NTSC-U 1.02 (GALE01) disc image. AI-Melee contains no game data.

## Contents

- [Built on](#built-on)
- [Install on Linux (CachyOS)](#install-on-linux-cachyos)
- [Install on Windows 10/11](#install-on-windows-1011)
- [Playing](#playing)
- [Training Mode (TM-CE)](#training-mode-tm-ce)
- [Gomihyu takes over P2 (optional)](#gomihyu-takes-over-p2-optional)
- [Troubleshooting](#troubleshooting)
- [For the maintainer: publishing the Windows download](#for-the-maintainer-publishing-the-windows-download)
- [License](#license)

## Built on

AI-Melee is glue between existing projects. Most of the code here is theirs.

| Project | What it provides |
|---|---|
| [melee-pc](https://github.com/999sian/melee-pc) by 999sian | The native PC port of Melee that AI-Melee is a fork of: the renderer, audio, input, menus and the decompiled game code it runs. |
| [doldecomp/melee](https://github.com/doldecomp/melee) | The decompilation of Melee that melee-pc builds on. |
| [slippi-ai](https://github.com/vladfi1/slippi-ai) by vladfi1 | The newer Phillip: the model, and the code that runs it. |
| [libmelee](https://github.com/altf4/libmelee) by altf4 | Reads the game state the way slippi-ai expects it. |
| [phillip](https://github.com/vladfi1/phillip) by vladfi1 | The 2017 agents and their trained networks. |

AI-Melee's own part:
- **The bridge.** The game hands its state to the AI over a local socket, and takes the AI's controller input back.
- **The Python side.** It runs both Phillips.
- **A few character-select conveniences:** P2 opens with a random AI character, and you can move P2's token.
- **Tournament-only random stages.**
- **The launcher**, with **Gomihyu** (an optional language-model AI for P2) and a **Training Mode** button that opens TM-CE in Dolphin.

## Install on Linux (CachyOS)

Written for CachyOS and other Arch-based systems, so package names are pacman's. Commands are typed in a terminal; they work in fish and bash alike. Plan on about 4 GB of disk space.

1. **Install the packages** (once). Also install your graphics card's Vulkan driver: `nvidia-utils` (NVIDIA), `vulkan-radeon` (AMD) or `vulkan-intel`.

   ```sh
   sudo pacman -S --needed base-devel git cmake ninja python python-numpy uv tk \
       vulkan-icd-loader openssl curl libx11 libxext libxrandr libxcursor libxi \
       libxfixes libxss libxkbcommon libxtst wayland wayland-protocols libdecor \
       alsa-lib libpulse dbus systemd-libs
   ```

2. **Download AI-Melee and Phillip** side by side.

   ```sh
   mkdir -p ~/src && cd ~/src
   git clone https://github.com/zurgace/aimelee.git ai-melee
   git clone https://github.com/vladfi1/phillip.git
   ```

3. **Build the game** (a few minutes; the first build downloads some libraries).

   ```sh
   cd ~/src/ai-melee
   cmake -B build -G Ninja && ninja -C build
   ```

4. **Add the AI-Melee shortcut.** This puts AI-Melee in your app menu (under Games) and on your desktop.

   ```sh
   python3 ~/src/ai-melee/tools/agent/launcher.py --install
   ```

5. **First run.** Click **AI-Melee**, choose your disc with **Change...**, and press **Play**. The first time, before the game opens, it sets up the AIs. The launcher shows each step:
   - it converts the 2017 agents (about 250 MB of downloads, a few minutes);
   - it sets up the newer Phillip (about 2.5 GB, the longest step) and downloads its model;
   - it times the model on your computer once.

   Later starts take seconds.

**Updating:**

```sh
cd ~/src/ai-melee && git pull && ninja -C build
```

**Removing the shortcut:** `python3 ~/src/ai-melee/tools/agent/launcher.py --uninstall`.

**Without the launcher:** `python3 ~/src/ai-melee/tools/agent/play.py` does the same from a terminal and prints the AI's log there. `play.py --help` lists every option.

## Install on Windows 10/11

For 64-bit Windows 10 or 11 with an up-to-date graphics driver. Nothing is compiled: you download a ready-made build. Plan on about 4 GB of disk space and an internet connection for the first run.

1. **Install Python 3.12.** Download the "Windows installer (64-bit)" from [python.org](https://www.python.org/downloads/windows/). On the installer's first screen, tick **Add python.exe to PATH**, then click **Install Now**.

2. **Install two Python packages.** Open PowerShell (Start menu, type `powershell`, press Enter) and run:

   ```powershell
   py -m pip install numpy uv
   ```

   If pip warns that a Scripts folder "is not on PATH", you can ignore it.

3. **Download AI-Melee.**
   - Open the [Releases page](https://github.com/zurgace/aimelee/releases) and download `AI-Melee-Windows-x86_64.zip` from the newest release.
   - Right-click the zip, choose **Extract All...**, and extract it somewhere you own, for example `C:\Games`. Not under `C:\Program Files`: the AI saves files next to itself.
   - You get a folder `AI-Melee` with **`AI-Melee.exe`** (the one to double-click), `Play AI-Melee.bat`, and the folders `ai-melee` (the AI) and `game` (the game itself).

4. **Download Phillip.**
   - On [github.com/vladfi1/phillip](https://github.com/vladfi1/phillip), click the green **Code** button, then **Download ZIP**.
   - Extract it into the `AI-Melee` folder, so that folder now also contains `phillip-master`.

5. **Play.** Double-click **`AI-Melee.exe`**. The AI-Melee launcher opens, the same window as on Linux. (Don't start `game\melee.exe` directly: that's the plain game, without the AI.)
   - **Add desktop shortcut** (bottom of the window) puts AI-Melee on your desktop and in the Start menu, so you don't need the folder again.
   - Choose your disc with **Change...** and press **Play**.
   - **First run only:** a one-time setup. The 2017 agents are converted, the newer Phillip is set up (about 2.5 GB), and the model is timed once. The launcher shows each step; wait for **AI ready**.
   - **Later starts** take seconds. Press **Esc** in the game (handy in fullscreen), close its window, or press **Quit game**, and the launcher comes back.

   `Play AI-Melee.bat` still works too: it starts AI-Melee the old way, with the log in a console window.

**Updating:** download the newest zip and extract it over your `AI-Melee` folder. Your settings and the converted agents are kept.

## Playing

**Controls.** The keyboard plays P1:

| Controller | Keyboard |
|---|---|
| stick | arrow keys or WASD |
| C-stick | IJKL |
| A, B, X, Y | X, Z, C, V |
| L, R, Z | Q, E, Tab |
| Start | Enter |

A gamepad on port 1 works too, and so does an official GameCube adapter. Click the game window first: the keyboard only reaches the game while its window has focus.

**Setting up a match.**
1. Go to VS Mode and pick your character with P1.
2. **Click P2's door once** with your cursor. P2 opens as HMN with a random character the AI plays, and the AI takes over that port.
   - **Another random character:** click the door three more times (CPU, closed, HMN).
   - **A character of your choice:** put your cursor on P2's token, press A to pick it up, and press A on another character.
   - **Sheik:** pick Zelda for P2 and hold A while the match loads.
3. Pick a stage, or press Start for a random one. Random picks only Battlefield, Final Destination, Pokémon Stadium, Yoshi's Story, Dream Land N64 or Fountain of Dreams.
4. The AI plays from GO! until the match ends. On the results screen, press Start once to go back to the character select, with P2 still seated.

**Which AI plays P2.**
- **The newer Phillip** plays its 12 characters, on any stage.
- **The 2017 agents** play the rest, with their training stage in mind:
  - Fox, Falco, Marth, Peach and Sheik learned on Final Destination.
  - Captain Falcon learned on Battlefield.
  - Ganondorf and Roy borrow Falcon's and Marth's agents.
- **Anyone else** stands still, so pick someone else for P2 (or set P2 to CPU for the game's own AI).

**Launcher options.**
- **AI:**
  - **Best available** (default): the newer Phillip where it can, the 2017 agents for the rest;
  - **Newer Phillip only**;
  - **2017 agents only**.
- **Random character for P2**, and **tournament-only random stages**. Both are on by default.
- **Gomihyu takes over P2**: she plays P2 as Mario, Fox or Falco, and the other AIs are off; see [below](#gomihyu-takes-over-p2-optional). Off by default.

The launcher remembers your choices. While you play, it shows **AI ready**, and who plays P2 each match. **Show log** has the details, which are also saved in `tools/agent/ai-melee.log`.

`Play AI-Melee.bat` (Windows) passes options to `play.py` from a console, for example `& '.\Play AI-Melee.bat' --brain classic` in PowerShell.

## Training Mode (TM-CE)

**Training Mode** in the launcher opens [TrainingMode-CommunityEdition](https://github.com/AlexanderHarrison/TrainingMode-CommunityEdition) (TM-CE): the 1P Event Match replaced by training events (the Training Lab with savestates, recording and CPU options; ledgedash, L-cancel, edgeguard and more). TM-CE patches the GameCube disc, so it runs in **Dolphin**, not in AI-Melee's game, and the AIs don't play in it. AI-Melee includes neither:
- **Dolphin:** install [Dolphin](https://dolphin-emu.org) or [Slippi Launcher](https://slippi.gg). The launcher finds `dolphin-emu`, its flatpak, Dolphin in Program Files or Slippi Launcher's Dolphin, else asks you to pick it.
- **TM-CE:** the first press offers TM-CE's download page. Pick the downloaded patch (`.xdelta`) and AI-Melee applies it to your disc (needs `xdelta3`: `sudo pacman -S xdelta3`), saving `<patch name>.iso` beside the patch. On Windows, patch as TM-CE's release says and pick the patched `.iso`. The disc must be a plain `.iso` (convert a `.rvz` in Dolphin).

Both are remembered; later presses start TM-CE straight away, and closing Dolphin brings the launcher back. **Change...** next to "Training Mode (TM-CE)" picks another version or Dolphin. Terminal: `python3 tools/agent/tmce.py --help`.

## Gomihyu takes over P2 (optional)

[Gomihyu](https://github.com/zurgace/gomihyu) is a small language model with an Archdemon persona (Gemma 4 E4B, run by [Ollama](https://ollama.com)). With her on, she plays P2 as **Mario**, **Fox** or **Falco**, and slippi-ai and the 2017 agents aren't loaded.

- **She calls the plays.** Twice a second she picks a game plan (approach, zoning, spacing, pressure, defending, edgeguarding, platforms); a move library plays it frame by frame, and recovery, teching and getups happen without her. She starts from a handbook of each character's basics (`gomi_handbook.py`).
- **She learns by trial and error.** Within a plan she tries options (grab, tilts, aerials, lasers, shine, waiting...), scores each by the damage traded per situation and opponent, and leans on what pays (`options.json`). She learns which states eat her inputs and presses on the first frame she can (`timing.json`).
- **She reads you** (humans only): she copies your tech (wavedash, L-cancel, SHFFL, shield drop, multishine) the second time she sees it, and punishes your habits (tech direction, getups, ledge options, shielding).
- **After each match** she reads a review, rewrites her lessons and picks one drill for the next match (recovery, neutral, punish, defense, edgeguard, or a copied technique), then checks whether its number improved. Without Ollama, her rules pick.
- **Her Falco learns from your Slippi replays.** **Teach Gomi from replays...** imitates every Falco player in your replay folder (their tech, option choices and short input clips), rereading new replays each time she starts (`gomi_replays.py`).
- **Her Falco can play like you.** **Train on my replays** fine-tunes slippi-ai's medium-v2 network on your own Falco games, on your CPU, in the background (an overnight run for a few months of netplay). **Pause training** / **Resume training** pick up exactly where it stopped, even after a restart; Play pauses it too. One game in five is held out, and the launcher shows how much closer it got to your inputs. Once trained, that network plays her Falco and Gomi watches, reviews and posts. Terminal: `tools/agent/slippi-env/bin/python tools/agent/gomi_train.py --replays <folder or .zip>`. Delete `tools/agent/gomi/falco/model` to give her Falco back to her.

**Setting up** (she needs about 3.5 GB of graphics memory next to the game):
1. Install Ollama and her model: `curl -fsSL https://ollama.com/install.sh | sh`, then `ollama pull gemma4:e4b`.
2. In the launcher, tick **Gomihyu takes over P2** and press **Play** (terminal: `play.py --gomi`).
3. Pick Mario, Fox or Falco for P2 (the door opens with one of them). The launcher shows her taunts, the result and her line after each match.

**Her memory** is `tools/agent/gomi/`, one folder per character (`lessons.md`, `scoreboard.json`, `matches.jsonl`, `drill.json`, `options.json`, `timing.json`), plus `rival.json` for what she knows about you. Delete the folder to start her over, or only `rival.json` to make her forget you. `GOMI_MODEL` and `GOMI_OLLAMA_URL` point her at another model or server; `GOMI_NUM_CTX` (default 8192) must equal her Discord bot's `OLLAMA_NUM_CTX`, or Ollama reloads the model each time they take turns.

**Discord.** Her [bot](https://github.com/zurgace/gomihyu) posts once per session, in her own words: when you close the game, when you say **"gg"** in Discord, or 20 minutes after the last match. AI-Melee leaves her lines in `~/.local/share/gomihyu/melee-outbox`; say **"gomi post melee here"** (as the bot's owner) to pick the channel. The launcher warns if posts pile up.

## Troubleshooting

- **P2 doesn't move.** Wait for **AI ready** (`slippi: model ready` on Windows) before starting a match. If P2's character is one the AI doesn't play, the log says so.
- **Wrong disc remembered.**
  - Linux: press **Change...** in the launcher.
  - Windows: delete `ai-melee\settings.json`, or run the `.bat` with `--iso <path>`.
- **"Windows protected your PC"** when starting: the build isn't code-signed. Click **More info**, then **Run anyway**.
- **"Python was not found" or `py` is not recognized** (Windows): run the Python installer again, choose **Modify**, and tick **py launcher** and **Add Python to environment variables**.
- **"cannot find Phillip's agent":** check that Phillip was downloaded next to AI-Melee:
  - Linux: `~/src/phillip`;
  - Windows: `AI-Melee\phillip-master`.
- **Can't dash on a box controller** (HayBox, B0XX, Frame1): these send the stick values Dolphin reads, and the game has to read them the same way.
  - Boxes in DInput mode are recognised by name.
  - One in XInput mode (most Pico-based HayBox boxes) looks like an Xbox pad. Tick **I play on a box controller** in the launcher.
- **The launcher doesn't open (Linux):** install Tk with `sudo pacman -S tk`.
- **The game doesn't start, or shows a black window:** update your graphics driver.
  - On Windows, double-click `game\RUN-AND-LOG.bat` and read `game\melee-pc.log`.
  - melee-pc's [FAQ](https://999sian.github.io/melee-pc/) covers first-run problems.
- **AI-Melee.exe says it needs Python:** install Python 3.12 as in step 1, with **Add python.exe to PATH** ticked.
- **Updating by extracting over an old folder:** the old game files are left at the top of the folder, including an old `melee.exe` that starts the game without the AI. The launcher offers to remove them; say yes.
- **The launcher doesn't open (Windows):** run the python.org installer again, choose **Modify**, and tick **tcl/tk and IDLE**. Or use `Play AI-Melee.bat`.
- **Anything else:** the full log is behind **Show log** in the launcher (also saved in `ai-melee.log`), or in the console window with `Play AI-Melee.bat`.

## For the maintainer: publishing the Windows download

1. On GitHub, open **Actions** → **AI-Melee Windows release** → **Run workflow**, and enter a `release_tag` one past the newest on the Releases page (e.g. `ai-melee-v0.10`). Pushing an `ai-melee-v...` tag does the same.
2. About 12 minutes later the release appears with `AI-Melee-Windows-x86_64.zip` and the text of `tools/agent/RELEASE_NOTES.md`. Re-running with the same tag replaces the zip. If you share the zip on Google Drive too, upload the new one.

Fallback on your own PC (builds the same zip in an Ubuntu container; needs podman): `tools/agent/build_windows_zip.sh` writes `dist/AI-Melee-Windows-x86_64.zip`, which you attach to a new release by hand. melee-pc's own Build workflow only runs when started by hand.

Developer notes: [CLAUDE.md](CLAUDE.md) (how the repo is laid out, the rules, what's next) and [tools/agent/NOTES.md](tools/agent/NOTES.md) (the bridge, `play.py` options, environment variables, verification, known gaps). melee-pc's own documentation is in its [README](https://github.com/999sian/melee-pc#readme) and [docs/](docs/).

## License

AI-Melee inherits melee-pc's licensing, spelled out in [licenses/LICENSE.md](licenses/LICENSE.md):
- **Not licensed:** the decompiled game code in `src/melee` and `src/sysdolphin` remains the property of its copyright holders.
- **GPL-3.0-or-later** ([licenses/COPYING](licenses/COPYING)): the port and AI-Melee code in `src/pc`, `tools`, `platforms`, `cmake` and `.github`.
- **Their own licenses:** bundled third-party components.

Because the game code can't be relicensed, the repository as a whole can't be distributed under the GPL.

slippi-ai (MIT), libmelee (LGPL-3.0) and phillip (GPL-3.0) are downloaded on first run, under their own licenses. No game assets are in this repository.
