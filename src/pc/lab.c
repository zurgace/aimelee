/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Training Lab, game side (lab.h): the hooks, the savestates and the pad
 * queue. The D-pad, the recording and the routing are lab_core.c. */
#include "compat.h"
#include "pc/lab.h"
#include "pc/lab_core.h"
#include "pc/net_internal.h"

#include <dolphin/ar.h>
#include <dolphin/dvd.h>
#include <dolphin/os.h>
#include <sysdolphin/baselib/controller.h>

#include <stdlib.h>
#include <string.h>

#define HSD_GOBJ_PLINK_FIGHTER 8 /* melee/ft/forward.h */
/* A take or load waits out a disc or ARAM transfer for up to two seconds. */
#define BUSY_RETRY_TICKS 120

enum { PENDING_NONE, PENDING_SAVE, PENDING_LOAD, PENDING_RECORD, PENDING_PLAY };

static int s_state; /* 0 unchecked, 1 on, -1 off */
static LabCore s_core;
static Snapshot s_slot; /* D-pad Right / Left */
static Snapshot s_rec;  /* where the recording starts */
static bool s_fight;
static int s_pending;
static int s_pending_ticks;

static void init_once(void) {
    const char* e = getenv("MELEE_LAB");
    s_state = e != NULL && e[0] != '\0' && strcmp(e, "0") != 0 ? 1 : -1;
    s_slot.frame = -1;
    s_rec.frame = -1;
    lab_core_reset(&s_core);
    if (s_state > 0) {
        const char* why = snapshot_state_region_missing();
        if (why != NULL) {
            pc_log_line("lab: savestates are not available on this build (%s)", why);
        } else {
            pc_log_line("lab: Training Lab on: you are P1, the dummy is P2; D-pad Right saves, "
                        "Left loads, Down records P2, Up plays it back");
        }
    }
}

bool pc_lab_enabled(void) {
    if (s_state == 0) {
        init_once();
    }
    return s_state > 0;
}

static bool usable(void) {
    return pc_lab_enabled() && !pc_net_deterministic();
}

bool pc_lab_css_door(int door) {
    return door == 1 && usable();
}

static bool io_busy(void) {
    return aurora_dvd_inflight() > 0 || aurora_arq_inflight() > 0;
}

/* The pad queue is the present's, not the saved timeline's (as for a
 * rollback, net.c rollback_to): keep the live queue and its bookkeeping
 * across the restore, so this tick still consumes the sample just read. */
static void restore(const Snapshot* s) {
    bool intr = OSDisableInterrupts();
    PadLibData pad = HSD_PadLibData;
    HSD_PadData queue[8];
    int qn = pad.qnum > 8 ? 8 : pad.qnum;
    memcpy(queue, pad.queue, qn * sizeof *queue);
    snapshot_restore(s);
    memcpy(pad.queue, queue, qn * sizeof *queue);
    pad.rumble_info = HSD_PadLibData.rumble_info;
    HSD_PadLibData = pad;
    OSRestoreInterrupts(intr);
}

/* NULL when `s` was restored, else why not. "busy" is worth a retry. */
static const char* try_restore(const Snapshot* s) {
    if (s->frame < 0) {
        return "nothing saved";
    }
    const char* why = snapshot_unusable(s);
    if (why != NULL) {
        return why;
    }
    if (io_busy()) {
        return "busy";
    }
    restore(s);
    return NULL;
}

/* NULL when `s` holds the state before this tick, else why not. */
static const char* try_take(Snapshot* s) {
    if (snapshot_take(s, net.frame)) {
        return NULL;
    }
    return snapshot_refused_io() ? "busy" : "out of memory";
}

static const char* const k_pending_what[] = {"", "save", "load", "start recording", "play back"};

/* Do `what` now, or keep it for the next ticks while a transfer is in
 * flight. True when it happened. */
static bool act(int what) {
    const char* why = NULL;
    switch (what) {
    case PENDING_SAVE:
        why = try_take(&s_slot);
        if (why == NULL) {
            pc_log_line("lab: saved state");
        }
        break;
    case PENDING_LOAD:
        why = try_restore(&s_slot);
        if (why == NULL) {
            pc_log_line("lab: loaded state");
        }
        break;
    case PENDING_RECORD:
        why = try_take(&s_rec);
        if (why == NULL) {
            lab_core_record_start(&s_core);
            pc_log_line("lab: recording P2 (your controller drives P2; D-pad Down stops)");
        }
        break;
    case PENDING_PLAY:
        why = try_restore(&s_rec);
        if (why == NULL) {
            lab_core_play_start(&s_core);
            pc_log_line("lab: playing back P2's recording, %d frames on a loop (D-pad Up stops)",
                s_core.rec_len);
        }
        break;
    default:
        return true;
    }
    if (why == NULL) {
        s_pending = PENDING_NONE;
        return true;
    }
    if (strcmp(why, "busy") == 0) {
        if (s_pending != what) {
            s_pending = what;
            s_pending_ticks = 0;
        }
        if (++s_pending_ticks <= BUSY_RETRY_TICKS) {
            return false;
        }
        why = "the disc was busy for two seconds";
    }
    s_pending = PENDING_NONE;
    pc_log_line("lab: could not %s: %s", k_pending_what[what], why);
    return false;
}

