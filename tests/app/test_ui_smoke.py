"""Offscreen instantiation of the full window — catches wiring/regression errors."""

import asyncio
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
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine
    from bytebarn.engine.providers.fake import FakeProvider, text_turn

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
        from bytebarn.engine.events import TaskStarted

        window.crew_stage.handle_event(
            TaskStarted(session_id=session.id, subagent_session_id="x",
                        agent="explore", description="d"))
        assert window.crew_stage.state.members
    finally:
        await engine.stop()


async def test_dialogs_construct(qapp):
    from bytebarn.app.permission_dialog import PermissionDialog
    from bytebarn.app.question_dialog import QuestionDialog

    p = PermissionDialog("bash", "rm -rf /tmp/x", {"command": "rm -rf /tmp/x"})
    assert p.verdict == "deny"
    p = PermissionDialog("edit", "a.py", {"path": "a.py", "old_string": "x", "new_string": "y"})
    q = QuestionDialog("Pick?", ["a", "b"])
    assert q.answer == ""


def test_transcript_streaming_updates(qapp):
    from bytebarn.app.transcript import Transcript

    t = Transcript()
    t.on_part_updated("p1", "text", {"text": "hello"})
    t.on_part_updated("p1", "text", {"text": "hello world"})
    t.on_part_updated("p2", "tool", {"tool": "bash", "status": "running", "input": {"command": "ls"}})
    t.on_part_updated("p2", "tool", {"tool": "bash", "status": "done", "output": "files"})
    assert len(t._part_widgets) == 2


def test_prompt_bar_fuzzy(qapp):
    from bytebarn.app.prompt_bar import fuzzy_match

    assert fuzzy_match("gl", "goal")
    assert fuzzy_match("", "anything")
    assert not fuzzy_match("xyz", "goal")


