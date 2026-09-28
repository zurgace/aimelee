"""slp_read.py against bytes from the real serializer (src/pc/slp_format.c).

A small C fixture fills a pre-frame and a post-frame event with known values
through slp_pre_frame / slp_post_frame, wraps them in the raw element the
recorder writes, and the parser has to get every field back. Needs a
configured build for the SDL headers slp_format.c includes; skipped without.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
ROOT = AGENT.parents[1]
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(ROOT / "tools"))

import slp_read  # noqa: E402

FIXTURE = r"""
#include "pc/slp_format.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
void pc_log_line(const char* fmt, ...) { (void)fmt; }
int main(int argc, char** argv) {
    (void)argc;
    static uint8_t buf[4096];
    size_t n = 0;
    uint8_t head[SLP_RAW_HEADER_SIZE];
    slp_raw_header(head);
    memcpy(buf, head, sizeof head);
    n = sizeof head;
    size_t raw_at = n;
    n += slp_event_payloads(buf + n);
    SlpPreFrame pre;
    memset(&pre, 0, sizeof pre);
    pre.frame = -120; pre.port = 1; pre.follower = 0; pre.seed = 0xDEADBEEF; pre.action = 0x0E;
    pre.x = -38.75f; pre.y = 27.25f; pre.facing = -1.0f; pre.buttons = 0x80000100u;
    pre.phys_buttons = 0x0140; pre.phys_l = 0.5f; pre.phys_r = 0.25f;
    pre.raw_x = -80; pre.raw_y = 79; pre.raw_cx = -3; pre.raw_cy = 4; pre.percent = 12.5f;
    n += slp_pre_frame(buf + n, &pre);
    SlpPostFrame po;
    memset(&po, 0, sizeof po);
    po.frame = -120; po.port = 1; po.follower = 0; po.character = 2; po.action = 0x155;
    po.x = 10.5f; po.y = -2.25f; po.facing = 1.0f; po.percent = 33.0f; po.shield = 59.5f;
    po.stocks = 4; po.action_frame = 7.0f; po.misc_as = 0x40490FDBu; po.airborne = 1;
    po.jumps = 1; po.hurtbox = 2; po.self_air_x = 0.125f; po.self_y = -1.5f;
    po.attack_x = 2.5f; po.attack_y = 3.75f; po.self_ground_x = -0.625f; po.hitlag = 6.0f;
    n += slp_post_frame(buf + n, &po);
    size_t len = n - raw_at;
    buf[raw_at - 4] = (uint8_t)(len >> 24); buf[raw_at - 3] = (uint8_t)(len >> 16);
    buf[raw_at - 2] = (uint8_t)(len >> 8); buf[raw_at - 1] = (uint8_t)len;
    FILE* f = fopen(argv[1], "wb");
    fwrite(buf, 1, n, f);
    fclose(f);
    return 0;
}
"""


class SlpReadTest(unittest.TestCase):
    def test_fields_roundtrip(self):
        cc = shutil.which("cc") or shutil.which("gcc")
        if cc is None:
            self.skipTest("no C compiler")
        try:
            import net_test_support
            includes = net_test_support.sdl_includes(ROOT)
        except Exception as e:  # noqa: BLE001 - no configured build
            self.skipTest(f"no SDL headers: {e}")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "fixture.c"
            src.write_text(FIXTURE)
            exe = Path(tmp) / "fixture"
            subprocess.run([cc, "-std=gnu11", "-O1", "-ffunction-sections", "-fdata-sections",
                            f"-I{ROOT / 'src'}", *[f"-I{p}" for p in includes], str(src),
                            str(ROOT / "src/pc/slp_format.c"), "-Wl,--gc-sections", "-o", str(exe)],
                           check=True)
            slp = Path(tmp) / "t.slp"
            subprocess.run([str(exe), str(slp)], check=True)
            frames = slp_read.read_frames(slp)
        pre, post = frames[-120][1]
        self.assertEqual((pre.frame, pre.port, pre.follower, pre.action), (-120, 1, 0, 0x0E))
        self.assertEqual((pre.x, pre.y, pre.facing), (-38.75, 27.25, -1.0))
        self.assertEqual((pre.buttons, pre.phys_buttons, pre.phys_l, pre.phys_r),
                         (0x80000100, 0x0140, 0.5, 0.25))
        self.assertEqual((pre.raw_x, pre.raw_y, pre.raw_cx, pre.raw_cy, pre.percent),
                         (-80, 79, -3, 4, 12.5))
        self.assertEqual((post.frame, post.port, post.character, post.action), (-120, 1, 2, 0x155))
        self.assertEqual((post.x, post.y, post.facing, post.percent, post.shield),
                         (10.5, -2.25, 1.0, 33.0, 59.5))
        self.assertEqual((post.stocks, post.action_frame, post.misc_as, post.airborne),
                         (4, 7.0, 0x40490FDB, 1))
        self.assertEqual((post.jumps, post.hurtbox), (1, 2))
        self.assertEqual((post.self_air_x, post.self_y, post.attack_x, post.attack_y,
                          post.self_ground_x, post.hitlag), (0.125, -1.5, 2.5, 3.75, -0.625, 6.0))


if __name__ == "__main__":
    unittest.main()
