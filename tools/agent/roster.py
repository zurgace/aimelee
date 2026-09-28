"""Which Phillip agent plays which character.

Phillip trained agents for seven characters only. Each gets its strongest
(delay 0) agent by default; --reaction N prefers the agent trained with N
network steps of action delay (3 frames each), or the nearest lower one.
Ganondorf and Roy, clones of Falcon and Marth, borrow their agents. Every
other character has no agent: the port is left alone for that match.

Not used: FoxFD1 (its params do not describe its checkpoint, NOTES.md),
delay12/MarthFD (the predictive model, which Phillip itself cannot build),
and the delay18 agents unless their weights (on Google Drive, not in the
phillip repo) have been put in place -- Puff's only agent is one of them.
"""

import json
from pathlib import Path

import phillip_obs as po

# Phillip character -> [(delay in network steps, agent)], strongest first.
AGENTS = {
    "falcon": [(0, "FalconFalconBF")],
    "fox": [(0, "delay0/FoxFD")],
    "falco": [(0, "delay0/FalcoFD")],
    "marth": [(0, "MarthFD0"), (1, "MarthFD1")],
    "peach": [(0, "PeachFD"), (1, "PeachFD1"), (2, "PeachFD2")],
    "sheik": [(0, "SheikFD"), (1, "SheikFD1"), (2, "SheikFD2")],
    "puff": [(4, "delay18/PuffFD")],
}

# Characters with no agent of their own -> the character whose agent stands in.
STAND_INS = {"ganon": "falcon", "roy": "marth"}

# CKind -> the name the character select shows.
DISPLAY = {
    0x00: "Captain Falcon", 0x01: "Donkey Kong", 0x02: "Fox", 0x03: "Mr. Game & Watch",
    0x04: "Kirby", 0x05: "Bowser", 0x06: "Link", 0x07: "Luigi", 0x08: "Mario", 0x09: "Marth",
    0x0A: "Mewtwo", 0x0B: "Ness", 0x0C: "Peach", 0x0D: "Pikachu", 0x0E: "Ice Climbers",
    0x0F: "Jigglypuff", 0x10: "Samus", 0x11: "Yoshi", 0x12: "Zelda", 0x13: "Sheik",
    0x14: "Falco", 0x15: "Young Link", 0x16: "Dr. Mario", 0x17: "Roy", 0x18: "Pichu",
    0x19: "Ganondorf",
}


def display(ckind):
    return DISPLAY.get(ckind, f"character {ckind}")


def has_weights(agent_dir):
    return (agent_dir / "params").exists() and any(
        (agent_dir / n).exists() for n in ("snapshot", "snapshot.index"))


def choose(phillip, reaction=0):
    """{phillip char: agent name} for the characters with a usable agent, and
    [(agent, reason)] for the ones left out."""
    chosen, skipped = {}, []
    for char, options in AGENTS.items():
        usable = [(d, a) for d, a in options if has_weights(Path(phillip) / "agents" / a)]
        skipped += [(a, "weights not in the phillip checkout") for d, a in options
                    if (d, a) not in usable]
        fit = [(d, a) for d, a in usable if d <= reaction]
        if fit:
            chosen[char] = max(fit)[1]
        elif usable:
            chosen[char] = min(usable)[1]  # only slower agents exist (Puff): take the fastest
    return chosen, skipped


def entries(chosen):
    """{ckind: (agent, agent's char, stand-in for)} including the stand-ins."""
    out = {}
    for char, agent in chosen.items():
        out[po.CKIND_BY_PHILLIP_NAME[char]] = (agent, char, None)
    for char, source in STAND_INS.items():
        if source in chosen:
            out[po.CKIND_BY_PHILLIP_NAME[char]] = (chosen[source], source, char)
    return out


def summary(chosen):
    own = [display(po.CKIND_BY_PHILLIP_NAME[c]) for c in AGENTS if c in chosen]
    extra = [display(po.CKIND_BY_PHILLIP_NAME[c]) for c, s in STAND_INS.items() if s in chosen]
    text = ", ".join(own)
    if extra:
        text += f" (+ {', '.join(extra)} as stand-ins)"
    return text


def write(path, chosen, weights_for):
    """The roster file agent.py --roster reads: ckind -> weights and characters."""
    data = {str(ck): {"agent": agent, "weights": str(weights_for(agent)), "char": char,
                      "stand_in_for": stand_in}
            for ck, (agent, char, stand_in) in entries(chosen).items()}
    Path(path).write_text(json.dumps(data, indent=1))


def read(path):
    return {int(ck): v for ck, v in json.loads(Path(path).read_text()).items()}
