/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Unit test for the agent bridge transport (src/pc/agent_link.c): a fake
 * game loop on one side of a real AF_UNIX socket and a hand-rolled agent on
 * the other. Covers the handshake, state framing, lockstep timing, late and
 * stale inputs, degraded mode, release, disconnects, protocol errors and an
 * agent that stops reading -- the game side must never block past one
 * timeout per tick. */
#include "pc/agent_link.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

static int s_fail;
static int s_logs;

void pc_log_line(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("  log: ", stderr);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    s_logs++;
}

#define EXPECT(cond)                                                                               \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            fprintf(stderr, "%s:%d: FAILED: %s\n", __FILE__, __LINE__, #cond);                     \
            s_fail++;                                                                              \
        }                                                                                          \
    } while (0)

static char s_path[108];
/* The address the link listens on: s_path, or tcp:127.0.0.1:<s_port>. */
static char s_addr[128];
static bool s_tcp;
static int s_port;

static int64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static int client_connect(void) {
    if (s_tcp) {
        const int fd = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in in = {.sin_family = AF_INET, .sin_port = htons((uint16_t)s_port)};
        in.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (connect(fd, (const struct sockaddr*)&in, sizeof in) != 0) {
            perror("connect");
            close(fd);
            return -1;
        }
        return fd;
    }
    const int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strcpy(addr.sun_path, s_path);
    if (connect(fd, (const struct sockaddr*)&addr, sizeof addr) != 0) {
        perror("connect");
        close(fd);
        return -1;
    }
    return fd;
}

/* Read one whole message with a deadline; returns its type or -1. */
static int client_read(int fd, void* payload, size_t cap, uint16_t* size) {
    AgentMsgHeader h;
    uint8_t buf[sizeof h];
    size_t have = 0;
    const int64_t deadline = now_us() + 1000000;
    while (now_us() < deadline) {
        const ssize_t n = recv(fd, buf + have, sizeof h - have, MSG_DONTWAIT);
        if (n > 0) {
            have += (size_t)n;
            if (have == sizeof h) {
                break;
            }
        } else if (n == 0) {
            return -1;
        } else {
            usleep(100);
        }
    }
    if (have < sizeof h) {
        return -1;
    }
    memcpy(&h, buf, sizeof h);
    if (h.magic != AGENT_MSG_MAGIC || h.size > cap) {
        return -1;
    }
    size_t got = 0;
    while (got < h.size && now_us() < deadline) {
        const ssize_t n = recv(fd, (uint8_t*)payload + got, h.size - got, MSG_DONTWAIT);
        if (n > 0) {
            got += (size_t)n;
        } else {
            usleep(100);
        }
    }
    *size = h.size;
    return got == h.size ? h.type : -1;
}

static void client_send_input(
    int fd, uint32_t target, uint8_t port, uint8_t flags, uint16_t button) {
    struct __attribute__((packed)) {
        AgentMsgHeader h;
        AgentInput in;
    } msg;
    memset(&msg, 0, sizeof msg);
    msg.h.magic = AGENT_MSG_MAGIC;
    msg.h.type = AGENT_MSG_INPUT;
    msg.h.size = sizeof msg.in;
    msg.in.target_tick = target;
    msg.in.port = port;
    msg.in.flags = flags;
    msg.in.pad.button = button;
    msg.in.pad.stick_x = 80;
    const ssize_t n = send(fd, &msg, sizeof msg, MSG_NOSIGNAL);
    EXPECT(n == (ssize_t)sizeof msg);
}

static AgentLinkConfig config(uint32_t timeout_us, uint32_t degrade_after, int sync) {
    AgentLinkConfig c = {
        .path = s_addr,
        .agent_port = 1,
        .sync_mode = sync,
        .timeout_us = timeout_us,
        .degrade_after = degrade_after,
        .build = "test-build",
    };
    return c;
}

/* The link accepts on poll(); give the kernel a moment to queue the connect. */
static void poll_until_connected(void) {
    for (int i = 0; i < 100 && !agent_link_connected(); i++) {
        agent_link_poll();
        usleep(1000);
    }
}

