/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Wire format of the agent bridge (MELEE_AGENT_SOCKET, see agent_bridge.h).
 *
 * One AF_UNIX SOCK_STREAM connection: the game listens, one agent process
 * connects. Every message is an AgentMsgHeader followed by `size` payload
 * bytes. All fields are little-endian and the structs are packed, so the
 * Python side (tools/agent/bridge.py) reads them with a fixed struct format;
 * the sizes below are part of the protocol and checked at compile time.
 *
 * game -> agent: AGENT_MSG_HELLO once on connect, then AGENT_MSG_STATE after
 *                every simulation tick (menus included).
 * agent -> game: AGENT_MSG_INPUT, the pad for one tick of the agent's port.
 *
 * Bump AGENT_PROTO_VERSION on any layout change. */
#ifndef PC_AGENT_PROTO_H
#define PC_AGENT_PROTO_H

#include <stdint.h>

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "agent_proto.h assumes a little-endian host"
#endif

#define AGENT_PROTO_VERSION 1
#define AGENT_MSG_MAGIC 0x4247414Du /* "MAGB" as bytes on the wire */

enum AgentMsgType {
    AGENT_MSG_HELLO = 1,
    AGENT_MSG_STATE = 2,
    AGENT_MSG_INPUT = 3,
};

#define AGENT_MAX_PORTS 4

typedef struct __attribute__((packed)) AgentMsgHeader {
    uint32_t magic;
    uint16_t type;
    uint16_t size; /* payload bytes after this header */
} AgentMsgHeader;

/* A PADStatus as the game's raw pad queue holds it: sticks centred at 0,
 * the game clamps them to radius 80 and triggers to 140 itself. */
typedef struct __attribute__((packed)) AgentPad {
    uint16_t button; /* PAD_BUTTON_* bits */
    int8_t stick_x, stick_y;
    int8_t cstick_x, cstick_y;
    uint8_t trigger_l, trigger_r;
    uint8_t analog_a, analog_b;
    int8_t err; /* PAD_ERR_NONE (0) or PAD_ERR_NO_CONTROLLER (-1) */
    uint8_t reserved;
} AgentPad;

enum AgentSyncMode {
    AGENT_SYNC_LOCKSTEP = 0, /* wait (bounded) for the input of each tick */
    AGENT_SYNC_ASYNC = 1,    /* never wait; use the newest input received */
};

typedef struct __attribute__((packed)) AgentHello {
    uint16_t proto_version;
    uint8_t agent_port; /* 0-3: the port the bridge lets the agent drive */
    uint8_t sync_mode;  /* AgentSyncMode */
    uint16_t timeout_us;
    uint16_t reserved;
    uint32_t state_size; /* sizeof(AgentState), a second layout check */
    char build[32];      /* melee-pc version string, NUL padded */
} AgentHello;

/* AgentFighter.flags */
enum {
    AGENT_FT_ASLEEP = 1 << 0,     /* fp+221F:3, e.g. between stocks or out of the match */
    AGENT_FT_IN_AIR = 1 << 1,     /* ground_or_air == GA_Air */
    AGENT_FT_IN_HITSTUN = 1 << 2, /* fp+221C:6 */
    AGENT_FT_IN_HITLAG = 1 << 3,  /* fp+2219:5 */
};

/* One port's active fighter after the tick. Units are the game's own:
 * positions and speeds in world units, frames as floats. */