def test_sprite_rendering_offscreen(qapp):
    from PySide6.QtGui import QColor, QImage, QPainter

    from bytebarn.app.sprites import draw_critter

    image = QImage(120, 120, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for state in ("working", "retrying", "done", "waiting"):
        draw_critter(painter, 10, 20, 4, "cat", QColor("#61afef"), state=state, frame=7, crowned=True)
    painter.end()
    # something was drawn
    assert any(image.pixel(x, y) != 0 for x in range(120) for y in range(0, 120, 10))


def test_look_for_known_types_and_stability(qapp):
    from bytebarn.app.sprites import ACCENTS, SPECIES, look_for

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

    from bytebarn.app.sprites import ACCENTS, draw_critter

    image = QImage(120, 120, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for accent in ACCENTS:
        draw_critter(painter, 10, 20, 4, "dog", QColor("#98c379"), accent=accent)
    painter.end()
    assert any(image.pixel(x, y) != 0 for x in range(120) for y in range(0, 120, 10))


def test_agent_editor_and_provider_manager_build(qapp, tmp_path):
    from bytebarn.app.agent_editor import AgentEditor
    from bytebarn.app.provider_manager import ProviderManager
    from bytebarn.engine.facade import Engine

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
    from bytebarn.app.skill_editor import SkillEditor
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "global")
    editor = SkillEditor(engine)
    # just constructing should be enough to prove imports & layout
    assert editor.list is not None
    assert editor.body is not None


def test_prompt_bar_two_stage_picker(qapp):
    from bytebarn.app.prompt_bar import PromptBar

    bar = PromptBar()
    picked: list[str] = []
    bar.model_changed.connect(picked.append)

    bar.set_providers(["groq", "ollama"], "groq")
    bar.set_models(["llama-3.3-70b-versatile", "llama-3.1-8b-instant"], "")
    assert bar.provider_combo.currentText() == "groq"
    # empty current → leave unselected (user must pick; no models[0] auto-pick)
    assert bar.current_model() == ""

    bar.model_combo.setCurrentIndex(1)
    assert picked[-1] == "groq/llama-3.1-8b-instant"
    assert bar.current_model() == "groq/llama-3.1-8b-instant"

    # explicit current still selects
    bar.set_models(["llama-3.3-70b-versatile", "llama-3.1-8b-instant"], "llama-3.3-70b-versatile")
    assert bar.current_model() == "groq/llama-3.3-70b-versatile"

    # empty state: no combined model, guidance placeholder
    bar.set_providers([], "")
    bar.set_models([], "")
    assert bar.current_model() == ""


def test_prompt_bar_set_models_does_not_auto_pick(qapp):
    """set_models with empty current must leave selection empty (no models[0])."""
    from bytebarn.app.prompt_bar import PromptBar

    bar = PromptBar()
    bar.set_providers(["anthropic"], "anthropic")
    bar.set_models(["claude-opus-4", "claude-sonnet-4"], "")
    assert bar.current_model() == ""
    # user then picks
    bar.model_combo.setCurrentIndex(0)
    assert bar.current_model() == "anthropic/claude-opus-4"


def test_transcript_image_attachment_preview(qapp, tmp_path):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QLabel

    from bytebarn.app.transcript import TextBlock, Transcript

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
    from bytebarn.app.transcript import Transcript

    t = Transcript()
    t.on_part_updated("p", "tool", {"tool": "bash", "status": "error",
        "input": {"command": "false"}, "output": "exit 1"})
    card = t._part_widgets["p"]
    # body should have been forced visible in update_data
    assert not card.body.isHidden()
    assert "exit 1" in card.body.text()


def test_tool_card_pretty_bash_input(qapp):
    from bytebarn.app.transcript import Transcript

    t = Transcript()
    t.on_part_updated("p", "tool", {"tool": "bash", "status": "done",
        "input": {"command": "echo hi"}, "output": "hi"})
    card = t._part_widgets["p"]
    assert "echo hi" in card.header.text() or "echo hi" in card.body.text()


def test_prompt_bar_queue_depth(qapp):
    from bytebarn.app.prompt_bar import PromptBar

    bar = PromptBar()
    bar.set_queue_depth(2)
    assert "2 queued" in bar.queue_label.text()
    assert not bar.queue_label.isHidden()
    bar.set_queue_depth(0)
    assert bar.queue_label.isHidden()


def test_thinking_indicator_lifecycle(qapp):
    from bytebarn.app.transcript import Transcript, _Thinking

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
    from bytebarn.app.transcript import TextBlock, Transcript

    t = Transcript()
    t.add_user_text("local-1", "first", queued=True)
    bubble = t._part_widgets["local-1"]
    assert isinstance(bubble, TextBlock) and bubble._queued
    assert "[queued]" in bubble.text()
    t.promote_queued()
    assert not bubble._queued
    assert "[queued]" not in bubble.text()


def test_text_block_streams_plain_then_markdown(qapp):
    from bytebarn.app.transcript import TextBlock

    block = TextBlock("hi", user=False)
    block.update_text("**bold**", streaming=True)
    # streaming path escapes rather than rendering markdown immediately
    assert "<strong>" not in block.text() and "<b>" not in block.text()
    assert "bold" in block.text()
    block._finalize_markdown()
    assert "bold" in block.text()


def _history_page(texts, t0):
    """Build a session_parts-shaped page: ascending timestamps, text parts."""
    from types import SimpleNamespace
    out = []
    for i, txt in enumerate(texts):
        msg = SimpleNamespace(role="user", created_at=float(t0 + i))
        part = SimpleNamespace(id=f"part-{txt}", type="text", data={"text": txt})
        out.append((msg, [part]))
    return out


def _transcript_texts(t):
    from bytebarn.app.transcript import TextBlock
    out = []
    for i in range(t._layout.count()):
        w = t._layout.itemAt(i).widget()
        if isinstance(w, TextBlock):
            out.append(w._raw)
        elif w is not None:  # user bubbles sit inside an alignment wrapper row
            out.extend(b._raw for b in w.findChildren(TextBlock))
    return out


def test_transcript_append_older_prepends_chronologically(qapp):
    from bytebarn.app.transcript import Transcript

    t = Transcript()
    t.load_history(_history_page(["c", "d"], 100))
    t.append_older(_history_page(["a", "b"], 50))
    assert _transcript_texts(t) == ["a", "b", "c", "d"]
    assert t.oldest_timestamp() == 50.0

    # a second older page keeps stacking above
    t.append_older(_history_page(["x", "y"], 10))
    assert _transcript_texts(t) == ["x", "y", "a", "b", "c", "d"]
    assert t.oldest_timestamp() == 10.0


def test_transcript_scroll_top_without_loader_is_safe(qapp):
    from bytebarn.app.transcript import Transcript

    t = Transcript()
    t.load_history(_history_page(["a"], 100))
    t._on_scroll(0)  # request_older not wired yet — must not raise

    calls = []
    t.request_older = lambda: calls.append(1)
    t._on_scroll(0)
    assert calls == [1]


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
    from bytebarn.app.session_list import SessionList

    sl = SessionList()
    picked: list[str] = []
    sl.session_selected.connect(picked.append)
    # sessions render flat beneath a time-bucket header, newest first
    import time as _time
    now = _time.time()
    a, b, c = _sess("a"), _sess("b"), _sess("c")
    a.updated_at, b.updated_at, c.updated_at = now, now - 60, now - 120
    sl.populate([_proj()], {"p1": [a, b, c]}, set(), "a")
    assert picked == []  # populate blocks signals
    bucket = sl.tree.topLevelItem(0)
    assert bucket.childCount() == 3
    sl.tree.setCurrentItem(bucket.child(1))
    assert picked == ["b"]
    sl.tree.setCurrentItem(bucket.child(2))
    assert picked == ["b", "c"]


def test_session_list_sessions_stay_within_projects(qapp):
    from PySide6.QtCore import Qt

    from bytebarn.app.session_list import SessionList

    sl = SessionList()
    projs = [_proj("p1", "Alpha"), _proj("p2", "Beta")]
    sl.populate(projs, {"p1": [_sess("a")], "p2": [_sess("b")]}, set(), "b",
                default_project_id="p1")
    # Projects section header + one "Today" bucket for the default project
    assert sl.tree.topLevelItemCount() == 2
    proj_header = sl.tree.topLevelItem(0)
    assert proj_header.data(0, Qt.UserRole + 1) == "header"
    beta = proj_header.child(0)
    assert beta.data(0, Qt.UserRole + 1) == "project"
    # Beta's session nests under Beta — not in the Recents buckets
    assert [beta.child(i).data(0, Qt.UserRole)
            for i in range(beta.childCount())] == ["b"]
    assert beta.isExpanded()  # contains the current session
    bucket = sl.tree.topLevelItem(1)
    assert bucket.text(0) == "Today"
    ids = {bucket.child(i).data(0, Qt.UserRole) for i in range(bucket.childCount())}
    assert ids == {"a"}
    # the current (project) session is the selected item
    assert sl.tree.currentItem().data(0, Qt.UserRole) == "b"


def test_session_list_buckets_by_recency(qapp):
    import time as _time

    from bytebarn.app.session_list import SessionList, bucket_label

    assert bucket_label(_time.time()) == "Today"
    assert bucket_label(_time.time() - 40 * 86400) == "Older"

    sl = SessionList()
    old = _sess("old")
    old.updated_at = _time.time() - 40 * 86400
    sl.populate([_proj()], {"p1": [_sess("new"), old]}, set(), "new")
    labels = [sl.tree.topLevelItem(i).text(0)
              for i in range(sl.tree.topLevelItemCount())]
    assert labels == ["Today", "Older"]


def test_session_list_hides_subagent_children(qapp):
    from PySide6.QtCore import Qt

    from bytebarn.app.session_list import SessionList

    sl = SessionList()
    child = _sess("kid")
    child.parent_session_id = "a"
    sl.populate([_proj()], {"p1": [_sess("a"), child]}, set(), "a")
    bucket = sl.tree.topLevelItem(0)
    ids = {bucket.child(i).data(0, Qt.UserRole) for i in range(bucket.childCount())}
    assert ids == {"a"}


def test_session_list_projects_have_no_folder_nodes(qapp):
    from PySide6.QtCore import Qt

    from bytebarn.app.session_list import SessionList

    sl = SessionList()
    projs = [_proj("p1", "Alpha"), _proj("p2", "Beta")]
    sl.populate(projs, {"p1": [_sess("a")], "p2": []}, set(), "a",
                default_project_id="p1")
    beta = sl.tree.topLevelItem(0).child(0)
    assert beta.data(0, Qt.UserRole + 1) == "project"
    assert beta.childCount() == 0  # empty project: sessions only, no folders


def test_session_list_double_click_opens_project(qapp):
    from bytebarn.app.session_list import SessionList

    sl = SessionList()
    opened: list = []
    sl.open_project.connect(opened.append)
    sl.populate([_proj("p1", "Alpha"), _proj("p2", "Beta")],
                {"p1": [_sess("a")], "p2": []}, set(), "a",
                default_project_id="p1")
    beta = sl.tree.topLevelItem(0).child(0)
    sl._on_double_click(beta)
    assert opened == ["p2"]
    # double-clicking a session does nothing
    bucket = sl.tree.topLevelItem(1)
    sl._on_double_click(bucket.child(0))
    assert opened == ["p2"]


def test_crew_stage_stop_signal(qapp):
    from bytebarn.app.crew_stage import CrewStage
    from bytebarn.engine.events import TaskStarted

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
    from bytebarn.app.session_list import SessionList

    sl = SessionList()
    renamed: list = []
    deleted: list = []
    sl.rename_project.connect(renamed.append)
    sl.delete_project.connect(deleted.append)
    sl.populate([_proj("p1", "Alpha")], {"p1": []}, set(), "")
    # context menu would emit; test the signals directly
    sl.rename_project.emit("p1")
    sl.delete_project.emit("p1")
    assert renamed == ["p1"]
    assert deleted == ["p1"]


def test_session_list_multi_select_delete(qapp):
    from bytebarn.app.session_list import SessionList

    sl = SessionList()
    emitted: list[list] = []
    sl.delete_sessions.connect(emitted.append)
    sl.populate([_proj()], {"p1": [_sess("a"), _sess("b"), _sess("c")]}, set(), "a")
    bucket = sl.tree.topLevelItem(0)
    for i in range(3):
        bucket.child(i).setSelected(True)
    ids = sl._selected_session_ids()
    assert set(ids) == {"a", "b", "c"}
    sl.delete_sessions.emit(ids)
    assert len(emitted) == 1 and set(emitted[0]) == {"a", "b", "c"}


def test_agent_list_grouped_by_mode(qapp, tmp_path):
    from PySide6.QtCore import Qt

    from bytebarn.app.agent_editor import AgentEditor
    from bytebarn.engine.facade import Engine

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
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj2"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db2.sqlite", global_dir=tmp_path / "g2")
    window = MainWindow(engine)
    titles = [a.menu().title() for a in window.menuBar().actions() if a.menu()]
    assert titles == ["&File", "&Projects", "&Session", "&Tools", "&Help"]


