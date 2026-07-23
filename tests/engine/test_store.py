import pytest

from bytebarn.engine.store import Store, Todo


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    await s.open()
    yield s
    await s.close()


async def test_project_roundtrip(store, tmp_path):
    p1 = await store.open_project(str(tmp_path), "proj")
    p2 = await store.open_project(str(tmp_path))
    assert p1.id == p2.id
    assert p2.name == "proj"


async def test_project_folders(store, tmp_path):
    proj = await store.add_project("catalog:Multi", name="Multi")
    assert await store.list_project_folders(proj.id) == []

    await store.add_project_folder(proj.id, tmp_path / "a")
    await store.add_project_folder(proj.id, tmp_path / "b")
    await store.add_project_folder(proj.id, tmp_path / "a")  # dup ignored
    folders = await store.list_project_folders(proj.id)
    assert folders == sorted([str(tmp_path / "a"), str(tmp_path / "b")])

    await store.remove_project_folder(proj.id, tmp_path / "a")
    assert await store.list_project_folders(proj.id) == [str(tmp_path / "b")]


async def test_rename_and_delete_project(store, tmp_path):
    p = await store.add_project("catalog:Test", name="Old")
    await store.create_session(p.id, title="s1")
    await store.add_project_folder(p.id, tmp_path)

    await store.rename_project(p.id, "New")
    renamed = await store.list_projects()
    assert renamed[0].name == "New"

    await store.delete_project(p.id)
    assert await store.list_projects() == []
    assert await store.list_sessions(p.id) == []


async def test_session_message_parts_ordering(store, tmp_path):
    proj = await store.open_project(str(tmp_path))
    sess = await store.create_session(proj.id, agent="build", model="a/m")
    msg = await store.add_message(sess.id, "assistant", model="m", provider="a")
    p0 = await store.add_part(msg.id, "text", {"text": "hello"})
    p1 = await store.add_part(msg.id, "tool", {"tool": "bash", "status": "pending"})
    parts = await store.list_parts(msg.id)
    assert [p.type for p in parts] == ["text", "tool"]
    assert parts[0].data["text"] == "hello"

    await store.update_part(p1.id, {"tool": "bash", "status": "done", "output": "ok"})
    parts = await store.list_parts(msg.id)
    assert parts[1].data["status"] == "done"

    history = await store.session_parts(sess.id)
    assert len(history) == 1
    assert history[0][0].id == msg.id
    assert p0.idx == 0 and p1.idx == 1


async def test_child_sessions(store, tmp_path):
    proj = await store.open_project(str(tmp_path))
    parent = await store.create_session(proj.id)
    child = await store.create_session(proj.id, agent="explore", parent_session_id=parent.id)
    kids = await store.child_sessions(parent.id)
    assert [k.id for k in kids] == [child.id]
    # top-level listing excludes children
    tops = await store.list_sessions(proj.id)
    assert [s.id for s in tops] != [] and child.id not in [s.id for s in tops]


async def test_todo_replace(store, tmp_path):
    proj = await store.open_project(str(tmp_path))
    sess = await store.create_session(proj.id)
    await store.set_todos(sess.id, [Todo("a"), Todo("b", "in_progress")])
    todos = await store.get_todos(sess.id)
    assert [(t.content, t.status) for t in todos] == [("a", "pending"), ("b", "in_progress")]
    await store.set_todos(sess.id, [Todo("c", "completed")])
    todos = await store.get_todos(sess.id)
    assert [(t.content, t.status) for t in todos] == [("c", "completed")]


async def test_update_session_title(store, tmp_path):
    proj = await store.open_project(str(tmp_path))
    sess = await store.create_session(proj.id)
    await store.update_session(sess.id, title="My session")
    got = await store.get_session(sess.id)
    assert got.title == "My session"


async def test_archive_session_hides_from_list(store, tmp_path):
    proj = await store.open_project(str(tmp_path))
    sess = await store.create_session(proj.id)
    await store.update_session(sess.id, archived=1)
    assert sess.id not in [s.id for s in await store.list_sessions(proj.id)]
    # still retrievable directly
    got = await store.get_session(sess.id)
    assert got is not None and got.archived


async def test_delete_session_cascades(store, tmp_path):
    proj = await store.open_project(str(tmp_path))
    parent = await store.create_session(proj.id)
    child = await store.create_session(proj.id, parent_session_id=parent.id)
    msg = await store.add_message(parent.id, "user")
    await store.add_part(msg.id, "text", {"text": "hi"})
    await store.set_todos(parent.id, [Todo("a")])

    await store.delete_session(parent.id)

    assert await store.get_session(parent.id) is None
    assert await store.get_session(child.id) is None
    assert await store.list_messages(parent.id) == []
    assert await store.get_todos(parent.id) == []


async def test_session_directory_roundtrip(store, tmp_path):
    proj = await store.open_project(str(tmp_path))
    sess = await store.create_session(proj.id, directory="/some/other/dir")
    got = await store.get_session(sess.id)
    assert got.directory == "/some/other/dir"
    # default: empty = project dir
    plain = await store.create_session(proj.id)
    assert (await store.get_session(plain.id)).directory == ""
    await store.update_session(sess.id, directory="/moved")
    assert (await store.get_session(sess.id)).directory == "/moved"


async def test_session_parts_limit_counts_messages_not_rows(store, tmp_path):
    p = await store.open_project(str(tmp_path))
    sess = await store.create_session(p.id)
    for i in range(5):
        msg = await store.add_message(sess.id, "assistant")
        for j in range(3):  # multi-part messages must not shrink the page
            await store.add_part(msg.id, "text", {"text": f"m{i}p{j}"})

    page = await store.session_parts(sess.id, limit=3)
    assert len(page) == 3                      # 3 messages, not 3 joined rows
    assert all(len(parts) == 3 for _, parts in page)
    # chronological order, most recent 3 messages
    texts = [parts[0].data["text"] for _, parts in page]
    assert texts == ["m2p0", "m3p0", "m4p0"]

    # pagination continues cleanly below the page boundary
    older = await store.session_parts(sess.id, limit=3, before=page[0][0].created_at)
    assert [parts[0].data["text"] for _, parts in older] == ["m0p0", "m1p0"]
    assert all(len(parts) == 3 for _, parts in older)


async def test_project_instructions_and_assets(store, tmp_path):
    p = await store.add_project("catalog:Docs", name="Docs")
    assert (await store.get_project(p.id)).instructions == ""

    await store.set_project_instructions(p.id, "Always answer in Spanish.")
    assert (await store.get_project(p.id)).instructions == "Always answer in Spanish."

    f = tmp_path / "notes.md"
    f.write_text("hello")
    await store.add_project_asset(p.id, f, "notes.md")
    assets = await store.list_project_assets(p.id)
    assert [(a.name, a.path) for a in assets] == [("notes.md", str(f))]

    await store.remove_project_asset(p.id, f)
    assert await store.list_project_assets(p.id) == []

    # delete cascades asset rows
    await store.add_project_asset(p.id, f, "notes.md")
    await store.delete_project(p.id)
    assert await store.list_project_assets(p.id) == []
