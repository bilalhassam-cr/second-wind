#!/usr/bin/env python3
"""One pseudo terminal driver, shared by the four usage readers.

A reader forks its client on a PTY wide enough for its panel to render, and
sends a keystroke only once a marker is on screen rather than on elapsed time.
What the client sent can be read two ways: as text in the order it was painted
(`clean`), or as the screen a terminal would be showing (`render`).
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
import unicodedata

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


def render(raw, rows=55, cols=200):
    """The screen a terminal would show after `raw`, and whether the client
    left it drawing full screen, as (text, full_screen).

    `clean` suits a client that prints its panel and moves on. A client on the
    alternate screen redraws instead: it places the cursor and sends only the
    cells that changed since its last frame. Codex has drawn that way since
    0.158. One row of its panel then arrives as several runs with a jump
    between them, and a letter already in the right cell is never sent again,
    so the order painted no longer says what is on screen. Only the screen
    does. Each row comes back with its trailing blanks removed."""
    term = _Terminal(rows, cols)
    term.feed(raw)
    return term.text(), term.full_screen


def _width(char):
    """The cells one character takes: none for a combining mark or a format
    character, two for a wide one such as an emoji."""
    if unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


class _Terminal:
    """Enough of a terminal to say what is on its screen: a grid of cells, a
    cursor, a scrolling region and the alternate screen. Colours, titles,
    modes and queries put nothing on screen and are passed over. The right
    half of a wide character is held as an empty cell.

    Not modelled, because no client read here uses them: scrollback (so
    CSI 3 J changes nothing), origin mode, insert mode, the soft reset, and
    the ways modes 47 and 1047 differ from 1049."""

    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.reset()

    def reset(self):
        self.main = self.blank(self.rows)
        self.cells, self.full_screen = self.main, False
        self.row = self.col = 0
        self.top, self.bottom = 0, self.rows - 1
        self.autowrap, self.pending = True, False
        self.saved = self.before = (0, 0)

    def blank(self, count):
        return [[" "] * self.cols for _ in range(count)]

    def text(self):
        lines = ["".join(line).rstrip() for line in self.cells]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def feed(self, raw):
        at = 0
        for hit in _SEQ.finditer(raw):
            self.write(raw[at:hit.start()].decode("utf-8", errors="replace"))
            at = hit.end()
            if hit.group(3) is not None:
                self.csi(hit.group(1).decode(), hit.group(2), hit.group(3).decode())
            elif len(hit.group(0)) == 2:
                self.escape(hit.group(0)[1:2])
        self.write(raw[at:].decode("utf-8", errors="replace"))

    def write(self, text):
        for char in text:
            if char == "\r":
                self.move(col=0)
            elif char in "\n\x0b\x0c":
                self.linefeed()
            elif char == "\b":
                self.move(col=self.col - 1)
            elif char == "\t":
                self.move(col=(self.col // 8 + 1) * 8)
            elif unicodedata.category(char) != "Cc":
                self.put(char)

    def put(self, char):
        width = _width(char)
        if width == 0:
            # A combining mark belongs to the character before it.
            line = self.cells[self.row]
            col = self.col if self.pending else self.col - 1
            while col > 0 and line[col] == "":
                col -= 1
            if col >= 0:
                line[col] += char
            return
        if self.autowrap and (self.pending or
                              (width == 2 and self.col == self.cols - 1)):
            self.move(col=0)
            self.linefeed()
        line = self.cells[self.row]
        self.free(line, self.col)
        line[self.col] = char
        if width == 2 and self.col + 1 < self.cols:
            self.free(line, self.col + 1)
            line[self.col + 1] = ""
        if self.col + width >= self.cols:
            # The last column: the next character starts the next line.
            self.col, self.pending = self.cols - 1, True
        else:
            self.col += width

    def free(self, line, col):
        """Writing over either half of a wide character blanks the other."""
        if line[col] == "" and col > 0:
            line[col - 1] = " "
        if col + 1 < self.cols and line[col + 1] == "":
            line[col + 1] = " "

    def tidy(self, line):
        """After a shift along the row, no right half is left without its
        left, and no wide character without the cell for its right half."""
        for col, cell in enumerate(line):
            left = line[col - 1] if col else ""
            if cell == "" and not (left and _width(left[0]) == 2):
                line[col] = " "
            elif cell and _width(cell[0]) == 2 and (
                    col + 1 == self.cols or line[col + 1] != ""):
                line[col] = " "

    def move(self, row=None, col=None):
        if row is not None:
            self.row = min(max(row, 0), self.rows - 1)
        if col is not None:
            self.col = min(max(col, 0), self.cols - 1)
        self.pending = False

    def linefeed(self):
        if self.row == self.bottom:
            self.scroll(1)
        elif self.row < self.rows - 1:
            self.row += 1
        self.pending = False

    def reverse_index(self):
        if self.row == self.top:
            self.scroll(1, up=False)
        elif self.row > 0:
            self.row -= 1
        self.pending = False

    def scroll(self, count, up=True, top=None):
        """Move the rows from `top`, the region's first unless given, to the
        region's last `count` lines up or down, blanking the rows they leave."""
        top = self.top if top is None else top
        span = self.bottom - top + 1
        count = min(count, span)
        lines, fresh = self.cells[top:self.bottom + 1], self.blank(count)
        self.cells[top:self.bottom + 1] = (lines[count:] + fresh if up
                                           else fresh + lines[:span - count])

    def erase(self, row, start, end):
        line = self.cells[row]
        start, end = max(start, 0), min(end, self.cols)
        if start < end:
            self.free(line, start)
            self.free(line, end - 1)
            line[start:end] = [" "] * (end - start)

    def erase_display(self, mode):
        if mode == 2:
            rows = range(self.rows)
        elif mode == 1:
            rows = range(self.row)
            self.erase(self.row, 0, self.col + 1)
        elif mode == 0:
            rows = range(self.row + 1, self.rows)
            self.erase(self.row, self.col, self.cols)
        else:
            return
        for row in rows:
            self.erase(row, 0, self.cols)

    def alternate(self, on, keeps_cursor):
        if on == self.full_screen:
            return
        if on:
            if keeps_cursor:
                self.before = (self.row, self.col)
            self.cells = self.blank(self.rows)
        else:
            self.cells = self.main
            if keeps_cursor:
                self.move(*self.before)
        self.full_screen = on

    def escape(self, char):
        if char == b"7":
            self.saved = (self.row, self.col)
        elif char == b"8":
            self.move(*self.saved)
        elif char == b"D":
            self.linefeed()
        elif char == b"E":
            self.move(col=0)
            self.linefeed()
        elif char == b"M":
            self.reverse_index()
        elif char == b"c":
            self.reset()

    def csi(self, params, inter, final):
        if inter:
            return
        if params[:1] in ("?", ">", "<", "="):
            # A private mode. Only the alternate screen and line wrapping
            # change what a later write puts where.
            if params[:1] == "?" and final in ("h", "l"):
                for mode in params[1:].split(";"):
                    if mode in ("1049", "1047", "47"):
                        self.alternate(final == "h", mode == "1049")
                    elif mode == "7":
                        self.autowrap = final == "h"
            return
        nums = [int(n) if n.isdigit() else 0 for n in params.split(";")]
        first = nums[0]
        count = first or 1
        line = self.cells[self.row]
        if final == "A":
            self.move(row=self.row - count)
        elif final in ("B", "e"):
            self.move(row=self.row + count)
        elif final in ("C", "a"):
            self.move(col=self.col + count)
        elif final == "D":
            self.move(col=self.col - count)
        elif final == "E":
            self.move(self.row + count, 0)
        elif final == "F":
            self.move(self.row - count, 0)
        elif final in ("G", "`"):
            self.move(col=count - 1)
        elif final == "d":
            self.move(row=count - 1)
        elif final in ("H", "f"):
            self.move(count - 1, (nums[1] if len(nums) > 1 and nums[1] else 1) - 1)
        elif final == "J":
            self.erase_display(first)
        elif final == "K" and first in (0, 1, 2):
            self.erase(self.row, *{0: (self.col, self.cols), 1: (0, self.col + 1),
                                   2: (0, self.cols)}[first])
        elif final == "X":
            self.erase(self.row, self.col, self.col + count)
        elif final == "@":
            count = min(count, self.cols - self.col)
            line[self.col:] = [" "] * count + line[self.col:self.cols - count]
            self.tidy(line)
        elif final == "P":
            count = min(count, self.cols - self.col)
            line[self.col:] = line[self.col + count:] + [" "] * count
            self.tidy(line)
        elif final in ("L", "M") and self.top <= self.row <= self.bottom:
            self.scroll(count, up=final == "M", top=self.row)
            self.move(col=0)
        elif final == "S":
            self.scroll(count)
        elif final == "T" and len(nums) == 1:
            self.scroll(count, up=False)
        elif final == "r":
            top = count - 1
            bottom = min(nums[1] if len(nums) > 1 and nums[1] else self.rows,
                         self.rows) - 1
            if top < bottom:
                self.top, self.bottom = top, bottom
                self.move(0, 0)
        elif final == "s" and not params:
            self.saved = (self.row, self.col)
        elif final == "u" and not params:
            self.move(*self.saved)


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

    @property
    def display(self):
        """What the screen shows now, and whether the client is drawing full
        screen: see render."""
        return render(b"".join(chunk for _, chunk in self.chunks),
                      self.rows, self.cols)

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
