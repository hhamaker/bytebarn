"""Hard rule (spec §3): crew/engine must never import Qt."""

import subprocess
import sys


def test_engine_importable_without_qt():
    code = (
        "import sys\n"
        "sys.modules['PySide6'] = None  # any Qt import will explode\n"
        "import bytebarn.engine.facade, bytebarn.engine.runner, bytebarn.engine.compaction\n"
        "import bytebarn.engine.tools.registry, bytebarn.engine.agents, bytebarn.engine.commands\n"
        "import bytebarn.cli\n"
        "qt = [m for m in sys.modules if m.startswith('PySide6.')]\n"
        "assert not qt, f'engine imported Qt modules: {qt}'\n"
        "print('clean')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
