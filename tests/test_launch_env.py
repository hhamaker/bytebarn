"""Foreign-Qt environment scrubbing (the two-Qt startup abort)."""

from bytebarn.main import scrub_qt_env

_OURS = "/Users/dev/proj/.venv/lib/python3.12/site-packages/PySide6"
_APP = "/Applications/Crew.app/Contents/Frameworks/PySide6"


def test_foreign_plugin_paths_removed():
    env = {
        "QT_PLUGIN_PATH": f"{_APP}/Qt/plugins",
        "QT_QPA_PLATFORM_PLUGIN_PATH": f"{_APP}/Qt/plugins/platforms",
    }
    removed = scrub_qt_env(env, _OURS)
    assert set(removed) == {"QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"}
    assert "QT_PLUGIN_PATH" not in env


def test_own_plugin_path_kept():
    env = {"QT_QPA_PLATFORM_PLUGIN_PATH": f"{_OURS}/Qt/plugins/platforms"}
    assert scrub_qt_env(env, _OURS) == []
    assert env  # untouched


def test_dyld_entries_filtered_not_nuked():
    env = {"DYLD_FRAMEWORK_PATH":
           f"{_APP}/Qt/lib:/usr/local/lib:{_OURS}/Qt/lib"}
    removed = scrub_qt_env(env, _OURS)
    assert removed == ["DYLD_FRAMEWORK_PATH"]
    # foreign Qt entry gone; unrelated + our own entries survive
    assert env["DYLD_FRAMEWORK_PATH"] == f"/usr/local/lib:{_OURS}/Qt/lib"


def test_headless_platform_dropped_for_gui_launch():
    env = {"QT_QPA_PLATFORM": "offscreen"}
    assert scrub_qt_env(env, _OURS) == ["QT_QPA_PLATFORM"]
    assert "QT_QPA_PLATFORM" not in env

    env = {"QT_QPA_PLATFORM": "cocoa"}
    assert scrub_qt_env(env, _OURS) == []
    assert env["QT_QPA_PLATFORM"] == "cocoa"


def test_clean_environment_untouched():
    env = {"PATH": "/usr/bin", "HOME": "/Users/dev"}
    assert scrub_qt_env(env, _OURS) == []
    assert env == {"PATH": "/usr/bin", "HOME": "/Users/dev"}
