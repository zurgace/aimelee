/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Agent bridge, game side (agent_bridge.h): configuration, the per-tick
 * hooks and what they read from and write to the game. The socket and the
 * wait live in agent_link.c. */
#include "compat.h"
#include "pc/agent_bridge.h"
#include "pc/agent_link.h"
#include "pc/net.h"
#include "pc/pc.h"

#include <dolphin/pad.h>
#include <sysdolphin/baselib/controller.h>
#include <sysdolphin/baselib/gobj.h>
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wscalar-storage-order" /* disc-struct unions in lb/types.h */
#include <melee/ft/fighter.h>
#include <melee/ft/inlines.h>
#include <melee/ft/types.h>
#include <melee/gm/gm_1A3F.h>
#include <melee/gm/gmscene.h>
#include <melee/gm/gmvs.h>
#include <melee/gm/types.h>
#include <melee/pl/player.h>
#pragma GCC diagnostic pop

#include <stdlib.h>
#include <string.h>

extern struct GameSceneInfo* gm_804D6720; /* current scene, gmscene.c */
const char* pc_app_version(void);         /* version.cpp */

#define DEFAULT_PORT 1          /* P2: Phillip's default seat, P1 is the keyboard */
#define DEFAULT_TIMEOUT_US 4000 /* lockstep wait per tick */
#define MAX_TIMEOUT_US 50000
#define DEGRADE_AFTER_MISSES 30 /* half a second of misses: stop waiting */

static int s_state; /* 0 unchecked, 1 on, -1 off */
static int s_port;
static uint32_t s_tick;
static bool s_fight;       /* this tick runs in a VS / Sudden Death scene */
static bool s_match_start; /* ...and is the first tick of it */
static uint32_t s_match_tick;
static bool s_pad_fresh;                     /* the tick has a queued pad sample to consume */
static AgentPad s_consumed[AGENT_MAX_PORTS]; /* what the tick consumes, per port */

static void shutdown_link(void) {
    agent_link_close();
}

static void init_once(void) {
    s_state = -1;
    const char* path = getenv("MELEE_AGENT_SOCKET");
    if (path == NULL || path[0] == '\0') {
        return;
    }
    AgentLinkConfig cfg = {
        .path = path,
        .agent_port = DEFAULT_PORT,
        .sync_mode = AGENT_SYNC_LOCKSTEP,
        .timeout_us = DEFAULT_TIMEOUT_US,
        .degrade_after = DEGRADE_AFTER_MISSES,
        .build = pc_app_version(),
    };
    const char* port = getenv("MELEE_AGENT_PORT");
    if (port != NULL && port[0] != '\0') {
        const int p = atoi(port);
        if (p >= 1 && p <= AGENT_MAX_PORTS) {
            cfg.agent_port = p - 1;
        } else {
            pc_log_line("agent: MELEE_AGENT_PORT=%s is not 1-4; using %d", port, DEFAULT_PORT + 1);
        }
    }
    const char* sync = getenv("MELEE_AGENT_SYNC");
    if (sync != NULL && strcmp(sync, "async") == 0) {
        cfg.sync_mode = AGENT_SYNC_ASYNC;
    } else if (sync != NULL && sync[0] != '\0' && strcmp(sync, "lockstep") != 0) {
        pc_log_line("agent: MELEE_AGENT_SYNC=%s is not lockstep or async; using lockstep", sync);
    }
    const char* timeout = getenv("MELEE_AGENT_TIMEOUT_MS");
    if (timeout != NULL && timeout[0] != '\0') {
        const double ms = strtod(timeout, NULL);
        if (ms >= 0.0 && ms * 1000.0 <= MAX_TIMEOUT_US) {
            cfg.timeout_us = (uint32_t)(ms * 1000.0);
        } else {
            pc_log_line("agent: MELEE_AGENT_TIMEOUT_MS=%s out of range 0-%d; using %d", timeout,
                MAX_TIMEOUT_US / 1000, DEFAULT_TIMEOUT_US / 1000);
        }
    }
    if (!agent_link_open(&cfg)) {
        return;
    }
    s_port = cfg.agent_port;
    s_state = 1;
    atexit(shutdown_link);
    pc_log_line("agent: listening on %s; agent drives port %d, %s, timeout %.1f ms", path,
        s_port + 1, cfg.sync_mode == AGENT_SYNC_ASYNC ? "async" : "lockstep",
        cfg.timeout_us / 1000.0);
}

