/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Agent bridge transport (agent_link.h). POSIX AF_UNIX stream socket; the
 * Windows build compiles stubs that refuse to open. */
#include "pc/agent_link.h"

#include <string.h>

void pc_log_line(const char* fmt, ...) __attribute__((format(printf, 1, 2)));

#ifdef _WIN32

bool agent_link_open(const AgentLinkConfig* cfg) {
    (void)cfg;
    pc_log_line("agent: MELEE_AGENT_SOCKET is not supported on Windows yet; bridge off");
    return false;
}
void agent_link_close(void) {}
void agent_link_poll(void) {}
bool agent_link_connected(void) {
    return false;
}
bool agent_link_active(void) {
    return false;
}
void agent_link_send_state(const AgentState* st) {
    (void)st;
}
AgentTake agent_link_take_input(uint32_t tick, bool wait, AgentPad* out) {
    (void)tick;
    (void)wait;
    (void)out;
    return AGENT_TAKE_NONE;
}
const AgentLinkStats* agent_link_stats(void) {
    static const AgentLinkStats none;
    return &none;
}

#else

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#ifdef MSG_NOSIGNAL
#define SEND_FLAGS (MSG_NOSIGNAL | MSG_DONTWAIT)
#else
#define SEND_FLAGS MSG_DONTWAIT /* Apple: SO_NOSIGPIPE is set on the socket instead */
#endif

#define IN_BUF_SIZE 4096
#define MAX_MSG_SIZE (sizeof(AgentMsgHeader) + sizeof(AgentState))
#define PENDING_INPUTS 16

static struct {
    AgentLinkConfig cfg;
    char path[sizeof(((struct sockaddr_un*)0)->sun_path)];
    char build[32];
    int listen_fd;
    int client_fd;
    uint8_t in[IN_BUF_SIZE];
    size_t in_len;
    /* The unsent tail of a message a full socket buffer cut short; nothing
     * else is sent until it is out, so the stream stays framed. */
    uint8_t out[MAX_MSG_SIZE];
    size_t out_len;
    size_t out_off;
    AgentInput pending[PENDING_INPUTS]; /* sorted by target_tick */
    int npending;
    bool active;
    bool have_applied;
    uint32_t applied_target; /* target_tick of the newest input applied */
    bool warned_port;
    AgentLinkStats stats;
} s = {.listen_fd = -1, .client_fd = -1};

static int64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static void set_nonblock_cloexec(int fd) {
    const int fl = fcntl(fd, F_GETFL, 0);
    if (fl >= 0) {
        fcntl(fd, F_SETFL, fl | O_NONBLOCK);
    }
    fcntl(fd, F_SETFD, FD_CLOEXEC);
#ifdef SO_NOSIGPIPE
    const int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#endif
}

static void drop_client(const char* why) {
    if (s.client_fd < 0) {
        return;
    }
    close(s.client_fd);
    s.client_fd = -1;
    s.in_len = 0;
    s.out_len = 0;
    s.out_off = 0;
    s.npending = 0;
    s.active = false;
    s.have_applied = false;
    s.stats.consecutive_misses = 0;
    s.stats.degraded = false;
    pc_log_line("agent: client dropped (%s); the port is back to its own controller", why);
}

/* Push out the tail of a cut-short message. False while some remains. */
static bool flush_out(void) {
    while (s.out_off < s.out_len) {
        const ssize_t n = send(s.client_fd, s.out + s.out_off, s.out_len - s.out_off, SEND_FLAGS);
        if (n > 0) {
            s.out_off += (size_t)n;
        } else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
            return false;
        } else {
            drop_client(n == 0 ? "send returned 0" : strerror(errno));
            return false;
        }
    }
    s.out_len = 0;
    s.out_off = 0;
    return true;
}

/* Send one whole message or none of it. False when it was not sent. */
static bool send_msg(uint16_t type, const void* payload, uint16_t size) {
    if (s.client_fd < 0 || !flush_out()) {
        return false;
    }
    uint8_t buf[MAX_MSG_SIZE];
    const AgentMsgHeader h = {.magic = AGENT_MSG_MAGIC, .type = type, .size = size};
    memcpy(buf, &h, sizeof h);
    memcpy(buf + sizeof h, payload, size);
    const size_t len = sizeof h + size;
    const ssize_t n = send(s.client_fd, buf, len, SEND_FLAGS);
    if (n < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
            drop_client(strerror(errno));
        }
        return false;
    }
    if ((size_t)n < len) {
        memcpy(s.out, buf + n, len - (size_t)n);
        s.out_len = len - (size_t)n;
        s.out_off = 0;
    }
    return true;
}

static void remove_pending_upto(uint32_t tick) {
    int keep = 0;
    for (int i = 0; i < s.npending; i++) {
        if (s.pending[i].target_tick > tick) {
            s.pending[keep++] = s.pending[i];
        }
    }
    s.npending = keep;
}