async def test_new_session_instant_inherits_directory(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    await engine.start()
    try:
        window = MainWindow(engine)

        # no context yet: instant creation rooted at the project dir, no modal
        await window._new_session()
        assert window.current_session_id is not None
        session = await engine.store.get_session(window.current_session_id)
        assert session.directory == str(engine.project_dir)

        # with an open session in another directory, a new one inherits it
        await engine.store.update_session(session.id, directory=str(workdir))
        message = await engine.store.add_message(session.id, "user")
        await engine.store.add_part(message.id, "text", {"text": "hi"})
        await window._new_session()
        assert window.current_session_id != session.id
        fresh = await engine.store.get_session(window.current_session_id)
        assert fresh.directory == str(workdir)

        # explicit directory still wins (New Session in Folder…)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        await window._new_session(directory=str(elsewhere))
        picked = await engine.store.get_session(window.current_session_id)
        assert picked.directory == str(elsewhere)
    finally:
        await engine.stop()


async def test_bootstrap_empty_shows_no_session(qapp, tmp_path, monkeypatch):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

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
    from bytebarn.app.model_picker import ModelPicker
    from bytebarn.app.settings import SettingsDialog
    from bytebarn.engine.facade import Engine

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
    from bytebarn.app.model_picker import ModelPicker
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj2"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db2.sqlite", global_dir=tmp_path / "g2")

    picker = ModelPicker(engine, allow_default=True)
    picker.set_model("")
    assert picker.provider_combo.currentText() == "(default)"
    assert picker.value() == ""  # (default) yields no explicit model
    assert not picker.model_combo.isEnabled()