static void test_open_refuses_regular_file(void) {
    fprintf(stderr, "open refuses a regular file\n");
    FILE* f = fopen(s_path, "w");
    fputs("not a socket", f);
    fclose(f);
    AgentLinkConfig c = config(2000, 30, AGENT_SYNC_LOCKSTEP);
    EXPECT(!agent_link_open(&c));
    struct stat st;
    EXPECT(stat(s_path, &st) == 0 && S_ISREG(st.st_mode)); /* left alone */
    unlink(s_path);
}

static void test_handshake_and_state(void) {
    fprintf(stderr, "handshake, state framing, stale socket replaced\n");
    AgentLinkConfig c = config(2000, 30, AGENT_SYNC_LOCKSTEP);
    EXPECT(agent_link_open(&c));
    agent_link_close(); /* leaves nothing behind */
    if (!s_tcp) {
        struct stat sb;
        EXPECT(stat(s_path, &sb) != 0);
        /* a stale socket file from a crashed run is replaced */
        const int stale = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un addr = {.sun_family = AF_UNIX};
        strcpy(addr.sun_path, s_path);
        EXPECT(bind(stale, (const struct sockaddr*)&addr, sizeof addr) == 0);
        close(stale);
    }
    EXPECT(agent_link_open(&c));

    const int fd = client_connect();
    EXPECT(fd >= 0);
    poll_until_connected();
    EXPECT(agent_link_connected());
    EXPECT(!agent_link_active());

    AgentHello hello;
    uint16_t size = 0;
    EXPECT(client_read(fd, &hello, sizeof hello, &size) == AGENT_MSG_HELLO);
    EXPECT(size == sizeof hello);
    EXPECT(hello.proto_version == AGENT_PROTO_VERSION);
    EXPECT(hello.agent_port == 1);
    EXPECT(hello.sync_mode == AGENT_SYNC_LOCKSTEP);
    EXPECT(hello.timeout_us == 2000);
    EXPECT(hello.state_size == sizeof(AgentState));
    EXPECT(strcmp(hello.build, "test-build") == 0);

    AgentState st;
    memset(&st, 0, sizeof st);
    st.tick = 42;
    st.fighters[1].motion_id = 0x0E;
    st.fighters[1].pos_x = -38.5f;
    agent_link_send_state(&st);
    AgentState got;
    EXPECT(client_read(fd, &got, sizeof got, &size) == AGENT_MSG_STATE);
    EXPECT(size == sizeof(AgentState));
    EXPECT(got.tick == 42 && got.fighters[1].motion_id == 0x0E && got.fighters[1].pos_x == -38.5f);

    /* Not active until the first input: no waiting, nothing to take. */
    AgentPad pad;
    int64_t t0 = now_us();
    EXPECT(agent_link_take_input(43, true, &pad) == AGENT_TAKE_NONE);
    EXPECT(now_us() - t0 < 1000);

    close(fd);
    agent_link_close();
}

