/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Training Lab, the part that knows nothing about the game (lab.h has the
 * rest): P1's D-pad presses, the recording of P2's inputs and which pad each
 * port gets on a tick. tools/test_lab_core.c drives it with fake pads.
 *
 * A tick, in order: lab_core_presses() on P1's raw sample (it strips the
 * D-pad, which the Lab owns), the caller acts on the presses (savestates,
 * lab_core_record_* / lab_core_play_*), then lab_core_route() writes the
 * tick's pads. If the fighters did not run that tick (pause, the GAME!
 * freeze), lab_core_unroute() takes the tick back so the recording holds
 * only frames the fighters played and playback does not skip one. */
#ifndef PC_LAB_CORE_H
#define PC_LAB_CORE_H

#include <dolphin/pad.h>

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LAB_DPAD (PAD_BUTTON_LEFT | PAD_BUTTON_RIGHT | PAD_BUTTON_DOWN | PAD_BUTTON_UP)
/* Two minutes at 60 ticks a second. */
#define LAB_REC_MAX (60 * 120)

enum { LAB_IDLE, LAB_RECORDING, LAB_PLAYING };

/* What lab_core_route did on top of routing the pads. */
enum {
    LAB_ROUTED = 0,
    LAB_REC_FULL = 1, /* the recording filled up and stopped */
};

typedef struct LabCore {
    uint16_t held; /* P1's D-pad bits on the last sample */
    int mode;      /* LAB_* */
    int rec_len;   /* frames recorded */
    int pos;       /* playback: the next frame to feed */
    bool routed;   /* the last lab_core_route fed or recorded a frame */
    PADStatus rec[LAB_REC_MAX];
} LabCore;

void lab_core_reset(LabCore* c);
/* The D-pad buttons P1 newly pressed on this sample; the D-pad bits are
 * cleared from it either way. */
uint16_t lab_core_presses(LabCore* c, PADStatus* p1);
void lab_core_record_start(LabCore* c);
void lab_core_record_stop(LabCore* c);
/* False with nothing recorded. */
bool lab_core_play_start(LabCore* c);
void lab_core_play_stop(LabCore* c);
/* Playback has fed the whole recording: rewind to its start state before
 * this tick, then lab_core_play_rewound. */
bool lab_core_play_at_end(const LabCore* c);
void lab_core_play_rewound(LabCore* c);
/* `pads` is the tick's four ports. Recording: P1's sample drives P2 and is
 * recorded, P1 stands still. Playing: P2 gets the next recorded frame. */
int lab_core_route(LabCore* c, PADStatus pads[4]);
/* The fighters did not run on the tick just routed. */
void lab_core_unroute(LabCore* c);

#ifdef __cplusplus
}
#endif

#endif
