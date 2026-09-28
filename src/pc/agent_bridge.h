/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Agent bridge: lets an external process (tools/agent, e.g. the Phillip AI)
 * watch the game and drive one controller port. Off unless
 * MELEE_AGENT_SOCKET=<path> is set; then the game listens on that AF_UNIX
 * socket, sends the state after every simulation tick and takes the agent
 * port's pad from the agent before the next one. Wire format: agent_proto.h.
 *
 *   MELEE_AGENT_SOCKET=<path>     enable, listening on <path>
 *   MELEE_AGENT_PORT=<1-4>        the port the agent drives (default 2)
 *   MELEE_AGENT_SYNC=lockstep|async
 *                                 lockstep (default) waits up to the timeout
 *                                 for each tick's input; async never waits
 *   MELEE_AGENT_TIMEOUT_MS=<ms>   lockstep wait per tick (default 4)
 *
 * Both hooks run on the game thread from existing per-tick calls, so the
 * decomp is untouched: pc_agent_pre_tick from pc_net_sync's offline path
 * (right before the tick pops the pad queue) and pc_agent_post_tick from
 * pc_slp_tick_end (right after the tick's GObj procs). The bridge stays off
 * under netplay, record/replay and sync test. */
#ifndef PC_AGENT_BRIDGE_H
#define PC_AGENT_BRIDGE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* MELEE_AGENT_SOCKET is set and the socket is listening. */
bool pc_agent_enabled(void);
/* Before a simulation tick consumes its pad sample. */
void pc_agent_pre_tick(void);
/* After the tick's GObj procs; `proc_mask` as for pc_slp_tick_end. */
void pc_agent_post_tick(uint64_t proc_mask);

#ifdef __cplusplus
}
#endif

#endif
