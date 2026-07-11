"""Crew-stage state model: pure event projection, no Qt needed for logic.

(Imports Qt types transitively, so runs offscreen-safe: no widgets created.)
"""

from crew.engine.events import RunFinished, TaskFinished, TaskStarted, TaskUpdated, TodoUpdated


def _state():
    from crew.app.crew_stage import StageState

    return StageState()


def test_task_lifecycle():
    state = _state()
    assert not state.active
    state.on_event(TaskStarted(session_id="p", subagent_session_id="c1", agent="explore",
                               description="find stuff"), {"explore": "#56b6c2"})
    assert state.active
    member = state.members["c1"]
    assert member.agent == "explore" and member.color == "#56b6c2" and member.status == "running"

    state.on_event(TaskUpdated(session_id="p", subagent_session_id="c1",
                               status="running", detail='grep "session"'))
    assert state.members["c1"].detail == 'grep "session"'

    state.on_event(TaskUpdated(session_id="p", subagent_session_id="c1", status="retrying"))
    assert state.members["c1"].status == "retrying"

    state.on_event(TaskFinished(session_id="p", subagent_session_id="c1", status="done"))
    assert state.members["c1"].status == "done"


def test_parallel_members_ordered():
    state = _state()
    for i in range(3):
        state.on_event(TaskStarted(session_id="p", subagent_session_id=f"c{i}",
                                   agent="general", description=f"task {i}"))
    assert [m.session_id for m in state.visible_members()] == ["c0", "c1", "c2"]
    assert state.overflow == 0


def test_overflow_count():
    state = _state()
    for i in range(11):
        state.on_event(TaskStarted(session_id="p", subagent_session_id=f"c{i}",
                                   agent="general", description="t"))
    assert state.overflow == 3  # 11 - MAX_VISIBLE(8)


def test_waiting_todos_from_events():
    state = _state()
    state.on_event(TodoUpdated(session_id="p", todos=[
        {"content": "write tests", "status": "pending"},
        {"content": "build feature", "status": "in_progress"},
        {"content": "done thing", "status": "completed"},
    ]))
    assert state.waiting == ["write tests"]


def test_run_finished_resets():
    state = _state()
    state.on_event(TaskStarted(session_id="p", subagent_session_id="c1", agent="g", description="t"))
    state.on_event(RunFinished(session_id="p"))
    assert not state.active and not state.members and not state.waiting


def test_species_stable():
    from crew.app.sprites import SPECIES, species_for

    assert species_for("explore") == species_for("explore")
    assert species_for("explore") in SPECIES


def test_current_todo_and_planning_activation():
    from crew.engine.events import TodoUpdated

    state = _state()
    state.on_event(TodoUpdated(session_id="p", todos=[
        {"content": "write tests", "status": "pending"},
        {"content": "build feature", "status": "in_progress"},
    ]))
    assert state.current_todo == "build feature"
    # a plan alone activates the stage (planning phase, no workers yet)
    assert state.active and state.started_at > 0


def test_summary_headline():
    from crew.engine.events import RunFinished, TaskFinished, TaskStarted, TaskUpdated, TodoUpdated

    state = _state()
    assert state.summary() == "planning…"
    state.on_event(TodoUpdated(session_id="p", todos=[
        {"content": "a", "status": "pending"},
        {"content": "b", "status": "pending"},
    ]))
    assert state.summary().startswith("2 queued · 0:")
    state.on_event(TaskStarted(session_id="p", subagent_session_id="c1",
                               agent="general", description="t"))
    state.on_event(TaskStarted(session_id="p", subagent_session_id="c2",
                               agent="explore", description="t"))
    state.on_event(TaskFinished(session_id="p", subagent_session_id="c2", status="done"))
    summary = state.summary()
    assert "1 working" in summary and "1 done" in summary and "2 queued" in summary
    state.on_event(TaskUpdated(session_id="p", subagent_session_id="c1", status="retrying"))
    assert "1 retrying" in state.summary()
    state.on_event(RunFinished(session_id="p"))
    assert state.summary() == "planning…" and state.current_todo == ""
