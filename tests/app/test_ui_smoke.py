"""Offscreen instantiation of the full window — catches wiring/regression errors."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


async def test_main_window_builds_and_loads(qapp, tmp_path):
    from crew.app.main_window import MainWindow
    from crew.engine.facade import Engine
    from crew.engine.providers.fake import FakeProvider, text_turn

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({"model": "fake/m", "small_model": "fake/m"}))

    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    engine.providers.register("fake", FakeProvider([text_turn("hi")]))
    await engine.start()
    try:
        window = MainWindow(engine)
        session = await engine.new_session()
        await window._load_session(session.id)
        await window._refresh_sessions()

        # widgets exist and are wired
        assert window.transcript is not None
        assert window.prompt_bar.agent_combo.count() >= 3  # build/plan/orchestrator
        assert window.session_list.tree.topLevelItemCount() == 1

        # crew stage responds to events
        from crew.engine.events import TaskStarted

        window.crew_stage.handle_event(
            TaskStarted(session_id=session.id, subagent_session_id="x",
                        agent="explore", description="d"))
        assert window.crew_stage.state.members
    finally:
        await engine.stop()


async def test_dialogs_construct(qapp):
    from crew.app.permission_dialog import PermissionDialog
    from crew.app.question_dialog import QuestionDialog

    p = PermissionDialog("bash", "rm -rf /tmp/x", {"command": "rm -rf /tmp/x"})
    assert p.verdict == "deny"
    p = PermissionDialog("edit", "a.py", {"path": "a.py", "old_string": "x", "new_string": "y"})
    q = QuestionDialog("Pick?", ["a", "b"])
    assert q.answer == ""


def test_transcript_streaming_updates(qapp):
    from crew.app.transcript import Transcript

    t = Transcript()
    t.on_part_updated("p1", "text", {"text": "hello"})
    t.on_part_updated("p1", "text", {"text": "hello world"})
    t.on_part_updated("p2", "tool", {"tool": "bash", "status": "running", "input": {"command": "ls"}})
    t.on_part_updated("p2", "tool", {"tool": "bash", "status": "done", "output": "files"})
    assert len(t._part_widgets) == 2


def test_prompt_bar_fuzzy(qapp):
    from crew.app.prompt_bar import fuzzy_match

    assert fuzzy_match("gl", "goal")
    assert fuzzy_match("", "anything")
    assert not fuzzy_match("xyz", "goal")


def test_sprite_rendering_offscreen(qapp):
    from PySide6.QtGui import QColor, QImage, QPainter

    from crew.app.sprites import draw_critter

    image = QImage(120, 120, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for state in ("working", "retrying", "done", "waiting"):
        draw_critter(painter, 10, 20, 4, "cat", QColor("#61afef"), state=state, frame=7, crowned=True)
    painter.end()
    # something was drawn
    assert any(image.pixel(x, y) != 0 for x in range(120) for y in range(0, 120, 10))


def test_look_for_known_types_and_stability(qapp):
    from crew.app.sprites import ACCENTS, SPECIES, look_for

    assert look_for("explore") == ("bunny", "none")
    assert look_for("orchestrator") == ("bear", "hat")
    assert look_for("Tester") == ("cat", "goggles")
    assert look_for("code-reviewer") == ("bear", "glasses")  # substring match
    # custom agents: stable hash-based look within valid ranges
    species, accent = look_for("my-custom-agent")
    assert species in SPECIES and accent in ACCENTS
    assert look_for("my-custom-agent") == (species, accent)


def test_accents_render(qapp):
    from PySide6.QtGui import QColor, QImage, QPainter

    from crew.app.sprites import ACCENTS, draw_critter

    image = QImage(120, 120, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for accent in ACCENTS:
        draw_critter(painter, 10, 20, 4, "dog", QColor("#98c379"), accent=accent)
    painter.end()
    assert any(image.pixel(x, y) != 0 for x in range(120) for y in range(0, 120, 10))


def test_agent_editor_and_provider_manager_build(qapp, tmp_path):
    from crew.app.agent_editor import AgentEditor
    from crew.app.provider_manager import ProviderManager
    from crew.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "global")

    editor = AgentEditor(engine)
    assert editor.agent_list.count() >= 5  # built-ins
    editor.agent_list.setCurrentRow(0)
    assert editor.model_combo.count() >= 1

    manager = ProviderManager(engine)
    assert manager.provider_list.count() >= 10
    manager.provider_list.setCurrentRow(0)
    # saving a key flips status to connected and grows the model list
    before = editor.model_combo.count()
    engine.providers.auth.set("groq", {"type": "api", "key": "gsk-test"})
    editor._reload_models()
    assert editor.model_combo.count() > before
