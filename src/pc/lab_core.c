/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Training Lab: D-pad presses, the P2 recording and the per-tick routing
 * (lab_core.h). */
#include "pc/lab_core.h"

#include <string.h>

void lab_core_reset(LabCore* c) {
    c->held = 0;
    c->mode = LAB_IDLE;
    c->rec_len = 0;
    c->pos = 0;
    c->routed = false;
}

uint16_t lab_core_presses(LabCore* c, PADStatus* p1) {
    const uint16_t now = p1->err == PAD_ERR_NONE ? (uint16_t)(p1->button & LAB_DPAD) : 0;
    const uint16_t pressed = (uint16_t)(now & ~c->held);
    c->held = now;
    p1->button = (uint16_t)(p1->button & ~LAB_DPAD);
    return pressed;
}

void lab_core_record_start(LabCore* c) {
    c->mode = LAB_RECORDING;
    c->rec_len = 0;
    c->pos = 0;
}

void lab_core_record_stop(LabCore* c) {
    if (c->mode == LAB_RECORDING) {
        c->mode = LAB_IDLE;
    }
}

bool lab_core_play_start(LabCore* c) {
    if (c->rec_len == 0) {
        return false;
    }
    c->mode = LAB_PLAYING;
    c->pos = 0;
    return true;
}

void lab_core_play_stop(LabCore* c) {
    if (c->mode == LAB_PLAYING) {
        c->mode = LAB_IDLE;
    }
}

bool lab_core_play_at_end(const LabCore* c) {
    return c->mode == LAB_PLAYING && c->pos >= c->rec_len;
}

void lab_core_play_rewound(LabCore* c) {
    c->pos = 0;
}

int lab_core_route(LabCore* c, PADStatus pads[4]) {
    c->routed = false;
    if (c->mode == LAB_RECORDING) {
        if (c->rec_len >= LAB_REC_MAX) {
            c->mode = LAB_IDLE;
            return LAB_REC_FULL;
        }
        PADStatus p = pads[0];
        p.button = (uint16_t)(p.button & ~LAB_DPAD);
        p.err = PAD_ERR_NONE;
        c->rec[c->rec_len++] = p;
        pads[1] = p;
        memset(&pads[0], 0, sizeof pads[0]);
        pads[0].err = PAD_ERR_NONE;
        c->routed = true;
    } else if (c->mode == LAB_PLAYING && c->pos < c->rec_len) {
        pads[1] = c->rec[c->pos++];
        c->routed = true;
    }
    return LAB_ROUTED;
}

void lab_core_unroute(LabCore* c) {
    if (!c->routed) {
        return;
    }
    c->routed = false;
    if (c->mode == LAB_RECORDING && c->rec_len > 0) {
        c->rec_len--;
    } else if (c->mode == LAB_PLAYING && c->pos > 0) {
        c->pos--;
    }
}