async def test_last_model_persists_for_new_sessions(qapp, tmp_path, monkeypatch):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

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


async def test_project_dialog_roundtrips_knowledge(qapp, tmp_path):
    from bytebarn.app.project_dialog import ProjectDialog
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    await engine.start()
    try:
        dlg = ProjectDialog(engine, engine.project.id)
        await dlg._load_task
        assert dlg.name_edit.text() == "proj"

        dlg.instructions.setPlainText("Be terse.")
        dlg.name_edit.setText("Renamed")
        dlg._save()
        await dlg._save_task
        stored = await engine.store.get_project(engine.project.id)
        assert stored.instructions == "Be terse."
        assert stored.name == "Renamed"

        src = tmp_path / "k.md"
        src.write_text("knowledge")
        await engine.add_project_asset(engine.project.id, src)
        await dlg._reload_assets()
        assert dlg.assets.count() == 1
        assert "k.md" in dlg.assets.item(0).text()
    finally:
        await engine.stop()


async def test_project_workspace_tabs_and_data(qapp, tmp_path):
    from bytebarn.app.project_workspace import ProjectWorkspace
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    await engine.start()
    try:
        s1 = await engine.store.create_session(engine.project.id, title="chat one")
        goal = await engine.store.create_session(
            engine.project.id, agent="orchestrator", title="ship it")
        from bytebarn.engine.store import Todo
        await engine.store.set_todos(goal.id, [Todo("a", "completed"), Todo("b", "pending")])
        mem = engine.memory_dir(engine.project.id)
        mem.mkdir(parents=True)
        (mem / "fact.md").write_text("---\ntype: \"Note\"\n---\nremember me")
        await engine.store.set_project_defaults(engine.project.id, agent="plan")

        ws = ProjectWorkspace(engine)
        await ws.load(engine.project.id)

        assert [ws.tabs.tabText(i) for i in range(ws.tabs.count())] == \
            ["Chats", "Goals", "Memory", "Agents"]
        assert ws.chat_list.count() == 2
        assert ws.goal_list.count() == 1
        assert "1/2 todos done" in ws.goal_list.item(0).text()
        assert ws.memory_files.count() == 1
        ws.memory_files.setCurrentRow(0)
        assert "remember me" in ws.memory_editor.toPlainText()
        assert ws.default_agent.currentText() == "plan"

        # memory edit round-trips to disk
        ws.memory_editor.setPlainText("---\ntype: \"Note\"\n---\nupdated")
        ws._memory_save()
        assert "updated" in (mem / "fact.md").read_text()

        picked: list[str] = []
        ws.session_selected.connect(picked.append)
        ws._chat_clicked(ws.chat_list.item(0))
        assert picked and picked[0] in (s1.id, goal.id)
    finally:
        await engine.stop()


