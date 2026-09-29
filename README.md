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
- [Gomihyu plays Mario (optional)](#gomihyu-plays-mario-optional)
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
- **The launcher.**

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
   - You get a folder `AI-Melee` with `melee.exe`, `Play AI-Melee.bat` and an `ai-melee` folder.

4. **Download Phillip.**
   - On [github.com/vladfi1/phillip](https://github.com/vladfi1/phillip), click the green **Code** button, then **Download ZIP**.
   - Extract it into the `AI-Melee` folder, so that folder now also contains `phillip-master`.

5. **Play.** Double-click `Play AI-Melee.bat`.
   - **First run only:**
     - a window asks for your disc image (it may open behind the console);
     - then a one-time setup: the 2017 agents are converted, the newer Phillip is set up (about 2.5 GB), and the model is timed once.

     Wait for `slippi: model ready` in the console.
   - **Later starts** take seconds.
   - **The console window** stays open with the AI's log. Close the game window to stop.

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

**Launcher options (Linux).**
- **AI:**
  - **Best available** (default): the newer Phillip where it can, the 2017 agents for the rest;
  - **Newer Phillip only**;
  - **2017 agents only**.
- **Random character for P2**, and **tournament-only random stages**. Both are on by default.
- **Gomihyu plays Mario**: see [below](#gomihyu-plays-mario-optional). Off by default.

The launcher remembers your choices. While you play, it shows **AI ready**, and who plays P2 each match. **Show log** has the details, which are also saved in `tools/agent/ai-melee.log`.

On Windows, `Play AI-Melee.bat` passes options to `play.py`, for example `& '.\Play AI-Melee.bat' --brain classic` in PowerShell.

## Gomihyu plays Mario (optional)

[Gomihyu](https://github.com/zurgace/gomihyu) is a small language model with an Archdemon persona (Gemma 4 E4B, run by [Ollama](https://ollama.com)). With her on, she plays P2 whenever P2 is Mario.
- **During a match:** twice a second she picks Mario's game plan: approach, fireballs, spacing, pressure, defending, or edgeguarding. A move library plays that plan frame by frame; she's far too slow to press the buttons herself. Recovering, teching and getting up happen without her.
- **After each match:** she reads how it went, what each plan dealt and took, and how she lost her stocks. Then she rewrites her lessons for the next match.
- **Across matches:** every plan's results are tallied per opponent, and she sees that scoreboard too. So she changes her game from match to match, but don't expect a pro Mario: she stays a quirky, beatable one.

**Setting up** (Linux; she needs about 3.5 GB of graphics memory next to the game):
1. Install Ollama and her model:

   ```sh
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull gemma4:e4b
   ```

2. In the launcher, tick **Gomihyu plays Mario** and press **Play**. From a terminal, use `play.py --gomi` instead.
3. Pick Mario for P2. The launcher shows her taunts, the result and her line after each match. **Show log** shows each plan she picks.

If Ollama isn't running, Mario still plays, on her rule-based fallback. That fallback also learns from the scoreboard.

**Her memory** lives in `tools/agent/gomi/`:
- `lessons.md` has her lessons;
- `scoreboard.json` has every plan's results;
- `matches.jsonl` has one line per match.

Delete that folder to start her over. `GOMI_MODEL` and `GOMI_OLLAMA_URL` point her at another model or Ollama server.

**Posting to Discord.** After each match she also leaves the result in `tools/agent/gomi/outbox/`. Her [Discord bot](https://github.com/zurgace/gomihyu) can post it as @Gomihyu, in her own words: point the bot's `MELEE_OUTBOX` setting at that folder and set `MELEE_CHANNEL_ID` (see her README, "Melee results"). Only the newest 20 files are kept, so nothing piles up while the bot is off.

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
- **The launcher doesn't open (Linux):** install Tk with `sudo pacman -S tk`.
- **The game doesn't start, or shows a black window:** update your graphics driver.
  - On Windows, double-click `RUN-AND-LOG.bat` and read `melee-pc.log`.
  - melee-pc's [FAQ](https://999sian.github.io/melee-pc/) covers first-run problems.
- **Anything else:** the full log is behind **Show log** (Linux) or in the console window (Windows).

## For the maintainer: publishing the Windows download

GitHub Actions and Releases are free for a public repository, so GitHub can build the Windows zip and publish it for you.

**On GitHub (recommended).**
1. Open the repository's **Actions** tab and choose **AI-Melee Windows release**.
2. Click **Run workflow**, enter a `release_tag` such as `ai-melee-v0.3`, and click **Run workflow** again. Pushing a tag named `ai-melee-v...` does the same.

After about 12 minutes the release appears on the Releases page, with `AI-Melee-Windows-x86_64.zip` and the text from `tools/agent/RELEASE_NOTES.md`. Running it again with the same tag replaces the zip. If you also share the zip on Google Drive, upload the new one there and keep the link in the Windows steps above current.

**On your own PC (fallback).** This builds the same zip inside an Ubuntu container, so the only thing it installs on your system is podman:

```sh
sudo pacman -S --needed podman
cd ~/src/ai-melee && git pull
tools/agent/build_windows_zip.sh
```

The result is `dist/AI-Melee-Windows-x86_64.zip`. To publish it, go to **Releases** → **Draft a new release**, create a tag, attach the zip, and click **Publish release**.

melee-pc's own Build workflow (every platform, including signing steps this fork has no secrets for) only runs when started by hand from the Actions tab.

Developer notes (the bridge, every `play.py` option, environment variables, verification) are in [tools/agent/NOTES.md](tools/agent/NOTES.md). melee-pc's own documentation is in its [README](https://github.com/999sian/melee-pc#readme) and in [docs/](docs/).

## License

AI-Melee inherits melee-pc's licensing, spelled out in [licenses/LICENSE.md](licenses/LICENSE.md):
- **Not licensed:** the decompiled game code in `src/melee` and `src/sysdolphin` remains the property of its copyright holders.
- **GPL-3.0-or-later** ([licenses/COPYING](licenses/COPYING)): the port and AI-Melee code in `src/pc`, `tools`, `platforms`, `cmake` and `.github`.
- **Their own licenses:** bundled third-party components.

Because the game code can't be relicensed, the repository as a whole can't be distributed under the GPL.

slippi-ai (MIT), libmelee (LGPL-3.0) and phillip (GPL-3.0) are downloaded on first run, under their own licenses. No game assets are in this repository.
