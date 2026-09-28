/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Transport half of the agent bridge (agent_bridge.h): the listening socket,
 * the one connected agent, message framing and the per-tick input wait. It
 * knows nothing about the game, so tools/test_agent_link.c drives it with a
 * fake game loop.
 *
 * Every call is non-blocking except agent_link_take_input, whose wait is
 * bounded by the configured timeout. A client that disconnects, stops
 * reading or sends garbage is dropped and the socket keeps listening; the
 * game never waits on it past one timeout per tick. */
#ifndef PC_AGENT_LINK_H
#define PC_AGENT_LINK_H

#include "pc/agent_proto.h"

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AgentLinkConfig {
    const char* path;       /* socket path; an existing socket there is replaced */
    int agent_port;         /* 0-3 */
    int sync_mode;          /* AgentSyncMode */
    uint32_t timeout_us;    /* lockstep wait per tick */
    uint32_t degrade_after; /* consecutive misses before lockstep stops waiting */
    const char* build;      /* version string for AgentHello */
} AgentLinkConfig;

typedef enum AgentTake {
    AGENT_TAKE_NONE = 0,    /* no input applies: keep driving with the last pad */
    AGENT_TAKE_ON_TIME = 1, /* the input targeted at this tick */
    AGENT_TAKE_LATE = 2,    /* an older input (async, or lockstep after a miss) */
} AgentTake;

typedef struct AgentLinkStats {
    uint32_t clients; /* connections accepted */
    uint32_t states_sent;
    uint32_t states_dropped; /* the agent was not reading */
    uint32_t inputs_received;
    uint32_t inputs_stale; /* arrived after a newer one had already applied */
    uint32_t misses;       /* lockstep waits that timed out */
    uint32_t consecutive_misses;
    bool degraded; /* lockstep has stopped waiting until an input is on time */
} AgentLinkStats;

bool agent_link_open(const AgentLinkConfig* cfg);
void agent_link_close(void);
/* Accept a new client (replacing the old one) and read what has arrived. */
void agent_link_poll(void);
bool agent_link_connected(void);
/* The client has sent a non-release input since it connected. */
bool agent_link_active(void);
/* Queue one state for the client; dropped (and counted) if it is not reading. */
void agent_link_send_state(const AgentState* st);
/* The pad the agent wants on `tick`. With `wait` in lockstep mode this blocks
 * up to the timeout for the input targeted at `tick`. */
AgentTake agent_link_take_input(uint32_t tick, bool wait, AgentPad* out);
const AgentLinkStats* agent_link_stats(void);

#ifdef __cplusplus
}
#endif

#endif
