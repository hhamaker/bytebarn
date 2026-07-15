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
    # "(default)" always present; model combo disabled until a provider is picked
    assert editor.provider_combo.itemText(0) == "(default)"
    assert not editor.model_combo.isEnabled()

    manager = ProviderManager(engine)
    assert manager.provider_list.count() >= 10
    manager.provider_list.setCurrentRow(0)
    # connect a provider -> it appears in the picker with its curated models
    engine.providers.auth.set("groq", {"type": "api", "key": "gsk-test"})
    editor._reload_models("groq/llama-3.3-70b-versatile")
    assert editor.provider_combo.currentText() == "groq"
    assert editor.model_combo.isEnabled()
    assert editor._selected_model() == "groq/llama-3.3-70b-versatile"


def test_skill_editor_builds(qapp, tmp_path):
    from crew.app.skill_editor import SkillEditor
    from crew.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "global")
    editor = SkillEditor(engine)
    # just constructing should be enough to prove imports & layout
    assert editor.list is not None
    assert editor.body is not None


def test_prompt_bar_two_stage_picker(qapp):
    from crew.app.prompt_bar import PromptBar

    bar = PromptBar()
    picked: list[str] = []
    bar.model_changed.connect(picked.append)

    bar.set_providers(["groq", "ollama"], "groq")
    bar.set_models(["llama-3.3-70b-versatile", "llama-3.1-8b-instant"], "")
    assert bar.provider_combo.currentText() == "groq"
    assert bar.current_model() == "groq/llama-3.3-70b-versatile"

    bar.model_combo.setCurrentIndex(1)
    assert picked[-1] == "groq/llama-3.1-8b-instant"

    # empty state: no combined model, guidance placeholder
    bar.set_providers([], "")
    bar.set_models([], "")
    assert bar.current_model() == ""


def test_transcript_image_attachment_preview(qapp, tmp_path):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QLabel

    from crew.app.transcript import TextBlock, Transcript

    img = tmp_path / "shot.png"
    QImage(600, 300, QImage.Format_ARGB32).save(str(img))

    t = Transcript()
    t.on_part_updated("f1", "file", {"path": str(img)}, role="user")
    widget = t._part_widgets["f1"]
    assert isinstance(widget, QLabel) and widget.pixmap() is not None
    assert widget.pixmap().width() <= 420  # scaled down

    # non-image attachments keep the paperclip line
    t.on_part_updated("f2", "file", {"path": str(tmp_path / "notes.txt")}, role="user")
    assert isinstance(t._part_widgets["f2"], TextBlock)


def test_tool_card_autoexpands_error(qapp):
    from crew.app.transcript import Transcript

    t = Transcript()
    t.on_part_updated("p", "tool", {"tool": "bash", "status": "error",
        "input": {"command": "false"}, "output": "exit 1"})
    card = t._part_widgets["p"]
    # body should have been forced visible in update_data
    assert not card.body.isHidden()
    assert "exit 1" in card.body.text()


def test_tool_card_pretty_bash_input(qapp):
    from crew.app.transcript import Transcript

    t = Transcript()
    t.on_part_updated("p", "tool", {"tool": "bash", "status": "done",
        "input": {"command": "echo hi"}, "output": "hi"})
    card = t._part_widgets["p"]
    assert "echo hi" in card.header.text() or "echo hi" in card.body.text()


def test_prompt_bar_queue_depth(qapp):
    from crew.app.prompt_bar import PromptBar

    bar = PromptBar()
    bar.set_queue_depth(2)
    assert "2 queued" in bar.queue_label.text()
    assert not bar.queue_label.isHidden()
    bar.set_queue_depth(0)
    assert bar.queue_label.isHidden()


def test_thinking_indicator_lifecycle(qapp):
    from crew.app.transcript import Transcript, _Thinking

    t = Transcript()
    t.show_thinking("chat", "#d19a66")
    assert isinstance(t._thinking, _Thinking)
    # assistant content replaces the indicator; user parts don't
    t.on_part_updated("u1", "text", {"text": "hi"}, role="user")
    assert t._thinking is not None
    t.on_part_updated("a1", "text", {"text": "reply"}, role="assistant")
    assert t._thinking is None
    # explicit dismissal is idempotent
    t.dismiss_thinking()


