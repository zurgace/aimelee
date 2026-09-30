"""What Gomihyu knows about Melee before her first match.

Her lessons (gomi/<character>/lessons.md) are hers: she writes them after
every match. This is the starting knowledge she doesn't have to learn the
hard way -- the basics of the character she plays and a note on each
opponent -- shown in every match prompt next to her lessons, and a prior for
each plan (in the scoreboard's units, net percent per minute) that her
rule-based chooser starts from and that fades as her own numbers come in.
"""

# The character she plays -> what every player of it learns first.
BASICS = {
    "Mario": [
        "Fireballs are slow but safe: they control the ground and make them jump, where up-air and "
        "up-tilt wait (fireball, pressure).",
        "Cape (side-B) turns them around and reflects projectiles: it ruins Fox, Falco and Captain "
        "Falcon recoveries at the ledge (edgeguard).",
        "Up-throw and up-air combo at low percent; forward smash and up-smash kill from about 100% "
        "(pressure).",
        "Mario is middleweight with a short recovery: double jump before up-B, and never chase deep "
        "offstage.",
        "When they hit your shield, grab them out of it (defend).",
    ],
    "Fox": [
        "Shine (down-B) hits on its first frame: right next to them, it beats almost everything "
        "(pressure, approach).",
        "Lasers make slower characters come to you; against Fox and Falco they do little (lasers).",
        "Up-smash and up-air kill: up-smash from about 100% on most characters, earlier on light ones "
        "(pressure).",
        "Fox is light and falls fast: he dies early and gets combo'd, so don't hang in the air above them.",
        "Your recovery is predictable: from low use Firefox, from ledge height Illusion; don't go deep "
        "offstage to edgeguard.",
    ],
    "Falco": [
        "Short-hop lasers are your neutral: they stop approaches and push them into shield (lasers).",
        "Shine pops them up: jump out of it into a down-air or up-air and shine again -- a pillar "
        "(pressure).",
        "Down-air spikes: offstage it kills at almost any percent (edgeguard).",
        "Falco falls fast and gets combo'd: land on the stage, not above them.",
        "Your recovery is short: Phantasm only from close at ledge height, else Fire Bird early; "
        "never chase deep offstage.",
    ],
}

# The opponent's character -> the one thing to know about it.
OPPONENTS = {
    "Fox": "fastest in the game, lasers and shine combos; light and falls fast so he dies early to "
           "up-attacks; recovers low with up-B or level with side-B: edgeguard him.",
    "Falco": "lasers stop your approach and his down-air spikes; his recovery is short and predictable: "
             "edgeguard him hard.",
    "Marth": "long sword range and early kills with the tip; don't approach into his forward-air, stay "
             "out of range and punish his whiffs; he edgeguards well, so recover to the ledge.",
    "Sheik": "needles zone you and her grab leads to combos; fast tilts up close; edgeguard her at "
             "the ledge when she vanishes back.",
    "Captain Falcon": "fast and hits hard (the knee kills); weak when hit out of the air; his up-B "
                      "recovery is slow and predictable: edgeguard it.",
    "Peach": "floats, pulls turnips and has a strong down-smash; heavy-ish, so kill her with up-attacks "
             "later; she's slow on the ground: pressure her.",
    "Jigglypuff": "floaty with many jumps; Rest kills from right next to her: don't linger beside her; "
                  "she dies very early to side kills.",
    "Samus": "heavy and floaty; charge shot and missiles zone you; she lives long, so rack up damage "
             "and approach through her projectiles.",
    "Ice Climbers": "two of them: separate Nana from Popo; their grab can be a death sentence, so "
                    "don't get grabbed; approach from the air.",
    "Pikachu": "small and fast; thunder jolt zones; quick attack recovers from anywhere, so don't "
               "chase it offstage.",
    "Luigi": "slippery wavedashes and a strong up-B up close; floaty: kill him with up-attacks.",
    "Mario": "fireballs and cape; middleweight, short recovery: edgeguard him low.",
    "Dr. Mario": "pills and cape, heavier hits than Mario, short recovery: edgeguard him low.",
    "Ganondorf": "slow but every hit is huge: shield, then punish his big whiffs; poor recovery: "
                 "edgeguard him.",
    "Link": "bombs, arrows and boomerang zone you; slow up close: get inside and pressure him.",
    "Young Link": "projectiles and fast aerials; light: kill him early; get inside his zoning.",
    "Donkey Kong": "big heavy target that's easy to combo; his cargo throw is dangerous; recovers low: "
                   "edgeguard him.",
    "Yoshi": "no up-B: once his double jump is gone, he's done; armored double jump, strong shield.",
    "Zelda": "slow but strong kicks (lightning kicks kill); pressure her up close.",
    "Ness": "PK fire traps, predictable PK thunder recovery: hit the thunder ball to stop it.",
    "Bowser": "heavy, slow, huge target: combo him; his shield is bad, grab him.",
    "Kirby": "light with many jumps; weak range: space him out and kill early.",
    "Mewtwo": "floaty, very light for his size; good throws; kill early with up-attacks.",
    "Pichu": "hurts itself with electric moves; tiny and very light: kill early.",
    "Mr. Game & Watch": "light, no L-cancel needed, strong aerials; kill him early.",
    "Roy": "like Marth but his sword is strongest at the base: don't stand close; short recovery.",
}

# Prior per plan, in net percent per minute: which plans tend to work, by opponent.
PROJECTILES = {"Falco", "Samus", "Sheik", "Link", "Young Link", "Pikachu", "Mario", "Dr. Mario", "Luigi", "Peach"}
POOR_RECOVERY = {"Falco", "Captain Falcon", "Ganondorf", "Donkey Kong", "Fox", "Yoshi", "Ice Climbers",
                 "Mario", "Dr. Mario", "Roy", "Ness"}
FLOATY = {"Peach", "Jigglypuff", "Samus", "Luigi", "Mewtwo", "Kirby"}
DANGEROUS_CLOSE = {"Jigglypuff", "Marth", "Roy", "Ganondorf"}


def priors(character, opponent):
    """{plan: prior} for her playing `character` against `opponent`."""
    p = {"approach": 6.0, "pressure": 6.0, "space": 4.0, "defend": 0.0, "edgeguard": 4.0, "platform": 0.0}
    zone = "lasers" if character in ("Fox", "Falco") else "fireball"
    p[zone] = 4.0
    if opponent in PROJECTILES:
        p[zone] -= 6.0      # they out-zone you or reflect it: go in instead
        p["approach"] += 4.0
    if opponent in ("Fox", "Falco") and character in ("Fox", "Falco"):
        p[zone] -= 4.0
    if opponent in POOR_RECOVERY:
        p["edgeguard"] += 8.0
    if opponent in FLOATY:
        p["pressure"] += 4.0
        p["platform"] += 3.0
    if opponent in DANGEROUS_CLOSE:
        p["space"] += 6.0
        p["approach"] -= 4.0
    return p


def lines(character, opponent):
    """The handbook part of her match prompt."""
    out = [f"- {tip}" for tip in BASICS.get(character, [])]
    note = OPPONENTS.get(opponent)
    if note:
        out.append(f"- {opponent}: {note}")
    return "\n".join(out)