async def test_main_window_two_project_views(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    await engine.start()
    try:
        window = MainWindow(engine)
        await window.bootstrap()
        # standard view: single-project workspace
        assert window.sidebar.currentIndex() == 1
        assert window.workspace.project_id == engine.project.id

        # back -> all-projects view; opening a project returns to workspace
        window._show_all_projects()
        assert window.sidebar.currentIndex() == 0
        window._enter_workspace(engine.project.id)
        assert window.sidebar.currentIndex() == 1
    finally:
        for t in getattr(window, "_tasks", []):
            t.cancel()
        await engine.stop()


def test_theme_modes_and_modern_overhaul(qapp):
    from bytebarn.app import theme

    # dark is the flagship and still the non-modern path
    theme.apply_theme(qapp, "dark")
    assert theme.current_mode() == "dark" and not theme.is_modern()
    dark_qss = qapp.styleSheet()
    assert theme.ACCENT in dark_qss
    assert "QFrame#composer" in dark_qss and "QLabel#userBubble" in dark_qss

    # the Night Workshop variant: opt-in, amber accent, same geometry
    theme.apply_theme(qapp, "modern")
    assert theme.is_modern()
    qss = qapp.styleSheet()
    assert theme.MODERN_ACCENT in qss
    assert "QPushButton#send" in qss

    # switching back restores the dark sheet exactly
    theme.apply_theme(qapp, "dark")
    assert not theme.is_modern() and qapp.styleSheet() == dark_qss

    # tokens() follows the active mode (widget paint code relies on it)
    assert theme.tokens()["accent"] == theme.ACCENT


def test_theme_crossfade_animates_only_in_modern(qapp):
    from PySide6.QtWidgets import QLabel, QStackedWidget

    from bytebarn.app import theme

    stack = QStackedWidget()
    stack.addWidget(QLabel("a"))
    stack.addWidget(QLabel("b"))

    theme.apply_theme(qapp, "dark")
    theme.crossfade(stack, 1)
    assert stack.currentIndex() == 1
    assert stack.currentWidget().graphicsEffect() is None  # classic: no anim

    theme.apply_theme(qapp, "modern")
    theme.crossfade(stack, 0)
    assert stack.currentIndex() == 0
    assert stack.currentWidget().graphicsEffect() is not None  # fading in

    theme.apply_theme(qapp, "dark")


async def test_ui_toggle_button_switches_theme(qapp, tmp_path):
    from bytebarn.app import theme
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    await engine.start()
    try:
        window = MainWindow(engine)
        theme.apply_theme(qapp, "dark")
        window._toggle_ui()
        assert theme.is_modern()
        assert json.loads((gdir / "config.json").read_text())["theme"] == "modern"
        window._toggle_ui()
        assert not theme.is_modern()
        assert json.loads((gdir / "config.json").read_text())["theme"] == "dark"
    finally:
        await engine.stop()


async def test_run_review_dialog_diff_and_revert(qapp, tmp_path):
    from bytebarn.app.run_review import RunReviewDialog
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    await engine.start()
    try:
        target = proj / "main.py"
        target.write_text("before\n")
        engine.checkpoints.begin("s1")
        engine.checkpoints.snapshot("s1", target)
        target.write_text("after\n")
        engine.checkpoints.finish("s1")

        dlg = RunReviewDialog(engine, "s1")
        assert dlg.files.count() == 1
        assert "-before" in dlg.diff.toPlainText()
        assert "+after" in dlg.diff.toPlainText()

        dlg._revert_file()
        assert target.read_text() == "before\n"

        # session with no checkpoint -> empty-state message, no crash
        empty = RunReviewDialog(engine, "nope")
        assert "no reviewable" in empty.diff.toPlainText()
    finally:
        await engine.stop()


async def test_workspace_goal_queue_ui(qapp, tmp_path):
    from bytebarn.app.project_workspace import ProjectWorkspace
    from bytebarn.engine.facade import Engine
    from bytebarn.engine.providers.fake import FakeProvider, text_turn

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps(
        {"model": "fake/m", "small_model": "fake/m"}))
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    engine.providers.register("fake", FakeProvider([text_turn("ok")]))
    await engine.start()
    try:
        await engine.queue_goal("ship the feature")
        ws = ProjectWorkspace(engine)
        await ws.load(engine.project.id)
        assert ws.queue_list.count() == 1
        assert "ship the feature" in ws.queue_list.item(0).text()
        assert ws.queue_list.item(0).data(0x0101) == "running"  # UserRole + 1
        run = await engine.store.list_goals(engine.project.id)
        await engine._runs[run[0].session_id].task
    finally:
        await engine.stop()