def test_promote_queued_clears_style(qapp):
    from crew.app.transcript import TextBlock, Transcript

    t = Transcript()
    t.add_user_text("local-1", "first", queued=True)
    bubble = t._part_widgets["local-1"]
    assert isinstance(bubble, TextBlock) and bubble._queued
    assert "[queued]" in bubble.text()
    t.promote_queued()
    assert not bubble._queued
    assert "[queued]" not in bubble.text()


def test_text_block_streams_plain_then_markdown(qapp):
    from crew.app.transcript import TextBlock

    block = TextBlock("hi", user=False)
    block.update_text("**bold**", streaming=True)
    # streaming path escapes rather than rendering markdown immediately
    assert "<strong>" not in block.text() and "<b>" not in block.text()
    assert "bold" in block.text()
    block._finalize_markdown()
    assert "bold" in block.text()


def _proj(pid="p1", name="Proj", path="/x"):
    from types import SimpleNamespace
    return SimpleNamespace(id=pid, name=name, path=path)


def _sess(sid, agent="build", children=None):
    from types import SimpleNamespace
    import time as _time
    return SimpleNamespace(id=sid, title=sid, agent=agent, model="",
                           updated_at=_time.time(), created_at=_time.time(),
                           parent_session_id=None, children=children or [])


def test_session_list_keyboard_navigation_selects(qapp):
    from crew.app.session_list import SessionList

    sl = SessionList()
    picked: list[str] = []
    sl.session_selected.connect(picked.append)
    # single project -> sessions flat at top level
    sl.populate([_proj()], {"p1": [_sess("a"), _sess("b"), _sess("c")]}, set(), "a")
    assert picked == []  # populate blocks signals
    sl.tree.setCurrentItem(sl.tree.topLevelItem(1))
    assert picked == ["b"]
    sl.tree.setCurrentItem(sl.tree.topLevelItem(2))
    assert picked == ["b", "c"]


def test_session_list_groups_by_project(qapp):
    from PySide6.QtCore import Qt

    from crew.app.session_list import SessionList

    sl = SessionList()
    projs = [_proj("p1", "Alpha"), _proj("p2", "Beta")]
    sl.populate(projs, {"p1": [_sess("a")], "p2": [_sess("b")]}, set(), "a")
    # two projects -> two project nodes at the top level
    assert sl.tree.topLevelItemCount() == 2
    top0 = sl.tree.topLevelItem(0)
    assert top0.data(0, Qt.UserRole + 1) == "project"
    assert top0.childCount() == 1


def test_session_list_shows_project_folders(qapp):
    from PySide6.QtCore import Qt

    from crew.app.session_list import SessionList

    sl = SessionList()
    projs = [_proj("p1", "Alpha"), _proj("p2", "Beta")]
    # a project owns folders which render as folder nodes beneath it
    sl.populate(projs, {"p1": [_sess("a")], "p2": []}, set(), "a",
                None, {"p1": ["/code/foo", "/code/bar"]})
    top0 = sl.tree.topLevelItem(0)
    kinds = [top0.child(i).data(0, Qt.UserRole + 1) for i in range(top0.childCount())]
    assert kinds.count("folder") == 2 and "session" in kinds
    folder = next(top0.child(i) for i in range(top0.childCount())
                  if top0.child(i).data(0, Qt.UserRole + 1) == "folder")
    assert folder.data(0, Qt.UserRole + 2) == "p1"          # owning project
    assert folder.data(0, Qt.UserRole) in ("/code/foo", "/code/bar")


