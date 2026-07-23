from crew.engine.agents import AgentRegistry, builtin_agents, parse_agent_file
from crew.engine.commands import CommandRegistry
from crew.engine.config import Config


def test_builtin_agents_present_and_restricted():
    agents = builtin_agents()
    assert set(agents) == {"build", "chat", "plan", "orchestrator", "general",
                           "explore", "research"}
    # chat is conversation-only: every tool disabled
    assert agents["chat"].tools == {}
    # research is web-only: can search and read pages, never write files
    research = agents["research"]
    assert research.tools.get("websearch") and research.tools.get("webfetch")
    assert not research.tools.get("edit") and not research.tools.get("write")
    assert not research.tools.get("bash")
    orch = agents["orchestrator"]
    assert orch.mode == "primary"
    assert orch.tools and not orch.tools.get("edit") and not orch.tools.get("write")
    assert orch.tools.get("task")
    assert "orchestrator" in orch.prompt.lower()
    assert agents["build"].tools is None  # all tools


def test_parse_agent_file(tmp_path):
    f = tmp_path / "tester.md"
    f.write_text(
        "---\n"
        "description: Writes and runs tests\n"
        "mode: subagent\n"
        "model: lmstudio/qwen3\n"
        "temperature: 0.1\n"
        "steps: 50\n"
        'color: "#61afef"\n'
        "tools: { read: true, bash: true }\n"
        'permission: { bash: "allow" }\n'
        "---\n"
        "You are the TESTER.\n"
    )
    front, body = parse_agent_file(f)
    assert front["model"] == "lmstudio/qwen3"
    assert front["tools"] == {"read": True, "bash": True}
    assert body == "You are the TESTER."


def _registry(tmp_path, config=None, with_files=None):
    gdir = tmp_path / "global"
    pdir = tmp_path / "proj"
    (gdir / "agent").mkdir(parents=True)
    (pdir / ".crew" / "agent").mkdir(parents=True)
    for scope, name, text in with_files or []:
        base = gdir / "agent" if scope == "global" else pdir / ".crew" / "agent"
        (base / f"{name}.md").write_text(text)
    return AgentRegistry(config or Config(), project_dir=pdir, global_dir=gdir)


def test_discovery_adds_subagent(tmp_path):
    reg = _registry(tmp_path, with_files=[
        ("project", "tester", "---\ndescription: runs tests\n---\nYou test.")
    ])
    agent = reg.get("tester")
    assert agent.mode == "subagent" and agent.prompt == "You test."
    assert ("tester", "runs tests") in reg.subagent_descriptions()


def test_file_merges_over_builtin_and_project_beats_global(tmp_path):
    reg = _registry(tmp_path, with_files=[
        ("global", "build", "---\nmodel: g/model\ntemperature: 0.9\n---\n"),
        ("project", "build", "---\nmodel: p/model\n---\n"),
    ])
    build = reg.get("build")
    assert build.model == "p/model"        # project wins
    assert build.temperature == 0.9        # global merge preserved
    assert build.builtin and build.prompt  # builtin prompt kept (empty body ignored)


def test_config_overrides_win(tmp_path):
    cfg = Config(agent={"build": {"model": "cfg/model", "color": "#ff0000"}})
    reg = _registry(tmp_path, config=cfg, with_files=[
        ("project", "build", "---\nmodel: p/model\n---\n"),
    ])
    build = reg.get("build")
    assert build.model == "cfg/model"
    assert build.color == "#ff0000"


def test_hidden_excluded_from_lists(tmp_path):
    reg = _registry(tmp_path, with_files=[
        ("project", "secret", "---\ndescription: x\nhidden: true\n---\nY")
    ])
    assert "secret" not in [a.name for a in reg.subagents()]
    assert reg.get("secret").hidden


def test_command_registry(tmp_path):
    gdir = tmp_path / "g"
    pdir = tmp_path / "p"
    (pdir / ".crew" / "command").mkdir(parents=True)
    (pdir / ".crew" / "command" / "review.md").write_text(
        "---\ndescription: review code\nagent: plan\n---\nReview: $ARGUMENTS"
    )
    reg = CommandRegistry(project_dir=pdir, global_dir=gdir)
    goal = reg.get("goal")
    assert goal.agent == "orchestrator"
    rendered = goal.render("add tests")
    assert "<goal>\nadd tests\n</goal>" in rendered
    review = reg.get("review")
    assert review.render("main.py") == "Review: main.py"
    assert reg.get("compact").action == "compact"


def test_user_command_overrides_builtin(tmp_path):
    gdir = tmp_path / "g"
    pdir = tmp_path / "p"
    (pdir / ".crew" / "command").mkdir(parents=True)
    (pdir / ".crew" / "command" / "goal.md").write_text("---\ndescription: my goal\n---\nCustom $ARGUMENTS")
    reg = CommandRegistry(project_dir=pdir, global_dir=gdir)
    assert reg.get("goal").render("x") == "Custom x"
