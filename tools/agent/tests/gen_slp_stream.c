/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Writes tests/data/slp_stream.bin, the synthetic Slippi event stream
 * test_slippi_agent.py feeds to libmelee: melee-pc's own serializer
 * (src/pc/slp_format.c, what slp.c streams over the bridge) for a short
 * Fox (P1) vs Falco (P2) match on Final Destination. Format: chunks of
 * [u32 little-endian length][Slippi events], the match header first, then
 * one frame per chunk, as AGENT_MSG_SLP_EVENTS carries them.
 *
 *   cc -std=gnu11 -Isrc tools/agent/tests/gen_slp_stream.c src/pc/slp_format.c \
 *      $(pkg-config --cflags --libs sdl3) -lm -o gen && ./gen > tools/agent/tests/data/slp_stream.bin
 */
#include "pc/slp_format.h"
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
void pc_log_line(const char* fmt, ...) { (void)fmt; }
static void chunk(const uint8_t* b, size_t n) {
    uint32_t len = (uint32_t)n;
    fwrite(&len, 4, 1, stdout);
    fwrite(b, 1, n, stdout);
}
int main(void) {
    static uint8_t buf[1 << 16];
    size_t n = slp_event_payloads(buf);
    SlpInfoBlock info;
    memset(&info, 0, sizeof info);
    info.stkind = 32; /* FD */
    info.bits2 = 0x80;
    for (int i = 0; i < 6; i++) {
        info.players[i].slot_type = 3;
        info.players[i].ckind = 0x1A;
    }
    info.players[0] = (SlpInfoPlayer){.ckind = 2, .slot_type = 0, .stocks = 4, .slot = 1};
    info.players[1] = (SlpInfoPlayer){.ckind = 20, .slot_type = 0, .stocks = 4, .slot = 2};
    info.damage_ratio = 1.0f;
    info.game_speed = 1.0f;
    SlpGameStart gs;
    memset(&gs, 0, sizeof gs);
    slp_info_block(gs.info, &info);
    gs.minor_scene = 2;
    gs.major_scene = 2;
    n += slp_game_start(buf + n, &gs);
    chunk(buf, n);
    for (int f = -123; f < 80; f++) {
        n = 0;
        SlpFrameStart fs = {.frame = f, .seed = 1, .scene_frame = (uint32_t)(f + 123)};
        n += slp_frame_start(buf + n, &fs);
        for (int p = 0; p < 2; p++) {
            SlpPreFrame pre;
            memset(&pre, 0, sizeof pre);
            pre.frame = f;
            pre.port = (uint8_t)p;
            pre.action = 14;
            pre.x = p ? 30.0f : -30.0f;
            pre.facing = p ? -1.0f : 1.0f;
            pre.joy_x = p ? 0.5f : 0.0f;
            n += slp_pre_frame(buf + n, &pre);
        }
        for (int p = 0; p < 2; p++) {
            SlpPostFrame po;
            memset(&po, 0, sizeof po);
            po.frame = f;
            po.port = (uint8_t)p;
            po.character = p ? 22 : 1; /* internal: Falco, Fox */
            po.action = 14;
            po.x = (p ? 30.0f : -30.0f) + 5.0f * sinf(f / 20.0f);
            po.facing = p ? -1.0f : 1.0f;
            po.percent = (float)(f > 0 ? f / 10 : 0);
            po.shield = 60.0f;
            po.stocks = 4;
            po.jumps = 2;
            n += slp_post_frame(buf + n, &po);
        }
        n += slp_frame_bookend(buf + n, f, f);
        chunk(buf, n);
    }
    return 0;
}