static void handle_input(const AgentInput* in) {
    s.stats.inputs_received++;
    if (in->port != (uint8_t)s.cfg.agent_port) {
        if (!s.warned_port) {
            s.warned_port = true;
            pc_log_line("agent: input for port %d ignored; the bridge drives port %d only",
                in->port + 1, s.cfg.agent_port + 1);
        }
        return;
    }
    if (in->flags & AGENT_IN_RELEASE) {
        if (s.active) {
            pc_log_line("agent: released port %d", s.cfg.agent_port + 1);
        }
        s.active = false;
        s.npending = 0;
        return;
    }
    if (!s.active) {
        pc_log_line("agent: driving port %d from tick %u", s.cfg.agent_port + 1, in->target_tick);
    }
    s.active = true;
    if (s.have_applied && in->target_tick <= s.applied_target) {
        s.stats.inputs_stale++;
        return;
    }
    int i = 0;
    while (i < s.npending && s.pending[i].target_tick < in->target_tick) {
        i++;
    }
    if (i < s.npending && s.pending[i].target_tick == in->target_tick) {
        s.pending[i] = *in; /* a resend for the same tick replaces it */
        return;
    }
    if (s.npending == PENDING_INPUTS) {
        if (i == 0) {
            return; /* older than everything queued in a full queue */
        }
        memmove(&s.pending[0], &s.pending[1], sizeof s.pending[0] * (size_t)(i - 1));
        i--;
        s.npending--;
    }
    memmove(&s.pending[i + 1], &s.pending[i], sizeof s.pending[0] * (size_t)(s.npending - i));
    s.pending[i] = *in;
    s.npending++;
}

static void read_client(void) {
    while (s.client_fd >= 0) {
        if (s.in_len == sizeof s.in) {
            drop_client("input buffer overflow");
            return;
        }
        const ssize_t n = recv(s.client_fd, s.in + s.in_len, sizeof s.in - s.in_len, MSG_DONTWAIT);
        if (n > 0) {
            s.in_len += (size_t)n;
            continue;
        }
        if (n == 0) {
            drop_client("agent closed the connection");
            return;
        }
        if (errno == EINTR) {
            continue;
        }
        if (errno != EAGAIN && errno != EWOULDBLOCK) {
            drop_client(strerror(errno));
            return;
        }
        break;
    }
    size_t off = 0;
    while (s.client_fd >= 0 && s.in_len - off >= sizeof(AgentMsgHeader)) {
        AgentMsgHeader h;
        memcpy(&h, s.in + off, sizeof h);
        if (h.magic != AGENT_MSG_MAGIC || h.type != AGENT_MSG_INPUT || h.size != sizeof(AgentInput))
        {
            drop_client("protocol error");
            return;
        }
        if (s.in_len - off < sizeof h + h.size) {
            break;
        }
        AgentInput in;
        memcpy(&in, s.in + off + sizeof h, sizeof in);
        off += sizeof h + h.size;
        handle_input(&in);
    }
    if (s.client_fd >= 0 && off > 0) {
        memmove(s.in, s.in + off, s.in_len - off);
        s.in_len -= off;
    }
}

static void accept_clients(void) {
    for (;;) {
        const int fd = accept(s.listen_fd, NULL, NULL);
        if (fd < 0) {
            return; /* EAGAIN: nobody waiting */
        }
        if (s.client_fd >= 0) {
            drop_client("replaced by a new connection");
        }
        set_nonblock_cloexec(fd);
        s.client_fd = fd;
        s.stats.clients++;
        s.warned_port = false;
        AgentHello hello = {
            .proto_version = AGENT_PROTO_VERSION,
            .agent_port = (uint8_t)s.cfg.agent_port,
            .sync_mode = (uint8_t)s.cfg.sync_mode,
            .timeout_us = (uint16_t)(s.cfg.timeout_us > 65535 ? 65535 : s.cfg.timeout_us),
            .state_size = sizeof(AgentState),
        };
        memcpy(hello.build, s.build, sizeof hello.build);
        send_msg(AGENT_MSG_HELLO, &hello, sizeof hello);
        pc_log_line("agent: client connected (#%u)", s.stats.clients);
    }
}