static void test_lockstep(void) {
    fprintf(stderr, "lockstep: on time, timeout, late, stale, degrade, release, wrong port\n");
    AgentLinkConfig c = config(3000, 3, AGENT_SYNC_LOCKSTEP);
    EXPECT(agent_link_open(&c));
    const int fd = client_connect();
    poll_until_connected();
    AgentHello hello;
    uint16_t size;
    client_read(fd, &hello, sizeof hello, &size);
    AgentPad pad;

    /* on time */
    client_send_input(fd, 5, 1, 0, 0x0100);
    usleep(2000);
    agent_link_poll();
    EXPECT(agent_link_active());
    memset(&pad, 0, sizeof pad);
    EXPECT(agent_link_take_input(5, true, &pad) == AGENT_TAKE_ON_TIME);
    EXPECT(pad.button == 0x0100 && pad.stick_x == 80);

    /* timeout: waits about the timeout, no older input to fall back on */
    int64_t t0 = now_us();
    EXPECT(agent_link_take_input(6, true, &pad) == AGENT_TAKE_NONE);
    int64_t dt = now_us() - t0;
    EXPECT(dt >= 2900 && dt < 20000);
    EXPECT(agent_link_stats()->misses == 1);

    /* the input for 6 arrives late: tick 7 takes it (one tick behind) */
    client_send_input(fd, 6, 1, 0, 0x0200);
    usleep(2000);
    t0 = now_us();
    EXPECT(agent_link_take_input(7, true, &pad) == AGENT_TAKE_LATE);
    EXPECT(pad.button == 0x0200);
    EXPECT(agent_link_stats()->misses == 2);

    /* the same tick again, or an older one, is stale */
    const uint32_t stale0 = agent_link_stats()->inputs_stale;
    client_send_input(fd, 6, 1, 0, 0x0400);
    client_send_input(fd, 4, 1, 0, 0x0400);
    usleep(2000);
    agent_link_poll();
    EXPECT(agent_link_stats()->inputs_stale == stale0 + 2);

    /* an input that arrives during the wait ends it early */
    client_send_input(fd, 8, 1, 0, 0x0800);
    t0 = now_us();
    EXPECT(agent_link_take_input(8, true, &pad) == AGENT_TAKE_ON_TIME);
    EXPECT(pad.button == 0x0800);
    EXPECT(now_us() - t0 < 3000);
    EXPECT(agent_link_stats()->consecutive_misses == 0);

    /* inputs queued ahead are kept for their tick */
    client_send_input(fd, 10, 1, 0, 0x0010);
    client_send_input(fd, 9, 1, 0, 0x0020);
    usleep(2000);
    EXPECT(agent_link_take_input(9, true, &pad) == AGENT_TAKE_ON_TIME && pad.button == 0x0020);
    EXPECT(agent_link_take_input(10, true, &pad) == AGENT_TAKE_ON_TIME && pad.button == 0x0010);

    /* three misses in a row: degraded, and it stops waiting */
    for (uint32_t t = 11; t < 14; t++) {
        agent_link_take_input(t, true, &pad);
    }
    EXPECT(agent_link_stats()->degraded);
    t0 = now_us();
    EXPECT(agent_link_take_input(14, true, &pad) == AGENT_TAKE_NONE);
    EXPECT(now_us() - t0 < 1000);
    /* an on-time input brings lockstep back */
    client_send_input(fd, 15, 1, 0, 0x1000);
    usleep(2000);
    EXPECT(agent_link_take_input(15, true, &pad) == AGENT_TAKE_ON_TIME);
    EXPECT(!agent_link_stats()->degraded);

    /* inputs for another port are ignored */
    client_send_input(fd, 16, 2, 0, 0x0100);
    usleep(2000);
    t0 = now_us();
    EXPECT(agent_link_take_input(16, false, &pad) == AGENT_TAKE_NONE);

    /* release: inactive, nothing to take, no waiting */
    client_send_input(fd, 17, 1, AGENT_IN_RELEASE, 0);
    usleep(2000);
    agent_link_poll();
    EXPECT(!agent_link_active());
    t0 = now_us();
    EXPECT(agent_link_take_input(17, true, &pad) == AGENT_TAKE_NONE);
    EXPECT(now_us() - t0 < 1000);

    close(fd);
    agent_link_close();
}

static void test_async(void) {
    fprintf(stderr, "async: never waits, takes the newest input\n");
    AgentLinkConfig c = config(3000, 3, AGENT_SYNC_ASYNC);
    EXPECT(agent_link_open(&c));
    const int fd = client_connect();
    poll_until_connected();
    AgentHello hello;
    uint16_t size;
    client_read(fd, &hello, sizeof hello, &size);
    AgentPad pad;
    client_send_input(fd, 3, 1, 0, 0x0001);
    client_send_input(fd, 4, 1, 0, 0x0002);
    usleep(2000);
    int64_t t0 = now_us();
    EXPECT(agent_link_take_input(6, true, &pad) == AGENT_TAKE_LATE && pad.button == 0x0002);
    EXPECT(agent_link_take_input(7, true, &pad) == AGENT_TAKE_NONE);
    EXPECT(now_us() - t0 < 1000);
    EXPECT(agent_link_stats()->misses == 0);
    close(fd);
    agent_link_close();
}