typedef struct __attribute__((packed)) AgentFighter {
    uint8_t present;    /* 1 when the port has a fighter in the current fight */
    uint8_t slot_type;  /* Gm_PKind: 0 human, 1 CPU, 2 demo, 3 none, 4 boss */
    uint8_t ckind;      /* CharacterKind (CSS/external id), CKind_Captain = 0 */
    uint8_t fkind;      /* FighterKind (internal id), Ft_Kind_Captain = 2 */
    int8_t stocks;      /* Player_GetStocks */
    uint8_t flags;      /* AGENT_FT_* */
    uint8_t jumps_used; /* fp+1968 */
    uint8_t max_jumps;  /* co_attrs.max_jumps */
    int16_t percent;    /* Player_GetDamage: the HUD's integer percent (Phillip's) */
    uint16_t reserved;
    uint32_t motion_id; /* action state, fp+10 */
    float percent_f;    /* fp+1830, the exact float percent */
    float facing;       /* fp+2C, +1 right, -1 left */
    float pos_x, pos_y; /* StaticPlayer player_poses[0], what Phillip read */
    float cur_x, cur_y; /* fp->cur_pos */
    float action_frame; /* fp+894 */
    float hitlag;       /* fp+195C, frames left */
    uint32_t mv0_bits;  /* fp+2340, first motion-variable word: hitstun float in damage states */
    float shield;       /* fp+1998 */
    float self_vx;      /* fp+80 */
    float self_vy;      /* fp+84 */
    float kb_vx;        /* fp+8C */
    float kb_vy;        /* fp+90 */
    float ground_vx;    /* fp+EC */
    int32_t body_state; /* fp+198C: 0 vulnerable, 1 invulnerable, 2 intangible */
    int32_t body_state_move; /* fp+1988, the move-induced one */
    uint32_t smash_state;    /* fp+2114 SmashState */
    AgentPad input;          /* the raw pad sample this tick consumed for the port */
} AgentFighter;

/* AgentState.flags */
enum {
    AGENT_ST_IN_FIGHT = 1 << 0,     /* a VS / Sudden Death scene */
    AGENT_ST_FIGHTERS_RAN = 1 << 1, /* the fighters' procs ran this tick (not paused/frozen) */
    AGENT_ST_MATCH_START = 1 << 2,  /* first tick of a new fight */
    AGENT_ST_AGENT_INPUT = 1 << 3,  /* this tick consumed an agent input on the agent port */
    AGENT_ST_INPUT_LATE = 1 << 4,   /* lockstep waited and the input did not arrive in time */
    AGENT_ST_PAD_FRESH = 1 << 5,    /* the tick consumed a fresh pad sample */
};

typedef struct __attribute__((packed)) AgentState {
    uint32_t tick;        /* bridge tick counter: +1 per simulation tick, every scene */
    uint32_t match_tick;  /* ticks since the fight started (0 on its first), 0 outside */
    uint32_t scene_frame; /* gm_801A4BB8(): the scene's unpaused tick count */
    uint32_t vs_frame;    /* VsSceneState.frame_count, 0 outside a fight */
    uint8_t scene_kind;   /* GameSceneKind, e.g. GS_VS = 2 */
    uint8_t game_mode;    /* GameModeKind */
    uint8_t flags;        /* AGENT_ST_* */
    uint8_t match_result; /* VsSceneState.match_result, 0 while playing */
    uint16_t stage;       /* StKind, e.g. Battlefield 0x1F, Final Destination 0x20 */
    uint8_t agent_port;
    uint8_t reserved;
    uint32_t late_inputs;    /* cumulative lockstep misses */
    uint32_t dropped_states; /* cumulative states not sent (agent not reading) */
    AgentFighter fighters[AGENT_MAX_PORTS];
} AgentState;

/* AgentInput.flags */
enum {
    AGENT_IN_RELEASE = 1 << 0, /* stop driving the port until the next non-release input */
};

typedef struct __attribute__((packed)) AgentInput {
    uint32_t target_tick; /* the tick to apply it on: AgentState.tick + 1 */
    uint8_t port;         /* must be the agent port from AgentHello */
    uint8_t flags;        /* AGENT_IN_* */
    uint16_t reserved;
    AgentPad pad;
} AgentInput;

_Static_assert(sizeof(AgentMsgHeader) == 8, "wire layout");
_Static_assert(sizeof(AgentPad) == 12, "wire layout");
_Static_assert(sizeof(AgentHello) == 44, "wire layout");
_Static_assert(sizeof(AgentFighter) == 100, "wire layout");
_Static_assert(sizeof(AgentState) == 32 + AGENT_MAX_PORTS * 100, "wire layout");
_Static_assert(sizeof(AgentInput) == 20, "wire layout");

#endif
