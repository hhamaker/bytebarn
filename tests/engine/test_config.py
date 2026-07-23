import json

from bytebarn.engine.config import (
    DELETE,
    Config,
    deep_merge,
    lenient_json_loads,
    load_config,
    normalize_permission,
    patch_config_file,
)


def test_lenient_parse_comments_and_trailing_commas():
    text = """
    {
      // default model
      "model": "anthropic/claude-sonnet-4-5",
      "agent": {
        "build": { "temperature": 0.2, },  // trailing comma
      },
    }
    """
    data = lenient_json_loads(text)
    assert data["model"] == "anthropic/claude-sonnet-4-5"
    assert data["agent"]["build"]["temperature"] == 0.2


def test_lenient_parse_slashes_inside_strings():
    data = lenient_json_loads('{"url": "http://x//y", "p": "a,"}')
    assert data["url"] == "http://x//y"
    assert data["p"] == "a,"


def test_deep_merge_project_wins_per_key():
    base = {"model": "a/m1", "permission": {"bash": "ask", "edit": "allow"}}
    override = {"permission": {"bash": "deny"}}
    merged = deep_merge(base, override)
    assert merged["model"] == "a/m1"
    assert merged["permission"] == {"bash": "deny", "edit": "allow"}


def test_load_config_layering(tmp_path):
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({"model": "g/model", "small_model": "g/small"}))
    proj = tmp_path / "proj"
    (proj / ".bytebarn").mkdir(parents=True)
    (proj / ".bytebarn" / "config.json").write_text(json.dumps({"model": "p/model"}))
    cfg = load_config(proj, global_dir=gdir)
    assert cfg.model == "p/model"
    assert cfg.small_model == "g/small"
    # defaults still present
    assert "anthropic" in cfg.provider


def test_permission_normalization():
    rule = normalize_permission("allow")
    assert rule.default == "allow" and rule.allow == []
    rule = normalize_permission({"default": "ask", "allow": ["git status*"], "deny": ["rm -rf*"]})
    assert rule.deny == ["rm -rf*"]
    cfg = Config(permission={"edit": "allow", "bash": {"default": "ask", "allow": ["ls*"]}})
    rules = cfg.permission_rules()
    assert rules["edit"].default == "allow"
    assert rules["bash"].allow == ["ls*"]


def test_patch_existing_key_preserves_comments(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        '{\n'
        '  // my comment\n'
        '  "model": "old/model",\n'
        '  "agent": {\n'
        '    "build": { "temperature": 0.2 }\n'
        '  }\n'
        '}\n'
    )
    patch_config_file(path, {"agent.build.temperature": 0.7})
    text = path.read_text()
    assert "// my comment" in text
    assert '"model": "old/model"' in text
    assert lenient_json_loads(text)["agent"]["build"]["temperature"] == 0.7


def test_patch_inserts_missing_nested_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{\n  // keep me\n  "model": "a/m"\n}\n')
    patch_config_file(path, {"agent.build.model": "openai/gpt-x"})
    text = path.read_text()
    assert "// keep me" in text
    data = lenient_json_loads(text)
    assert data["agent"]["build"]["model"] == "openai/gpt-x"
    assert data["model"] == "a/m"


def test_patch_creates_file(tmp_path):
    path = tmp_path / "sub" / "config.json"
    patch_config_file(path, {"agent.tester.color": "#61afef"})
    assert lenient_json_loads(path.read_text())["agent"]["tester"]["color"] == "#61afef"


def test_patch_delete_key(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        '{\n'
        '  "agent": {\n'
        '    "build": {\n'
        '      "model": "x/y", // override\n'
        '      "temperature": 0.2\n'
        '    }\n'
        '  }\n'
        '}\n'
    )
    patch_config_file(path, {"agent.build.model": DELETE})
    data = lenient_json_loads(path.read_text())
    assert "model" not in data["agent"]["build"]
    assert data["agent"]["build"]["temperature"] == 0.2


def test_patch_roundtrip_multiple_updates(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{\n  "permission": { "bash": { "allow": ["ls*"] } }\n}\n')
    patch_config_file(path, {"permission.bash.allow": ["ls*", "git status*"]})
    data = lenient_json_loads(path.read_text())
    assert data["permission"]["bash"]["allow"] == ["ls*", "git status*"]