/* The bridge acts on this tick: configured, and not in a session whose
 * simulation has to stay reproducible elsewhere. */
static bool usable(void) {
    if (s_state == 0) {
        init_once();
    }
    if (s_state < 0) {
        return false;
    }
    if (pc_net_deterministic()) {
        static bool logged;
        if (!logged) {
            logged = true;
            pc_log_line("agent: netplay, record/replay or sync test is on; the bridge stays idle");
        }
        return false;
    }
    return true;
}

bool pc_agent_enabled(void) {
    if (s_state == 0) {
        init_once();
    }
    return s_state > 0;
}

static bool in_fight(void) {
    const int k = gm_804D6720 != NULL ? gm_804D6720->scene_kind : -1;
    return k == GS_VS || k == GS_SUDDEN_DEATH;
}

static void pad_to_wire(AgentPad* w, const PADStatus* p) {
    memset(w, 0, sizeof *w);
    w->button = p->button;
    w->stick_x = p->stickX;
    w->stick_y = p->stickY;
    w->cstick_x = p->substickX;
    w->cstick_y = p->substickY;
    w->trigger_l = p->triggerLeft;
    w->trigger_r = p->triggerRight;
    w->analog_a = p->analogA;
    w->analog_b = p->analogB;
    w->err = p->err;
}

/* The queue slot HSD_PadRenewMasterStatus pops at the start of the tick
 * (net.c pad_head); NULL when the tick has nothing queued and will run on
 * the previous sample. */
static PADStatus* pad_head(void) {
    PadLibData* p = &HSD_PadLibData;
    if (p->queue == NULL || p->qcount == 0) {
        return NULL;
    }
    return p->queue[p->qread].stat;
}

/* The port's active fighter, while it has one in the current fight (the
 * guard slp.c's fighter_gobj uses; player slots keep dangling entity
 * pointers between scenes, so only call this in a fight). */
static HSD_GObj* fighter_gobj(int port) {
    if (Player_GetPlayerSlotType(port) == Gm_PKind_NA) {
        return NULL;
    }
    HSD_GObj* g = Player_GetEntity(port);
    if (g == NULL || g->classifier != HSD_GOBJ_CLASS_FIGHTER || g->user_data == NULL) {
        return NULL;
    }
    return g;
}

/* One port after the tick. The field comments give the GameCube player
 * block offset Phillip's memory watcher read (its pointer was fp - 0x60,
 * so its "0x70" is fp+10); everything is read by name. */
