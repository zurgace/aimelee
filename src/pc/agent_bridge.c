/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Agent bridge, game side (agent_bridge.h): configuration, the per-tick
 * hooks and what they read from and write to the game. The socket and the
 * wait live in agent_link.c. */
#include "compat.h"
#include "pc/agent_bridge.h"
#include "pc/agent_link.h"
#include "pc/net.h"
#include "pc/pc.h"

#include <sysdolphin/baselib/controller.h>
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wscalar-storage-order" /* disc-struct unions in lb/types.h */
#include <melee/gm/gm_1A3F.h>
#include <melee/gm/gmscene.h>
#include <melee/gm/types.h>
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

void pc_agent_pre_tick(void) {
    if (!usable()) {
        return;
    }
    s_tick++;
    agent_link_poll();
}

void pc_agent_post_tick(uint64_t proc_mask) {
    (void)proc_mask;
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
    st.scene_kind = gm_804D6720 != NULL ? gm_804D6720->scene_kind : 0xFF;
    st.game_mode = gm_GetCurrentGameMode();
    st.scene_frame = gm_801A4BB8();
    st.agent_port = (uint8_t)s_port;
    if (in_fight()) {
        st.flags |= AGENT_ST_IN_FIGHT;
    }
    st.dropped_states = agent_link_stats()->states_dropped;
    agent_link_send_state(&st);
}