static void test_disconnect_and_errors(void) {
    fprintf(stderr, "disconnect mid-wait, protocol error, replacement, slow reader\n");
    AgentLinkConfig c = config(20000, 30, AGENT_SYNC_LOCKSTEP);
    EXPECT(agent_link_open(&c));
    AgentPad pad;
    AgentHello hello;
    uint16_t size;

    /* the agent dies: the wait ends at once, the port is released */
    int fd = client_connect();
    poll_until_connected();
    client_read(fd, &hello, sizeof hello, &size);
    client_send_input(fd, 1, 1, 0, 0x0100);
    usleep(2000);
    EXPECT(agent_link_take_input(1, true, &pad) == AGENT_TAKE_ON_TIME);
    close(fd);
    int64_t t0 = now_us();
    EXPECT(agent_link_take_input(2, true, &pad) == AGENT_TAKE_NONE);
    EXPECT(now_us() - t0 < 5000);
    EXPECT(!agent_link_connected());

    /* garbage on the wire drops the client, and a new one gets in */
    fd = client_connect();
    poll_until_connected();
    EXPECT(agent_link_connected());
    const char junk[16] = "not a message!!";
    EXPECT(send(fd, junk, sizeof junk, MSG_NOSIGNAL) == (ssize_t)sizeof junk);
    usleep(2000);
    agent_link_poll();
    EXPECT(!agent_link_connected());
    close(fd);

    /* a second connection replaces the first */
    const int a = client_connect();
    poll_until_connected();
    const int b = client_connect();
    usleep(2000);
    agent_link_poll();
    EXPECT(agent_link_connected());
    char tmp[64];
    EXPECT(recv(a, tmp, sizeof tmp, 0) > 0);  /* its hello... */
    EXPECT(recv(a, tmp, sizeof tmp, 0) == 0); /* ...then EOF: it was dropped */
    close(a);

    /* an agent that never reads: states drop, the game never blocks */
    AgentState st;
    memset(&st, 0, sizeof st);
    const uint32_t clients = agent_link_stats()->clients;
    t0 = now_us();
    /* until the buffers are full: a few hundred KB for AF_UNIX, megabytes
     * for loopback TCP */
    for (uint32_t i = 0; i < 200000 && agent_link_stats()->states_dropped < 100; i++) {
        st.tick = i;
        agent_link_send_state(&st);
    }
    EXPECT(now_us() - t0 < 2000000);
    EXPECT(agent_link_stats()->states_dropped > 0);
    EXPECT(agent_link_connected() && agent_link_stats()->clients == clients);
    /* and the stream is still framed: drain it and parse every message */
    static uint8_t all[64 << 20];
    size_t total = 0;
    int64_t idle_since = now_us();
    while (now_us() - idle_since < 200000 && total < sizeof all) {
        const ssize_t n = recv(b, all + total, sizeof all - total, MSG_DONTWAIT);
        if (n > 0) {
            total += (size_t)n;
            idle_since = now_us();
            agent_link_poll(); /* lets a cut-short tail go out */
        } else {
            usleep(500);
        }
    }
    size_t off = 0;
    int states = 0;
    int64_t last_tick = -1;
    bool framed = true;
    while (off + sizeof(AgentMsgHeader) <= total) {
        AgentMsgHeader h;
        memcpy(&h, all + off, sizeof h);
        if (h.magic != AGENT_MSG_MAGIC || off + sizeof h + h.size > total) {
            framed = false;
            break;
        }
        if (h.type == AGENT_MSG_STATE) {
            AgentState one;
            memcpy(&one, all + off + sizeof h, sizeof one);
            if ((int64_t)one.tick <= last_tick) {
                framed = false;
            }
            last_tick = one.tick;
            states++;
        }
        off += sizeof h + h.size;
    }
    EXPECT(framed && off == total);
    EXPECT(states > 0 && (uint32_t)states == agent_link_stats()->states_sent);
    close(b);
    agent_link_close();
}

