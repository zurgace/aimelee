AI-Melee for Windows 10/11 (64-bit)
===================================

Play Super Smash Bros. Melee against Phillip, the deep-RL Melee agent
(https://github.com/vladfi1/phillip). You need your own Melee disc image:
NTSC-U 1.02 (GALE01), usually a .iso file.

One-time setup
--------------
1. Install Python 3.12 (64-bit) from https://www.python.org/downloads/
   In the first installer screen tick "Add python.exe to PATH", then click
   "Install Now". (Or, in PowerShell: winget install Python.Python.3.12)

2. Open PowerShell (Start menu, type "powershell") and run:

       py -m pip install numpy uv

3. Keep this folder somewhere you can write to, for example
   C:\Games\AI-Melee (not inside C:\Program Files).

4. Get Phillip: open https://github.com/vladfi1/phillip, click the green
   "Code" button, then "Download ZIP". Extract it into this folder, so that
   this folder contains a "phillip-master" folder next to melee.exe.

Playing
-------
Double-click "Play AI-Melee.bat".

- The first time, a window asks for your disc image; the choice is
  remembered. Then it downloads TensorFlow once (about 250 MB, a few
  minutes) to convert Phillip's network. Later starts take seconds.
- In the game: take P1 (keyboard, or a controller) and pick anyone.
  P2 shows as plugged in: with P1's cursor click P2's door to CPU, pick
  Captain Falcon, Fox, Falco, Marth, Peach or Sheik for it (Ganondorf and
  Roy borrow Falcon's and Marth's agents), then click the door on to HMN.
  Choose Final Destination (Battlefield for Falcon). Phillip plays P2 from
  GO! until the match ends; press Start on the results screen to go on.
  Other characters have no Phillip agent: set P2 to CPU for those.
- Click the game window before playing so it gets the keyboard.
- Close the game window, or press Ctrl-C in the black console window, to
  stop.

Options go after the .bat name in a console, for example:

    "Play AI-Melee.bat" --quick                  skip the menus
    "Play AI-Melee.bat" --agent delay0/FoxFD     another agent
    py ai-melee\play.py --help                   every option

Troubleshooting
---------------
- "Windows protected your PC": the build is not code-signed. Click
  "More info", then "Run anyway".
- "Python was not found": install Python as in step 1 and tick the PATH box.
- A firewall prompt for melee.exe: the agent connection only listens on
  127.0.0.1 (this computer); allowing or cancelling makes no difference to
  it. Netplay needs it allowed.
- The game itself will not start or shows a black screen: update your GPU
  driver, then run RUN-AND-LOG.bat and read melee-pc.log.