async def test_context_meter_updates(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine
    from bytebarn.engine.providers.fake import FakeProvider, text_turn

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps(
        {"model": "fake/m", "small_model": "fake/m"}))
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    engine.providers.register("fake", FakeProvider([text_turn("hello"), text_turn("t")]))
    await engine.start()
    try:
        window = MainWindow(engine)
        session = await engine.new_session(directory=str(proj))
        await window._load_session(session.id)
        assert not window.context_meter.isVisible()  # no metered turn yet

        await engine.submit_prompt(session.id, "hi")
        await engine._runs[session.id].task
        await window._refresh_cost()
        assert "% ctx" in window.context_meter.text()
    finally:
        await engine.stop()


async def test_mcp_dialog_builds(qapp, tmp_path):
    from bytebarn.app.mcp_dialog import MCPDialog
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    await engine.start()
    try:
        dlg = MCPDialog(engine)
        # no servers configured -> friendly empty state
        assert dlg.tree.topLevelItemCount() == 1
        assert "no MCP servers" in dlg.tree.topLevelItem(0).text(0)
    finally:
        await engine.stop()


async def test_mcp_dialog_add_and_remove_server(qapp, tmp_path):
    from bytebarn.app.mcp_dialog import MCPDialog
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    await engine.start()
    try:
        dlg = MCPDialog(engine)
        # curated picks + Custom in the picker
        labels = [dlg.picker.itemText(i) for i in range(dlg.picker.count())]
        assert "GitHub" in labels and "Google Drive" in labels and "Custom…" in labels

        # GitHub selected -> a single secret token field
        dlg.picker.setCurrentText("GitHub")
        assert set(dlg._fields) == {"token"}
        dlg._fields["token"].setText("ghp_test")
        dlg._add()
        await asyncio.sleep(0.1)  # let the reconnect task settle
        config = json.loads((gdir / "config.json").read_text())
        assert config["mcp"]["github"]["headers"]["Authorization"] == "Bearer ghp_test"

        # custom entry -> command split into command/args
        dlg.picker.setCurrentText("Custom…")
        dlg._fields["name"].setText("memory")
        dlg._fields["command"].setText("npx -y @modelcontextprotocol/server-memory")
        dlg._add()
        await asyncio.sleep(0.1)
        config = json.loads((gdir / "config.json").read_text())
        assert config["mcp"]["memory"]["command"] == "npx"
        assert config["mcp"]["memory"]["args"][-1].endswith("server-memory")

        # remove via config patch (menu path shares this code)
        from bytebarn.engine.config import DELETE, patch_config_file
        patch_config_file(gdir / "config.json", {"mcp.github": DELETE})
        config = json.loads((gdir / "config.json").read_text())
        assert "github" not in config["mcp"]
        await asyncio.sleep(0.3)  # drain pending reconnect tasks before stop
    finally:
        await engine.stop()


async def test_persisted_full_auto_mode_applies_to_engine(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine
    from bytebarn.engine.permissions import FULL_AUTO

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps(
        {"model": "fake/m", "session_mode": "full"}))
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    await engine.start()
    try:
        window = MainWindow(engine)
        # the persisted mode must reach the ENGINE, not just the combo box
        assert window.mode_combo.currentIndex() == 2
        assert engine.session_mode == FULL_AUTO
        # and the policy the runner consults must auto-allow
        policy = engine.policy_for(engine.agents.get("build"))
        assert policy.resolve("bash", "rm -rf build") == "allow"
    finally:
        await engine.stop()


