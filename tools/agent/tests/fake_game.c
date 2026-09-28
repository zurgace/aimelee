/* SPDX-License-Identifier: GPL-3.0-or-later */
/* A stand-in for the game side of the agent bridge, built from the real
 * src/pc/agent_link.c and agent_proto.h by test_bridge_proto.py. It listens,
 * waits for a client, then for each tick takes that tick's input (lockstep)
 * and sends a state whose every field is a known function of the tick, so
 * the Python client's struct layout is checked field by field against the C
 * one.
 *
 *   fake_game <socket> <ticks> <timeout_us>
 * prints "take <tick> <kind> <button> <stick_x>" per tick on stdout. */
#include "pc/agent_link.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void pc_log_line(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

/* Keep in step with expected_state() in test_bridge_proto.py. */
static void fill(AgentState* st, uint32_t t) {
    memset(st, 0, sizeof *st);
    st->tick = t;
    st->match_tick = t * 2;
    st->scene_frame = t * 3;
    st->vs_frame = t * 5;
    st->scene_kind = 2;
    st->game_mode = 0x21;
    st->flags = 0x3F;
    st->match_result = 7;
    st->stage = 0x1F;
    st->agent_port = 1;
    st->late_inputs = t + 11;
    st->dropped_states = t + 13;
    for (int i = 0; i < AGENT_MAX_PORTS; i++) {
        AgentFighter* f = &st->fighters[i];
        f->present = 1;
        f->slot_type = (uint8_t)i;
        f->ckind = (uint8_t)(i + 10);
        f->fkind = (uint8_t)(i + 20);
        f->stocks = (int8_t)-(i + 1);
        f->flags = (uint8_t)(i + 1);
        f->jumps_used = (uint8_t)(i + 2);
        f->max_jumps = (uint8_t)(i + 3);
        f->percent = (int16_t)(-(i * 100) - (int)(t % 50));
        f->motion_id = 0x100u + (uint32_t)i + t;
        f->percent_f = 1.5f * (float)i + (float)t;
        f->facing = (i % 2) ? -1.0f : 1.0f;
        f->pos_x = (float)t + 0.25f * (float)i;
        f->pos_y = -(float)t - 0.5f * (float)i;
        f->cur_x = (float)t + 0.125f * (float)i;
        f->cur_y = -(float)t - 0.0625f * (float)i;
        f->action_frame = 2.0f * (float)i + 0.5f;
        f->hitlag = 3.0f * (float)i;
        f->mv0_bits = 0x40490FDBu ^ (uint32_t)i;
        f->shield = 60.0f - (float)i;
        f->self_vx = 0.1f * (float)(i + 1);
        f->self_vy = -0.2f * (float)(i + 1);
        f->kb_vx = 0.3f * (float)(i + 1);
        f->kb_vy = -0.4f * (float)(i + 1);
        f->ground_vx = 0.5f * (float)(i + 1);
        f->body_state = -i;
        f->body_state_move = i * 7;
        f->smash_state = 3;
        f->input.button = (uint16_t)(0x1000 | i);
        f->input.stick_x = (int8_t)(-80 + i);
        f->input.stick_y = (int8_t)(80 - i);
        f->input.cstick_x = (int8_t)-(i + 1);
        f->input.cstick_y = (int8_t)(i + 1);
        f->input.trigger_l = (uint8_t)(140 + i);
        f->input.trigger_r = (uint8_t)(10 + i);
        f->input.analog_a = (uint8_t)(200 + i);
        f->input.analog_b = (uint8_t)(100 + i);
        f->input.err = (int8_t)(i == 3 ? -1 : 0);
    }
}

int main(int argc, char** argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: fake_game <socket> <ticks> <timeout_us>\n");
        return 2;
    }
    AgentLinkConfig cfg = {
        .path = argv[1],
        .agent_port = 1,
        .sync_mode = AGENT_SYNC_LOCKSTEP,
        .timeout_us = (uint32_t)atoi(argv[3]),
        .degrade_after = 1000,
        .build = "fake-game",
    };
    if (!agent_link_open(&cfg)) {
        return 1;
    }
    const int ticks = atoi(argv[2]);
    for (int i = 0; i < 5000 && !agent_link_connected(); i++) {
        agent_link_poll();
        usleep(1000);
    }
    if (!agent_link_connected()) {
        fprintf(stderr, "fake_game: no client\n");
        return 1;
    }
    AgentPad pad;
    memset(&pad, 0, sizeof pad);
    for (uint32_t t = 1; t <= (uint32_t)ticks; t++) {
        agent_link_poll();
        const AgentTake k = agent_link_take_input(t, true, &pad);
        printf("take %u %d %u %d\n", t, (int)k, pad.button, pad.stick_x);
        AgentState st;
        fill(&st, t);
        agent_link_send_state(&st);
    }
    fflush(stdout);
    const AgentLinkStats* s = agent_link_stats();
    printf("stats sent %u dropped %u received %u misses %u\n", s->states_sent, s->states_dropped,
        s->inputs_received, s->misses);
    agent_link_close();
    return 0;
}