static void test_tcp_addresses(void) {
    fprintf(stderr, "tcp: loopback only, bad addresses refused\n");
    static const char* bad[] = {"tcp:0.0.0.0:47000", "tcp:192.168.1.2:47000", "tcp:example.com:1",
        "tcp:", "tcp:0", "tcp:70000", "tcp:12ab"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        snprintf(s_addr, sizeof s_addr, "%s", bad[i]);
        AgentLinkConfig c = config(2000, 30, AGENT_SYNC_LOCKSTEP);
        EXPECT(!agent_link_open(&c));
    }
    snprintf(s_addr, sizeof s_addr, "tcp:localhost:%d", s_port);
    AgentLinkConfig c = config(2000, 30, AGENT_SYNC_LOCKSTEP);
    EXPECT(agent_link_open(&c));
    agent_link_close();
    snprintf(s_addr, sizeof s_addr, "tcp:%d", s_port);
    c = config(2000, 30, AGENT_SYNC_LOCKSTEP);
    EXPECT(agent_link_open(&c));
    agent_link_close();
}

/* A loopback port nothing listens on right now. */
static int free_port(void) {
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in in = {.sin_family = AF_INET};
    in.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    bind(fd, (const struct sockaddr*)&in, sizeof in);
    socklen_t len = sizeof in;
    getsockname(fd, (struct sockaddr*)&in, &len);
    close(fd);
    return ntohs(in.sin_port);
}

static void test_events(void) {
    fprintf(stderr, "Slippi event messages\n");
    AgentLinkConfig c = config(2000, 30, AGENT_SYNC_LOCKSTEP);
    EXPECT(agent_link_open(&c));
    static uint8_t data[AGENT_MAX_EVENTS_SIZE + 1];
    for (size_t i = 0; i < sizeof data; i++) {
        data[i] = (uint8_t)(i * 7);
    }
    EXPECT(!agent_link_send_events(data, 100)); /* nobody connected */
    const int fd = client_connect();
    EXPECT(fd >= 0);
    poll_until_connected();
    AgentHello hello;
    uint16_t size = 0;
    EXPECT(client_read(fd, &hello, sizeof hello, &size) == AGENT_MSG_HELLO);

    static uint8_t got[AGENT_MAX_EVENTS_SIZE];
    EXPECT(agent_link_send_events(data, 3000));
    EXPECT(client_read(fd, got, sizeof got, &size) == AGENT_MSG_SLP_EVENTS);
    EXPECT(size == 3000 && memcmp(got, data, 3000) == 0);
    EXPECT(agent_link_send_events(data, AGENT_MAX_EVENTS_SIZE)); /* the largest */
    EXPECT(client_read(fd, got, sizeof got, &size) == AGENT_MSG_SLP_EVENTS);
    EXPECT(size == AGENT_MAX_EVENTS_SIZE && memcmp(got, data, size) == 0);
    EXPECT(!agent_link_send_events(data, AGENT_MAX_EVENTS_SIZE + 1)); /* refused, not cut */
    /* The framing survives: a state still reads whole after them. */
    AgentState st;
    memset(&st, 0, sizeof st);
    st.tick = 7;
    agent_link_send_state(&st);
    AgentState gst;
    EXPECT(client_read(fd, &gst, sizeof gst, &size) == AGENT_MSG_STATE && gst.tick == 7);
    EXPECT(agent_link_stats()->events_sent == 2);
    close(fd);
    agent_link_close();
}

static void run_suite(void) {
    test_handshake_and_state();
    test_events();
    test_lockstep();
    test_async();
    test_disconnect_and_errors();
}

int main(void) {
    snprintf(s_path, sizeof s_path, "/tmp/agent_link_test_%d.sock", (int)getpid());
    fprintf(stderr, "== AF_UNIX %s\n", s_path);
    snprintf(s_addr, sizeof s_addr, "%s", s_path);
    test_open_refuses_regular_file();
    run_suite();
    unlink(s_path);

    s_tcp = true;
    s_port = free_port();
    fprintf(stderr, "== TCP 127.0.0.1:%d\n", s_port);
    test_tcp_addresses();
    snprintf(s_addr, sizeof s_addr, "tcp:127.0.0.1:%d", s_port);
    run_suite();
    if (s_fail) {
        fprintf(stderr, "agent_link: %d FAILED\n", s_fail);
        return 1;
    }
    fprintf(stderr, "agent_link: all passed\n");
    return 0;
}
