/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Training Lab: savestates and a recorded dummy in an ordinary VS match, the
 * first of TrainingMode-CommunityEdition's events rebuilt for AI-Melee (no
 * code of it is used; it is PowerPC patched into the disc, this is the PC
 * layer). Off unless MELEE_LAB=1, and idle under netplay, record/replay and
 * sync test.
 *
 * You are P1, the dummy is P2 (open P2's door as HMN with no controller;
 * P1's cursor can then pick its character). In a fight, P1's D-pad belongs
 * to the Lab (no taunt):
 *
 *   Right  save state
 *   Left   load state (stops recording and playback)
 *   Down   record P2: your controller drives P2 and P1 stands still; Down
 *          again stops. The recording starts from a state of its own.
 *   Up     play the recording back on P2, from that state, on a loop, while
 *          you play P1; Up again stops
 *
 * Savestates are the netcode's rollback snapshots (net_snapshot.c), taken
 * and restored right before a tick consumes its pads, so a recording played
 * back from its state feeds every tick the inputs it had. Each action logs
 * a "lab: " line. The savestate and the recording last until the fight
 * ends.
 *
 * Hooks, like the agent bridge's: pc_lab_pre_tick from pc_net_sync's offline
 * path, pc_lab_post_tick from pc_slp_tick_end. */
#ifndef PC_LAB_H
#define PC_LAB_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* MELEE_LAB is set. */
bool pc_lab_enabled(void);
/* Before a simulation tick consumes its pad sample (after the agent's). */
void pc_lab_pre_tick(void);
/* After the tick's GObj procs; `proc_mask` as for pc_slp_tick_end. */
void pc_lab_post_tick(uint64_t proc_mask);
/* Character select: `door` is the dummy's, so P1's cursor may pick up its
 * HMN token. */
bool pc_lab_css_door(int door);

#ifdef __cplusplus
}
#endif

#endif