bool agent_link_open(const AgentLinkConfig* cfg) {
    agent_link_close();
    memset(&s.stats, 0, sizeof s.stats);
    s.cfg = *cfg;
    if (cfg->path == NULL || strlen(cfg->path) >= sizeof s.path) {
        pc_log_line(
            "agent: socket path missing or longer than %zu bytes; bridge off", sizeof s.path - 1);
        return false;
    }
    strcpy(s.path, cfg->path);
    s.cfg.path = s.path;
    memset(s.build, 0, sizeof s.build);
    if (cfg->build != NULL) {
        strncpy(s.build, cfg->build, sizeof s.build - 1);
    }
    s.cfg.build = s.build;

    struct stat st;
    if (lstat(s.path, &st) == 0) {
        if (!S_ISSOCK(st.st_mode)) {
            pc_log_line("agent: %s exists and is not a socket; bridge off", s.path);
            return false;
        }
        unlink(s.path); /* a stale socket from an earlier run */
    }
    const int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        pc_log_line("agent: socket(): %s; bridge off", strerror(errno));
        return false;
    }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    memcpy(addr.sun_path, s.path, strlen(s.path));
    if (bind(fd, (const struct sockaddr*)&addr, sizeof addr) != 0 || listen(fd, 2) != 0) {
        pc_log_line("agent: cannot listen on %s: %s; bridge off", s.path, strerror(errno));
        close(fd);
        return false;
    }
    set_nonblock_cloexec(fd);
    s.listen_fd = fd;
    return true;
}

void agent_link_close(void) {
    drop_client("bridge closing");
    if (s.listen_fd >= 0) {
        close(s.listen_fd);
        s.listen_fd = -1;
        unlink(s.path);
    }
}

void agent_link_poll(void) {
    if (s.listen_fd < 0) {
        return;
    }
    accept_clients();
    if (s.client_fd >= 0 && flush_out()) {
        read_client();
    }
}

bool agent_link_connected(void) {
    return s.client_fd >= 0;
}

bool agent_link_active(void) {
    return s.client_fd >= 0 && s.active;
}

void agent_link_send_state(const AgentState* st) {
    if (s.client_fd < 0) {
        return;
    }
    if (send_msg(AGENT_MSG_STATE, st, sizeof *st)) {
        s.stats.states_sent++;
    } else if (s.client_fd >= 0) {
        s.stats.states_dropped++;
    }
}

/* Index of the input for exactly `tick`, or of the newest one before it. */
static int find_input(uint32_t tick, bool exact) {
    int best = -1;
    for (int i = 0; i < s.npending && s.pending[i].target_tick <= tick; i++) {
        best = i;
    }
    if (exact && (best < 0 || s.pending[best].target_tick != tick)) {
        return -1;
    }
    return best;
}

static AgentTake take(int i, uint32_t tick, AgentPad* out, AgentTake kind) {
    *out = s.pending[i].pad;
    s.have_applied = true;
    s.applied_target = s.pending[i].target_tick;
    remove_pending_upto(tick);
    if (kind == AGENT_TAKE_ON_TIME) {
        if (s.stats.degraded) {
            pc_log_line("agent: inputs on time again at tick %u; lockstep resumes", tick);
        }
        s.stats.consecutive_misses = 0;
        s.stats.degraded = false;
    }
    return kind;
}

AgentTake agent_link_take_input(uint32_t tick, bool wait, AgentPad* out) {
    if (s.client_fd >= 0) {
        read_client(); /* first: the input that makes it active may have just arrived */
    }
    if (s.client_fd < 0 || !s.active) {
        return AGENT_TAKE_NONE;
    }
    int i = find_input(tick, true);
    if (i >= 0) {
        return take(i, tick, out, AGENT_TAKE_ON_TIME);
    }
    if (wait && s.cfg.sync_mode == AGENT_SYNC_LOCKSTEP && !s.stats.degraded && s.active) {
        const int64_t deadline = now_us() + (int64_t)s.cfg.timeout_us;
        for (;;) {
            const int64_t left = deadline - now_us();
            if (left <= 0 || s.client_fd < 0) {
                break;
            }
            struct pollfd pfd = {.fd = s.client_fd, .events = POLLIN};
            /* poll() takes whole milliseconds: round up, so the loop never
             * spins; the deadline above still ends the wait. */
            poll(&pfd, 1, (int)((left + 999) / 1000));
            read_client();
            if (s.client_fd < 0 || !s.active) {
                return AGENT_TAKE_NONE;
            }
            i = find_input(tick, true);
            if (i >= 0) {
                return take(i, tick, out, AGENT_TAKE_ON_TIME);
            }
        }
        if (s.client_fd < 0) {
            return AGENT_TAKE_NONE;
        }
        s.stats.misses++;
        if (++s.stats.consecutive_misses >= s.cfg.degrade_after && s.cfg.degrade_after > 0) {
            s.stats.degraded = true;
            pc_log_line("agent: %u ticks in a row without an input in %u us; "
                        "not waiting until the agent catches up",
                s.stats.consecutive_misses, s.cfg.timeout_us);
        }
    }
    /* Late: the newest input for an earlier tick is still the agent's latest
     * word, so a slow agent drives one tick behind instead of not at all. */
    i = find_input(tick, false);
    if (i >= 0) {
        return take(i, tick, out, AGENT_TAKE_LATE);
    }
    return AGENT_TAKE_NONE;
}

const AgentLinkStats* agent_link_stats(void) {
    return &s.stats;
}

#endif
