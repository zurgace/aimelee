"""Phillip's decision loop (agent.Agent.act), without TensorFlow or I/O.

Call act() once per game frame with the two players' observations, opponent
first and the agent second (Phillip's view after its optional swap). It
returns what Phillip's Pad would have sent that frame: a SimpleController,
or REPEAT when the chain step sends nothing.

Mirrors vladfi1/phillip (MIT) exactly, including its ring buffers:
- a network step runs every act_every frames; in between the chain repeats
  (ssbm.ActionChain), re-checking the banned rules against the current frame;
- `delay` is in network steps: the action executed now was chosen `delay`
  steps ago, and the network sees the queued ones (Agent.actions);
- `memory` extra past steps are fed, each [state, previous executed action];
- sampling is numpy's choice() on (1 - eps) * softmax + eps / A.
"""

import copy

import numpy as np

import phillip_obs as po


class CircularQueue:
    """util.CircularQueue, verbatim semantics."""

    def __init__(self, size, init):
        self.size = size
        self.array = [copy.deepcopy(init) for _ in range(size)]
        self.index = 0

    def push(self, obj):
        self.array[self.index] = obj
        self.increment()
        return self.array[self.index]

    def peek(self):
        return self.array[self.index]

    def increment(self):
        self.index = (self.index + 1) % self.size

    def __getitem__(self, index):
        return self.array[(self.size + self.index + index) % self.size]

    def as_list(self):
        return self.array[self.index:] + self.array[:self.index]


class PhillipAgent:
    def __init__(self, model, char, epsilon=0.0, real_delay=0, seed=None, policy_fn=None):
        """model: a PhillipModel. char: Phillip's name for the agent's character
        (for the banned-action rules). policy_fn(history, delayed, hidden)
        replaces model.policy (the verifier plugs TF in here)."""
        self.model = model
        self.char = char
        self.epsilon = epsilon
        self.real_delay = real_delay
        self.rng = np.random.RandomState(seed)
        self.policy_fn = policy_fn or model.policy
        self.steps = 0
        self.reset()

    def reset(self):
        """A new match: Phillip started each game with a fresh Agent."""
        m = self.model
        self.actions = CircularQueue(m.delay + 1, 0)
        self.history = CircularQueue(m.memory + 1, [[po.PlayerObs(), po.PlayerObs()], 0])
        self.hidden = m.zero_hidden()
        self.action = 0
        self.chain = None
        self.chain_index = 0
        self.last_probs = None

    def _send(self, me):
        c = self.chain[self.chain_index]
        self.chain_index += 1
        if c is po.REPEAT:
            return po.REPEAT
        if c.banned(me, self.char):
            return po.NEUTRAL
        return c

    def act(self, players):
        """players: [opponent PlayerObs, agent PlayerObs] for the current frame."""
        if self.chain is not None and self.chain_index < len(self.chain):
            return self._send(players[1])

        current = self.history.peek()
        current[0] = [copy.copy(players[0]), copy.copy(players[1])]
        current[1] = self.action
        self.history.increment()
        history = [(entry[0], entry[1]) for entry in self.history.as_list()]
        delayed = self.actions.as_list()[1:]

        self.model.epsilon = self.epsilon
        probs, self.hidden = self.policy_fn(history, delayed, self.hidden)
        self.last_probs = probs
        action = self.rng.choice(self.model.num_actions, p=probs)
        self.steps += 1

        self.action = self.actions.push(action)
        real_action = self.actions[self.real_delay]
        self.chain = po.action_chain(self.model.action_type, int(real_action), self.model.act_every)
        self.chain_index = 0
        return self._send(players[1])
