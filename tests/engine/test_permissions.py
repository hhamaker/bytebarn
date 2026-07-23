from bytebarn.engine.permissions import ALLOW, ASK, DENY, FULL_AUTO, SAFE, PermissionPolicy


def test_defaults_ask_for_dangerous_tools():
    p = PermissionPolicy()
    assert p.resolve("bash", "ls") == ASK
    assert p.resolve("edit", "/x.py") == ASK
    assert p.resolve("read", "/x.py") == ALLOW
    assert p.resolve("grep", "foo") == ALLOW


def test_deny_beats_allow_beats_default():
    p = PermissionPolicy({"bash": {"default": "ask", "allow": ["git *", "rm *"], "deny": ["rm -rf*"]}})
    assert p.resolve("bash", "git status") == ALLOW
    assert p.resolve("bash", "rm -rf /") == DENY
    assert p.resolve("bash", "rm x.txt") == ALLOW
    assert p.resolve("bash", "make build") == ASK


def test_string_shorthand():
    p = PermissionPolicy({"edit": "allow", "webfetch": "deny"})
    assert p.resolve("edit", "any/path.py") == ALLOW
    assert p.resolve("webfetch", "http://x") == DENY


def test_agent_override_wins():
    p = PermissionPolicy({"bash": "ask"}, agent_permission={"bash": "allow"})
    assert p.resolve("bash", "anything") == ALLOW


def test_path_patterns_for_write():
    p = PermissionPolicy({"write": {"default": "ask", "allow": ["*.md"], "deny": ["/etc/*"]}})
    assert p.resolve("write", "notes.md") == ALLOW
    assert p.resolve("write", "/etc/passwd") == DENY
    assert p.resolve("write", "src/app.py") == ASK


def test_session_presets():
    p = PermissionPolicy({"bash": "allow"}, session_mode=SAFE)
    assert p.resolve("bash", "ls") == DENY
    assert p.resolve("read", "x") == ALLOW
    p = PermissionPolicy({"bash": "deny"}, session_mode=FULL_AUTO)
    assert p.resolve("bash", "anything") == ALLOW


def test_allow_always_session_effect():
    p = PermissionPolicy({"bash": "ask"})
    assert p.resolve("bash", "git status") == ASK
    p.with_added_allow("bash", "git status*")
    assert p.resolve("bash", "git status --short") == ALLOW


def test_session_mode_is_live_when_callable():
    from bytebarn.engine.permissions import ASK_MODE, FULL_AUTO, PermissionPolicy

    mode = {"value": ASK_MODE}
    policy = PermissionPolicy({}, {}, session_mode=lambda: mode["value"])
    assert policy.resolve("bash", "rm -rf /tmp/x") == "ask"
    # flipping the mode mid-run affects an already-created policy
    mode["value"] = FULL_AUTO
    assert policy.resolve("bash", "rm -rf /tmp/x") == "allow"
