"""Which Phillip agent plays which character, on which stage.

Phillip trained agents for seven characters only, each on Final Destination
or Battlefield. A match on Final Destination gets the character's FD agent,
any other stage its Battlefield agent; when it has only one of the two, that
one plays everywhere. Per stage, the strongest (delay 0) agent is the
default; --reaction N prefers the agent trained with N network steps of
action delay (3 frames each), or the nearest lower one. Ganondorf and Roy,
clones of Falcon and Marth, borrow their agents. Every other character has
no agent: the port is left alone for that match.

Not used: FoxFD1 (its params do not describe its checkpoint, NOTES.md),
delay12/MarthFD (the predictive model, which Phillip itself cannot build),
and the delay18 agents unless their weights (on Google Drive, not in the
phillip repo) have been put in place -- Puff's only agent is one of them.
"""

import json
from pathlib import Path

import phillip_obs as po

FD = "final_destination"
BF = "battlefield"

# Phillip character -> [(delay in network steps, agent, stage)]. The stage
# follows the agent's name: delay18/FalcoFD's params say battlefield, but it
# is named, and was released, as the FD agent.
AGENTS = {
    "falcon": [(0, "FalconFalconBF", BF)],
    "fox": [(0, "delay0/FoxFD", FD), (6, "delay18/FoxFD", FD)],
    "falco": [(0, "delay0/FalcoFD", FD), (6, "delay18/FalcoFD", FD), (6, "delay18/FalcoBF", BF)],
    "marth": [(0, "MarthFD0", FD), (1, "MarthFD1", FD)],
    "peach": [(0, "PeachFD", FD), (1, "PeachFD1", FD), (2, "PeachFD2", FD)],
    "sheik": [(0, "SheikFD", FD), (1, "SheikFD1", FD), (2, "SheikFD2", FD)],
    "puff": [(4, "delay18/PuffFD", FD)],
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


def pick_delay(options, reaction):
    """The agent trained with `reaction` steps of delay, else the nearest
    lower; when only slower ones exist (Puff), the fastest of those."""
    fit = [(d, a) for d, a in options if d <= reaction]
    return max(fit)[1] if fit else min(options)[1]


def choose(phillip, reaction=0):
    """{phillip char: {stage: agent name}} for the characters with a usable
    agent, and [(agent, reason)] for the ones left out."""
    chosen, skipped = {}, []
    for char, options in AGENTS.items():
        per_stage = {}
        for d, a, stage in options:
            if has_weights(Path(phillip) / "agents" / a):
                per_stage.setdefault(stage, []).append((d, a))
            else:
                skipped.append((a, "weights not in the phillip checkout"))
        if per_stage:
            chosen[char] = {stage: pick_delay(opts, reaction) for stage, opts in per_stage.items()}
    return chosen, skipped


def for_stage(by_stage, stkind):
    """Final Destination gets the FD agent, any other stage the Battlefield
    one; with only one of them, that one. by_stage: {stage: anything}."""
    want = FD if stkind == po.STKIND_BY_PHILLIP_NAME[FD] else BF
    if want in by_stage:
        return by_stage[want]
    return next(iter(by_stage.values()))


def entries(chosen):
    """{ckind: {stage: (agent, agent's char, stand-in for)}} including the stand-ins."""
    out = {}
    for char, by_stage in chosen.items():
        out[po.CKIND_BY_PHILLIP_NAME[char]] = {st: (a, char, None) for st, a in by_stage.items()}
    for char, source in STAND_INS.items():
        if source in chosen:
            out[po.CKIND_BY_PHILLIP_NAME[char]] = {st: (a, source, char)
                                                   for st, a in chosen[source].items()}
    return out


def summary(chosen):
    def one(char, by_stage):
        name = display(po.CKIND_BY_PHILLIP_NAME[char])
        if len(by_stage) > 1:
            name += " (FD and BF agents)"
        return name
    own = [one(c, chosen[c]) for c in AGENTS if c in chosen]
    extra = [display(po.CKIND_BY_PHILLIP_NAME[c]) for c, s in STAND_INS.items() if s in chosen]
    text = ", ".join(own)
    if extra:
        text += f" (+ {', '.join(extra)} as stand-ins)"
    return text


def agents_in(chosen):
    return sorted({a for by_stage in chosen.values() for a in by_stage.values()})


def write(path, chosen, weights_for):
    """The roster file agent.py --roster reads: ckind -> stage -> weights and characters."""
    data = {str(ck): {st: {"agent": agent, "weights": str(weights_for(agent)), "char": char,
                           "stand_in_for": stand_in}
                      for st, (agent, char, stand_in) in by_stage.items()}
            for ck, by_stage in entries(chosen).items()}
    Path(path).write_text(json.dumps(data, indent=1))


def read(path):
    return {int(ck): v for ck, v in json.loads(Path(path).read_text()).items()}