def test_session_list_folder_signals(qapp):
    from crew.app.session_list import SessionList

    sl = SessionList()
    added: list = []
    removed: list = []
    sl.add_folder_to_project.connect(added.append)
    sl.remove_folder_from_project.connect(lambda p, f: removed.append((p, f)))
    sl.add_folder_to_project.emit("p1")
    sl.remove_folder_from_project.emit("p1", "/code/foo")
    assert added == ["p1"]
    assert removed == [("p1", "/code/foo")]


def test_session_list_delete_key_removes_folder(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    from crew.app.session_list import SessionList

    sl = SessionList()
    removed: list = []
    sl.remove_folder_from_project.connect(lambda p, f: removed.append((p, f)))
    sl.populate([_proj("p1", "Alpha"), _proj("p2", "Beta")],
                {"p1": [_sess("a")], "p2": []}, set(), "a",
                None, {"p1": ["/code/foo"]})
    top0 = sl.tree.topLevelItem(0)
    folder = next(top0.child(i) for i in range(top0.childCount())
                  if top0.child(i).data(0, Qt.UserRole + 1) == "folder")
    sl.tree.setCurrentItem(folder)
    monkeypatch.setattr(sl, "_confirm_remove_folder", lambda: True)

    event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
    sl.keyPressEvent(event)
    assert removed == [("p1", "/code/foo")]


def test_crew_stage_stop_signal(qapp):
    from crew.app.crew_stage import CrewStage
    from crew.engine.events import TaskStarted

    stage = CrewStage()
    stopped: list[str] = []
    stage.stop_requested.connect(stopped.append)
    stage.handle_event(
        TaskStarted(session_id="p", subagent_session_id="child1",
                    agent="explore", description="x"))
    # simulate right-click on the only visible member
    stage.stop_requested.emit("child1")
    assert stopped == ["child1"]


def test_session_list_project_rename_delete_signals(qapp, monkeypatch):
    from crew.app.session_list import SessionList

    sl = SessionList()
    renamed: list = []
    deleted: list = []
    sl.rename_project.connect(renamed.append)
    sl.delete_project.connect(deleted.append)
    sl.populate([_proj("p1", "Alpha")], {"p1": []}, set(), "", None, {})
    top = sl.tree.topLevelItem(0)
    # context menu would emit; test the signals directly
    sl.rename_project.emit("p1")
    sl.delete_project.emit("p1")
    assert renamed == ["p1"]
    assert deleted == ["p1"]


def test_session_list_multi_select_delete(qapp):
    from crew.app.session_list import SessionList

    sl = SessionList()
    emitted: list[list] = []
    sl.delete_sessions.connect(emitted.append)
    sl.populate([_proj()], {"p1": [_sess("a"), _sess("b"), _sess("c")]}, set(), "a")
    for i in range(3):
        sl.tree.topLevelItem(i).setSelected(True)
    ids = sl._selected_session_ids()
    assert set(ids) == {"a", "b", "c"}
    sl.delete_sessions.emit(ids)
    assert emitted == [["a", "b", "c"]]


def test_agent_list_grouped_by_mode(qapp, tmp_path):
    from PySide6.QtCore import Qt

    from crew.app.agent_editor import AgentEditor
    from crew.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    editor = AgentEditor(engine)

    labels = [editor.agent_list.item(i).text() for i in range(editor.agent_list.count())]
    assert any("PRIMARY" in l for l in labels)
    assert any("SUBAGENTS" in l for l in labels)
    # headers are not selectable and carry no agent
    headers = [editor.agent_list.item(i) for i in range(editor.agent_list.count())
               if editor.agent_list.item(i).data(Qt.UserRole) is None]
    assert headers and all(not (h.flags() & Qt.ItemIsSelectable) for h in headers)
    # primaries listed before subagents; explore (subagent) after build (primary)
    names = [editor.agent_list.item(i).data(Qt.UserRole)
             for i in range(editor.agent_list.count())]
    assert names.index("build") < names.index("explore")


def test_menu_bar_has_menus(qapp, tmp_path):
    from crew.app.main_window import MainWindow
    from crew.engine.facade import Engine

    proj = tmp_path / "proj2"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db2.sqlite", global_dir=tmp_path / "g2")
    window = MainWindow(engine)
    titles = [a.menu().title() for a in window.menuBar().actions() if a.menu()]
    assert titles == ["&File", "&Projects", "&Session", "&Tools", "&Help"]


async def test_new_session_requires_directory(qapp, tmp_path, monkeypatch):
    from crew.app.main_window import MainWindow
    from crew.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    await engine.start()
    try:
        window = MainWindow(engine)

        # cancelling the picker (empty string) creates nothing
        monkeypatch.setattr(window, "_prompt_directory", lambda _caption: "")
        await window._new_session()
        assert window.current_session_id is None
        assert not await engine.store.list_sessions(engine.project.id)

        # choosing a directory creates a session rooted there
        monkeypatch.setattr(window, "_prompt_directory", lambda _caption: str(workdir))
        await window._new_session()
        assert window.current_session_id is not None
        session = await engine.store.get_session(window.current_session_id)
        assert session.directory == str(workdir)
    finally:
        await engine.stop()


async def test_bootstrap_empty_shows_no_session(qapp, tmp_path, monkeypatch):
    from crew.app.main_window import MainWindow
    from crew.engine.facade import Engine

    proj = tmp_path / "proj2"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db2.sqlite", global_dir=tmp_path / "g2")
    await engine.start()
    try:
        window = MainWindow(engine)
        # no folder chosen on first launch -> no session, welcome header
        monkeypatch.setattr(window, "_prompt_directory", lambda _caption: "")
        await window.bootstrap()
        assert window.current_session_id is None
        assert "No session" in window.header_title.text()
    finally:
        for task in getattr(window, "_tasks", []):
            task.cancel()
        await engine.stop()


def test_settings_uses_model_pickers(qapp, tmp_path):
    from crew.app.model_picker import ModelPicker
    from crew.app.settings import SettingsDialog
    from crew.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    engine.providers.auth.set("groq", {"type": "api", "key": "gsk-x"})

    dlg = SettingsDialog(engine)
    assert isinstance(dlg.model, ModelPicker)
    assert isinstance(dlg.small_model, ModelPicker)
    # a connected provider populates the provider dropdown
    dlg.model.set_model("groq/llama-3.3-70b-versatile")
    assert dlg.model.provider_combo.currentText() == "groq"
    assert dlg.model.value() == "groq/llama-3.3-70b-versatile"


def test_model_picker_default_and_empty(qapp, tmp_path):
    from crew.app.model_picker import ModelPicker
    from crew.engine.facade import Engine

    proj = tmp_path / "proj2"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db2.sqlite", global_dir=tmp_path / "g2")

    picker = ModelPicker(engine, allow_default=True)
    picker.set_model("")
    assert picker.provider_combo.currentText() == "(default)"
    assert picker.value() == ""  # (default) yields no explicit model
    assert not picker.model_combo.isEnabled()


async def test_last_model_persists_for_new_sessions(qapp, tmp_path, monkeypatch):
    from crew.app.main_window import MainWindow
    from crew.engine.facade import Engine

    proj = tmp_path / "lm-proj"
    proj.mkdir()
    work = tmp_path / "lm-work"
    work.mkdir()
    engine = Engine(proj, db_path=tmp_path / "lm.sqlite", global_dir=tmp_path / "lm-g")
    engine.providers.auth.set("groq", {"type": "api", "key": "gsk-x"})
    await engine.start()
    try:
        window = MainWindow(engine)
        monkeypatch.setattr(window, "_prompt_directory", lambda _c: str(work))

        # user picks a non-default model; it becomes the remembered default
        window._model_changed("groq/llama-3.1-8b-instant")
        assert window._default_model() == "groq/llama-3.1-8b-instant"

        # a brand new session starts on that model, not config.model
        await window._new_session()
        session = await engine.store.get_session(window.current_session_id)
        assert session.model == "groq/llama-3.1-8b-instant"
        assert session.model != engine.config.model
    finally:
        await engine.stop()
