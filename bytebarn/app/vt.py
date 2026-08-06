"""Minimal VT100/xterm screen emulator for the Terminal Manager.

Pure Python, no Qt. Handles the sequences real shells and TUIs emit so a
cell grid can be painted instead of dumping stripped ANSI into a text edit.
Not a full xterm — enough for zsh/bash prompts, colors, clear, cursor move,
alt screen, and basic scroll regions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable


class Color(IntEnum):
    DEFAULT = -1
    BLACK = 0
    RED = 1
    GREEN = 2
    YELLOW = 3
    BLUE = 4
    MAGENTA = 5
    CYAN = 6
    WHITE = 7
    BRIGHT_BLACK = 8
    BRIGHT_RED = 9
    BRIGHT_GREEN = 10
    BRIGHT_YELLOW = 11
    BRIGHT_BLUE = 12
    BRIGHT_MAGENTA = 13
    BRIGHT_CYAN = 14
    BRIGHT_WHITE = 15


@dataclass(slots=True)
class Cell:
    char: str = " "
    fg: int = Color.DEFAULT
    bg: int = Color.DEFAULT
    bold: bool = False
    underline: bool = False
    inverse: bool = False
    italic: bool = False

    def copy(self) -> Cell:
        return Cell(
            self.char, self.fg, self.bg,
            self.bold, self.underline, self.inverse, self.italic,
        )


def _blank_row(cols: int, template: Cell | None = None) -> list[Cell]:
    if template is None:
        return [Cell() for _ in range(cols)]
    return [
        Cell(" ", template.fg, template.bg, template.bold,
             template.underline, template.inverse, template.italic)
        for _ in range(cols)
    ]


@dataclass
class VtScreen:
    """Character-cell terminal screen with scrollback."""

    cols: int = 80
    rows: int = 24
    scrollback_limit: int = 5_000

    # public state
    cursor_x: int = 0
    cursor_y: int = 0
    cursor_visible: bool = True
    title: str = ""
    dirty: bool = True

    # screens
    _primary: list[list[Cell]] = field(default_factory=list, repr=False)
    _alt: list[list[Cell]] = field(default_factory=list, repr=False)
    _scrollback: list[list[Cell]] = field(default_factory=list, repr=False)
    _use_alt: bool = False

    # pen
    _fg: int = Color.DEFAULT
    _bg: int = Color.DEFAULT
    _bold: bool = False
    _underline: bool = False
    _inverse: bool = False
    _italic: bool = False

    # parser
    _buf: str = ""
    _state: str = "ground"  # ground | esc | csi | osc | dcs | charset
    _osc: str = ""
    _csi_param: str = ""
    _csi_inter: str = ""
    _csi_priv: bool = False

    # modes / saved cursor
    _origin: bool = False
    _auto_wrap: bool = True
    _insert: bool = False
    _scroll_top: int = 0
    _scroll_bot: int = 23
    _saved: tuple[int, int, int, int, bool, bool, bool, bool] | None = None
    _pending_wrap: bool = False
    _tabstops: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._primary = [_blank_row(self.cols) for _ in range(self.rows)]
        self._alt = [_blank_row(self.cols) for _ in range(self.rows)]
        self._scroll_bot = self.rows - 1
        self._tabstops = set(range(8, self.cols, 8))

    # -- public API ----------------------------------------------------------

    @property
    def buffer(self) -> list[list[Cell]]:
        return self._alt if self._use_alt else self._primary

    @property
    def scrollback(self) -> list[list[Cell]]:
        return self._scrollback

    def resize(self, rows: int, cols: int) -> None:
        rows = max(1, rows)
        cols = max(1, cols)
        if rows == self.rows and cols == self.cols:
            return
        self._resize_buf(self._primary, rows, cols)
        self._resize_buf(self._alt, rows, cols)
        self.rows = rows
        self.cols = cols
        self.cursor_x = min(self.cursor_x, cols - 1)
        self.cursor_y = min(self.cursor_y, rows - 1)
        self._scroll_top = 0
        self._scroll_bot = rows - 1
        self._tabstops = {t for t in self._tabstops if t < cols} | set(
            range(8, cols, 8))
        self._pending_wrap = False
        self.dirty = True

    def feed(self, data: str) -> None:
        if not data:
            return
        for ch in data:
            self._feed_char(ch)
        self.dirty = True

    def clear(self) -> None:
        self._primary = [_blank_row(self.cols) for _ in range(self.rows)]
        self._alt = [_blank_row(self.cols) for _ in range(self.rows)]
        self._scrollback.clear()
        self.cursor_x = self.cursor_y = 0
        self._pending_wrap = False
        self._use_alt = False
        self._reset_pen()
        self.dirty = True

    def display_lines(
        self, *, scrollback_offset: int = 0,
    ) -> list[list[Cell]]:
        """Rows visible in the viewport (bottom-aligned when scrolled)."""
        if self._use_alt or scrollback_offset <= 0:
            return [row[:] for row in self.buffer]
        sb = self._scrollback
        off = min(scrollback_offset, len(sb))
        if off == 0:
            return [row[:] for row in self.buffer]
        head = sb[-off:]
        body = self.buffer
        # take last `rows` of head+body
        combined = head + body
        return [r[:] for r in combined[-self.rows:]]

    # -- resize helper -------------------------------------------------------

    def _resize_buf(
        self, buf: list[list[Cell]], rows: int, cols: int,
    ) -> None:
        # width
        for i, row in enumerate(buf):
            if len(row) < cols:
                row.extend(Cell() for _ in range(cols - len(row)))
            elif len(row) > cols:
                buf[i] = row[:cols]
        # height — shrink from bottom; grow with blanks
        if len(buf) > rows:
            del buf[rows:]
        while len(buf) < rows:
            buf.append(_blank_row(cols))

    # -- pen -----------------------------------------------------------------

    def _reset_pen(self) -> None:
        self._fg = Color.DEFAULT
        self._bg = Color.DEFAULT
        self._bold = self._underline = self._inverse = self._italic = False

    def _pen_cell(self, char: str) -> Cell:
        return Cell(
            char, self._fg, self._bg,
            self._bold, self._underline, self._inverse, self._italic,
        )

    # -- cursor / scroll -----------------------------------------------------

    def _clamp_cursor(self) -> None:
        self.cursor_x = max(0, min(self.cursor_x, self.cols - 1))
        top, bot = self._scroll_top, self._scroll_bot
        if self._origin:
            self.cursor_y = max(top, min(self.cursor_y, bot))
        else:
            self.cursor_y = max(0, min(self.cursor_y, self.rows - 1))

    def _scroll_up(self, n: int = 1) -> None:
        top, bot = self._scroll_top, self._scroll_bot
        buf = self.buffer
        n = max(1, n)
        for _ in range(n):
            row = buf[top]
            if not self._use_alt and top == 0:
                # preserve into scrollback (copy cells)
                self._scrollback.append([c.copy() for c in row])
                while len(self._scrollback) > self.scrollback_limit:
                    self._scrollback.pop(0)
            del buf[top]
            buf.insert(bot, _blank_row(self.cols, self._pen_cell(" ")))

    def _scroll_down(self, n: int = 1) -> None:
        top, bot = self._scroll_top, self._scroll_bot
        buf = self.buffer
        n = max(1, n)
        for _ in range(n):
            del buf[bot]
            buf.insert(top, _blank_row(self.cols, self._pen_cell(" ")))

    def _put(self, ch: str) -> None:
        if self._pending_wrap and self._auto_wrap:
            self._pending_wrap = False
            self.cursor_x = 0
            if self.cursor_y >= self._scroll_bot:
                self._scroll_up(1)
                self.cursor_y = self._scroll_bot
            else:
                self.cursor_y += 1
        buf = self.buffer
        if self._insert:
            row = buf[self.cursor_y]
            row.insert(self.cursor_x, self._pen_cell(ch))
            if len(row) > self.cols:
                del row[self.cols:]
        else:
            buf[self.cursor_y][self.cursor_x] = self._pen_cell(ch)
        if self.cursor_x >= self.cols - 1:
            self._pending_wrap = self._auto_wrap
        else:
            self.cursor_x += 1

    # -- parser --------------------------------------------------------------

    def _feed_char(self, ch: str) -> None:
        st = self._state
        if st == "ground":
            o = ord(ch)
            if ch == "\x1b":
                self._state = "esc"
            elif ch == "\n" or ch == "\x0b" or ch == "\x0c":  # LF/VT/FF
                self._linefeed()
            elif ch == "\r":
                self.cursor_x = 0
                self._pending_wrap = False
            elif ch == "\b":
                self._pending_wrap = False
                if self.cursor_x > 0:
                    self.cursor_x -= 1
            elif ch == "\t":
                self._tab()
            elif ch == "\x07":  # BEL
                pass
            elif ch == "\x0e" or ch == "\x0f":  # SI/SO charset — ignore
                pass
            elif o < 0x20:
                pass
            elif ch == "\x7f":
                pass
            else:
                # printable (incl. unicode)
                self._put(ch)
        elif st == "esc":
            self._esc(ch)
        elif st == "csi":
            self._csi(ch)
        elif st in ("osc", "osc_esc"):
            self._osc_feed(ch)
        elif st == "charset":
            self._state = "ground"  # skip designate
        elif st == "dcs":
            # swallow until ST (ESC \) or BEL
            if ch == "\x1b":
                self._state = "esc_dcs"
            elif ch == "\x07":
                self._state = "ground"
        elif st == "esc_dcs":
            self._state = "ground" if ch == "\\" else "dcs"

    def _linefeed(self) -> None:
        self._pending_wrap = False
        if self.cursor_y >= self._scroll_bot:
            self._scroll_up(1)
            self.cursor_y = self._scroll_bot
        else:
            self.cursor_y += 1

    def _tab(self) -> None:
        self._pending_wrap = False
        nxt = self.cols - 1
        for t in sorted(self._tabstops):
            if t > self.cursor_x:
                nxt = t
                break
        self.cursor_x = min(nxt, self.cols - 1)

    def _esc(self, ch: str) -> None:
        if ch == "[":
            self._state = "csi"
            self._csi_param = ""
            self._csi_inter = ""
            self._csi_priv = False
            return
        if ch == "]":
            self._state = "osc"
            self._osc = ""
            return
        if ch == "P":
            self._state = "dcs"
            return
        if ch == "\\":  # ST stray
            self._state = "ground"
            return
        if ch in "()*%+-.":  # charset designate, next char ignored
            self._state = "charset"
            return
        if ch == "7":
            self._save_cursor()
        elif ch == "8":
            self._restore_cursor()
        elif ch == "D":  # IND
            self._linefeed()
        elif ch == "E":  # NEL
            self.cursor_x = 0
            self._linefeed()
        elif ch == "M":  # RI
            self._pending_wrap = False
            if self.cursor_y <= self._scroll_top:
                self._scroll_down(1)
                self.cursor_y = self._scroll_top
            else:
                self.cursor_y -= 1
        elif ch == "c":  # RIS
            self.clear()
            self.cursor_visible = True
            self._auto_wrap = True
            self._origin = False
            self._insert = False
        elif ch == "=":  # application keypad — ignore
            pass
        elif ch == ">":
            pass
        self._state = "ground"

    def _osc_feed(self, ch: str) -> None:
        if self._state == "osc_esc":
            if ch == "\\":
                self._osc_dispatch()
                self._state = "ground"
            else:
                if len(self._osc) < 4096:
                    self._osc += "\x1b" + ch
                self._state = "osc"
            return
        if ch == "\x07":
            self._osc_dispatch()
            self._state = "ground"
        elif ch == "\x1b":
            self._state = "osc_esc"
        else:
            if len(self._osc) < 4096:
                self._osc += ch

    def _osc_dispatch(self) -> None:
        body = self._osc
        self._osc = ""
        if body.startswith("0;") or body.startswith("2;"):
            self.title = body[2:]
        # ignore the rest (colors, hyperlinks, cwd)

    def _csi(self, ch: str) -> None:
        if ch == "?" and not self._csi_param and not self._csi_inter:
            self._csi_priv = True
            return
        if ch in "0123456789:;":
            self._csi_param += ch
            return
        if 0x20 <= ord(ch) <= 0x2F:
            self._csi_inter += ch
            return
        # final
        self._state = "ground"
        params = self._parse_params(self._csi_param)
        if self._csi_priv:
            self._csi_private(ch, params)
        else:
            self._csi_dispatch(ch, params, self._csi_inter)

    def _parse_params(self, raw: str) -> list[int]:
        if not raw:
            return []
        out: list[int] = []
        for part in raw.split(";"):
            if part == "" or part == ":":
                out.append(0)
            else:
                # take first subparam only (colon-separated)
                head = part.split(":", 1)[0]
                try:
                    out.append(int(head))
                except ValueError:
                    out.append(0)
        return out

    def _p(self, params: list[int], idx: int, default: int = 1) -> int:
        if idx >= len(params) or params[idx] == 0:
            return default
        return params[idx]

    def _csi_dispatch(self, final: str, params: list[int], inter: str) -> None:
        if inter:
            # ignore rare intermediates (e.g. CSI ! p soft reset partially)
            if inter == "!" and final == "p":
                self._soft_reset()
            return
        if final == "A":  # CUU
            self._pending_wrap = False
            self.cursor_y = max(self._scroll_top, self.cursor_y - self._p(params, 0))
        elif final == "B":  # CUD
            self._pending_wrap = False
            self.cursor_y = min(self._scroll_bot, self.cursor_y + self._p(params, 0))
        elif final == "C":  # CUF
            self._pending_wrap = False
            self.cursor_x = min(self.cols - 1, self.cursor_x + self._p(params, 0))
        elif final == "D":  # CUB
            self._pending_wrap = False
            self.cursor_x = max(0, self.cursor_x - self._p(params, 0))
        elif final == "E":  # CNL
            self._pending_wrap = False
            self.cursor_y = min(self._scroll_bot, self.cursor_y + self._p(params, 0))
            self.cursor_x = 0
        elif final == "F":  # CPL
            self._pending_wrap = False
            self.cursor_y = max(self._scroll_top, self.cursor_y - self._p(params, 0))
            self.cursor_x = 0
        elif final == "G":  # CHA
            self._pending_wrap = False
            self.cursor_x = min(self.cols - 1, max(0, self._p(params, 0) - 1))
        elif final in "Hf":  # CUP / HVP
            self._pending_wrap = False
            row = self._p(params, 0, 1) - 1
            col = self._p(params, 1, 1) - 1
            if self._origin:
                row += self._scroll_top
            self.cursor_y = row
            self.cursor_x = col
            self._clamp_cursor()
        elif final == "J":  # ED
            self._erase_display(params[0] if params else 0)
        elif final == "K":  # EL
            self._erase_line(params[0] if params else 0)
        elif final == "L":  # IL
            self._insert_lines(self._p(params, 0))
        elif final == "M":  # DL
            self._delete_lines(self._p(params, 0))
        elif final == "P":  # DCH
            self._delete_chars(self._p(params, 0))
        elif final == "S":  # SU
            self._scroll_up(self._p(params, 0))
        elif final == "T":  # SD
            self._scroll_down(self._p(params, 0))
        elif final == "X":  # ECH
            self._erase_chars(self._p(params, 0))
        elif final == "@":  # ICH
            self._insert_chars(self._p(params, 0))
        elif final == "d":  # VPA
            self._pending_wrap = False
            row = self._p(params, 0, 1) - 1
            if self._origin:
                row += self._scroll_top
            self.cursor_y = row
            self._clamp_cursor()
        elif final == "m":  # SGR
            self._sgr(params if params else [0])
        elif final == "n":  # DSR — no reply channel
            pass
        elif final == "r":  # DECSTBM
            top = (params[0] if params else 1) - 1
            bot = (params[1] if len(params) > 1 else self.rows) - 1
            top = max(0, min(top, self.rows - 1))
            bot = max(0, min(bot, self.rows - 1))
            if bot > top:
                self._scroll_top = top
                self._scroll_bot = bot
            self.cursor_x = 0
            self.cursor_y = self._scroll_top if self._origin else 0
            self._pending_wrap = False
        elif final == "s":
            self._save_cursor()
        elif final == "u":
            self._restore_cursor()
        elif final == "h":
            self._mode(params, True)
        elif final == "l":
            self._mode(params, False)
        elif final == "c":  # DA — no reply
            pass
        elif final == "g":  # TBC
            mode = params[0] if params else 0
            if mode == 0:
                self._tabstops.discard(self.cursor_x)
            elif mode == 3:
                self._tabstops.clear()

    def _csi_private(self, final: str, params: list[int]) -> None:
        enable = final == "h"
        if final not in "hl":
            return
        for p in params or [0]:
            if p == 1:  # cursor keys app mode — ignore
                pass
            elif p == 3:  # 132 col — ignore
                pass
            elif p == 6:
                self._origin = enable
                self.cursor_x = 0
                self.cursor_y = self._scroll_top if enable else 0
            elif p == 7:
                self._auto_wrap = enable
            elif p == 12:  # blink cursor
                pass
            elif p == 25:
                self.cursor_visible = enable
            elif p in (47, 1047):
                self._set_alt(enable, clear=False)
            elif p == 1049:
                if enable:
                    self._save_cursor()
                    self._set_alt(True, clear=True)
                else:
                    self._set_alt(False, clear=False)
                    self._restore_cursor()
            elif p == 1048:
                if enable:
                    self._save_cursor()
                else:
                    self._restore_cursor()
            elif p == 2004:  # bracketed paste
                pass
            elif p == 1000 or p == 1002 or p == 1003 or p == 1006:
                pass  # mouse — ignore

    def _mode(self, params: list[int], enable: bool) -> None:
        for p in params or [0]:
            if p == 4:
                self._insert = enable
            elif p == 20:
                pass  # LNM

    def _set_alt(self, on: bool, *, clear: bool) -> None:
        self._use_alt = on
        if on and clear:
            self._alt = [_blank_row(self.cols) for _ in range(self.rows)]
            self.cursor_x = self.cursor_y = 0
            self._pending_wrap = False
        if not on:
            self._scroll_top = 0
            self._scroll_bot = self.rows - 1

    def _soft_reset(self) -> None:
        self._reset_pen()
        self._insert = False
        self._origin = False
        self._auto_wrap = True
        self.cursor_visible = True
        self._scroll_top = 0
        self._scroll_bot = self.rows - 1
        self._pending_wrap = False

    def _save_cursor(self) -> None:
        self._saved = (
            self.cursor_x, self.cursor_y,
            self._fg, self._bg,
            self._bold, self._underline, self._inverse, self._italic,
        )

    def _restore_cursor(self) -> None:
        if self._saved is None:
            return
        (self.cursor_x, self.cursor_y, self._fg, self._bg,
         self._bold, self._underline, self._inverse, self._italic) = self._saved
        self._pending_wrap = False
        self._clamp_cursor()

    # -- erase / edit --------------------------------------------------------

    def _erase_display(self, mode: int) -> None:
        buf = self.buffer
        blank = self._pen_cell(" ")
        if mode == 0:  # cursor to end
            self._erase_line(0)
            for y in range(self.cursor_y + 1, self.rows):
                buf[y] = _blank_row(self.cols, blank)
        elif mode == 1:  # start to cursor
            for y in range(0, self.cursor_y):
                buf[y] = _blank_row(self.cols, blank)
            self._erase_line(1)
        elif mode in (2, 3):  # full (+scrollback for 3)
            for y in range(self.rows):
                buf[y] = _blank_row(self.cols, blank)
            if mode == 3 and not self._use_alt:
                self._scrollback.clear()

    def _erase_line(self, mode: int) -> None:
        buf = self.buffer
        row = buf[self.cursor_y]
        blank = self._pen_cell(" ")
        if mode == 0:
            for x in range(self.cursor_x, self.cols):
                row[x] = blank.copy()
        elif mode == 1:
            for x in range(0, self.cursor_x + 1):
                row[x] = blank.copy()
        elif mode == 2:
            buf[self.cursor_y] = _blank_row(self.cols, blank)

    def _erase_chars(self, n: int) -> None:
        row = self.buffer[self.cursor_y]
        blank = self._pen_cell(" ")
        for x in range(self.cursor_x, min(self.cols, self.cursor_x + n)):
            row[x] = blank.copy()

    def _delete_chars(self, n: int) -> None:
        row = self.buffer[self.cursor_y]
        n = max(1, n)
        del row[self.cursor_x:self.cursor_x + n]
        row.extend(_blank_row(n, self._pen_cell(" ")))
        if len(row) > self.cols:
            del row[self.cols:]

    def _insert_chars(self, n: int) -> None:
        row = self.buffer[self.cursor_y]
        n = max(1, n)
        blanks = _blank_row(n, self._pen_cell(" "))
        for i, c in enumerate(blanks):
            row.insert(self.cursor_x + i, c)
        del row[self.cols:]

    def _insert_lines(self, n: int) -> None:
        if self.cursor_y < self._scroll_top or self.cursor_y > self._scroll_bot:
            return
        buf = self.buffer
        n = max(1, n)
        for _ in range(n):
            if self._scroll_bot < len(buf):
                del buf[self._scroll_bot]
            buf.insert(self.cursor_y, _blank_row(self.cols, self._pen_cell(" ")))

    def _delete_lines(self, n: int) -> None:
        if self.cursor_y < self._scroll_top or self.cursor_y > self._scroll_bot:
            return
        buf = self.buffer
        n = max(1, n)
        for _ in range(n):
            del buf[self.cursor_y]
            buf.insert(self._scroll_bot, _blank_row(self.cols, self._pen_cell(" ")))

    # -- SGR -----------------------------------------------------------------

    def _sgr(self, params: Iterable[int]) -> None:
        ps = list(params)
        if not ps:
            ps = [0]
        i = 0
        while i < len(ps):
            p = ps[i]
            if p == 0:
                self._reset_pen()
            elif p == 1:
                self._bold = True
            elif p == 3:
                self._italic = True
            elif p == 4:
                self._underline = True
            elif p == 7:
                self._inverse = True
            elif p == 22:
                self._bold = False
            elif p == 23:
                self._italic = False
            elif p == 24:
                self._underline = False
            elif p == 27:
                self._inverse = False
            elif 30 <= p <= 37:
                self._fg = p - 30
            elif p == 38:
                i += self._extended_color(ps, i + 1, is_fg=True)
            elif p == 39:
                self._fg = Color.DEFAULT
            elif 40 <= p <= 47:
                self._bg = p - 40
            elif p == 48:
                i += self._extended_color(ps, i + 1, is_fg=False)
            elif p == 49:
                self._bg = Color.DEFAULT
            elif 90 <= p <= 97:
                self._fg = (p - 90) + 8
            elif 100 <= p <= 107:
                self._bg = (p - 100) + 8
            i += 1

    def _extended_color(self, ps: list[int], i: int, *, is_fg: bool) -> int:
        """Consume extended color params; return how many extra indices used."""
        if i >= len(ps):
            return 0
        mode = ps[i]
        if mode == 5 and i + 1 < len(ps):  # 256-color
            idx = ps[i + 1]
            # map 0-15 to our palette; others → approximate 16-color
            val = idx if 0 <= idx <= 15 else self._approx_256(idx)
            if is_fg:
                self._fg = val
            else:
                self._bg = val
            return 2
        if mode == 2 and i + 3 < len(ps):  # truecolor
            r, g, b = ps[i + 1], ps[i + 2], ps[i + 3]
            val = self._approx_rgb(r, g, b)
            if is_fg:
                self._fg = val
            else:
                self._bg = val
            return 4
        return 1

    @staticmethod
    def _approx_256(idx: int) -> int:
        if idx < 16:
            return idx
        if idx >= 232:  # grayscale
            level = idx - 232
            return Color.BRIGHT_WHITE if level > 12 else (
                Color.WHITE if level > 6 else Color.BRIGHT_BLACK if level > 2
                else Color.BLACK)
        # 6x6x6 cube 16..231
        c = idx - 16
        r, g, b = c // 36, (c // 6) % 6, c % 6
        return VtScreen._approx_rgb(r * 51, g * 51, b * 51)

    @staticmethod
    def _approx_rgb(r: int, g: int, b: int) -> int:
        # nearest of 16 ANSI colors (rough)
        colors = [
            (0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0),
            (0, 0, 170), (170, 0, 170), (0, 170, 170), (170, 170, 170),
            (85, 85, 85), (255, 85, 85), (85, 255, 85), (255, 255, 85),
            (85, 85, 255), (255, 85, 255), (85, 255, 255), (255, 255, 255),
        ]
        best, best_d = 7, 1 << 30
        for i, (cr, cg, cb) in enumerate(colors):
            d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
            if d < best_d:
                best, best_d = i, d
        return best
