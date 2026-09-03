#!/usr/bin/env python3
"""One pseudo terminal driver, shared by the four usage readers.

A reader forks its client on a PTY wide enough for its panel to render, and
sends a keystroke only once a marker is on screen rather than on elapsed time.
"""
import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import time

_SEQ = re.compile(
    rb"\x1b\[([0-9;:<=>?]*)([ -/]*)([@-~])"
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    rb"|\x1b[PX^_][^\x1b]*\x1b\\"
    rb"|\x1b[()][0-9A-Za-z]"
    rb"|\x1b."
)


def clean(raw):
    """Bytes off the PTY to plain text. Colour codes go; a horizontal cursor
    move becomes spaces, because these clients lay a panel out by jumping the
    cursor and a label would otherwise touch its value. A vertical move starts
    a line, so all of it is kept in the order painted."""
    out, start, at = bytearray(), 0, 0
    for hit in _SEQ.finditer(raw):
        out += raw[at:hit.start()]
        at = hit.end()
        if b"\n" in out[start:]:
            start = out.rindex(b"\n") + 1
        final = hit.group(3)
        if final == b"C":
            out += b" " * min(int(hit.group(1) or 1), 200)
        elif final == b"G":
            gap = int(hit.group(1) or 1) - 1 - (len(out) - start)
            out += b" " * (gap if 0 < gap < 200 else 1)
        elif final in (b"A", b"B", b"E", b"F", b"H", b"d"):
            out += b"\n"
            start = len(out)
    out += raw[at:]
    return out.translate(None, b"\x00\x07\x08\x0e\x0f") \
              .replace(b"\r\n", b"\n").replace(b"\r", b"\n") \
              .decode("utf-8", errors="replace")


def budget_left(ends, cap, now=None):
    """Seconds still allowed for one wait: never more than the budget has left,
    and zero once it is gone. Zero means stop, because a wait started after the
    budget expired only overruns it, a second at a time."""
    return max(0.0, min(cap, ends - (time.time() if now is None else now)))


class Screen:  # a client running on a PTY, and the text it has painted
    def __init__(self, argv, cwd=None, env_set=None, env_drop=(),
                 rows=55, cols=200):
        self.argv, self.cwd = list(argv), cwd
        self.env_set, self.env_drop = dict(env_set or {}), tuple(env_drop)
        self.rows, self.cols = rows, cols
        self.pid = self.fd = None
        self.chunks, self.closed = [], False

    def start(self):
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.environ["TERM"] = "xterm-256color"
                for key in self.env_drop:
                    os.environ.pop(key, None)
                os.environ.update(self.env_set)
                if self.cwd:
                    os.chdir(self.cwd)
                os.execvp(self.argv[0], self.argv)
            except BaseException:
                os._exit(127)
        self.pid, self.fd = pid, fd
        # Without a real window size the panels wrap and cannot be parsed.
        fcntl.ioctl(fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", self.rows, self.cols, 0, 0))
        return self

    def pump(self, seconds=0.4):
        if self.closed:
            time.sleep(min(seconds, 0.2))
        elif select.select([self.fd], [], [], seconds)[0]:
            try:
                data = os.read(self.fd, 1 << 16)
            except OSError:
                data = b""
            if data:
                self.chunks.append((time.time(), data))
            else:
                self.closed = True

    @property
    def text(self):
        return clean(b"".join(chunk for _, chunk in self.chunks))

    def first_of(self, patterns, timeout=30.0):
        """The key of the first pattern to appear, or None on the timeout."""
        end = time.time() + timeout
        while True:
            self.pump(0.4)
            text = self.text
            for key, pattern in patterns.items():
                if re.search(pattern, text, re.I):
                    return key
            if time.time() >= end:
                return None

    def wait_for(self, present=(), absent=(), timeout=30.0, window=3.0, quiet=0.0):
        """True once every `present` pattern has been printed, no `absent`
        pattern is still painted, and nothing has been painted for `quiet`
        seconds: a client starting its MCP servers repaints a spinner several
        times a second, so silence is the sign it is ready."""
        end = time.time() + timeout
        while True:
            self.pump(0.4)
            text = self.text
            cut = time.time() - window
            fresh = clean(b"".join(c for at, c in self.chunks if at >= cut))
            idle = self.chunks and time.time() - self.chunks[-1][0] >= quiet
            if (not quiet or idle) and \
                    all(re.search(p, text, re.I) for p in present) and \
                    not any(re.search(p, fresh, re.I) for p in absent):
                return True
            if time.time() >= end:
                return False

    def send(self, data):
        try:
            os.write(self.fd, data)
        except OSError:
            self.closed = True

    def close(self):
        """SIGKILL. TypeError covers a screen that never started."""
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.close(self.fd)
            os.waitpid(self.pid, 0)
        except (OSError, TypeError):
            pass
        self.closed = True