static void capture_fighter(AgentFighter* f, int port) {
    f->slot_type = (uint8_t)Player_GetPlayerSlotType(port);
    HSD_GObj* g = fighter_gobj(port);
    if (g == NULL) {
        return;
    }
    const Fighter* fp = GET_FIGHTER(g);
    const StaticPlayer* sp = Player_GetPtrForSlot(port);
    f->present = 1;
    f->ckind = (uint8_t)Player_GetPlayerCharacter(port);
    f->fkind = (uint8_t)fp->kind; /* fp+4 */
    f->stocks = (int8_t)Player_GetStocks(port);
    f->flags = (uint8_t)((fp->x221F_b3 ? AGENT_FT_ASLEEP : 0) |
                         (fp->ground_or_air == GA_Air ? AGENT_FT_IN_AIR : 0) |
                         (fp->x221C_b6 ? AGENT_FT_IN_HITSTUN : 0) |
                         (fp->x2219_b5 ? AGENT_FT_IN_HITLAG : 0));
    f->jumps_used = fp->x1968_jumpsUsed;            /* fp+1968 */
    f->max_jumps = (uint8_t)fp->co_attrs.max_jumps; /* fp+168, big-endian disc attrs */
    /* StaticPlayer+0x60: the HUD's integer percent, what Phillip embedded. */
    f->percent = (int16_t)Player_GetDamage(port);
    f->motion_id = (uint32_t)fp->motion_id; /* fp+10 */
    f->percent_f = fp->dmg.x1830_percent;   /* fp+1830 */
    f->facing = fp->facing_dir;             /* fp+2C */
    /* StaticPlayer+0x10: player_poses[0], copied from cur_pos by the last
     * fighter proc (Fighter_8006DA4C) and left alone while asleep. */
    f->pos_x = sp->player_poses.byIndex[0].x;
    f->pos_y = sp->player_poses.byIndex[0].y;
    f->cur_x = fp->cur_pos.x;                          /* fp+B0 */
    f->cur_y = fp->cur_pos.y;                          /* fp+B4 */
    f->action_frame = fp->cur_anim_frame;              /* fp+894 */
    f->hitlag = fp->dmg.x195c_hitlag_frames;           /* fp+195C */
    memcpy(&f->mv0_bits, &fp->mv, sizeof f->mv0_bits); /* fp+2340: hitstun left in damage states */
    f->shield = fp->shield_health;                     /* fp+1998 */
    f->self_vx = fp->self_vel.x;                       /* fp+80 */
    f->self_vy = fp->self_vel.y;                       /* fp+84 */
    f->kb_vx = fp->x8c_kb_vel.x;                       /* fp+8C */
    f->kb_vy = fp->x8c_kb_vel.y;                       /* fp+90 */
    f->ground_vx = fp->gr_vel;                         /* fp+EC */
    f->body_state = fp->x198C;                         /* fp+198C */
    f->body_state_move = (int32_t)fp->x1988;           /* fp+1988 */
    f->smash_state = (uint32_t)fp->smash_attrs.state;  /* fp+2114 */
}

void pc_agent_pre_tick(void) {
    if (!usable()) {
        return;
    }
    s_tick++;
    const bool fight = in_fight();
    s_match_start = fight && !s_fight;
    s_match_tick = s_match_start || !fight ? 0 : s_match_tick + 1;
    s_fight = fight;
    agent_link_poll();

    PADStatus* head = pad_head();
    s_pad_fresh = head != NULL;
    for (int i = 0; i < AGENT_MAX_PORTS; i++) {
        if (head != NULL) {
            pad_to_wire(&s_consumed[i], &head[i]);
        } else {
            memset(&s_consumed[i], 0, sizeof s_consumed[i]);
        }
    }
}

void pc_agent_post_tick(uint64_t proc_mask) {
    if (!usable()) {
        return;
    }
    agent_link_poll();
    if (!agent_link_connected()) {
        return;
    }
    AgentState st;
    memset(&st, 0, sizeof st);
    st.tick = s_tick;
    st.match_tick = s_match_tick;
    st.scene_kind = gm_804D6720 != NULL ? gm_804D6720->scene_kind : 0xFF;
    st.game_mode = gm_GetCurrentGameMode();
    st.scene_frame = gm_801A4BB8();
    st.agent_port = (uint8_t)s_port;
    if (s_pad_fresh) {
        st.flags |= AGENT_ST_PAD_FRESH;
    }
    if (s_fight) {
        st.flags |= AGENT_ST_IN_FIGHT;
        if (s_match_start) {
            st.flags |= AGENT_ST_MATCH_START;
        }
        /* The p_link mask holds the fighters on a paused tick and from
         * GAME! on (as slp.c reads it). */
        if (!(proc_mask & (1ULL << HSD_GOBJ_PLINK_FIGHTER))) {
            st.flags |= AGENT_ST_FIGHTERS_RAN;
        }
        const VsSceneController* vs = gmVs_GetSceneController();
        st.vs_frame = vs->state.frame_count;
        st.match_result = vs->state.match_result;
        st.stage = gm_GetStKind();
        for (int i = 0; i < AGENT_MAX_PORTS; i++) {
            capture_fighter(&st.fighters[i], i);
        }
    }
    for (int i = 0; i < AGENT_MAX_PORTS; i++) {
        st.fighters[i].input = s_consumed[i];
    }
    st.dropped_states = agent_link_stats()->states_dropped;
    agent_link_send_state(&st);
}
