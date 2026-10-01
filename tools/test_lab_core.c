/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Unit test for the Training Lab's core (src/pc/lab_core.c): D-pad presses
 * and their stripping, the routing while recording and playing back, the
 * loop, a full recording, and paused ticks taken back. */
#include "pc/lab_core.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(x)                                                                                   \
    do {                                                                                           \
        if (!(x)) {                                                                                \
            fprintf(stderr, "failed line %d: %s\n", __LINE__, #x);                                 \
            exit(1);                                                                               \
        }                                                                                          \
    } while (0)

static LabCore s_core;

static void ticks_pads(PADStatus pads[4], uint16_t p1_button, int8_t p1_x) {
    memset(pads, 0, 4 * sizeof *pads);
    pads[0].button = p1_button;
    pads[0].stickX = p1_x;
    pads[1].err = PAD_ERR_NO_CONTROLLER; /* the dummy has no controller */
}

static void test_presses(void) {
    LabCore* c = &s_core;
    lab_core_reset(c);
    PADStatus p = {0};
    p.button = PAD_BUTTON_RIGHT | PAD_BUTTON_A;
    CHECK(lab_core_presses(c, &p) == PAD_BUTTON_RIGHT);
    CHECK(p.button == PAD_BUTTON_A); /* the D-pad is the Lab's: no taunt */
    p.button = PAD_BUTTON_RIGHT;
    CHECK(lab_core_presses(c, &p) == 0); /* held, not pressed again */
    CHECK(p.button == 0);
    p.button = PAD_BUTTON_RIGHT | PAD_BUTTON_UP;
    CHECK(lab_core_presses(c, &p) == PAD_BUTTON_UP);
    p.button = 0;
    CHECK(lab_core_presses(c, &p) == 0);
    p.button = PAD_BUTTON_RIGHT;
    CHECK(lab_core_presses(c, &p) == PAD_BUTTON_RIGHT); /* released, pressed again */
    p.button = PAD_BUTTON_LEFT;
    p.err = PAD_ERR_NO_CONTROLLER;
    CHECK(lab_core_presses(c, &p) == 0); /* an unplugged sample presses nothing */
    p.button = PAD_BUTTON_LEFT;
    p.err = PAD_ERR_NONE;
    CHECK(lab_core_presses(c, &p) == PAD_BUTTON_LEFT);
}

static void test_record_and_play(void) {
    LabCore* c = &s_core;
    lab_core_reset(c);
    PADStatus pads[4];
    CHECK(!lab_core_play_start(c)); /* nothing recorded */

    /* Idle: nothing is touched. */
    ticks_pads(pads, PAD_BUTTON_A, 10);
    CHECK(lab_core_route(c, pads) == LAB_ROUTED);
    CHECK(pads[0].button == PAD_BUTTON_A && pads[0].stickX == 10);
    CHECK(pads[1].err == PAD_ERR_NO_CONTROLLER);

    /* Recording: P1's controller drives P2, P1 stands still. */
    lab_core_record_start(c);
    for (int i = 0; i < 5; i++) {
        ticks_pads(pads, (uint16_t)(PAD_BUTTON_B | PAD_BUTTON_DOWN), (int8_t)(i * 10));
        lab_core_route(c, pads);
        CHECK(pads[1].button == PAD_BUTTON_B); /* never the Lab's D-pad */
        CHECK(pads[1].stickX == i * 10);
        CHECK(pads[1].err == PAD_ERR_NONE);
        CHECK(pads[0].button == 0 && pads[0].stickX == 0 && pads[0].err == PAD_ERR_NONE);
    }
    /* A paused tick is not a frame of the recording. */
    ticks_pads(pads, PAD_BUTTON_START, 99);
    lab_core_route(c, pads);
    CHECK(c->rec_len == 6);
    lab_core_unroute(c);
    CHECK(c->rec_len == 5);
    lab_core_unroute(c); /* only the tick just routed */
    CHECK(c->rec_len == 5);
    lab_core_record_stop(c);
    CHECK(c->mode == LAB_IDLE && c->rec_len == 5);

    /* Playback: P2 gets the recording, P1 stays the player's. */
    CHECK(lab_core_play_start(c));
    for (int loop = 0; loop < 2; loop++) {
        for (int i = 0; i < 5; i++) {
            CHECK(!lab_core_play_at_end(c));
            ticks_pads(pads, PAD_BUTTON_X, -40);
            lab_core_route(c, pads);
            CHECK(pads[1].button == PAD_BUTTON_B && pads[1].stickX == i * 10);
            CHECK(pads[1].err == PAD_ERR_NONE);
            CHECK(pads[0].button == PAD_BUTTON_X && pads[0].stickX == -40);
            if (i == 2) {
                lab_core_unroute(c); /* paused on frame 2: it is fed again */
                ticks_pads(pads, 0, 0);
                lab_core_route(c, pads);
                CHECK(pads[1].stickX == 20);
            }
        }
        CHECK(lab_core_play_at_end(c));
        /* The caller restores the recording's start state here. */
        lab_core_play_rewound(c);
    }
    /* Not rewound (the restore was busy): P2 is left alone this tick. */
    for (int i = 0; i < 5; i++) {
        lab_core_route(c, pads);
    }
    CHECK(lab_core_play_at_end(c));
    ticks_pads(pads, 0, 0);
    lab_core_route(c, pads);
    CHECK(pads[1].err == PAD_ERR_NO_CONTROLLER);
    lab_core_unroute(c); /* nothing was fed: nothing to take back */
    CHECK(c->pos == 5);
    lab_core_play_stop(c);
    CHECK(c->mode == LAB_IDLE && c->rec_len == 5);

    /* Recording again starts over. */
    lab_core_record_start(c);
    CHECK(c->rec_len == 0);
}

static void test_full(void) {
    LabCore* c = &s_core;
    lab_core_reset(c);
    PADStatus pads[4];
    lab_core_record_start(c);
    for (int i = 0; i < LAB_REC_MAX; i++) {
        ticks_pads(pads, 0, 1);
        CHECK(lab_core_route(c, pads) == LAB_ROUTED);
    }
    ticks_pads(pads, 0, 1);
    CHECK(lab_core_route(c, pads) == LAB_REC_FULL);
    CHECK(c->mode == LAB_IDLE && c->rec_len == LAB_REC_MAX);
    CHECK(pads[0].stickX == 1); /* P1 is the player's again */
    CHECK(lab_core_play_start(c));
}

int main(void) {
    test_presses();
    test_record_and_play();
    test_full();
    printf("lab_core: ok\n");
    return 0;
}