static void on_press(uint16_t pressed) {
    if (pressed & PAD_BUTTON_RIGHT) {
        act(PENDING_SAVE);
    }
    if (pressed & PAD_BUTTON_LEFT) {
        lab_core_record_stop(&s_core);
        lab_core_play_stop(&s_core);
        act(PENDING_LOAD);
    }
    if (pressed & PAD_BUTTON_DOWN) {
        if (s_core.mode == LAB_RECORDING) {
            lab_core_record_stop(&s_core);
            pc_log_line("lab: recorded %d frames (%.1f s); D-pad Up plays them back",
                s_core.rec_len, s_core.rec_len / 60.0);
        } else {
            lab_core_play_stop(&s_core);
            act(PENDING_RECORD);
        }
    }
    if (pressed & PAD_BUTTON_UP) {
        if (s_core.mode == LAB_PLAYING) {
            lab_core_play_stop(&s_core);
            pc_log_line("lab: playback stopped");
        } else if (s_core.mode == LAB_RECORDING) {
            lab_core_record_stop(&s_core);
            pc_log_line("lab: recorded %d frames (%.1f s)", s_core.rec_len, s_core.rec_len / 60.0);
            act(PENDING_PLAY);
        } else if (s_core.rec_len == 0) {
            pc_log_line("lab: nothing recorded yet (D-pad Down records P2)");
        } else {
            act(PENDING_PLAY);
        }
    }
}

/* Re-expose the slot the last tick consumed (net.c unconsume): a tick with
 * nothing queued would rerun the last sample and the Lab could not feed
 * it. NULL when the queue is full or was never set up. */
static PADStatus* unconsume(void) {
    PadLibData* p = &HSD_PadLibData;
    if (p->queue == NULL || p->qnum == 0 || p->qcount >= p->qnum) {
        return NULL;
    }
    p->qread = (uint8_t)((p->qread + p->qnum - 1) % p->qnum);
    p->qcount++;
    return p->queue[p->qread].stat;
}

static PADStatus* pad_head(bool feeding) {
    PadLibData* p = &HSD_PadLibData;
    if (p->queue == NULL) {
        return NULL;
    }
    if (p->qcount > 0) {
        return p->queue[p->qread].stat;
    }
    if (!feeding) {
        return NULL;
    }
    bool intr = OSDisableInterrupts();
    PADStatus* head = p->qcount > 0 ? p->queue[p->qread].stat : unconsume();
    OSRestoreInterrupts(intr);
    return head;
}

static void fight_over(void) {
    if (s_slot.frame >= 0 || s_core.rec_len > 0) {
        pc_log_line("lab: fight over; the savestate and the recording are cleared");
    }
    s_slot.frame = -1;
    s_rec.frame = -1;
    s_pending = PENDING_NONE;
    lab_core_reset(&s_core);
}

void pc_lab_pre_tick(void) {
    if (!usable()) {
        return;
    }
    const bool fight = in_fight();
    if (s_fight && !fight) {
        fight_over();
    }
    s_fight = fight;
    s_core.routed = false;
    if (!fight) {
        return;
    }
    PADStatus* head = pad_head(s_core.mode != LAB_IDLE || s_pending != PENDING_NONE);
    if (head == NULL) {
        return; /* the tick reruns the last sample: nothing new to read */
    }
    const uint16_t pressed = lab_core_presses(&s_core, &head[0]);
    if (s_pending != PENDING_NONE) {
        act(s_pending);
    }
    if (pressed != 0) {
        on_press(pressed);
    }
    if (lab_core_play_at_end(&s_core)) {
        const char* why = try_restore(&s_rec);
        if (why == NULL) {
            lab_core_play_rewound(&s_core);
        } else if (strcmp(why, "busy") != 0) {
            lab_core_play_stop(&s_core);
            pc_log_line("lab: playback stopped: could not go back to its start (%s)", why);
        }
        /* busy: P2 gets its own controller this tick and the loop restarts on
         * the next one */
    }
    if (lab_core_route(&s_core, head) == LAB_REC_FULL) {
        pc_log_line("lab: recording full at %d frames (%d s); D-pad Up plays it back",
            s_core.rec_len, LAB_REC_MAX / 60);
    }
}

void pc_lab_post_tick(uint64_t proc_mask) {
    if (s_state <= 0 || !s_fight) {
        return;
    }
    if (proc_mask & (1ULL << HSD_GOBJ_PLINK_FIGHTER)) {
        lab_core_unroute(&s_core); /* paused or frozen: that frame did not happen */
    }
}
