"""Unit tests for the in-process VT screen emulator."""

from __future__ import annotations

from bytebarn.app.vt import Color, VtScreen


def test_basic_print_and_newline():
    s = VtScreen(cols=10, rows=4)
    s.feed("hi\r\nthere")
    assert "".join(c.char for c in s.buffer[0]).startswith("hi")
    assert "".join(c.char for c in s.buffer[1]).startswith("there")
    assert s.cursor_y == 1
    assert s.cursor_x == 5


def test_sgr_colors_and_reset():
    s = VtScreen(cols=20, rows=3)
    s.feed("\x1b[31;1mR\x1b[0m.")
    assert s.buffer[0][0].char == "R"
    assert s.buffer[0][0].fg == Color.RED
    assert s.buffer[0][0].bold is True
    assert s.buffer[0][1].char == "."
    assert s.buffer[0][1].fg == Color.DEFAULT
    assert s.buffer[0][1].bold is False


def test_cursor_addressing_and_erase():
    s = VtScreen(cols=8, rows=4)
    s.feed("ABCDEFGH")
    s.feed("\x1b[1;1H\x1b[2K")  # home + erase line
    assert all(c.char == " " for c in s.buffer[0])
    s.feed("\x1b[2;3HX")
    assert s.buffer[1][2].char == "X"
    assert s.cursor_x == 3 and s.cursor_y == 1


def test_scroll_up_builds_scrollback():
    s = VtScreen(cols=5, rows=3)
    s.feed("one\r\ntwo\r\nthree\r\nfour")
    assert len(s.scrollback) >= 1
    sb0 = "".join(c.char for c in s.scrollback[0]).rstrip()
    assert sb0 == "one"
    # live screen bottom should hold latest
    last = "".join(c.char for c in s.buffer[-1]).rstrip()
    assert "four" in last


def test_alt_screen_1049():
    s = VtScreen(cols=10, rows=4)
    s.feed("main")
    s.feed("\x1b[?1049h")  # enter alt, clear
    assert all(c.char == " " for c in s.buffer[0])
    s.feed("alt")
    assert "".join(c.char for c in s.buffer[0]).startswith("alt")
    s.feed("\x1b[?1049l")  # back to primary
    assert "".join(c.char for c in s.buffer[0]).startswith("main")


def test_cup_and_el():
    s = VtScreen(cols=10, rows=3)
    s.feed("0123456789")
    s.feed("\x1b[1;5H\x1b[K")  # erase from col 5 to end
    line = "".join(c.char for c in s.buffer[0])
    assert line[:4] == "0123"
    assert line[4:].strip() == ""


def test_backspace_and_cr():
    s = VtScreen(cols=10, rows=2)
    s.feed("abc\b\bXY")
    assert "".join(c.char for c in s.buffer[0]).startswith("aXY")
    s.feed("\rZ")
    assert s.buffer[0][0].char == "Z"


def test_osc_title_bel_and_st():
    s = VtScreen(cols=10, rows=2)
    s.feed("\x1b]0;Hello\x07")
    assert s.title == "Hello"
    s.feed("\x1b]2;World\x1b\\")
    assert s.title == "World"


def test_resize_preserves_content():
    s = VtScreen(cols=5, rows=2)
    s.feed("hello")
    s.resize(3, 8)
    assert s.cols == 8 and s.rows == 3
    assert "".join(c.char for c in s.buffer[0]).startswith("hello")


def test_truecolor_approx_does_not_crash():
    s = VtScreen(cols=4, rows=2)
    s.feed("\x1b[38;2;255;128;0mX\x1b[0m")
    assert s.buffer[0][0].char == "X"
    assert s.buffer[0][0].fg >= 0


def test_scroll_region():
    s = VtScreen(cols=4, rows=5)
    s.feed("\x1b[2;4r")  # scroll region rows 2-4
    s.feed("\x1b[4;1H")  # go to bottom of region
    s.feed("a\r\nb\r\nc")
    # row 0 (outside region) untouched
    assert all(c.char == " " for c in s.buffer[0])