def test_waifu_mode_swaps_sprites(qapp):
    from PySide6.QtGui import QImage

    from bytebarn.app import sprites

    def render() -> QImage:
        pixmap = sprites.critter_pixmap("build", "#61afef", scale=4)
        return pixmap.toImage()

    sprites.set_waifu(False)
    critter = render()
    sprites.set_waifu(True)
    try:
        waifu = render()
        assert waifu != critter                       # different art
        assert any(waifu.pixel(x, y) != 0             # actually drew something
                   for x in range(waifu.width()) for y in range(0, waifu.height(), 5))
        # all four hairstyles render for every state without errors
        from PySide6.QtGui import QColor, QPainter

        image = QImage(300, 300, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        for i, species in enumerate(sprites.SPECIES):
            for j, state in enumerate(("working", "retrying", "done", "waiting")):
                sprites.draw_critter(painter, 10 + i * 60, 10 + j * 60, 4,
                                     species, QColor("#e5a458"), state=state,
                                     frame=7, crowned=(i == 0), accent="bow")
        painter.end()
    finally:
        sprites.set_waifu(False)
    assert not sprites.waifu_enabled()


async def test_settings_waifu_toggle_persists(qapp, tmp_path):
    from bytebarn.app import sprites
    from bytebarn.app.settings import SettingsDialog
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    try:
        dlg = SettingsDialog(engine)
        assert dlg.crew_style.currentText() == "farm"
        dlg.crew_style.setCurrentText("waifu")
        dlg._save()
        config = json.loads((gdir / "config.json").read_text())
        assert config["crew_style"] == "waifu"
        assert sprites.waifu_enabled()

        dlg2 = SettingsDialog(engine)
        dlg2.crew_style.setCurrentText("dogs")
        dlg2._save()
        assert sprites.crew_style() == "dogs"
    finally:
        sprites.set_crew_style("farm")


def test_dog_and_cat_modes_render_distinct_breeds(qapp):
    from PySide6.QtGui import QColor, QImage, QPainter

    from bytebarn.app import sprites

    def render(style: str, species: str) -> QImage:
        sprites.set_crew_style(style)
        image = QImage(80, 80, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        sprites.draw_critter(painter, 5, 15, 5, species, QColor("#61afef"),
                             state="working", frame=7)
        painter.end()
        return image

    try:
        # every species slot renders in both modes, and breeds differ
        for style in ("dogs", "cats"):
            images = [render(style, s) for s in sprites.SPECIES]
            assert all(any(img.pixel(x, y) != 0 for x in range(80)
                           for y in range(0, 80, 4)) for img in images)
            assert len({img.cacheKey() for img in images}) == 4  # sanity
            assert images[0] != images[2]        # distinct breed grids
        # a dog-mode render differs from critter mode for the same agent
        dogs = render("dogs", "bear")
        sprites.set_crew_style("critters")
        critter = render("critters", "bear")
        assert dogs != critter
        # farm mode (the default) renders its own distinct grids
        farm = render("farm", "bear")
        assert farm != critter and farm != dogs
    finally:
        sprites.set_crew_style("farm")


async def test_crew_stage_resizable_and_persisted(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps(
        {"model": "fake/m", "stage_height": 240}))
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    await engine.start()
    try:
        window = MainWindow(engine)
        window.resize(1100, 800)
        window.show()
        # stage appears (goal starts) -> persisted height restored
        window.crew_stage.setVisible(True)
        window._restore_stage_height()
        assert window.stage_split.sizes()[1] == 240
        # no fixed height anymore — the pane can grow and shrink
        assert window.crew_stage.minimumHeight() <= 120
        assert window.crew_stage.maximumHeight() > 100000

        # drag simulation: set sizes then persist path
        window.stage_split.setSizes([500, 300])
        window._save_stage_height()
        config = json.loads((gdir / "config.json").read_text())
        assert config["stage_height"] == window.stage_split.sizes()[1] > 0
    finally:
        await engine.stop()


async def test_load_live_models_preserves_selection(qapp, tmp_path):
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "p"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    engine.providers.auth.set("groq", {"type": "api", "key": "gsk-x"})
    await engine.start()
    try:
        window = MainWindow(engine)
        window.prompt_bar.set_providers(["groq"], "groq")
        window.prompt_bar.set_models(["keep-me", "other"], "keep-me")
        assert window.prompt_bar.current_model() == "groq/keep-me"

        async def fake_list(provider, force=False):
            return ["other", "brand-new"]  # keep-me NOT in live

        engine.list_models = fake_list
        await window._load_live_models("groq")
        # keep-me must still be selected (prepended), not swapped to other/brand-new
        assert window.prompt_bar.current_model() == "groq/keep-me"

        # empty selection must stay empty (not auto models[0])
        window.prompt_bar.set_models(["a", "b"], "")
        assert window.prompt_bar.current_model() == ""
        await window._load_live_models("groq")
        assert window.prompt_bar.current_model() == ""
    finally:
        for task in getattr(window, "_tasks", []):
            task.cancel()
        await engine.stop()


async def test_model_picker_empty_current_does_not_autopick(qapp, tmp_path):
    from bytebarn.app.model_picker import ModelPicker
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "p"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    engine.providers.auth.set("openai", {"type": "api", "key": "sk-x"})
    await engine.start()
    try:
        picker = ModelPicker(engine)
        # direct construction leaves combos empty; simulate provider set with no model
        picker._set_provider_models("openai", "")
        assert picker.model_combo.currentText() == ""
    finally:
        await engine.stop()


async def test_refresh_pickers_preserves_provider_on_bare_model_id(qapp, tmp_path):
    """AgentRegistryChanged used to pass model_combo bare text, flipping provider."""
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "p"
    proj.mkdir()
    g = tmp_path / "g"
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=g)
    engine.providers.auth.set("groq", {"type": "api", "key": "gsk-x"})
    engine.providers.auth.set("anthropic", {"type": "api", "key": "sk-x"})
    # remember a non-anthropic default
    from bytebarn.engine.config import patch_config_file
    patch_config_file(g / "config.json", {"last_model": "groq/llama-3.1-8b-instant", "model": "groq/llama-3.1-8b-instant"})
    engine.reload_config()
    await engine.start()
    try:
        window = MainWindow(engine)
        window.prompt_bar.set_providers(["anthropic", "groq"], "groq")
        window.prompt_bar.set_models(["llama-3.1-8b-instant", "llama-3.3-70b-versatile"], "llama-3.1-8b-instant")
        assert window.prompt_bar.provider_combo.currentText() == "groq"
        assert window.prompt_bar.current_model() == "groq/llama-3.1-8b-instant"

        # simulate the OLD buggy call shape (bare model id) — must NOT flip to anthropic
        window._refresh_pickers("build", window.prompt_bar.model_combo.currentText())
        assert window.prompt_bar.provider_combo.currentText() == "groq"
        assert "llama-3.1-8b-instant" in window.prompt_bar.model_combo.currentText()

        # and the fixed call shape also works
        window._refresh_pickers("build", window.prompt_bar.current_model())
        assert window.prompt_bar.provider_combo.currentText() == "groq"
    finally:
        for task in getattr(window, "_tasks", []):
            task.cancel()
        await engine.stop()


async def test_switching_provider_clears_previous_model(qapp, tmp_path):
    """Selecting a new provider must not keep the old provider's model shown."""
    from bytebarn.app.main_window import MainWindow
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "p"
    proj.mkdir()
    engine = Engine(proj, db_path=tmp_path / "db.sqlite", global_dir=tmp_path / "g")
    engine.providers.auth.set("groq", {"type": "api", "key": "gsk-x"})
    engine.providers.auth.set("xai", {"type": "api", "key": "xai-x"})
    await engine.start()
    try:
        window = MainWindow(engine)
        # start on xai with a model selected
        window.prompt_bar.set_providers(["xai", "groq"], "xai")
        window.prompt_bar.set_models(["grok-4.5", "grok-4"], "grok-4.5")
        assert window.prompt_bar.current_model() == "xai/grok-4.5"

        # user switches provider to groq — simulate provider_combo change
        window.prompt_bar.provider_combo.setCurrentText("groq")  # fires provider_changed
        window._provider_changed("groq")  # ensure handler ran (blockSignals-safe)

        # the old xai model must NOT be shown for groq
        shown = window.prompt_bar.model_combo.currentText().strip()
        assert shown != "grok-4.5", f"stale model still shown: {shown}"
        # current_model is either empty or a groq model, never xai/grok-4.5
        cm = window.prompt_bar.current_model()
        assert cm == "" or cm.startswith("groq/"), cm
    finally:
        for task in getattr(window, "_tasks", []):
            task.cancel()
        await engine.stop()


def test_transcript_search_finds_and_steps(qapp):
    from bytebarn.app.transcript import Transcript

    t = Transcript()
    t.load_history(_history_page(["alpha beast", "gamma", "beta again"], 100))
    assert t.search("bet") == 1          # only "beta again" contains 'bet'
    assert t.search("a") >= 2
    first = t.search_step(1)
    second = t.search_step(1)
    assert first != second or len(t._matches) == 1
    t.clear_search()
    assert t._matches == []
    assert t.search("") == 0


def test_prompt_bar_attachment_chips(qapp, tmp_path):
    from bytebarn.app.prompt_bar import PromptBar

    bar = PromptBar(attachments_dir=tmp_path)
    bar.add_attachments([str(tmp_path / "a.png"), str(tmp_path / "b.txt")])
    bar.add_attachments([str(tmp_path / "a.png")])  # dedup
    assert len(bar._attachments) == 2
    assert bar._chips.count() == 3  # two chips + stretch
    taken = bar.take_attachments()
    assert [p.endswith(("a.png", "b.txt")) for p in taken] == [True, True]
    assert bar._attachments == [] and bar._chips.count() == 1

    # pasted image lands in the attachments dir as a png
    from PySide6.QtGui import QImage

    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(0xFF0000)
    bar._add_pasted_image(image)
    assert len(bar._attachments) == 1
    assert bar._attachments[0].endswith(".png")
    from pathlib import Path as _P

    assert _P(bar._attachments[0]).exists()


def test_session_list_content_search_results(qapp):
    from types import SimpleNamespace

    from bytebarn.app.session_list import SessionList

    sl = SessionList()
    session = SimpleNamespace(id="s1", title="found me", agent="build",
                              model="", updated_at=0.0, directory="",
                              parent_session_id=None)
    sl.show_search_results([(session, "matching snippet")], set(), {})
    header = sl.tree.topLevelItem(0)
    assert "Search results" in header.text(0)
    assert header.childCount() == 1
    sl.show_search_results([], set(), {})
    assert "No matches" in sl.tree.topLevelItem(0).text(0)


def test_transcript_stamps_message_ids_for_edit(qapp):
    from bytebarn.app.transcript import TextBlock, Transcript
    from types import SimpleNamespace

    t = Transcript()
    msg = SimpleNamespace(role="user", created_at=1.0, id="m-42")
    part = SimpleNamespace(id="p1", type="text", data={"text": "hi"})
    t.load_history([(msg, [part])])
    block = t._part_widgets["p1"]
    assert isinstance(block, TextBlock)
    assert block.property("message_id") == "m-42"
