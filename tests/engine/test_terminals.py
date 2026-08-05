"""ProcessHub ring buffer + events."""

from __future__ import annotations

from bytebarn.engine.events import EventBus, TerminalChunk, TerminalClosed, TerminalOpened
from bytebarn.engine.terminals import ProcessHub


def test_hub_open_append_snapshot_close():
    bus = EventBus()
    q = bus.queue()
    hub = ProcessHub(bus)

    tid = hub.open(
        kind="claude-code",
        title="CC · demo",
        session_id="sess1",
        cwd="/tmp",
        pid=1234,
        terminal_id="cc:sess1",
    )
    assert tid == "cc:sess1"
    ev = q.get_nowait()
    assert isinstance(ev, TerminalOpened)
    assert ev.terminal_id == tid
    assert ev.kind == "claude-code"

    hub.append(tid, "hello ")
    hub.append(tid, "world\n")
    # force flush via large chunk or close
    hub.append(tid, "x" * 5000)
    # drain chunks
    chunks = []
    while not q.empty():
        e = q.get_nowait()
        if isinstance(e, TerminalChunk):
            chunks.append(e.text)
    assert any("hello" in c or "world" in c or "xxx" in c for c in chunks)

    text = hub.snapshot(tid)
    assert "hello world" in text
    assert "x" * 100 in text

    hub.close(tid, exit_code=0)
    closed = q.get_nowait()
    assert isinstance(closed, TerminalClosed)
    assert closed.exit_code == 0
    info = hub.get(tid)
    assert info is not None and info.status == "exited"

    listed = hub.list()
    assert any(t.id == tid for t in listed)


def test_hub_ring_buffer_caps_memory():
    hub = ProcessHub()
    tid = hub.open(kind="claude-code", title="t", terminal_id="t1")
    hub.append(tid, "a" * 300_000)
    hub.append(tid, "b" * 300_000)
    snap = hub.snapshot(tid)
    assert len(snap) <= 520_000
    assert "b" in snap  # newest kept
